# merchant/oauth/store.py
import time

# code -> {client_id, scopes, code_challenge, redirect_uri, expires_at}
pending_codes: dict[str, dict] = {}