"""The registry holds together, and `docs/CODES.md` is not stale."""

from __future__ import annotations

import subprocess
import sys
from enum import StrEnum

import pytest
from openstore.sidecar.core.codes import (
    CLOSED_SETS,
    GATE_CHECK_ORDER,
    HTTP_STATUS,
    TOOL_SCOPES,
    AuthorityKind,
    GateCheck,
    OrderStatus,
    ReasonCode,
    Scope,
    ToolName,
    http_status,
)


def test_every_reason_code_has_exactly_one_http_status() -> None:
    assert set(HTTP_STATUS) == set(ReasonCode)
    for code in ReasonCode:
        assert http_status(code) in {400, 401, 403, 404, 409, 429}


def test_every_tool_has_a_scope() -> None:
    assert set(TOOL_SCOPES) == set(ToolName)
    assert set(TOOL_SCOPES.values()) <= set(Scope)


def test_only_place_order_needs_confirm() -> None:
    """`confirm` is the spend scope. Anything else holding it would widen what a
    self-registered stranger can reach for free (ADR-0012)."""
    confirm = [t for t, s in TOOL_SCOPES.items() if s is Scope.CONFIRM]
    assert confirm == [ToolName.PLACE_ORDER]


def test_eight_statuses_and_never_a_ninth() -> None:
    assert len(OrderStatus) == 8


def test_four_authority_kinds_and_four_scopes() -> None:
    assert len(AuthorityKind) == 4
    assert len(Scope) == 4


def test_gate_check_order_is_complete_and_starts_with_authority() -> None:
    """The Transcript's bytes depend on this order (SPEC §4)."""
    assert list(GATE_CHECK_ORDER) == list(GateCheck)
    assert GATE_CHECK_ORDER[0] is GateCheck.AUTHORITY_PRESENT_AND_ACCEPTED
    assert GATE_CHECK_ORDER[-1] is GateCheck.METHOD_ENABLED
    assert GATE_CHECK_ORDER.index(GateCheck.QUOTE_FRESH) > GATE_CHECK_ORDER.index(
        GateCheck.QUOTE_CONSISTENT
    )


def test_no_member_value_collides_across_sets() -> None:
    """Two sets may share a value only where the spec says they do: `passkey` is
    both an Authority kind and a `confirmed-intent` mechanism, `refund` is both a
    Ledger kind and a stock-move channel and a Provider op, and so on. What must
    never happen is a *reason code* colliding with anything, because that is what
    a refusal is looked up by."""
    others: set[str] = set()
    for title, enum_cls in CLOSED_SETS.items():
        if enum_cls is ReasonCode:
            continue
        others.update(m.value for m in enum_cls)
    collisions = {c.value for c in ReasonCode} & others
    assert collisions == {"sold-out"}, collisions  # also an Availability Bucket, deliberately


def test_every_closed_set_is_registered_for_the_doc() -> None:
    """A new enum that is not in CLOSED_SETS never reaches `docs/CODES.md`, and a
    code nobody can see is a code nobody reviews."""
    import openstore.sidecar.core.codes as codes

    declared = {
        obj
        for obj in vars(codes).values()
        if isinstance(obj, type) and issubclass(obj, StrEnum) and obj is not StrEnum
    }
    assert declared == set(CLOSED_SETS.values())


@pytest.mark.parametrize("enum_cls", list(CLOSED_SETS.values()), ids=list(CLOSED_SETS))
def test_values_are_lowercase_kebab_except_ledger_kinds(enum_cls: type[StrEnum]) -> None:
    for member in enum_cls:
        value = member.value
        if value.isupper():  # Ledger kinds are SHOUTED on purpose
            continue
        assert value == value.lower()
        assert " " not in value and "_" not in value


def test_generated_doc_is_not_stale() -> None:
    """`docs/CODES.md` is generated. Editing it by hand instead of the enum is the
    drift this whole file exists to prevent."""
    result = subprocess.run(
        [sys.executable, "scripts/registry_diff.py", "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_code_appears_in_spec_prose_without_being_registered() -> None:
    """SPEC §12: a code named in prose but missing from the registry is a red
    build. Scoped to the normative files — `docs/` holds review findings that
    name rejected candidates on purpose."""
    result = subprocess.run(
        [sys.executable, "scripts/registry_diff.py", "--prose"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
