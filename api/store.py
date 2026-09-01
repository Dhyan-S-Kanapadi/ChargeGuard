from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from core.state import ChargebackState, MerchantProfile


def _provider_event_claim_timeout_seconds() -> int:
    try:
        return max(1, int(os.getenv("PROVIDER_EVENT_CLAIM_TIMEOUT_SECONDS", "300")))
    except ValueError:
        return 300


class InMemoryStore:
    """Thread-safe repository with optional JSON persistence for local durability."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = RLock()
        self._path = Path(path) if path else None
        self._merchants: dict[str, MerchantProfile] = {}
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
            self._merchants[merchant_id] = deepcopy(profile)
            self._save()
            return True

    def get_merchant(self, merchant_id: str) -> MerchantProfile | None:
        with self._lock:
            profile = self._merchants.get(merchant_id)
            return deepcopy(profile) if profile else None

    def get_merchant_by_razorpay_account_id(self, account_id: str) -> MerchantProfile | None:
        with self._lock:
            profile = next(
                (
                    merchant
                    for merchant in self._merchants.values()
                    if merchant.get("razorpay_account_id") == account_id
                ),
                None,
            )
            return deepcopy(profile) if profile else None

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
            self._save()
            return True

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
                last_attempt = existing.get("last_attempt_at") or existing.get("received_at")
                stale_processing = (
                    processing_state in {"received", "processing"}
                    and isinstance(last_attempt, datetime)
                    and last_attempt
                    <= now
                    - timedelta(seconds=_provider_event_claim_timeout_seconds())
                )
                if processing_state != "failed" and not stale_processing:
                    return False
                existing["processing_state"] = "received"
                existing["processing_status"] = "received"
                existing["failure_reason"] = None
                existing["error"] = None
                existing["processed_at"] = None
                existing["last_attempt_at"] = now
                existing["attempt_count"] = int(existing.get("attempt_count") or 1) + 1
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
            record.setdefault("last_attempt_at", now)
            record.setdefault("attempt_count", 1)
            record.setdefault("processed_at", None)
            failure_reason = record.get("failure_reason") or record.get("error")
            record["failure_reason"] = failure_reason
            record["error"] = failure_reason
            processing_state = record.get("processing_state") or record.get(
                "processing_status", "received"
            )
            record["processing_state"] = processing_state
            record["processing_status"] = processing_state
            self._provider_events[event_id] = record
            self._save()
            return True

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
            event.update(deepcopy(updates))
            processing_state = updates.get("processing_state")
            if processing_state and processing_state not in {"received", "processing"}:
                event["processed_at"] = datetime.now(timezone.utc)
            self._save()

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
        disputes = payload.get("disputes", {})
        if not isinstance(merchants, dict) or not isinstance(disputes, dict):
            raise ValueError("Store file must contain object maps for merchants and disputes.")

        self._merchants = deepcopy(merchants)
        self._disputes = deepcopy(disputes)
        provider_events = payload.get("provider_events", {})
        simulator_disputes = payload.get("simulator_disputes", {})
        if not isinstance(provider_events, dict) or not isinstance(simulator_disputes, dict):
            raise ValueError("Store provider event maps must be objects.")
        self._provider_events = deepcopy(provider_events)
        self._simulator_disputes = deepcopy(simulator_disputes)

    def _save(self) -> None:
        if self._path is None:
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "merchants": self._merchants,
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
