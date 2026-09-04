from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from core.state import (
    ClassificationSuggestion,
    ChargebackState,
    MerchantProfile,
    OrderRecord,
    PaymentConnector,
)


PROVIDER_EVENT_STATES = frozenset(
    {
        "received",
        "queued",
        "processing",
        "scheduled",
        "manual_review",
        "updated",
        "ignored",
        "unresolved",
        "failed",
        "stale",
        "outcome_not_eligible",
    }
)
NON_TERMINAL_PROVIDER_EVENT_STATES = frozenset(
    {"received", "queued", "processing"}
)
TERMINAL_PROVIDER_EVENT_STATES = PROVIDER_EVENT_STATES - NON_TERMINAL_PROVIDER_EVENT_STATES
_MERCHANT_CREDENTIAL_KEYS = (
    "shopify_admin_api_token",
    "woocommerce_api_key",
    "woocommerce_api_secret",
)


class OrderIdentifierConflictError(ValueError):
    """Raised when one merchant maps a provider identifier to two orders."""


def _provider_event_claim_timeout_seconds() -> int:
    try:
        return max(1, int(os.getenv("PROVIDER_EVENT_CLAIM_TIMEOUT_SECONDS", "300")))
    except ValueError:
        return 300


def _provider_event_is_stale(
    event: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    if (event.get("processing_state") or event.get("processing_status")) != "processing":
        return False
    reference = event.get("last_attempt_at") or event.get("received_at")
    if not isinstance(reference, datetime):
        return True
    current_time = now or datetime.now(timezone.utc)
    return reference <= current_time - timedelta(
        seconds=_provider_event_claim_timeout_seconds()
    )


class InMemoryStore:
    """Thread-safe repository with optional JSON persistence for local durability."""

    # TODO: Multi-worker production requires a shared transactional database and
    # durable queue/outbox with atomic provider-event claim and job creation.

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = RLock()
        self._path = Path(path) if path else None
        self._merchants: dict[str, MerchantProfile] = {}
        self._payment_connectors: dict[str, PaymentConnector] = {}
        self._payment_connector_audit: list[dict[str, Any]] = []
        self._orders: dict[str, OrderRecord] = {}
        self._disputes: dict[str, dict[str, Any]] = {}
        self._provider_events: dict[str, dict[str, Any]] = {}
        self._simulator_disputes: dict[str, dict[str, Any]] = {}
        self._load()

    @classmethod
    def from_env(cls) -> "InMemoryStore":
        return cls(path=os.getenv("CHARGEGUARD_STORE_PATH"))

    def clear(self) -> None:
        with self._lock:
            self._merchants.clear()
            self._payment_connectors.clear()
            self._payment_connector_audit.clear()
            self._orders.clear()
            self._disputes.clear()
            self._provider_events.clear()
            self._simulator_disputes.clear()
            self._save()

    def create_merchant(self, profile: MerchantProfile) -> bool:
        merchant_id = profile["merchant_id"]
        with self._lock:
            if merchant_id in self._merchants:
                return False
            account_id = profile.get("razorpay_account_id")
            if account_id and any(
                merchant.get("razorpay_account_id") == account_id
                for merchant in self._merchants.values()
            ):
                return False
            if account_id and any(
                connector["provider"] == "razorpay"
                and connector["provider_account_id"] == account_id
                and connector["status"] == "verified"
                for connector in self._payment_connectors.values()
            ):
                return False
            self._merchants[merchant_id] = deepcopy(profile)
            self._save()
            return True

    def get_merchant(self, merchant_id: str) -> MerchantProfile | None:
        with self._lock:
            profile = self._merchants.get(merchant_id)
            return deepcopy(profile) if profile else None

    def get_merchant_by_razorpay_account_id(
        self,
        account_id: str,
        *,
        allow_legacy: bool = True,
    ) -> MerchantProfile | None:
        with self._lock:
            connector = next(
                (
                    item
                    for item in self._payment_connectors.values()
                    if item["provider"] == "razorpay"
                    and item["provider_account_id"] == account_id
                    and item["status"] == "verified"
                ),
                None,
            )
            if connector is not None:
                profile = self._merchants.get(connector["merchant_id"])
                return deepcopy(profile) if profile else None
            if not allow_legacy:
                return None
            profile = next(
                (
                    merchant
                    for merchant in self._merchants.values()
                    if merchant.get("razorpay_account_id") == account_id
                ),
                None,
            )
            return deepcopy(profile) if profile else None

    def create_payment_connector(
        self,
        connector: PaymentConnector,
        *,
        audit_action: str,
    ) -> bool:
        """Persist a non-active connector attempt without any credential material."""
        with self._lock:
            if connector["merchant_id"] not in self._merchants:
                raise KeyError(connector["merchant_id"])
            if connector["connector_id"] in self._payment_connectors:
                return False
            connectors_before = deepcopy(self._payment_connectors)
            audit_before = deepcopy(self._payment_connector_audit)
            try:
                self._payment_connectors[connector["connector_id"]] = deepcopy(connector)
                self._append_connector_audit(connector, audit_action)
                self._save()
            except Exception:
                self._payment_connectors = connectors_before
                self._payment_connector_audit = audit_before
                raise
            return True

    def activate_payment_connector(
        self,
        connector: PaymentConnector,
        *,
        audit_action: str,
    ) -> str | None:
        """Atomically activate one verified connector and detach its predecessor."""
        if connector["status"] != "verified":
            raise ValueError("Only verified payment connectors can be activated.")
        with self._lock:
            merchant = self._merchants.get(connector["merchant_id"])
            if merchant is None:
                raise KeyError(connector["merchant_id"])
            if connector["provider"] == "razorpay" and connector["provider_account_id"]:
                conflict = any(
                    item["merchant_id"] != connector["merchant_id"]
                    and item["provider"] == "razorpay"
                    and item["provider_account_id"] == connector["provider_account_id"]
                    and item["status"] == "verified"
                    for item in self._payment_connectors.values()
                )
                conflict = conflict or any(
                    merchant_id != connector["merchant_id"]
                    and item.get("razorpay_account_id") == connector["provider_account_id"]
                    for merchant_id, item in self._merchants.items()
                )
                if conflict:
                    raise ValueError("razorpay_account_already_connected")

            merchants_before = deepcopy(self._merchants)
            connectors_before = deepcopy(self._payment_connectors)
            audit_before = deepcopy(self._payment_connector_audit)

            previous = next(
                (
                    item
                    for item in self._payment_connectors.values()
                    if item["merchant_id"] == connector["merchant_id"]
                    and item["provider"] == connector["provider"]
                    and item["status"] == "verified"
                ),
                None,
            )
            previous_id = previous["connector_id"] if previous else None
            if previous is not None and previous_id != connector["connector_id"]:
                previous["status"] = "disconnected"
                previous["updated_at"] = connector["updated_at"]
                self._append_connector_audit(previous, "rotated_out")

            self._payment_connectors[connector["connector_id"]] = deepcopy(connector)
            connector_ids = dict(merchant.get("payment_connector_ids", {}))
            connector_ids[connector["provider"]] = connector["connector_id"]
            merchant["payment_connector_ids"] = connector_ids
            merchant["payment_connector_id"] = connector["connector_id"]
            merchant["payment_provider"] = connector["provider"]
            if connector["provider"] == "razorpay":
                merchant["razorpay_account_id"] = connector["provider_account_id"]
            self._append_connector_audit(connector, audit_action)
            try:
                self._save()
            except Exception:
                self._merchants = merchants_before
                self._payment_connectors = connectors_before
                self._payment_connector_audit = audit_before
                raise
            return previous_id

    def get_payment_connector(
        self,
        merchant_id: str,
        connector_id: str,
    ) -> PaymentConnector | None:
        with self._lock:
            connector = self._payment_connectors.get(connector_id)
            if connector is None or connector["merchant_id"] != merchant_id:
                return None
            return deepcopy(connector)

    def list_payment_connectors(self, merchant_id: str) -> list[PaymentConnector]:
        with self._lock:
            connectors = [
                deepcopy(item)
                for item in self._payment_connectors.values()
                if item["merchant_id"] == merchant_id
            ]
        return sorted(connectors, key=lambda item: item["created_at"], reverse=True)

    def update_payment_connector_status(
        self,
        merchant_id: str,
        connector_id: str,
        *,
        status: str | None = None,
        last_error_code: str | None,
        verified_at: datetime | None = None,
        audit_action: str,
    ) -> PaymentConnector | None:
        with self._lock:
            connector = self._payment_connectors.get(connector_id)
            if connector is None or connector["merchant_id"] != merchant_id:
                return None
            merchants_before = deepcopy(self._merchants)
            connectors_before = deepcopy(self._payment_connectors)
            audit_before = deepcopy(self._payment_connector_audit)
            if status is not None:
                if status not in {"pending", "verified", "invalid", "disconnected"}:
                    raise ValueError("Invalid payment connector status.")
                connector["status"] = status  # type: ignore[typeddict-item]
            connector["last_error_code"] = last_error_code
            connector["verified_at"] = verified_at
            connector["updated_at"] = datetime.now(timezone.utc)
            if connector["status"] in {"invalid", "disconnected"}:
                self._detach_payment_connector(connector)
            self._append_connector_audit(connector, audit_action)
            try:
                self._save()
            except Exception:
                self._merchants = merchants_before
                self._payment_connectors = connectors_before
                self._payment_connector_audit = audit_before
                raise
            return deepcopy(connector)

    def disconnect_payment_connector(
        self,
        merchant_id: str,
        connector_id: str,
    ) -> PaymentConnector | None:
        return self.update_payment_connector_status(
            merchant_id,
            connector_id,
            status="disconnected",
            last_error_code=None,
            audit_action="deleted",
        )

    def list_payment_connector_audit(self, merchant_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [
                deepcopy(item)
                for item in self._payment_connector_audit
                if item["merchant_id"] == merchant_id
            ]

    def _detach_payment_connector(self, connector: PaymentConnector) -> None:
        merchant = self._merchants.get(connector["merchant_id"])
        if merchant is None:
            return
        connector_ids = dict(merchant.get("payment_connector_ids", {}))
        if connector_ids.get(connector["provider"]) == connector["connector_id"]:
            connector_ids.pop(connector["provider"], None)
            merchant["payment_connector_ids"] = connector_ids
        if merchant.get("payment_connector_id") == connector["connector_id"]:
            replacements = [
                item
                for item in self._payment_connectors.values()
                if item["merchant_id"] == connector["merchant_id"]
                and item["connector_id"] != connector["connector_id"]
                and item["status"] == "verified"
            ]
            replacement = max(
                replacements,
                key=lambda item: item["updated_at"],
                default=None,
            )
            merchant["payment_connector_id"] = (
                replacement["connector_id"] if replacement else None
            )
            merchant["payment_provider"] = replacement["provider"] if replacement else None
        if connector["provider"] == "razorpay" and (
            merchant.get("razorpay_account_id") == connector["provider_account_id"]
        ):
            replacement_id = connector_ids.get("razorpay")
            replacement = self._payment_connectors.get(replacement_id or "")
            merchant["razorpay_account_id"] = (
                replacement["provider_account_id"] if replacement else None
            )

    def _append_connector_audit(
        self,
        connector: PaymentConnector,
        action: str,
    ) -> None:
        self._payment_connector_audit.append(
            {
                "connector_id": connector["connector_id"],
                "merchant_id": connector["merchant_id"],
                "provider": connector["provider"],
                "action": action,
                "created_at": datetime.now(timezone.utc),
                "status": connector["status"],
                "error_code": connector["last_error_code"],
            }
        )

    def list_merchants(self) -> list[MerchantProfile]:
        with self._lock:
            profiles = [deepcopy(profile) for profile in self._merchants.values()]
        return sorted(profiles, key=lambda profile: profile["name"].casefold())

    def update_merchant(self, merchant_id: str, updates: dict[str, Any]) -> MerchantProfile | None:
        with self._lock:
            profile = self._merchants.get(merchant_id)
            if profile is None:
                return None
            account_id = updates.get("razorpay_account_id")
            if account_id and any(
                other_id != merchant_id and other.get("razorpay_account_id") == account_id
                for other_id, other in self._merchants.items()
            ):
                raise ValueError("Razorpay account ID already exists.")
            if account_id and any(
                connector["merchant_id"] != merchant_id
                and connector["provider"] == "razorpay"
                and connector["provider_account_id"] == account_id
                and connector["status"] == "verified"
                for connector in self._payment_connectors.values()
            ):
                raise ValueError("Razorpay account ID already exists.")
            profile.update(deepcopy(updates))
            self._save()
            return deepcopy(profile)

    def upsert_order(self, order: OrderRecord) -> bool:
        """Create or replace one merchant-scoped order; returns True when created."""
        key = self._order_key(order["merchant_id"], order["order_id"])
        with self._lock:
            self._validate_order_identifiers(order, key)
            created = key not in self._orders
            existing = self._orders.get(key)
            value = deepcopy(existing) if existing is not None else {}
            value.update(deepcopy(order))
            if existing is not None:
                value["is_disputed"] = existing["is_disputed"]
                value["is_fraud_flagged"] = existing["is_fraud_flagged"]
            if self._order_has_dispute(order["merchant_id"], order["order_id"]):
                value["is_disputed"] = True
            self._orders[key] = value
            self._save()
            return created

    def _validate_order_identifiers(self, order: OrderRecord, key: str) -> None:
        merchant_id = order["merchant_id"]
        for field in ("provider_payment_id", "provider_order_id"):
            identifier = order.get(field)
            if not identifier:
                continue
            for existing_key, existing in self._orders.items():
                if (
                    existing_key != key
                    and existing["merchant_id"] == merchant_id
                    and existing.get(field) == identifier
                ):
                    raise OrderIdentifierConflictError(
                        f"{field} is already mapped to another order for this merchant."
                    )

    def get_order(self, merchant_id: str, order_id: str) -> OrderRecord | None:
        with self._lock:
            order = self._orders.get(self._order_key(merchant_id, order_id))
            return deepcopy(order) if order else None

    def mark_order_disputed(self, merchant_id: str, order_id: str) -> None:
        with self._lock:
            order = self._orders.get(self._order_key(merchant_id, order_id))
            if order is not None and not order["is_disputed"]:
                order["is_disputed"] = True
                self._save()

    def get_order_by_provider_payment_id(
        self,
        merchant_id: str,
        provider_payment_id: str,
    ) -> OrderRecord | None:
        return self._get_order_by_identifier(
            merchant_id,
            "provider_payment_id",
            provider_payment_id,
        )

    def get_order_by_provider_order_id(
        self,
        merchant_id: str,
        provider_order_id: str,
    ) -> OrderRecord | None:
        return self._get_order_by_identifier(
            merchant_id,
            "provider_order_id",
            provider_order_id,
        )

    def get_order_by_commerce_order_number(
        self,
        merchant_id: str,
        commerce_order_number: str,
    ) -> OrderRecord | None:
        return self._get_order_by_identifier(
            merchant_id,
            "commerce_order_number",
            commerce_order_number,
        )

    def _get_order_by_identifier(
        self,
        merchant_id: str,
        field: str,
        value: str,
    ) -> OrderRecord | None:
        with self._lock:
            matches = [
                item
                for item in self._orders.values()
                if item["merchant_id"] == merchant_id and item.get(field) == value
            ]
            return deepcopy(matches[0]) if len(matches) == 1 else None

    def query_orders(
        self,
        *,
        merchant_id: str,
        customer_email: str,
        start: datetime,
        end: datetime,
        exclude_order_id: str | None = None,
    ) -> list[OrderRecord]:
        email = customer_email.strip().casefold()
        with self._lock:
            orders = [
                deepcopy(order)
                for order in self._orders.values()
                if order["merchant_id"] == merchant_id
                and order["customer_email"].strip().casefold() == email
                and order["order_id"] != exclude_order_id
                and start <= order["order_date"] <= end
            ]
        return sorted(orders, key=lambda order: order["order_date"], reverse=True)

    @staticmethod
    def _order_key(merchant_id: str, order_id: str) -> str:
        return f"{merchant_id}\x1f{order_id}"

    def _order_has_dispute(self, merchant_id: str, order_id: str) -> bool:
        return any(
            (
                record.get("state", {}).get("commerce_order_id") == order_id
                or record.get("state", {}).get("order_id") == order_id
            )
            and record.get("state", {}).get("merchant_profile", {}).get("merchant_id")
            == merchant_id
            for record in self._disputes.values()
        )

    def create_dispute(self, state: ChargebackState) -> bool:
        chargeback_id = state["chargeback_id"]
        now = datetime.now(timezone.utc)
        with self._lock:
            if chargeback_id in self._disputes:
                return False
            self._disputes[chargeback_id] = {
                "chargeback_id": chargeback_id,
                "status": "received",
                "state": deepcopy(state),
                "error": None,
                "created_at": now,
                "updated_at": now,
            }
            merchant_id = state.get("merchant_profile", {}).get("merchant_id")
            order_id = state.get("commerce_order_id") or state.get("order_id")
            if merchant_id and order_id:
                order = self._orders.get(self._order_key(merchant_id, order_id))
                if order is not None:
                    order["is_disputed"] = True
            self._save()
            return True

    def claim_dispute_classification(
        self,
        chargeback_id: str,
        *,
        card_network: str,
        network_reason_code: str,
        actor_id: str,
        suggestion_id: str | None = None,
        minimum_suggestion_confidence: float | None = None,
    ) -> ChargebackState | None:
        """Atomically persist a valid operator classification and claim one graph run."""
        with self._lock:
            record = self._disputes.get(chargeback_id)
            if record is None:
                raise KeyError(chargeback_id)
            state = record["state"]
            if state.get("classification_resume_scheduled"):
                return None
            if record["status"] != "completed" or state.get("provider") != "razorpay":
                return None
            reasons = state.get("degraded_reasons", [])
            if (
                state.get("decision") != "ESCALATE_DEGRADED"
                or state.get("provider_event") != "payment.dispute.created"
                or not {
                    "card_network_unavailable",
                    "network_reason_code_unavailable",
                    "network_playbook_unavailable",
                }.intersection(reasons)
            ):
                return None

            resolved = {
                "card_network_unavailable",
                "network_reason_code_unavailable",
                "network_playbook_unavailable",
            }
            remaining = [
                reason for reason in reasons if reason not in resolved
            ]
            if remaining:
                raise ValueError(
                    "Dispute has unresolved manual-review requirements: "
                    + ", ".join(remaining)
                )

            suggestion = state.get("classification_suggestion")
            if suggestion_id is not None:
                deadline = state.get("provider_respond_by")
                deadline_valid = isinstance(deadline, datetime) and deadline.replace(
                    tzinfo=deadline.tzinfo or timezone.utc
                ) > datetime.now(timezone.utc)
                if not suggestion or suggestion.get("suggestion_id") != suggestion_id:
                    raise ValueError("Classification suggestion does not belong to this dispute.")
                if suggestion.get("status") != "pending":
                    raise ValueError("Classification suggestion is no longer pending.")
                if (
                    state.get("card_network") != card_network
                    or state.get("network_reason_code")
                    or state.get("deadline_overdue")
                    or not deadline_valid
                ):
                    raise ValueError("Classification suggestion is no longer eligible for approval.")
                if (
                    suggestion.get("card_network") != card_network
                    or suggestion.get("recommended_reason_code") != network_reason_code
                ):
                    raise ValueError(
                        "Submitted classification does not match the pending suggestion."
                    )
                if (
                    minimum_suggestion_confidence is None
                    or suggestion.get("confidence", -1) < minimum_suggestion_confidence
                ):
                    raise ValueError("Classification suggestion is below the approval threshold.")

            now = datetime.now(timezone.utc)
            state["card_network"] = card_network
            state["network_reason_code"] = network_reason_code
            state["reason_code"] = network_reason_code
            state["reason_mapping_version"] = "operator-v1"
            source = "llm_assisted_operator" if suggestion_id is not None else "authenticated_operator"
            state["reason_mapping_source"] = (
                "llm_assisted_operator" if suggestion_id is not None else "manual_classification"
            )
            state["classification_audit"] = {
                "actor_id": actor_id,
                "classified_at": now,
                "source": source,
                "card_network": card_network,
                "network_reason_code": network_reason_code,
            }
            if suggestion_id is not None and suggestion is not None:
                suggestion["status"] = "approved"
                suggestion["resolved_at"] = now
                suggestion["resolved_by_actor_id"] = actor_id
            elif suggestion and suggestion.get("status") == "pending":
                suggestion["status"] = "rejected"
                suggestion["unavailability_reason"] = "manual_classification_used"
                suggestion["resolved_at"] = now
                suggestion["resolved_by_actor_id"] = actor_id
            state["classification_resume_scheduled"] = True
            state["degraded_reasons"] = []
            state["evidence_collection_degraded"] = False
            state["requires_human_review"] = False
            state["decision"] = None
            state["decision_reasoning"] = None
            state["filing_confirmation"] = None
            state["final_outcome"] = None
            state["outcome_reason"] = None
            state["human_review_summary"] = None
            record["status"] = "received"
            record["state"] = state
            record["error"] = None
            record["updated_at"] = now
            self._save()
            return deepcopy(state)

    def save_classification_suggestion(
        self,
        chargeback_id: str,
        suggestion: ClassificationSuggestion,
    ) -> ClassificationSuggestion | None:
        """Persist a recommendation only while its dispute is still unresolved."""
        with self._lock:
            record = self._disputes.get(chargeback_id)
            if record is None:
                raise KeyError(chargeback_id)
            state = record["state"]
            existing = state.get("classification_suggestion")
            reasons = set(state.get("degraded_reasons", []))
            deadline = state.get("provider_respond_by")
            deadline_valid = isinstance(deadline, datetime) and deadline.replace(
                tzinfo=deadline.tzinfo or timezone.utc
            ) > datetime.now(timezone.utc)
            if (
                record.get("status") != "completed"
                or state.get("classification_resume_scheduled")
                or state.get("network_reason_code")
                or state.get("card_network") != suggestion["card_network"]
                or state.get("decision") != "ESCALATE_DEGRADED"
                or not state.get("requires_human_review")
                or state.get("provider") != "razorpay"
                or state.get("provider_event") != "payment.dispute.created"
                or state.get("payment_rail") != "CARD"
                or "network_reason_code_unavailable" not in reasons
                or reasons - {
                    "network_reason_code_unavailable",
                    "network_playbook_unavailable",
                }
                or state.get("deadline_overdue")
                or not deadline_valid
            ):
                return None
            if existing and existing.get("status") == "pending":
                return deepcopy(existing)
            state["classification_suggestion"] = deepcopy(suggestion)
            record["updated_at"] = datetime.now(timezone.utc)
            self._save()
            return deepcopy(suggestion)

    def reject_classification_suggestion(
        self,
        chargeback_id: str,
        *,
        suggestion_id: str,
        actor_id: str,
    ) -> ClassificationSuggestion | None:
        """Atomically record a human rejection without changing classification."""
        with self._lock:
            record = self._disputes.get(chargeback_id)
            if record is None:
                raise KeyError(chargeback_id)
            suggestion = record["state"].get("classification_suggestion")
            if not suggestion or suggestion.get("suggestion_id") != suggestion_id:
                raise ValueError("Classification suggestion does not belong to this dispute.")
            if suggestion.get("status") != "pending":
                return None
            suggestion["status"] = "rejected"
            suggestion["resolved_at"] = datetime.now(timezone.utc)
            suggestion["resolved_by_actor_id"] = actor_id
            record["updated_at"] = suggestion["resolved_at"]
            self._save()
            return deepcopy(suggestion)

    def update_dispute(
        self,
        chargeback_id: str,
        *,
        status: str,
        state: ChargebackState | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            record = self._disputes[chargeback_id]
            record["status"] = status
            if state is not None:
                record["state"] = deepcopy(state)
            record["error"] = error
            record["updated_at"] = datetime.now(timezone.utc)
            self._save()

    def get_dispute(self, chargeback_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._disputes.get(chargeback_id)
            return deepcopy(record) if record else None

    def list_disputes(self) -> list[dict[str, Any]]:
        with self._lock:
            records = [deepcopy(record) for record in self._disputes.values()]
        return sorted(records, key=lambda record: record["created_at"], reverse=True)

    def claim_provider_event(self, event: dict[str, Any]) -> bool:
        """Atomically claim a provider event before any workflow scheduling."""
        event_id = str(event.get("event_id") or event.get("provider_event_id") or "")
        if not event_id:
            raise ValueError("Provider event requires an event ID.")
        with self._lock:
            now = datetime.now(timezone.utc)
            existing = self._provider_events.get(event_id)
            if existing is not None:
                processing_state = existing.get("processing_state") or existing.get(
                    "processing_status"
                )
                stale_processing = _provider_event_is_stale(existing, now)
                if processing_state != "failed" and not stale_processing:
                    return False
                attempt_count = int(existing.get("attempt_count") or 0)
                received_at = existing.get("received_at") or now
                existing.update(deepcopy(event))
                existing["event_id"] = event_id
                existing["provider_event_id"] = event_id
                existing["processing_state"] = "received"
                existing["processing_status"] = "received"
                existing["failure_reason"] = None
                existing["error"] = None
                existing["processed_at"] = None
                existing["last_attempt_at"] = existing.get("last_attempt_at")
                existing["attempt_count"] = attempt_count
                existing["received_at"] = received_at
                self._save()
                return True
            record = deepcopy(event)
            record["event_id"] = event_id
            record["provider_event_id"] = event_id
            event_type = record.get("event_type") or record.get("event_name")
            record["event_type"] = event_type
            record["event_name"] = event_type
            dispute_id = record.get("provider_dispute_id") or record.get("chargeback_id")
            record["provider_dispute_id"] = dispute_id
            record["chargeback_id"] = dispute_id
            record.setdefault("received_at", now)
            failure_reason = record.get("failure_reason") or record.get("error")
            record["failure_reason"] = failure_reason
            record["error"] = failure_reason
            processing_state = record.get("processing_state") or record.get(
                "processing_status", "received"
            )
            if processing_state not in PROVIDER_EVENT_STATES:
                raise ValueError(f"Invalid provider event state: {processing_state}")
            record["processing_state"] = processing_state
            record["processing_status"] = processing_state
            record.setdefault("attempt_count", 1 if processing_state == "processing" else 0)
            record.setdefault(
                "last_attempt_at",
                now if processing_state == "processing" else None,
            )
            record["processed_at"] = None
            if processing_state in TERMINAL_PROVIDER_EVENT_STATES:
                record["processed_at"] = record.get("processed_at") or now
            self._provider_events[event_id] = record
            self._save()
            return True

    def queue_provider_event(self, event_id: str) -> bool:
        """Move a newly received event to the recoverable queue."""
        with self._lock:
            event = self._provider_events.get(event_id)
            if event is None or event.get("processing_state") != "received":
                return False
            self._set_provider_event_state(event, "queued")
            self._save()
            return True

    def start_provider_event_processing(self, event_id: str) -> bool:
        """Atomically acquire a queued event for one processing attempt."""
        with self._lock:
            event = self._provider_events.get(event_id)
            if event is None:
                return False
            if event.get("processing_state") != "queued":
                return False
            now = datetime.now(timezone.utc)
            self._set_provider_event_state(event, "processing")
            event["attempt_count"] = int(event.get("attempt_count") or 0) + 1
            event["last_attempt_at"] = now
            self._save()
            return True

    def requeue_provider_event(
        self,
        event_id: str,
        *,
        include_received: bool = True,
    ) -> bool:
        """Make an eligible failed, unresolved, queued, or abandoned event runnable."""
        with self._lock:
            event = self._provider_events.get(event_id)
            if event is None:
                return False
            processing_state = event.get("processing_state") or event.get(
                "processing_status"
            )
            eligible_states = {"queued", "failed", "unresolved"}
            if include_received:
                eligible_states.add("received")
            eligible = processing_state in eligible_states
            if processing_state == "processing":
                eligible = _provider_event_is_stale(event)
            if not eligible:
                return False
            self._set_provider_event_state(event, "queued")
            self._save()
            return True

    def list_recoverable_provider_events(
        self,
        *,
        provider: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return a bounded snapshot of events safe to enqueue for recovery."""
        bounded_limit = max(0, limit)
        with self._lock:
            recoverable = []
            for event in self._provider_events.values():
                state = event.get("processing_state") or event.get("processing_status")
                if event.get("provider") != provider:
                    continue
                if state in {"received", "queued", "failed", "unresolved"} or (
                    state == "processing" and _provider_event_is_stale(event)
                ):
                    recoverable.append(deepcopy(event))
            recoverable.sort(
                key=lambda item: item.get("received_at")
                or datetime.min.replace(tzinfo=timezone.utc)
            )
            return recoverable[:bounded_limit]

    def update_provider_event(self, event_id: str, **updates: Any) -> None:
        with self._lock:
            event = self._provider_events[event_id]
            if "processing_status" in updates and "processing_state" not in updates:
                updates["processing_state"] = updates["processing_status"]
            if "processing_state" in updates and "processing_status" not in updates:
                updates["processing_status"] = updates["processing_state"]
            if "error" in updates and "failure_reason" not in updates:
                updates["failure_reason"] = updates["error"]
            if "failure_reason" in updates and "error" not in updates:
                updates["error"] = updates["failure_reason"]
            processing_state = updates.get("processing_state")
            if processing_state and processing_state not in PROVIDER_EVENT_STATES:
                raise ValueError(f"Invalid provider event state: {processing_state}")
            event.update(deepcopy(updates))
            if processing_state:
                self._set_provider_event_state(event, processing_state)
            self._save()

    @staticmethod
    def _set_provider_event_state(event: dict[str, Any], processing_state: str) -> None:
        event["processing_state"] = processing_state
        event["processing_status"] = processing_state
        if processing_state in NON_TERMINAL_PROVIDER_EVENT_STATES:
            event["processed_at"] = None
        else:
            event["processed_at"] = datetime.now(timezone.utc)
        if processing_state in {"received", "queued", "processing"}:
            event["failure_reason"] = None
            event["error"] = None

    def get_provider_event(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            event = self._provider_events.get(event_id)
            return deepcopy(event) if event else None

    def list_provider_events(self) -> list[dict[str, Any]]:
        with self._lock:
            events = [deepcopy(event) for event in self._provider_events.values()]
        return sorted(events, key=lambda event: event["received_at"], reverse=True)

    def list_provider_events_for_dispute(
        self,
        provider: str,
        provider_dispute_id: str,
    ) -> list[dict[str, Any]]:
        return [
            event
            for event in self.list_provider_events()
            if event.get("provider") == provider
            and event.get("provider_dispute_id") == provider_dispute_id
        ]

    def create_simulator_dispute(self, dispute: dict[str, Any]) -> bool:
        dispute_id = str(dispute["dispute_id"])
        with self._lock:
            if dispute_id in self._simulator_disputes:
                return False
            self._simulator_disputes[dispute_id] = deepcopy(dispute)
            self._save()
            return True

    def get_simulator_dispute(self, dispute_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._simulator_disputes.get(dispute_id)
            return deepcopy(record) if record else None

    def update_simulator_dispute(self, dispute_id: str, **updates: Any) -> None:
        with self._lock:
            self._simulator_disputes[dispute_id].update(deepcopy(updates))
            self._save()

    def list_simulator_disputes(self) -> list[dict[str, Any]]:
        with self._lock:
            records = [deepcopy(record) for record in self._simulator_disputes.values()]
        return sorted(records, key=lambda record: record["created_at"], reverse=True)

    def _load(self) -> None:
        if self._path is None or not self._path.is_file():
            return

        with self._path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle, object_hook=_decode_json_value)

        merchants = payload.get("merchants", {})
        payment_connectors = payload.get("payment_connectors", {})
        payment_connector_audit = payload.get("payment_connector_audit", [])
        orders = payload.get("orders", {})
        disputes = payload.get("disputes", {})
        if (
            not isinstance(merchants, dict)
            or not isinstance(payment_connectors, dict)
            or not isinstance(payment_connector_audit, list)
            or not isinstance(orders, dict)
            or not isinstance(disputes, dict)
        ):
            raise ValueError("Store file must contain valid merchant, connector, order, and dispute data.")

        self._merchants = deepcopy(merchants)
        for merchant in self._merchants.values():
            merchant.setdefault("storefront_platform", "unknown")
            if merchant.get("platform_credential_verified") and not any(
                merchant.get(key) for key in _MERCHANT_CREDENTIAL_KEYS
            ):
                merchant["platform_credential_verified"] = False
                merchant["platform_credential_verified_at"] = None
                merchant["platform_credential_verification_reason"] = (
                    "platform_credential_reverification_required"
                )
        self._orders = deepcopy(orders)
        self._payment_connectors = deepcopy(payment_connectors)
        self._payment_connector_audit = deepcopy(payment_connector_audit)
        self._disputes = deepcopy(disputes)
        provider_events = payload.get("provider_events", {})
        simulator_disputes = payload.get("simulator_disputes", {})
        if not isinstance(provider_events, dict) or not isinstance(simulator_disputes, dict):
            raise ValueError("Store provider event maps must be objects.")
        self._provider_events = deepcopy(provider_events)
        for event_id, event in self._provider_events.items():
            event.setdefault("event_id", event_id)
            event.setdefault("provider_event_id", event_id)
            processing_state = event.get("processing_state") or event.get(
                "processing_status", "received"
            )
            event["processing_state"] = processing_state
            event["processing_status"] = processing_state
            event.setdefault("attempt_count", 0)
            event.setdefault("last_attempt_at", event.get("received_at"))
            if processing_state in NON_TERMINAL_PROVIDER_EVENT_STATES:
                event["processed_at"] = None
            elif event.get("processed_at") is None:
                event["processed_at"] = event.get("received_at") or datetime.now(
                    timezone.utc
                )
        self._simulator_disputes = deepcopy(simulator_disputes)

    def _save(self) -> None:
        if self._path is None:
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        merchants = deepcopy(self._merchants)
        for merchant in merchants.values():
            for key in _MERCHANT_CREDENTIAL_KEYS:
                merchant.pop(key, None)
        payload = {
            "merchants": merchants,
            "payment_connectors": self._payment_connectors,
            "payment_connector_audit": self._payment_connector_audit,
            "orders": self._orders,
            "disputes": self._disputes,
            "provider_events": self._provider_events,
            "simulator_disputes": self._simulator_disputes,
        }
        temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(_encode_json_value(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
        temp_path.replace(self._path)


def _encode_json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return {
            "__chargeguard_type__": "datetime",
            "value": value.isoformat(),
        }
    if isinstance(value, dict):
        return {key: _encode_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_encode_json_value(item) for item in value]
    return value


def _decode_json_value(value: dict[str, Any]) -> Any:
    if value.get("__chargeguard_type__") == "datetime":
        return datetime.fromisoformat(str(value["value"]))
    return value


store = InMemoryStore.from_env()
