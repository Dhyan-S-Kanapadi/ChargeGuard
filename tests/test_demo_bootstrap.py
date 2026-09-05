import pytest

from api.demo_bootstrap import seed_demo_merchant
from api.store import store


@pytest.fixture(autouse=True)
def isolated_demo(monkeypatch):
    store.clear()
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("CHARGEGUARD_DEMO_SEED", "true")
    monkeypatch.setenv("CHARGEGUARD_USE_STUBS", "true")
    monkeypatch.setenv("RAZORPAY_SIMULATOR_ENABLED", "true")
    for provider in ("RAZORPAY", "STRIPE", "SHIPROCKET", "DELHIVERY", "SEON", "GMAIL",
                     "FRESHDESK", "ETHOCA", "VERIFI", "CLAUDE_VISION"):
        monkeypatch.delenv(f"{provider}_USE_STUBS", raising=False)
    yield
    store.clear()


def test_demo_seed_is_opt_in(monkeypatch):
    monkeypatch.delenv("CHARGEGUARD_DEMO_SEED")
    seed_demo_merchant()
    assert store.get_merchant("merchant_reviewer_demo") is None


def test_demo_seed_is_idempotent():
    seed_demo_merchant()
    original = store.get_merchant("merchant_reviewer_demo")
    seed_demo_merchant()
    assert store.get_merchant("merchant_reviewer_demo") == original
    assert original["razorpay_account_id"] == "acc_REVIEWERDEMO"


@pytest.mark.parametrize(("key", "value"), [
    ("ENVIRONMENT", "production"), ("CHARGEGUARD_USE_STUBS", "false"),
    ("RAZORPAY_SIMULATOR_ENABLED", "false"), ("SEON_USE_STUBS", "false"),
])
def test_demo_seed_refuses_live_or_production_environment(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError):
        seed_demo_merchant()
    assert store.get_merchant("merchant_reviewer_demo") is None
