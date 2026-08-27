import webbrowser
import httpx


def ensure_intent_policy_signed(
    merchant_url: str, user_id: str = "default-user"
) -> bool:
    """Opens the merchant's Intent Policy signing ceremony in the browser.
    Returns True if a policy is already registered for the user, False if the user needs
    to sign one (browser opened to signing page)."""
    try:
        resp = httpx.get(
            f"{merchant_url}/internal/intent-status",
            params={"user_id": user_id},
            timeout=5.0,
        )
        if resp.status_code == 200 and resp.json().get("has_policy"):
            return True
    except httpx.HTTPError:
        pass

    webbrowser.open(f"{merchant_url}/intent/sign")
    return False
