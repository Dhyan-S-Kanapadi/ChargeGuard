from datetime import datetime, timedelta, timezone
from typing import cast

from agents.rebuttal_builder import _build_rebuttal_packet
from agents.scoring import scoring_agent
from core import graph as graph_module
from core.state import ChargebackState
from documents.pdf_builder import build_rebuttal_pdf


def _state(*, network: str = "VISA", reason_code: str = "10.4") -> ChargebackState:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return cast(
        ChargebackState,
        {
            "chargeback_id": "cb_ce3_wiring",
            "order_id": "current",
            "reason_code": reason_code,
            "card_network": network,
            "dispute_amount": 2500.0,
            "currency": "INR",
            "filing_deadline": now + timedelta(days=30),
            "merchant_profile": {
                "merchant_id": "merchant_ce3",
                "name": "CE3 Merchant",
                "vertical": "ecommerce",
                "freshdesk_domain": "",
                "average_order_value": 0,
                "chargeback_history_count": 0,
            },
            "investigation_plan": {"priority": "normal"},
            "requires_food_agents": False,
            "transaction": None,
            "shipping": None,
            "comms": None,
            "device": None,
            "consortium": None,
            "delivery_photo": None,
            "order_timeline": None,
            "evidence_collection_degraded": False,
            "degraded_reasons": [],
            "decision": None,
        },
    )


def test_scoring_applies_ce3_override_only_to_qualifying_visa_10_4(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "agents.scoring._predict_win_probability",
        lambda state: (calls.append(state["chargeback_id"]) or 0.25, "test_model"),
    )
    qualifying = _state()
    qualifying["ce3_qualification"] = {
        "qualifies": True,
        "matched_elements": ["customer_email", "customer_ip"],
        "prior_transaction_refs": ["prior_1", "prior_2"],
        "reason": "qualified",
    }

    result = scoring_agent(qualifying)

    assert result["win_probability"] == 0.95
    assert result["ce3_override_applied"] is True
    assert calls == []

    cases = [
        (_state(reason_code="13.1"), True),
        (_state(network="MASTERCARD"), True),
        (_state(), False),
    ]
    for state, qualifies in cases:
        state["ce3_qualification"] = {
            "qualifies": qualifies,
            "matched_elements": [],
            "prior_transaction_refs": [],
            "reason": "test",
        }
        result = scoring_agent(state)
        assert result["win_probability"] == 0.25
        assert result["ce3_override_applied"] is False
    assert len(calls) == 3


def test_graph_invokes_purchase_history_only_for_visa_10_4(monkeypatch) -> None:
    calls: list[str] = []

    def identity(state: ChargebackState) -> ChargebackState:
        return state

    def score(state: ChargebackState) -> ChargebackState:
        state["decision"] = "ACCEPT"
        return state

    def purchase(state: ChargebackState) -> ChargebackState:
        calls.append(f"{state['card_network']}:{state['reason_code']}")
        return state

    for name in (
        "orchestrator_agent", "transaction_agent", "shipping_agent", "device_agent",
        "comms_agent", "consortium_agent", "delivery_photo_agent", "order_timeline_agent",
        "order_correlation_agent", "accept_and_log_agent",
    ):
        monkeypatch.setattr(graph_module, name, identity)
    monkeypatch.setattr(graph_module, "purchase_history_agent", purchase)
    monkeypatch.setattr(graph_module, "scoring_agent", score)
    app = graph_module.build_graph().compile()

    app.invoke(_state())
    app.invoke(_state(reason_code="13.1"))
    app.invoke(_state(network="MASTERCARD"))

    assert calls == ["VISA:10.4"]


def test_graph_resolves_order_after_transaction_before_shipping(monkeypatch) -> None:
    calls: list[str] = []

    def identity(state: ChargebackState) -> ChargebackState:
        return state

    def transaction(state: ChargebackState) -> ChargebackState:
        calls.append("transaction")
        return state

    def correlation(state: ChargebackState) -> ChargebackState:
        calls.append("correlation")
        return state

    def shipping(state: ChargebackState) -> ChargebackState:
        calls.append("shipping")
        return state

    def score(state: ChargebackState) -> ChargebackState:
        state["decision"] = "ACCEPT"
        return state

    for name in (
        "orchestrator_agent", "device_agent", "comms_agent", "consortium_agent",
        "delivery_photo_agent", "order_timeline_agent", "purchase_history_agent",
        "accept_and_log_agent",
    ):
        monkeypatch.setattr(graph_module, name, identity)
    monkeypatch.setattr(graph_module, "transaction_agent", transaction)
    monkeypatch.setattr(graph_module, "order_correlation_agent", correlation)
    monkeypatch.setattr(graph_module, "shipping_agent", shipping)
    monkeypatch.setattr(graph_module, "scoring_agent", score)
    app = graph_module.build_graph().compile()

    app.invoke(_state(reason_code="13.1"))
    expedited = _state(reason_code="13.1")
    expedited["investigation_plan"] = {"priority": "overdue"}
    app.invoke(expedited)

    assert calls == [
        "transaction", "correlation", "shipping",
        "transaction", "correlation", "shipping",
    ]


def test_rebuttal_packet_and_pdf_include_ce3_qualified_transaction_table(tmp_path) -> None:
    state = _state()
    state["ce3_qualification"] = {
        "qualifies": True,
        "matched_elements": ["customer_email", "customer_ip"],
        "prior_transaction_refs": ["prior_1", "prior_2"],
        "reason": "qualified",
    }
    packet = _build_rebuttal_packet(state)
    output = tmp_path / "ce3.pdf"

    build_rebuttal_pdf(packet, output, template_text="Qualified transaction data follows.")

    assert packet["ce3_qualified_transaction_data"] == [
        {"prior_transaction_ref": "prior_1", "matched_elements": ["customer_email", "customer_ip"]},
        {"prior_transaction_ref": "prior_2", "matched_elements": ["customer_email", "customer_ip"]},
    ]
    assert output.read_bytes().startswith(b"%PDF")
