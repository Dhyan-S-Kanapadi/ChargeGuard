"""Deterministic, synthetic scenario catalog for the local Razorpay simulator."""

from copy import deepcopy
import os
from typing import Any


_BASE_CARD = {
    "currency": "INR",
    "method": "card",
    "card_network": "VISA",
    "network_reason_code": "13.1",
    "razorpay_reason_code": "product_not_received",
    "respond_within_hours": 72,
}


def _scenario(
    scenario_id: str,
    amount_paise: int,
    family: str,
    title: str,
    description: str,
    expected: str,
    *,
    behavior: str = "created",
    device: dict[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    payload = {**_BASE_CARD, "payment_amount_paise": amount_paise,
               "dispute_amount_paise": amount_paise, **overrides}
    return {
        "id": scenario_id,
        "family": family,
        "title": title,
        "description": description,
        "expected": expected,
        "behavior": behavior,
        "device": device,
        "payload": payload,
    }


_SCENARIOS = (
    # Decision routing: four representative business outcomes.
    _scenario(
        "friendly-fraud-high-value",
        2499900,
        "Decision routing",
        "High-value delivered card order",
        "Strong stubbed authentication and delivery evidence on a high-value case.",
        "FIGHT and build a locally filed rebuttal when the model artifact is ready.",
    ),
    _scenario(
        "low-value-delivered",
        49900,
        "Decision routing",
        "Low-value delivered card order",
        "The same strong evidence is uneconomic to represent because response cost exceeds recovery.",
        "ACCEPT with ACCEPTED_NO_CONTEST.",
    ),
    _scenario(
        "upi-chargeback",
        124900,
        "Decision routing",
        "UPI dispute received on card workflow",
        "A real non-card rail must never be inferred to be RuPay or another card network.",
        "ESCALATE_DEGRADED for unsupported_payment_rail:UPI.",
        method="upi",
        card_network=None,
        network_reason_code=None,
        razorpay_reason_code="unauthorised_transaction",
    ),
    _scenario(
        "expired-response-window",
        875000,
        "Decision routing",
        "Expired provider deadline",
        "The provider respond-by deadline has already passed.",
        "Manual review without starting automated evidence collection.",
        respond_within_hours=-1,
    ),

    # Every currently implemented card-network playbook.
    _scenario(
        "visa-10-4-card-absent-fraud",
        1249900,
        "Network playbooks",
        "Visa 10.4 card-absent fraud",
        "Exercises Visa fraud routing and the CE3 purchase-history check.",
        "Use Visa 10.4 playbook; CE3 remains unqualified without two eligible prior orders.",
        network_reason_code="10.4",
        razorpay_reason_code="fraudulent",
    ),
    _scenario(
        "visa-13-3-not-as-described",
        379900,
        "Network playbooks",
        "Visa 13.3 not as described",
        "Customer disputes the quality or description of fulfilled merchandise/services.",
        "Use Visa 13.3 transaction, communications, and shipping requirements.",
        network_reason_code="13.3",
        razorpay_reason_code="product_not_as_described",
    ),
    _scenario(
        "mastercard-4853-cardholder-dispute",
        745000,
        "Network playbooks",
        "Mastercard 4853 cardholder dispute",
        "Exercises the implemented Mastercard evidence and 15-page quality cap.",
        "Use Mastercard 4853 playbook and template.",
        card_network="MASTERCARD",
        network_reason_code="4853",
        razorpay_reason_code="cardholder_dispute",
    ),
    _scenario(
        "rupay-ua02-unauthorized-cnp",
        219900,
        "Network playbooks",
        "RuPay UA02 unauthorized CNP",
        "Exercises the RuPay namespaced playbook and working-day deadline policy.",
        "Use RuPay UA02 playbook and template.",
        card_network="RUPAY",
        network_reason_code="UA02",
        razorpay_reason_code="unauthorised_transaction",
    ),

    # All rails accepted by the simulator request schema.
    _scenario(
        "rail-card",
        159900,
        "Payment rails",
        "Card payment",
        "A supported card payment with explicit network and mapped reason.",
        "Eligible for the automated graph.",
    ),
    _scenario(
        "rail-upi",
        74900,
        "Payment rails",
        "UPI payment",
        "A UPI payment carries no card entity even if a caller supplies a card hint.",
        "Manual review; card network remains empty.",
        method="upi",
        card_network=None,
        network_reason_code=None,
    ),
    _scenario(
        "rail-netbanking",
        1825000,
        "Payment rails",
        "Netbanking payment",
        "A bank-transfer rail enters the dispute receiver but not card representment automation.",
        "Manual review for unsupported_payment_rail:NETBANKING.",
        method="netbanking",
        card_network=None,
        network_reason_code=None,
    ),
    _scenario(
        "rail-wallet",
        34900,
        "Payment rails",
        "Wallet payment",
        "A wallet payment is retained as WALLET and is not assigned a card network.",
        "Manual review for unsupported_payment_rail:WALLET.",
        method="wallet",
        card_network=None,
        network_reason_code=None,
    ),

    # Receiver trust boundary and idempotency.
    _scenario(
        "webhook-valid-signature",
        629900,
        "Webhook trust",
        "Valid exact-body signature",
        "Signs the exact serialized request body with the configured simulator secret.",
        "HTTP 202 and one claimed provider event.",
    ),
    _scenario(
        "webhook-invalid-signature",
        99900,
        "Webhook trust",
        "Invalid signature",
        "Sends a structurally valid event signed by the wrong synthetic secret.",
        "HTTP 401 and no provider-event claim.",
        behavior="invalid_signature",
    ),
    _scenario(
        "webhook-duplicate-event",
        449900,
        "Webhook trust",
        "Duplicate event delivery",
        "Delivers the same body and event ID twice, as a provider retry may do.",
        "First request queues; second returns duplicate without a second graph run.",
        behavior="duplicate",
    ),
    _scenario(
        "webhook-unknown-account",
        1579900,
        "Webhook trust",
        "Authentic event for unknown account",
        "Uses a valid signature but an unmapped provider account ID.",
        "HTTP 202 followed by recoverable unresolved state; merchant is never guessed.",
        behavior="unknown_account",
    ),

    # Provider lifecycle and event ordering.
    _scenario(
        "lifecycle-action-required",
        525000,
        "Provider lifecycle",
        "Provider action required",
        "Delivers created followed by payment.dispute.action_required.",
        "Case is flagged for operator action without inventing an outcome.",
        behavior="created_then_action_required",
    ),
    _scenario(
        "lifecycle-under-review",
        1199000,
        "Provider lifecycle",
        "Provider under review",
        "Delivers created followed by payment.dispute.under_review.",
        "Provider action-required is cleared; final outcome remains pending.",
        behavior="created_then_under_review",
    ),
    _scenario(
        "lifecycle-won-before-created",
        2250000,
        "Provider lifecycle",
        "Out-of-order WON event",
        "A terminal event arrives before ChargeGuard has observed the create event.",
        "Degrade to human review; never invent a filed representment or training label.",
        behavior="out_of_order_won",
    ),
    _scenario(
        "lifecycle-closed",
        285000,
        "Provider lifecycle",
        "Provider closes dispute",
        "Delivers created followed by payment.dispute.closed.",
        "Lifecycle metadata is updated; CLOSED does not create WIN or LOSS.",
        behavior="created_then_closed",
    ),

    # Common real-world boundaries where automation must stop or preserve exact facts.
    _scenario(
        "manual-missing-network-reason",
        189900,
        "Automation boundaries",
        "Unknown network reason",
        "Card network is known but no verified network reason mapping is available.",
        "Manual review for network_reason_code_unavailable.",
        network_reason_code=None,
        razorpay_reason_code="provider_specific_unknown",
    ),
    _scenario(
        "manual-unsupported-amex",
        3149900,
        "Automation boundaries",
        "Unsupported Amex playbook",
        "The card network is recognized but this repository has no Amex playbook/template.",
        "Manual review for network_playbook_unavailable.",
        card_network="AMEX",
        network_reason_code="F29",
        razorpay_reason_code="fraudulent",
    ),
    _scenario(
        "manual-partial-dispute",
        1250000,
        "Automation boundaries",
        "Partial-amount dispute",
        "Only part of a captured payment is disputed, a common provider event shape.",
        "Preserve the exact disputed amount and apply normal deterministic routing.",
        payment_amount_paise=5_000_000,
    ),
    _scenario(
        "manual-urgent-deadline",
        949900,
        "Automation boundaries",
        "Urgent future deadline",
        "Only six hours remain before respond-by.",
        "Use urgent priority but still run the full evidence route.",
        respond_within_hours=6,
    ),
)


_SCENARIOS += (
    _scenario(
        "device-consistent", 689900, "Device and IP", "Consistent device and location",
        "An authenticated purchase from a familiar device near the delivery address.",
        "Risk score 8, matching location, no VPN; normal deterministic scoring.",
        device={"ip": "192.0.2.21", "device_id": "sim_familiar_phone",
                "risk": {"fraud_score": 8, "geo": {"matches_shipping_region": True},
                         "network": {"vpn": False}, "login": {"pattern": "normal"}}},
    ),
    _scenario(
        "device-new-mobile", 429900, "Device and IP", "New phone on mobile data",
        "A legitimate authenticated purchase uses a new phone and changed mobile IP.",
        "Risk score 22 with matching location and no VPN; device change alone cannot decide fraud.",
        device={"ip": "198.51.100.22", "device_id": "sim_new_phone",
                "risk": {"fraud_score": 22, "geo": {"matches_shipping_region": True},
                         "network": {"vpn": False}, "login": {"pattern": "normal"}}},
    ),
    _scenario(
        "device-vpn-mismatch", 2899900, "Device and IP", "VPN and distant IP location",
        "An unfamiliar device has a high provider risk score, VPN, and a location mismatch.",
        "Risk score 91, VPN true, location false; score all evidence without declaring fraud automatically.",
        device={"ip": "203.0.113.23", "device_id": "sim_unfamiliar_device",
                "risk": {"fraud_score": 91, "geo": {"matches_shipping_region": False},
                         "network": {"vpn": True}, "login": {"pattern": "unusual"}}},
    ),
    _scenario(
        "device-travelling", 1299900, "Device and IP", "Familiar device while travelling",
        "A familiar device uses a distant IP while the parcel goes to the home address.",
        "Risk score 35 and location mismatch without VPN; a mismatch alone does not establish fraud.",
        device={"ip": "2001:db8::24", "device_id": "sim_familiar_phone",
                "risk": {"fraud_score": 35, "geo": {"matches_shipping_region": False},
                         "network": {"vpn": False}, "login": {"pattern": "normal"}}},
    ),
    _scenario(
        "device-timeout", 569900, "Device failures", "Device provider timeout",
        "The device-risk request times out while other evidence sources remain available.",
        "Device evidence is unavailable; ESCALATE_DEGRADED and no filing.",
        device={"ip": "192.0.2.25", "device_id": "sim_timeout", "error": "timeout"},
    ),
    _scenario(
        "device-missing-ip", 799900, "Device failures", "Missing checkout IP",
        "The payment record contains neither a checkout IP nor a device fingerprint.",
        "Missing context cannot become safe evidence; ESCALATE_DEGRADED.",
        device={"ip": "", "device_id": "", "risk": {"fraud_score": 18}},
    ),
    _scenario(
        "device-invalid-response", 1099900, "Device failures", "Malformed risk response",
        "The provider returns a non-finite risk score instead of usable evidence.",
        "Reject invalid risk evidence and ESCALATE_DEGRADED.",
        device={"ip": "198.51.100.27", "device_id": "sim_bad_response",
                "risk": {"fraud_score": "NaN"}},
    ),
    _scenario(
        "device-auth-failure", 1749900, "Device failures", "Device provider rejects credentials",
        "A simulated SEON request returns an authentication error.",
        "Device evidence unavailable; ESCALATE_DEGRADED without changing real connectors.",
        device={"ip": "203.0.113.28", "device_id": "sim_auth_failure", "error": "authentication"},
    ),
)


def simulation_record_for_state(state: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve only locally registered, merchant-owned simulation evidence."""
    from api.store import store

    if os.getenv("ENVIRONMENT", "development").strip().lower() == "production":
        return None
    if os.getenv("RAZORPAY_SIMULATOR_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    if not str(state.get("chargeback_id", "")).startswith("disp_SIM_"):
        return None
    record = store.get_simulator_dispute(state["chargeback_id"])
    if record is None or record["merchant_id"] != state["merchant_profile"]["merchant_id"]:
        return None
    if record["payment_id"] != state.get("payment_id"):
        return None
    return record


def list_simulation_scenarios() -> list[dict[str, Any]]:
    """Return a caller-safe copy of the fixed scenario catalog."""
    return deepcopy(list(_SCENARIOS))


def get_simulation_scenario(scenario_id: str) -> dict[str, Any] | None:
    """Return one scenario without allowing a caller to mutate the catalog."""
    scenario = next((item for item in _SCENARIOS if item["id"] == scenario_id), None)
    return deepcopy(scenario) if scenario is not None else None
