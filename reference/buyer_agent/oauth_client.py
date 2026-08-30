import base64
import hashlib
import json
import secrets
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading

import httpx


_LOOPBACK_PORT = 8765
_TOKEN_CACHE_PATH = "buyer_agent_token_cache.json"


def generate_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge). SHA-256 the verifier,
    base64url-encode, strip padding."""
    code_verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/callback":
            params = parse_qs(parsed.query)
            self.server.oauth_code = params.get("code", [None])[0]
            self.server.oauth_state = params.get("state", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Authorization complete.</h2>"
                b"<p>You may close this tab.</p></body></html>"
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress console noise


class LoopbackServer:
    def __init__(self, port: int = _LOOPBACK_PORT):
        self.port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.oauth_code: str | None = None
        self.oauth_state: str | None = None

    def start(self):
        self._server = HTTPServer(("127.0.0.1", self.port), _OAuthCallbackHandler)
        self._server.oauth_code = None
        self._server.oauth_state = None
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def wait_for_code(self, timeout: float = 120.0) -> tuple[str | None, str | None]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._server.oauth_code is not None:
                code = self._server.oauth_code
                state = self._server.oauth_state
                self._server.oauth_code = None
                self._server.oauth_state = None
                return code, state
            time.sleep(0.1)
        return None, None

    def stop(self):
        if self._server:
            self._server.shutdown()


def open_authorize_url(
    auth_endpoint: str,
    client_id: str,
    scopes: list[str],
    redirect_uri: str,
    code_challenge: str,
    state: str,
) -> None:
    """Builds the full /oauth/authorize URL and opens it in the system browser."""
    params = {
        "client_id": client_id,
        "scope": " ".join(scopes),
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "response_type": "code",
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{auth_endpoint}?{qs}"
    print(f"[buyer-agent] open this URL to authorize: {url}")
    webbrowser.open(url)


def exchange_code_for_token(
    token_endpoint: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
) -> dict:
    """POSTs the authorization_code grant. Returns the full token response."""
    resp = httpx.post(
        token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def _load_cache() -> dict | None:
    try:
        with open(_TOKEN_CACHE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_cache(data: dict) -> None:
    with open(_TOKEN_CACHE_PATH, "w") as f:
        json.dump(data, f)


def load_or_refresh_token(
    token_endpoint: str,
    client_id: str,
) -> str | None:
    """Reads a cached token from disk; if expired, uses the stored
    refresh_token to get a new access_token. Returns a valid access_token,
    or None if no usable cache/refresh exists."""
    cache = _load_cache()
    if cache is None:
        return None

    if cache.get("expires_at", 0) > time.time() + 30:
        return cache["access_token"]

    refresh_token = cache.get("refresh_token")
    if refresh_token is None:
        return None

    try:
        resp = httpx.post(
            token_endpoint,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        token_data = resp.json()
        _save_cache(
            {
                "access_token": token_data["access_token"],
                "refresh_token": token_data.get("refresh_token", refresh_token),
                "expires_at": time.time() + token_data.get("expires_in", 900),
                "client_id": client_id,
            }
        )
        return token_data["access_token"]
    except httpx.HTTPError:
        return None


def save_token(token_data: dict, client_id: str) -> None:
    """Persist a fresh token to disk cache."""
    _save_cache(
        {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token"),
            "expires_at": time.time() + token_data.get("expires_in", 900),
            "client_id": client_id,
        }
    )
