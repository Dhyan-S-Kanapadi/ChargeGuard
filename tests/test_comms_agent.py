from datetime import datetime, timedelta, timezone

from agents.evidence import comms
from agents.evidence.comms import _build_comms_evidence, comms_agent
from core.state import ChargebackState


def _state() -> ChargebackState:
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    return {
        "chargeback_id": "cb_comms_001",
        "order_id": "order_demo_001",
        "payment_id": "pay_demo_001",
        "reason_code": "10.4",
        "card_network": "VISA",
        "dispute_amount": 2500.0,
        "currency": "INR",
        "filing_deadline": now + timedelta(days=30),
        "merchant_profile": {
            "merchant_id": "merchant_001",
            "name": "Demo Merchant",
            "vertical": "ecommerce",
            "razorpay_key": "rzp_test_demo",
            "shiprocket_key": "shiprocket_demo",
            "freshdesk_domain": "demo.freshdesk.com",
            "average_order_value": 1800.0,
            "chargeback_history_count": 4,
        },
        "investigation_plan": {},
        "requires_food_agents": False,
        "transaction": {
            "order_id": "order_demo_001",
            "payment_id": "pay_demo_001",
            "amount": 2500.0,
            "currency": "INR",
            "otp_verified": True,
            "three_ds_authenticated": True,
            "device_id": "device_demo_123",
            "ip_address": "49.36.18.22",
            "customer_email": "buyer@example.com",
            "order_history_count": 8,
            "previous_chargebacks": 0,
            "raw": {},
        },
        "shipping": None,
        "comms": None,
        "device": None,
        "consortium": None,
        "delivery_photo": None,
        "order_timeline": None,
        "win_probability": None,
        "expected_value": None,
        "decision": "ACCEPT",
        "decision_reasoning": None,
        "rebuttal_document_path": None,
        "quality_approved": False,
        "quality_rejection_reason": None,
        "quality_loop_count": 0,
        "filing_confirmation": None,
        "filed_at": None,
        "final_outcome": None,
        "outcome_reason": None,
        "outcome_recorded_at": None,
    }


def test_comms_agent_populates_only_comms_evidence(monkeypatch) -> None:
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    state = _state()
    delivered_at = state["filing_deadline"] - timedelta(days=12)
    state["shipping"] = {
        "tracking_id": "trk_demo_001",
        "courier": "Shiprocket",
        "status": "DELIVERED",
        "delivered_at": delivered_at,
        "delivery_latitude": None,
        "delivery_longitude": None,
        "signature_obtained": False,
        "delivery_photo_url": None,
        "raw": {},
    }
    result = comms_agent(state)

    assert result["comms"] is not None
    assert len(result["comms"]["emails"]) == 2
    assert result["comms"]["emails"][0]["to"] == "buyer@example.com"
    assert result["comms"]["post_delivery_interaction"] is True
    assert result["comms"]["complaint_raised_before_chargeback"] is False
    assert result["shipping"]["delivered_at"] == delivered_at
    assert result["device"] is None


def test_comms_builder_detects_pre_chargeback_complaints() -> None:
    state = _state()
    delivered_at = state["filing_deadline"] - timedelta(days=12)
    state["shipping"] = {
        "tracking_id": "trk_demo_001",
        "courier": "Shiprocket",
        "status": "DELIVERED",
        "delivered_at": delivered_at,
        "delivery_latitude": None,
        "delivery_longitude": None,
        "signature_obtained": False,
        "delivery_photo_url": None,
        "raw": {},
    }
    state["chargeback_received_at"] = delivered_at + timedelta(days=3)
    emails = [
        {
            "from": "buyer@example.com",
            "direction": "inbound",
            "timestamp": (delivered_at + timedelta(hours=1)).isoformat(),
        }
    ]
    support_tickets = [
        {
            "id": "ticket_001",
            "type": "pre_chargeback_complaint",
            "raised_before_chargeback": True,
            "created_at": (delivered_at + timedelta(days=1)).isoformat(),
        }
    ]

    evidence = _build_comms_evidence(state, emails, support_tickets)

    assert evidence["post_delivery_interaction"] is True
    assert evidence["complaint_raised_before_chargeback"] is True
    assert evidence["support_tickets"] == support_tickets


