"""Alert test: simulated attacks run the reporting path but never trigger a defense."""

from __future__ import annotations

import pytest

from sentinel.monitor import simulate
from sentinel.permissions import Autonomy, Severity
from sentinel.services import Services


def test_catalog_covers_all_groups() -> None:
    catalog = simulate.catalog()
    assert {t["group"] for t in catalog} == {"PC", "Home network", "Server"}
    assert len({t["rule"] for t in catalog}) == len(catalog)


def test_test_alert_notifies_and_is_marked(svc: Services, monkeypatch) -> None:
    notes: list[tuple] = []
    monkeypatch.setattr(svc.notifier, "notify", lambda *a, **k: notes.append((a, k)))
    result = simulate.run_test(svc, "mass_file_change")
    alert = svc.alerts.list()[0]
    assert alert["id"] == result["id"] and alert["title"].startswith("TEST: ")
    assert alert["detail"]["test"] is True
    assert notes and notes[0][1]["severity"] is Severity.CRITICAL


def test_info_rules_are_raised_to_warn_for_push(svc: Services) -> None:
    result = simulate.run_test(svc, "new_listener")
    assert result["severity"] is Severity.WARN


def test_repeated_tests_are_not_deduplicated(svc: Services) -> None:
    simulate.run_test(svc, "new_device")
    simulate.run_test(svc, "new_device")
    assert len(svc.alerts.list()) == 2


@pytest.mark.parametrize("rule", [s.rule for s in simulate.SCENARIOS])
def test_never_triggers_a_response_even_as_guardian(svc: Services, rule: str) -> None:
    svc.engine.set_autonomy(Autonomy.GUARDIAN, actor="test")
    before = len(svc.db.query("SELECT id FROM audit"))
    simulate.run_test(svc, rule)
    alert = svc.alerts.list()[0]
    assert alert["response"] is None
    assert svc.engine.list_approvals() == []
    added = svc.db.query("SELECT action FROM audit ORDER BY id")[before:]
    assert [r["action"] for r in added] == ["alert_test"]


def test_unknown_rule(svc: Services) -> None:
    with pytest.raises(ValueError):
        simulate.run_test(svc, "rm_rf")


def test_test_route(svc: Services) -> None:
    from fastapi.testclient import TestClient

    from sentinel.api.app import create_app

    client = TestClient(
        create_app(svc, token="t-1", start_background=False), base_url="http://127.0.0.1:8765"
    )
    headers = {"Authorization": "Bearer t-1"}
    assert len(client.get("/security/tests", headers=headers).json()) == len(simulate.SCENARIOS)
    assert (
        client.post("/security/tests", json={"rule": "usb_new_device"}, headers=headers).status_code
        == 200
    )
    assert client.post("/security/tests", json={"rule": "nope"}, headers=headers).status_code == 400


def test_not_available_to_the_model(svc: Services) -> None:
    names = {spec.name for spec in svc.registry.all()}
    assert not any("test" in name and "alert" in name for name in names)


def test_every_scenario_has_defense_and_tips() -> None:
    for test in simulate.catalog():
        assert len(test["defense"]) == 4, test["rule"]
        assert test["tips"], test["rule"]
        assert all(t["url"].startswith("https://") for t in test["tips"])


def test_run_returns_defense_steps(svc: Services) -> None:
    result = simulate.run_test(svc, "malware_file")
    assert "quarantine" in result["defense"][2]
