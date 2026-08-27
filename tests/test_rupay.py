from datetime import datetime, timedelta, timezone

from agents.rebuttal_builder import _load_playbook, _template_network
from core.deadlines import filing_deadline_for_network


def _rupay_state() -> dict:
    return {
        "chargeback_id": "cb_rupay_001",
        "reason_code": "UA02",
        "card_network": "RUPAY",
    }


def test_rupay_uses_namespaced_playbook() -> None:
    state = _rupay_state()

    playbook = _load_playbook(state)

    assert _template_network(state) == "rupay"
    assert playbook["name"] == "Unauthorized Card-Not-Present Transaction"
    assert playbook["filing_window_working_days"] == 7


def test_rupay_deadline_is_seven_working_days() -> None:
    received_at = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)  # Friday
    provider_deadline = received_at + timedelta(days=30)

    rupay_deadline = filing_deadline_for_network(
        "RUPAY",
        received_at=received_at,
        provided_deadline=provider_deadline,
    )
    visa_deadline = filing_deadline_for_network(
        "VISA",
        received_at=received_at,
        provided_deadline=provider_deadline,
    )

    assert rupay_deadline == datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    assert visa_deadline == provider_deadline
