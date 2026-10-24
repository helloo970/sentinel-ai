"""Decision matrix of the permissions engine (pure function)."""

from __future__ import annotations

import pytest

from sentinel.permissions import (
    ActionResult,
    ActionSpec,
    Autonomy,
    NoParams,
    Outcome,
    Risk,
    Severity,
    Source,
    decide,
)


def spec(risk: Risk, *, guardian: bool = False) -> ActionSpec:
    return ActionSpec(
        name=f"dummy_{risk}",
        title="Dummy",
        description="",
        risk=risk,
        params=NoParams,
        execute=lambda p, s: ActionResult("ok"),
        guardian_auto=guardian,
    )


ALL_LEVELS = list(Autonomy)


@pytest.mark.parametrize("risk", list(Risk))
@pytest.mark.parametrize("level", ALL_LEVELS)
def test_untrusted_source_is_always_denied(risk: Risk, level: Autonomy) -> None:
    assert decide(spec(risk), Source.UNTRUSTED, level).outcome is Outcome.DENY


@pytest.mark.parametrize("source", [Source.USER, Source.ASSISTANT, Source.RULE, Source.AGENT])
def test_read_is_executed(source: Source) -> None:
    assert decide(spec(Risk.READ), source, Autonomy.OBSERVER).outcome is Outcome.EXECUTE


@pytest.mark.parametrize("level", ALL_LEVELS)
@pytest.mark.parametrize("source", [Source.USER, Source.ASSISTANT, Source.AGENT])
def test_final_always_needs_confirmation(level: Autonomy, source: Source) -> None:
    decision = decide(spec(Risk.FINAL, guardian=True), source, level, severity=Severity.CRITICAL)
    assert decision.outcome is Outcome.CONFIRM


def test_rules_never_trigger_final_actions() -> None:
    decision = decide(spec(Risk.FINAL), Source.RULE, Autonomy.GUARDIAN, severity=Severity.CRITICAL)
    assert decision.outcome is Outcome.DENY


@pytest.mark.parametrize("risk", [Risk.LOW, Risk.REVERSIBLE, Risk.HIGH])
def test_agents_only_propose(risk: Risk) -> None:
    assert decide(spec(risk), Source.AGENT, Autonomy.GUARDIAN).outcome is Outcome.CONFIRM


def test_user_direct_low_and_reversible_execute_high_confirms() -> None:
    assert decide(spec(Risk.LOW), Source.USER, Autonomy.OBSERVER).outcome is Outcome.EXECUTE
    assert decide(spec(Risk.REVERSIBLE), Source.USER, Autonomy.OBSERVER).outcome is Outcome.EXECUTE
    assert decide(spec(Risk.HIGH), Source.USER, Autonomy.GUARDIAN).outcome is Outcome.CONFIRM


def test_observer_mode() -> None:
    assert decide(spec(Risk.LOW), Source.ASSISTANT, Autonomy.OBSERVER).outcome is Outcome.CONFIRM
    assert decide(spec(Risk.LOW), Source.RULE, Autonomy.OBSERVER).outcome is Outcome.DENY


def test_assistant_mode_level_1() -> None:
    low = decide(spec(Risk.LOW), Source.ASSISTANT, Autonomy.ASSISTANT)
    assert low.outcome is Outcome.EXECUTE and low.autonomous
    rev = decide(spec(Risk.REVERSIBLE), Source.ASSISTANT, Autonomy.ASSISTANT)
    assert rev.outcome is Outcome.CONFIRM


def test_autopilot_executes_reversible() -> None:
    decision = decide(spec(Risk.REVERSIBLE), Source.ASSISTANT, Autonomy.AUTOPILOT)
    assert decision.outcome is Outcome.EXECUTE and decision.autonomous


@pytest.mark.parametrize("level", [Autonomy.ASSISTANT, Autonomy.AUTOPILOT, Autonomy.GUARDIAN])
def test_tainted_context_always_confirms(level: Autonomy) -> None:
    for risk in (Risk.LOW, Risk.REVERSIBLE, Risk.HIGH):
        assert decide(spec(risk), Source.ASSISTANT, level, tainted=True).outcome is Outcome.CONFIRM


def test_guardian_kills_only_on_critical_rule() -> None:
    high = spec(Risk.HIGH, guardian=True)
    ok = decide(high, Source.RULE, Autonomy.GUARDIAN, severity=Severity.CRITICAL)
    assert ok.outcome is Outcome.EXECUTE and ok.autonomous
    assert decide(high, Source.RULE, Autonomy.GUARDIAN, severity=Severity.WARN).outcome is (
        Outcome.CONFIRM
    )
    assert decide(
        high, Source.ASSISTANT, Autonomy.GUARDIAN, severity=Severity.CRITICAL
    ).outcome is (Outcome.CONFIRM)
    assert decide(high, Source.RULE, Autonomy.AUTOPILOT, severity=Severity.CRITICAL).outcome is (
        Outcome.CONFIRM
    )


def test_guardian_needs_guardian_flag_on_action() -> None:
    plain_high = spec(Risk.HIGH, guardian=False)
    decision = decide(plain_high, Source.RULE, Autonomy.GUARDIAN, severity=Severity.CRITICAL)
    assert decision.outcome is Outcome.CONFIRM
