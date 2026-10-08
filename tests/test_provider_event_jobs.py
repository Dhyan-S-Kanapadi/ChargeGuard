from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from api.local_store import InMemoryStore


def _event(event_id: str = "evt_job") -> dict:
    return {
        "event_id": event_id,
        "provider": "razorpay",
        "event_type": "payment.dispute.created",
        "processing_state": "received",
    }


def test_event_and_one_job_survive_restart(tmp_path) -> None:
    path = tmp_path / "store.json"
    store = InMemoryStore(path)

    assert store.claim_and_enqueue_provider_event(_event())
    assert not store.claim_and_enqueue_provider_event(_event())

    reopened = InMemoryStore(path)
    assert reopened.get_provider_event("evt_job")["processing_state"] == "queued"
    jobs = reopened.list_provider_event_jobs()
    assert len(jobs) == 1
    assert jobs[0]["event_id"] == "evt_job"
    assert jobs[0]["job_state"] == "queued"


def test_only_one_worker_claims_a_job() -> None:
    store = InMemoryStore()
    assert store.claim_and_enqueue_provider_event(_event())

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: store.claim_next_provider_event_job(provider="razorpay"), range(8)))

    claimed = [job for job in claims if job]
    assert len(claimed) == 1
    assert claimed[0]["attempt_count"] == 1


def test_expired_lease_is_recovered_and_dead_letters_after_bounded_retries(monkeypatch) -> None:
    monkeypatch.setenv("PROVIDER_EVENT_MAX_ATTEMPTS", "2")
    store = InMemoryStore()
    assert store.claim_and_enqueue_provider_event(_event())
    first = store.claim_next_provider_event_job(provider="razorpay")
    assert first is not None

    store._provider_event_jobs["evt_job"]["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    second = store.claim_next_provider_event_job(provider="razorpay")
    assert second is not None
    assert second["lease_token"] != first["lease_token"]
    assert second["attempt_count"] == 2
    assert store.retry_provider_event_job(
        "evt_job", lease_token=second["lease_token"], failure_reason="safe failure"
    ) == "dead_letter"
    assert store.list_provider_event_jobs()[0]["job_state"] == "dead_letter"
    assert store.get_provider_event("evt_job")["processing_state"] == "failed"
    assert not store.claim_and_enqueue_provider_event(_event())
