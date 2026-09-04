import pytest

from scripts import demo


def _dispute(decision: str, degraded_reasons: list[str] | None = None) -> dict:
    return {
        "state": {
            "decision": decision,
            "degraded_reasons": degraded_reasons or [],
        }
    }


def test_demo_payload_uses_run_scoped_identifiers() -> None:
    payload = demo._webhook_payload("cb_demo_test", 100.0)

    assert payload["merchant_id"] == demo.MERCHANT_ID
    assert demo.RUN_ID in demo.MERCHANT_ID
    assert payload["order_id"] == "order_cb_demo_test"
    assert payload["simulate_evidence_degraded"] is False


def test_demo_rejects_an_unexpected_decision() -> None:
    with pytest.raises(RuntimeError, match="Expected FIGHT, received ACCEPT"):
        demo._require_decision(_dispute("ACCEPT"), "FIGHT")


def test_demo_requires_healthy_stub_target(monkeypatch) -> None:
    monkeypatch.setattr(demo, "API_KEY", "test-key")
    monkeypatch.setattr(
        demo,
        "_request",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "model_loaded": True,
            "stub_mode": False,
        },
    )

    with pytest.raises(RuntimeError, match="CHARGEGUARD_USE_STUBS=true"):
        demo._require_ready_server()
