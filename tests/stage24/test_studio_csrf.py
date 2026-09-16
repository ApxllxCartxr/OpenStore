# tests/stage24/test_studio_csrf.py
# Stage 24 regression cover: the studio router's CSRF gate was defined but
# never imported (NameError on call) and never attached to any route.

from __future__ import annotations

import pytest
from openstore.core.api import CommerceError
from openstore.core.session import SESSION_COOKIE, csrf_token_for
from openstore.surfaces import studio


class _Req:
    def __init__(self, cookies: dict[str, str], headers: dict[str, str]) -> None:
        self.cookies = cookies
        self.headers = headers


class TestStudioCsrfGate:
    def test_cookieless_caller_is_unaffected(self) -> None:
        """Header/query operator flows carry no ambient credential, so there
        is no CSRF exposure to gate."""
        studio._require_csrf_if_session(_Req({}, {}))

    def test_session_cookie_without_header_is_rejected(self) -> None:
        with pytest.raises(Exception) as ei:
            studio._require_csrf_if_session(_Req({SESSION_COOKIE: "raw-token"}, {}))
        detail = getattr(ei.value, "detail", {})
        assert detail.get("reason_code") == "auth.csrf_invalid"

    def test_session_cookie_with_derived_token_passes(self) -> None:
        raw = "raw-token"
        studio._require_csrf_if_session(
            _Req({SESSION_COOKIE: raw}, {"X-OpenStore-CSRF": csrf_token_for(raw)})
        )

    def test_wrong_token_is_rejected(self) -> None:
        with pytest.raises(Exception) as ei:
            studio._require_csrf_if_session(
                _Req({SESSION_COOKIE: "raw-token"}, {"X-OpenStore-CSRF": "not-the-token"})
            )
        assert getattr(ei.value, "detail", {}).get("reason_code") == "auth.csrf_invalid"

    def test_gate_is_attached_to_every_mutating_studio_route(self) -> None:
        """The helper existed but no route referenced it — the whole point."""
        import inspect

        from openstore.config import Settings  # noqa: F401

        src = inspect.getsource(studio)
        posts = src.count("@router.post(")
        guarded = src.count("dependencies=[Depends(_require_csrf_if_session)]")
        assert posts > 0
        assert guarded == posts, f"{posts - guarded} mutating studio routes are ungated"


def test_check_csrf_is_importable_in_studio() -> None:
    """It was referenced but never imported: NameError on the first call."""
    assert studio.check_csrf is not None
    with pytest.raises(CommerceError):
        studio.check_csrf("raw", None)
