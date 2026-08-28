"""Finance Q&A skill: answer read-only questions about order history.

Classifies the question into a small fixed set of supported query shapes
(order count in a date range, revenue sum in a date range), runs the
corresponding read-only SQLModel query, and returns the answer. The LLM
is only used to phrase the answer in natural language — never to decide
what data to fetch or compute the numbers.
"""

from datetime import datetime, timedelta

from sqlmodel import select, func

from merchant_agent.db import get_readonly_session
from merchant.models import Order

_SUPPORTED_SHAPES = {"order_count", "revenue_total"}


def _classify_question(question: str) -> str:
    """Classify a finance question into a supported query shape.

    Uses simple keyword matching — no LLM call, no free-form SQL.
    """
    q = question.lower()
    if any(w in q for w in ("revenue", "total", "earn", "income", "money", "sales")):
        return "revenue_total"
    if any(w in q for w in ("order", "count", "how many", "number of")):
        return "order_count"
    return "order_count"


def _parse_date_range(question: str) -> tuple[datetime, datetime]:
    """Extract a rough date range from the question text.

    Supports: 'today', 'this week', 'this month', 'last 7 days', 'last 30 days'.
    Falls back to last 7 days.
    """
    q = question.lower()
    now = datetime.utcnow()

    if "today" in q:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now

    if "this week" in q:
        start = now - timedelta(days=now.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now

    if "this month" in q:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, now

    if "last 30" in q or "past 30" in q or "last month" in q:
        return now - timedelta(days=30), now

    if "last 7" in q or "past 7" in q or "last week" in q:
        return now - timedelta(days=7), now

    return now - timedelta(days=7), now


def finance_qa_skill(question: str) -> dict:
    """Answer a read-only finance question using parameterized queries.

    Args:
        question: natural language question about orders/revenue

    Returns:
        {"answer_text": str, "figures": dict}
    """
    shape = _classify_question(question)
    start, end = _parse_date_range(question)

    session = get_readonly_session()
    try:
        if shape == "revenue_total":
            result = session.exec(
                select(func.coalesce(func.sum(Order.total_minor), 0))
                .where(Order.created_at >= start)
                .where(Order.created_at <= end)
                .where(Order.status.in_(["CREATED", "PAID"]))
            )
            total_minor = result.one()
            total_rupees = total_minor / 100
            figures = {
                "revenue_minor": total_minor,
                "revenue_rupees": total_rupees,
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
            }
            answer = f"Total revenue from {start.strftime('%b %d')} to {end.strftime('%b %d')}: ₹{total_rupees:,.2f} ({total_minor} paise)."
        else:
            result = session.exec(
                select(func.count(Order.id))
                .where(Order.created_at >= start)
                .where(Order.created_at <= end)
            )
            count = result.one()
            figures = {
                "order_count": count,
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
            }
            answer = f"Order count from {start.strftime('%b %d')} to {end.strftime('%b %d')}: {count} orders."
    finally:
        session.close()

    return {
        "answer_text": answer,
        "figures": figures,
    }
