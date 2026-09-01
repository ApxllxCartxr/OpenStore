# OpenStore core — health, readiness, and metrics
# SID-2: /health/live (liveness) and /health/ready (readiness). The sidecar MUST
# refuse agent traffic when not ready. Readiness checks: config loaded, schema
# present (migrations or test create_all), DB reachable, Razorpay test-mode keys,
# signing keys derivable. Rekor is NOT required (DECISIONS §11.1.1).
#
# SID-7: /internal/metrics exposes Prometheus text exposition format, hand-rolled
# (Q-011 provisional): no prometheus-client dependency. We emit only gauges with
# # HELP / # TYPE lines.

from __future__ import annotations

from dataclasses import dataclass, field

from openstore.config import Settings

# Gated paths: agent/money routes refused with 503 until readiness is true (SID-2).
# Discovery/oauth/health/infra routes stay open so agents can still read manifests.
GATED_PATHS = {
    "/agent/catalog",
    "/agent/mcp",
    "/agent/acp",
    "/agent/campaigns",
    "/campaign/{campaign_id}/approve",
    "/campaign/{campaign_id}/reject",
    "/hold/{cancel_token}/cancel",
    "/webhooks/razorpay",
}


@dataclass
class ReadinessState:
    ready: bool = False
    checks: dict[str, tuple[bool, str]] = field(default_factory=dict)

    @property
    def reason_codes(self) -> list[str]:
        out: list[str] = []
        for name, (ok, status) in self.checks.items():
            if not ok:
                out.append(
                    f"health.{name}.{status}" if status else f"health.{name}.failed"
                )
        return out


_readiness: ReadinessState | None = None


def _db_is_reachable(config: Settings) -> bool:
    from openstore.core.database import get_engine

    engine = get_engine(config)
    with engine.connect() as conn:
        from sqlalchemy import text

        conn.execute(text("SELECT 1"))
    return True


def _razorpay_test_keys(config: Settings) -> bool:
    # SID-2/SID-5: never report ready against LIVE Razorpay keys. Test mode is
    # the `rzp_test` prefix (rzp_test... / rzp_test_...). Live keys `rzp_live_`
    # fail readiness.
    return config.razorpay.key_id.startswith("rzp_test")


def _schema_present(config: Settings) -> bool:
    # Schema is considered present when either alembic migrations were applied
    # (serve path) or create_all was run (tests; DECISIONS §11.1.13). See the
    # flag set by apply_migrations/init_database.
    from openstore.core.database import schema_ready

    return schema_ready(config)


def _signing_keys_ready(config: Settings) -> bool:
    from openstore.surfaces.wellknown import _load_or_generate_poai_keys, _merchant_id

    keys = _load_or_generate_poai_keys(_merchant_id(config))
    return bool(keys.get("private_key") and keys.get("jwk"))


def compute_readiness(config: Settings) -> ReadinessState:
    """Determine readiness. R0.5: every failing check is named. No silent fallback."""
    checks: dict[str, tuple[bool, str]] = {}

    # Config loaded is tautologically true here (we received a Settings).
    checks["config"] = (True, "")

    try:
        db_ok = _db_is_reachable(config)
    except Exception:
        db_ok = False
        checks["database"] = (False, "db_unreachable")
    else:
        checks["database"] = (db_ok, "")

    checks["schema"] = (
        _schema_present(config),
        "" if _schema_present(config) else "migrations_not_applied",
    )
    # Razorpay test-mode keys (SID-2): empty key or a LIVE key fails readiness.
    checks["razorpay_test_keys"] = (
        _razorpay_test_keys(config),
        "" if _razorpay_test_keys(config) else "not_test_mode",
    )

    try:
        signing_ok = _signing_keys_ready(config)
    except Exception:
        signing_ok = False
        checks["signing_keys"] = (False, "signing_keys_error")
    else:
        checks["signing_keys"] = (signing_ok, "")

    all_ok = all(ok for ok, _ in checks.values())
    return ReadinessState(ready=all_ok, checks=checks)


def is_ready(config: Settings) -> bool:
    global _readiness
    _readiness = compute_readiness(config)
    return _readiness.ready


def is_gated(path: str) -> bool:
    return path in GATED_PATHS


def metrics_text(config: Settings) -> str:
    """SID-7: minimal Prometheus text exposition (Q-011, hand-rolled gauges)."""
    state = compute_readiness(config)
    lines: list[str] = []

    def gauge(name: str, help_: str, value: int | float) -> None:
        lines.append(f"# HELP openstore_{name} {help_}")
        lines.append(f"# TYPE openstore_{name} gauge")
        lines.append(f"openstore_{name} {value}")

    def labeled_gauge(name: str, help_: str, label: str, value: int | float) -> None:
        lines.append(f"# HELP openstore_{name} {help_}")
        lines.append(f"# TYPE openstore_{name} gauge")
        lines.append(f'openstore_{name}{{label="{label}"}} {value}')

    gauge("health_ready", "1 if the sidecar is ready, else 0", int(state.ready))

    # Hold counts by state (from the DB). Loaded live so drift is visible.
    hold_counts = _hold_counts_by_state(config)
    for state_name, count in sorted(hold_counts.items()):
        labeled_gauge(
            "checkout_hold_state", f"Checkouts held in state {state_name}",
            state_name, count,
        )

    # Ledger balances by account.
    ledger = _ledger_balances(config)
    for account, minor in sorted(ledger.items()):
        labeled_gauge(
            "ledger_balance_minor", f"Ledger balance (paise) for {account}",
            account, minor,
        )

    # Reconciliation drift (R0.8 recompute from the last sweep's audit entry).
    drift = _reconciliation_drift(config)
    gauge("reconciliation_drift_total", "Latest reconciliation sweep drift (paise)", drift)

    return "\n".join(lines) + "\n"


def _hold_counts_by_state(config: Settings) -> dict[str, int]:
    from sqlmodel import select

    from openstore.core.database import get_session
    from openstore.models import Checkout

    counts: dict[str, int] = {}
    session = get_session(config)
    try:
        rows = session.exec(select(Checkout)).all()
        for ck in rows:
            key = str(ck.state.value)
            counts[key] = counts.get(key, 0) + 1
    finally:
        session.close()
    return counts


def _ledger_balances(config: Settings) -> dict[str, int]:
    from sqlmodel import select

    from openstore.core.database import get_session
    from openstore.models import LedgerEntry

    balances: dict[str, int] = {}
    session = get_session(config)
    try:
        for entry in session.exec(select(LedgerEntry)).all():
            account = str(entry.account)
            balances[account] = balances.get(account, 0) + entry.amount_minor
    finally:
        session.close()
    return balances


def _reconciliation_drift(config: Settings) -> int:
    # INV-7: the reconciliation sweeper persists its drift_minor in an audit_log
    # entry (action="reconciliation_sweep", resource_type="reconciliation_drift").
    # The metric recomputes from that entry — server-side truth, never a constant.
    from sqlmodel import select

    from openstore.core.database import get_session
    from openstore.models import AuditLog

    session = get_session(config)
    try:
        row = session.exec(
            select(AuditLog).where(AuditLog.resource_type == "reconciliation_drift")
        ).first()
        if row and row.audit_metadata:
            return int(row.audit_metadata.get("drift_minor", 0))
        return 0
    finally:
        session.close()