def test_comms_agent_records_empty_evidence_on_collection_failure(monkeypatch) -> None:
    def fail_collection(state: ChargebackState) -> tuple[list[dict], list[dict], dict]:
        raise RuntimeError("gmail unavailable")

    monkeypatch.setattr(comms, "_collect_communications", fail_collection)

    state = _state()
    result = comms_agent(state)

    assert result["comms"] is not None
    assert result["comms"]["emails"] == []
    assert result["comms"]["post_delivery_interaction"] is False
    assert result["comms"]["raw"]["source"] == "comms_agent_empty"
    assert result["comms"]["raw"]["error"] == "comms_processing_failed"
    assert "comms_processing_failed" in result["degraded_reasons"]


def test_comms_agent_keeps_freshdesk_when_gmail_fails(monkeypatch) -> None:
    state = _state()
    state["chargeback_received_at"] = state["filing_deadline"] - timedelta(days=20)
    monkeypatch.delenv("CHARGEGUARD_USE_STUBS", raising=False)
    monkeypatch.setattr(
        comms,
        "_collect_gmail",
        lambda state: (_ for _ in ()).throw(RuntimeError("gmail unavailable")),
    )
    monkeypatch.setattr(
        comms,
        "_collect_freshdesk",
        lambda state: [
            {
                "id": 123,
                "subject": "Problem with order_demo_001",
                "created_at": "2026-05-15T10:00:00Z",
            }
        ],
    )

    result = comms_agent(state)

    assert result["comms"] is not None
    assert len(result["comms"]["support_tickets"]) == 1
    assert result["comms"]["raw"]["source_errors"] == {
        "gmail_provider_unavailable": "gmail_provider_unavailable"
    }
    assert "gmail_provider_unavailable" in result["degraded_reasons"]


def test_comms_collection_uses_merchant_support_connector(monkeypatch) -> None:
    state = _state()
    state["provider"] = "razorpay"
    state["provider_order_id"] = "order_rzp_do_not_search"
    state["commerce_order_id"] = "shopify_1001"
    state["commerce_order_number"] = "#1001"
    state["merchant_profile"]["support_connector_ref"] = "ACME"
    state["merchant_profile"]["gmail_user_id"] = "support@acme.example"
    calls: dict[str, dict] = {}

    class FakeGmailReader:
        @classmethod
        def from_env(cls, **kwargs):
            calls["gmail"] = kwargs
            return cls()

        def search_messages(self, query):
            calls["gmail_query"] = {"query": query}
            return []

    class FakeFreshdeskClient:
        @classmethod
        def from_env(cls, **kwargs):
            calls["freshdesk"] = kwargs
            return cls()

        def search_tickets(self, *, email):
            calls["freshdesk_query"] = {"email": email}
            return [
                {"id": 1, "subject": "Question about #1001"},
                {"id": 2, "subject": "Question about shopify_1001"},
                {"id": 3, "subject": "Question about order_rzp_do_not_search"},
            ]

    monkeypatch.setattr(comms, "GmailReader", FakeGmailReader)
    monkeypatch.setattr(comms, "FreshdeskClient", FakeFreshdeskClient)

    assert comms._collect_gmail(state) == []
    assert [ticket["id"] for ticket in comms._collect_freshdesk(state)] == [1, 2]
    assert calls["gmail"] == {
        "connector_ref": "ACME",
        "user_id": "support@acme.example",
    }
    assert calls["freshdesk"] == {
        "connector_ref": "ACME",
        "domain": "demo.freshdesk.com",
    }
    assert "#1001" in calls["gmail_query"]["query"]
    assert "shopify_1001" in calls["gmail_query"]["query"]
    assert "order_rzp_do_not_search" not in calls["gmail_query"]["query"]
    assert calls["freshdesk_query"] == {"email": "buyer@example.com"}
