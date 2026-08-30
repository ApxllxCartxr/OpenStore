"""Shared Gemini client for buyer agent LLM calls.

The client is instantiated once on first use and reused across all calls.
Both wrapper functions return None on any API/parse/validation failure —
callers are responsible for their own fallback behavior.
"""

import json
import logging

from pydantic import BaseModel

log = logging.getLogger(__name__)

_client = None

_MODEL = "gemini-2.5-flash"


def _get_client():
    """Lazy-init a single genai.Client for the process lifetime."""
    global _client
    if _client is not None:
        return _client

    import os
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.warning("GEMINI_API_KEY not set — LLM calls will fail")
        return None

    _client = genai.Client(api_key=api_key)
    return _client


def call_gemini_structured(
    prompt: str, response_schema: type[BaseModel]
) -> BaseModel | None:
    """Call Gemini with output constrained to response_schema via JSON mode.

    Returns a validated instance of response_schema, or None on any
    API/parse/validation failure.
    """
    client = _get_client()
    if client is None:
        return None

    try:
        from google.genai import types

        response = client.models.generate_content(
            model=_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                response_json_schema=response_schema.model_json_schema(),
            ),
        )
        text = response.text
        parsed = json.loads(text)
        return response_schema.model_validate(parsed)
    except Exception:
        log.exception("Gemini structured call failed")
        return None


def call_gemini_text(prompt: str) -> str | None:
    """Call Gemini for free-text generation.

    Returns the text, or None on failure.
    """
    client = _get_client()
    if client is None:
        return None

    try:
        from google.genai import types

        response = client.models.generate_content(
            model=_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.3),
        )
        return response.text
    except Exception:
        log.exception("Gemini text call failed")
        return None
