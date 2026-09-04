"""Deterministic, synthetic scenario catalog for the local Razorpay simulator."""

from copy import deepcopy
from typing import Any


_BASE_CARD = {
    "payment_amount_paise": 250_000,
    "dispute_amount_paise": 250_000,
    "currency": "INR",
    "method": "card",
    "card_network": "VISA",
    "network_reason_code": "13.1",
    "razorpay_reason_code": "product_not_received",
    "respond_within_hours": 72,
}


def _scenario(
    scenario_id: str,
    family: str,
    title: str,
    description: str,
    expected: str,
    *,
    behavior: str = "created",
    **overrides: Any,
) -> dict[str, Any]:
    payload = {**_BASE_CARD, **overrides}
    return {
        "id": scenario_id,
        "family": family,
        "title": title,
        "description": description,
        "expected": expected,
        "behavior": behavior,
        "payload": payload,
    }


_SCENARIOS = (
    # Decision routing: four representative business outcomes.
    _scenario(
        "friendly-fraud-high-value",
        "Decision routing",
        "High-value delivered card order",
        "Strong stubbed authentication and delivery evidence on a high-value case.",
        "FIGHT and build a locally filed rebuttal when the model artifact is ready.",
    ),
    _scenario(
        "low-value-delivered",
        "Decision routing",
        "Low-value delivered card order",
        "The same strong evidence is uneconomic to represent because response cost exceeds recovery.",
        "ACCEPT with ACCEPTED_NO_CONTEST.",
        payment_amount_paise=500,
        dispute_amount_paise=500,
    ),
    _scenario(
        "upi-chargeback",
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
        "Decision routing",
        "Expired provider deadline",
        "The provider respond-by deadline has already passed.",
        "Manual review without starting automated evidence collection.",
        respond_within_hours=-1,
    ),

    # Every currently implemented card-network playbook.
    _scenario(
        "visa-10-4-card-absent-fraud",
        "Network playbooks",
        "Visa 10.4 card-absent fraud",
        "Exercises Visa fraud routing and the CE3 purchase-history check.",
        "Use Visa 10.4 playbook; CE3 remains unqualified without two eligible prior orders.",
        network_reason_code="10.4",
        razorpay_reason_code="fraudulent",
    ),
    _scenario(
        "visa-13-3-not-as-described",
        "Network playbooks",
        "Visa 13.3 not as described",
        "Customer disputes the quality or description of fulfilled merchandise/services.",
        "Use Visa 13.3 transaction, communications, and shipping requirements.",
        network_reason_code="13.3",
        razorpay_reason_code="product_not_as_described",
    ),
    _scenario(
        "mastercard-4853-cardholder-dispute",
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
        "Payment rails",
        "Card payment",
        "A supported card payment with explicit network and mapped reason.",
        "Eligible for the automated graph.",
    ),
    _scenario(
        "rail-upi",
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
        "Webhook trust",
        "Valid exact-body signature",
        "Signs the exact serialized request body with the configured simulator secret.",
        "HTTP 202 and one claimed provider event.",
    ),
    _scenario(
        "webhook-invalid-signature",
        "Webhook trust",
        "Invalid signature",
        "Sends a structurally valid event signed by the wrong synthetic secret.",
        "HTTP 401 and no provider-event claim.",
        behavior="invalid_signature",
    ),
    _scenario(
        "webhook-duplicate-event",
        "Webhook trust",
        "Duplicate event delivery",
        "Delivers the same body and event ID twice, as a provider retry may do.",
        "First request queues; second returns duplicate without a second graph run.",
        behavior="duplicate",
    ),
    _scenario(
        "webhook-unknown-account",
        "Webhook trust",
        "Authentic event for unknown account",
        "Uses a valid signature but an unmapped provider account ID.",
        "HTTP 202 followed by recoverable unresolved state; merchant is never guessed.",
        behavior="unknown_account",
    ),

    # Provider lifecycle and event ordering.
    _scenario(
        "lifecycle-action-required",
        "Provider lifecycle",
        "Provider action required",
        "Delivers created followed by payment.dispute.action_required.",
        "Case is flagged for operator action without inventing an outcome.",
        behavior="created_then_action_required",
    ),
    _scenario(
        "lifecycle-under-review",
        "Provider lifecycle",
        "Provider under review",
        "Delivers created followed by payment.dispute.under_review.",
        "Provider action-required is cleared; final outcome remains pending.",
        behavior="created_then_under_review",
    ),
    _scenario(
        "lifecycle-won-before-created",
        "Provider lifecycle",
        "Out-of-order WON event",
        "A terminal event arrives before ChargeGuard has observed the create event.",
        "Degrade to human review; never invent a filed representment or training label.",
        behavior="out_of_order_won",
    ),
    _scenario(
        "lifecycle-closed",
        "Provider lifecycle",
        "Provider closes dispute",
        "Delivers created followed by payment.dispute.closed.",
        "Lifecycle metadata is updated; CLOSED does not create WIN or LOSS.",
        behavior="created_then_closed",
    ),

    # Common real-world boundaries where automation must stop or preserve exact facts.
    _scenario(
        "manual-missing-network-reason",
        "Automation boundaries",
        "Unknown network reason",
        "Card network is known but no verified network reason mapping is available.",
        "Manual review for network_reason_code_unavailable.",
        network_reason_code=None,
        razorpay_reason_code="provider_specific_unknown",
    ),
    _scenario(
        "manual-unsupported-amex",
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
        "Automation boundaries",
        "Partial-amount dispute",
        "Only part of a captured payment is disputed, a common provider event shape.",
        "Preserve the exact disputed amount and apply normal deterministic routing.",
        payment_amount_paise=500_000,
        dispute_amount_paise=125_000,
    ),
    _scenario(
        "manual-urgent-deadline",
        "Automation boundaries",
        "Urgent future deadline",
        "Only six hours remain before respond-by.",
        "Use urgent priority but still run the full evidence route.",
        respond_within_hours=6,
    ),
)


def list_simulation_scenarios() -> list[dict[str, Any]]:
    """Return a caller-safe copy of the fixed scenario catalog."""
    return deepcopy(list(_SCENARIOS))


def get_simulation_scenario(scenario_id: str) -> dict[str, Any] | None:
    """Return one scenario without allowing a caller to mutate the catalog."""
    scenario = next((item for item in _SCENARIOS if item["id"] == scenario_id), None)
    return deepcopy(scenario) if scenario is not None else None
