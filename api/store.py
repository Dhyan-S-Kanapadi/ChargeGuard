from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from core.state import ChargebackState, MerchantProfile


class InMemoryStore:
    """Thread-safe phase-one repository, replaceable by Neo4j later."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._merchants: dict[str, MerchantProfile] = {}
        self._disputes: dict[str, dict[str, Any]] = {}

    def clear(self) -> None:
        with self._lock:
            self._merchants.clear()
            self._disputes.clear()

    def create_merchant(self, profile: MerchantProfile) -> bool:
        merchant_id = profile["merchant_id"]
        with self._lock:
            if merchant_id in self._merchants:
                return False
            self._merchants[merchant_id] = deepcopy(profile)
            return True

    def get_merchant(self, merchant_id: str) -> MerchantProfile | None:
        with self._lock:
            profile = self._merchants.get(merchant_id)
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

    def get_dispute(self, chargeback_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._disputes.get(chargeback_id)
            return deepcopy(record) if record else None

    def list_disputes(self) -> list[dict[str, Any]]:
        with self._lock:
            records = [deepcopy(record) for record in self._disputes.values()]
        return sorted(records, key=lambda record: record["created_at"], reverse=True)


store = InMemoryStore()
