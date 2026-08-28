"""Merchant Reasoning Agent — A2A server.

A separate FastAPI process exposing three skills via the A2A protocol:
cross-sell suggestions, campaign drafts, and read-only finance Q&A.

This process holds NO signing key and NO Razorpay write credentials.
Its .env file must not contain RAZORPAY_KEY_SECRET or
MERCHANT_SIGNING_KEY_PATH. The guarantee is architectural.

Run: uvicorn merchant_agent.app:app --host 0.0.0.0 --port 8001
"""

import json
import logging
import uuid
from contextlib import asynccontextmanager

import anyio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from merchant_agent.agent_card import get_agent_card
from merchant_agent.executor import execute_task
from merchant_agent.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

agent_card = get_agent_card()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Merchant reasoning agent started (port %s). "
        "Skills: cross_sell, campaign_draft, finance_qa. "
        "No signing key loaded. No Razorpay credentials loaded.",
        settings.a2a_port,
    )
    yield


app = FastAPI(
    title="Merchant Reasoning Agent",
    description="A2A agent for cross-sell, campaign drafts, and finance Q&A. Read-only.",
    lifespan=lifespan,
)


@app.get("/.well-known/agent-card.json")
async def agent_card_endpoint():
    """A2A discovery: serve the agent card."""
    from a2a.server.request_handlers.response_helpers import agent_card_to_dict
    return JSONResponse(agent_card_to_dict(agent_card))


@app.post("/a2a")
async def a2a_endpoint(request: Request):
    """A2A JSON-RPC endpoint: accepts task submissions, dispatches to skills.

    Handles the A2A message/send method. The request body is a JSON-RPC
    envelope containing a SendMessage request with a skill_id and params
    in the message text.
    """
    body = await request.json()

    method = body.get("method")
    params = body.get("params", {})
    req_id = body.get("id", str(uuid.uuid4()))

    if method == "message/send":
        return await _handle_message_send(params, req_id)
    elif method == "tasks/get":
        return _handle_task_get(params, req_id)
    else:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            },
            status_code=200,
        )


# In-memory task store (simple demo — no persistence needed)
_tasks: dict[str, dict] = {}


async def _handle_message_send(params: dict, req_id: str) -> JSONResponse:
    """Handle a message/send JSON-RPC request."""
    message = params.get("message", {})
    task_id = message.get("task_id") or str(uuid.uuid4())
    context_id = message.get("context_id") or str(uuid.uuid4())

    # Parse the message text as JSON with skill_id and params
    text = message.get("parts", [{}])[0].get("text", "{}") if message.get("parts") else "{}"
    try:
        payload = json.loads(text) if isinstance(text, str) else text
    except json.JSONDecodeError:
        payload = {"text_query": text}

    skill_id = payload.get("skill_id", "")
    skill_params = payload.get("params", {})

    # Create task in submitted state
    task = {
        "id": task_id,
        "context_id": context_id,
        "status": {"state": "submitted"},
    }
    _tasks[task_id] = task

    # Execute the skill
    task["status"] = {"state": "working"}
    result = await anyio.to_thread.run_sync(execute_task, skill_id, skill_params)

    if result["status"] == "completed":
        task["status"] = {"state": "completed"}
        task["artifacts"] = [
            {
                "parts": [{"type": "text", "text": json.dumps(result["result"])}],
                "name": skill_id,
            }
        ]
    else:
        task["status"] = {"state": "failed", "message": result.get("error", "Unknown error")}
        task["artifacts"] = [
            {
                "parts": [{"type": "text", "text": json.dumps({"error": result.get("error")})}],
                "name": skill_id,
            }
        ]

    _tasks[task_id] = task

    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": task,
        }
    )


def _handle_task_get(params: dict, req_id: str) -> JSONResponse:
    """Handle a tasks/get JSON-RPC request."""
    task_id = params.get("id")
    task = _tasks.get(task_id)
    if task is None:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": f"Task not found: {task_id}"},
            }
        )
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": task,
        }
    )


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "merchant-reasoning-agent", "skills": ["cross_sell", "campaign_draft", "finance_qa"]}
