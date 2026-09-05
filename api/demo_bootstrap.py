"""Opt-in synthetic merchant for isolated reviewer deployments."""

import os

from api.schemas import MerchantCreate
from api.store import store


def seed_demo_merchant() -> None:
    enabled = {"1", "true", "yes", "on"}
    if os.getenv("CHARGEGUARD_DEMO_SEED", "false").strip().lower() not in enabled:
        return
    if os.getenv("ENVIRONMENT", "development").strip().lower() == "production":
        raise RuntimeError("Demo seeding is unavailable in production")
    if (os.getenv("CHARGEGUARD_USE_STUBS", "false").strip().lower() not in enabled
            or os.getenv("RAZORPAY_SIMULATOR_ENABLED", "false").strip().lower() not in enabled):
        raise RuntimeError("Demo seeding requires evidence stubs and the simulator")
    for provider in ("RAZORPAY", "STRIPE", "SHIPROCKET", "DELHIVERY", "SEON", "GMAIL",
                     "FRESHDESK", "ETHOCA", "VERIFI", "CLAUDE_VISION"):
        override = os.getenv(f"{provider}_USE_STUBS")
        if override is not None and override.strip().lower() not in enabled:
            raise RuntimeError("Demo seeding requires all evidence providers to use stubs")
    merchant_id = "merchant_reviewer_demo"
    if store.get_merchant(merchant_id) is not None:
        return
    merchant = MerchantCreate(
        merchant_id=merchant_id,
        name="Reviewer Demo Merchant",
        vertical="ecommerce",
        payment_provider="razorpay",
        razorpay_account_id="acc_REVIEWERDEMO",
        average_order_value=3799,
    )
    if not store.create_merchant(merchant.model_dump(exclude_none=True)):
        raise RuntimeError("Demo merchant account mapping conflicts with an existing merchant")
