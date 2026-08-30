"""A2A Agent Card — discovery document for the merchant reasoning agent.

Served at GET /.well-known/agent-card.json on this agent's own FastAPI app.
Lists the agent's name, description, and its three skills.
"""

from a2a.types.a2a_pb2 import AgentCard, AgentSkill, AgentCapabilities, AgentInterface

from reference.merchant_agent.config import settings


def get_agent_card() -> AgentCard:
    """Returns the A2A AgentCard describing this agent's capabilities."""
    merchant = settings.merchant_name
    return AgentCard(
        name=f"{merchant} — Merchant Reasoning Agent",
        description=(
            f"A reasoning agent for the {merchant} merchant. Provides "
            "cross-sell suggestions based on cart contents, drafts promotional "
            "campaign messages, and answers read-only finance questions about "
            "order history. Holds no signing key and no payment credentials."
        ),
        version="0.1",
        supported_interfaces=[
            AgentInterface(
                url=f"http://{settings.a2a_host}:{settings.a2a_port}",
                protocol_binding="JSONRPC",
            )
        ],
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
        ),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id="cross_sell",
                name="Cross-sell Suggestions",
                description=(
                    "Given a cart's contents, suggest a complementary product "
                    "from the catalog using related_skus and tags metadata. "
                    "Returns a suggested SKU and a natural-language reason. "
                    "Pure suggestion — no side effects."
                ),
                tags=["catalog", "suggestion", "cross-sell"],
            ),
            AgentSkill(
                id="campaign_draft",
                name="Campaign Draft",
                description=(
                    "Given an occasion tag (e.g. occasion:birthday), draft a "
                    "short promotional message referencing matching products. "
                    "Purely generative — produces text for a human to review."
                ),
                tags=["marketing", "draft", "campaign"],
            ),
            AgentSkill(
                id="finance_qa",
                name="Finance Q&A",
                description=(
                    "Answer read-only questions about the merchant's order "
                    "history: order counts, revenue totals in date ranges. "
                    "Uses pre-written parameterized queries — never free-form "
                    "SQL generation."
                ),
                tags=["finance", "orders", "read-only", "analytics"],
            ),
        ],
    )
