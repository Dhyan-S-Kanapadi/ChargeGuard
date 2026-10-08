"""Small PostgreSQL-backed worker for deferred Razorpay provider events."""

import logging
import os
import time
from typing import Any

from api.razorpay_processor import process_razorpay_provider_event
from api.store import store


logger = logging.getLogger(__name__)


def _poll_seconds() -> float:
    try:
        return min(30.0, max(0.1, float(os.getenv("PROVIDER_EVENT_WORKER_POLL_SECONDS", "1"))))
    except ValueError:
        return 1.0


def process_next_razorpay_provider_event_job() -> dict[str, Any] | None:
    """Claim and process one job; the job lease fences completion and retries."""
    job = store.claim_next_provider_event_job(provider="razorpay")
    if job is None:
        return None
    return process_razorpay_provider_event(str(job["event_id"]), job=job)


def recover_persisted_razorpay_jobs(*, limit: int = 100) -> int:
    """Backfill jobs for recoverable records written before this worker started."""
    recovered = 0
    for event in store.list_recoverable_provider_events(provider="razorpay", limit=limit):
        if event.get("processing_state") == "unresolved" and not (
            event.get("account_id")
            and store.get_merchant_by_razorpay_account_id(event["account_id"])
        ):
            continue
        if store.enqueue_provider_event_job(str(event["event_id"])):
            recovered += 1
    return recovered


def run() -> None:
    """Run the dedicated worker only with shared PostgreSQL persistence."""
    if not hasattr(store, "check_ready"):
        raise RuntimeError("The Razorpay worker requires PostgreSQL storage.")
    store.check_ready()
    recovered = recover_persisted_razorpay_jobs()
    if recovered:
        logger.info("Recovered Razorpay provider event jobs", extra={"count": recovered})
    while True:
        result = process_next_razorpay_provider_event_job()
        if result is None:
            time.sleep(_poll_seconds())
        else:
            logger.info(
                "Razorpay provider event job processed",
                extra={"provider": "razorpay", "provider_event_id": result["event_id"], "status": result["status"]},
            )


if __name__ == "__main__":
    run()
