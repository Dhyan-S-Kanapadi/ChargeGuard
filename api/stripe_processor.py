"""Deferred Stripe event processing backed by the shared provider-event lifecycle."""
from datetime import datetime, timezone
from typing import Any

from agents.escalation import human_escalation_agent
from api.store import store
from api.webhooks import build_initial_state, run_chargeback_graph

_TERMINAL = {"charge.dispute.closed", "charge.dispute.funds_reinstated", "charge.dispute.funds_withdrawn"}

def process_stripe_provider_event(event_id: str) -> dict[str, Any]:
    event = store.get_provider_event(event_id)
    if event is None: return {"status": "missing", "event_id": event_id}
    if not store.start_provider_event_processing(event_id): return {"status": "skipped", "event_id": event_id}
    try:
        data = event["event_data"]["data"]["object"]
        account = event.get("account_id")
        merchant = store.get_merchant_by_payment_connector_account("stripe", account) if account else None
        if merchant is None:
            store.update_provider_event(event_id, processing_state="unresolved", failure_reason="No merchant mapping for Stripe account ID.")
            return {"status": "unresolved", "event_id": event_id}
        dispute_id = str(data.get("id") or "")
        if not dispute_id: raise ValueError("Stripe dispute identifier missing")
        created = datetime.fromtimestamp(int(data.get("created") or 0), timezone.utc) if data.get("created") else datetime.now(timezone.utc)
        current = store.get_dispute(dispute_id)
        incoming = event.get("provider_event_timestamp")
        if current:
            state = current["state"]
            if state.get("merchant_profile", {}).get("merchant_id") != merchant["merchant_id"]:
                store.update_provider_event(event_id, processing_state="unresolved", failure_reason="Stripe dispute ownership could not be resolved.")
                return {"status": "unresolved", "event_id": event_id}
            previous = state.get("provider_event_timestamp")
            if state.get("provider_event") in _TERMINAL or (previous and incoming and incoming < previous):
                store.update_provider_event(event_id, processing_state="stale", merchant_id=merchant["merchant_id"])
                return {"status": "stale", "event_id": event_id}
            state["provider"] = "stripe"; state["provider_event"] = event["event_type"]; state["provider_event_id"] = event_id
            state["provider_event_timestamp"] = incoming; state["provider_status"] = data.get("status")
            store.update_dispute(dispute_id, status=current["status"], state=state, error=current.get("error"))
            store.update_provider_event(event_id, processing_state="updated", merchant_id=merchant["merchant_id"])
            return {"status": "updated", "event_id": event_id}
        rail = str(data.get("payment_method_type") or "").upper()
        network = {"visa":"VISA", "mastercard":"MASTERCARD", "amex":"AMEX"}.get(str(data.get("network") or "").lower())
        reasons = ["unsupported_payment_rail:" + (rail or "UNKNOWN") if rail != "CARD" else "network_playbook_unavailable", "network_reason_code_unavailable", "respond_by_unavailable"]
        state = build_initial_state(chargeback_id=dispute_id, order_id=None, payment_id=str(data.get("payment_intent") or data.get("charge") or ""), reason_code="", card_network=network, dispute_amount=float(data.get("amount") or 0) / 100, currency=str(data.get("currency") or "USD").upper(), filing_deadline=created, merchant_profile=merchant, received_at=created, evidence_collection_degraded=True, degraded_reasons=reasons)
        state.update({"provider":"stripe", "provider_dispute_id":dispute_id, "provider_event":event["event_type"], "provider_event_id":event_id, "provider_event_timestamp":incoming, "provider_account_id":account, "provider_status":data.get("status"), "payment_rail":rail or None, "requires_human_review":True, "decision":"ESCALATE_DEGRADED"})
        state = human_escalation_agent(state)
        store.create_dispute(state); store.update_dispute(dispute_id, status="completed", state=state)
        store.update_provider_event(event_id, processing_state="manual_review", merchant_id=merchant["merchant_id"])
        return {"status":"manual_review", "event_id":event_id}
    except Exception:
        store.update_provider_event(event_id, processing_state="failed", failure_reason="Stripe provider event processing failed.")
        return {"status":"failed", "event_id":event_id}
