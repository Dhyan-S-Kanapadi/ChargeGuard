"""Fail-closed deployment boundaries; capability flags are not verification."""

import os
from collections.abc import Mapping
from pathlib import Path

from cryptography.fernet import Fernet


class RuntimeConfigurationError(RuntimeError):
    """Safe configuration errors contain setting names, never values."""


EVIDENCE_PROVIDERS = (
    "razorpay", "stripe", "shiprocket", "delhivery", "seon", "gmail",
    "freshdesk", "ethoca", "verifi", "claude_vision", "food_platform",
)
UNSAFE_PRODUCTION_FLAGS = (
    "PUBLIC_DEMO_ENABLED", "CHARGEGUARD_DEMO_SEED", "RAZORPAY_SIMULATOR_ENABLED",
    "CHARGEGUARD_USE_STUBS", "ALLOW_GLOBAL_PAYMENT_CREDENTIAL_FALLBACK",
    "ALLOW_GLOBAL_SEON_CREDENTIAL_FALLBACK",
)


def runtime_environment(env: Mapping[str, str] | None = None) -> str:
    values = os.environ if env is None else env
    name = values.get("ENVIRONMENT", "development").strip().lower()
    if name not in {"development", "test", "demo", "staging", "production"}:
        raise RuntimeConfigurationError("ENVIRONMENT must name a supported environment.")
    return name


def _flag(values: Mapping[str, str], name: str, default: bool = False) -> bool:
    value = values.get(name, "").strip().lower()
    if not value:
        return default
    if value not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise RuntimeConfigurationError(f"{name} must be a boolean.")
    return value in {"1", "true", "yes", "on"}


def _live_providers(values: Mapping[str, str]) -> set[str]:
    names = {name.strip().lower() for name in
             values.get("CHARGEGUARD_LIVE_EVIDENCE_PROVIDERS", "").split(",") if name.strip()}
    if names - (set(EVIDENCE_PROVIDERS) - {"food_platform"}):
        raise RuntimeConfigurationError("CHARGEGUARD_LIVE_EVIDENCE_PROVIDERS contains an unsupported provider.")
    return names


def validate_runtime_environment(env: Mapping[str, str] | None = None) -> None:
    values = os.environ if env is None else env
    environment = runtime_environment(values)
    flags = {name: _flag(values, name) for name in UNSAFE_PRODUCTION_FLAGS}
    for provider in EVIDENCE_PROVIDERS:
        name = f"{provider.upper()}_USE_STUBS"
        flags[name] = _flag(values, name, flags["CHARGEGUARD_USE_STUBS"])
    webhook_enabled = _flag(values, "RAZORPAY_WEBHOOK_ENABLED", True)
    _live_providers(values)
    if environment in {"development", "test"}:
        return
    required = ["API_KEY"]
    if webhook_enabled:
        required.append("RAZORPAY_WEBHOOK_SECRET")
    for name in required:
        if not values.get(name, "").strip():
            raise RuntimeConfigurationError(f"{name} is required.")
    if environment != "production":
        return
    for name, enabled in flags.items():
        if enabled:
            raise RuntimeConfigurationError(f"{name} is forbidden in production.")
    secrets = [key.strip() for key in values["API_KEY"].split(",")]
    for name in required:
        entries = secrets if name == "API_KEY" else [values[name]]
        if any(len(value) < 32 for value in entries):
            raise RuntimeConfigurationError(f"{name} requires at least 32 characters per secret.")
    secrets += [values[name] for name in required if name != "API_KEY"]
    internal = values.get("INTERNAL_API_TOKEN", "").strip()
    if internal:
        if len(internal) < 32:
            raise RuntimeConfigurationError("INTERNAL_API_TOKEN requires at least 32 characters.")
        secrets.append(internal)
    if len(secrets) != len(set(secrets)):
        raise RuntimeConfigurationError("API_KEY, webhook and internal secrets must be distinct.")
    try:
        Fernet(values.get("CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY", "").encode())
    except (ValueError, TypeError):
        raise RuntimeConfigurationError("CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key.") from None
    if values.get("CHARGEGUARD_STORE_BACKEND", "postgres") != "postgres":
        raise RuntimeConfigurationError("CHARGEGUARD_STORE_BACKEND must be postgres in production.")
    if not values.get("DATABASE_URL", "").strip():
        raise RuntimeConfigurationError("DATABASE_URL is required in production.")
    if not Path(values.get("CHARGEGUARD_CREDENTIAL_STORE_PATH", "")).is_absolute():
        raise RuntimeConfigurationError("CHARGEGUARD_CREDENTIAL_STORE_PATH requires an absolute, environment-isolated path.")


def provider_modes(env: Mapping[str, str] | None = None) -> dict[str, str]:
    values = os.environ if env is None else env
    production = runtime_environment(values) == "production"
    enabled = _live_providers(values)
    global_stub = _flag(values, "CHARGEGUARD_USE_STUBS")
    return {
        name: ("unsupported" if production and name == "food_platform" else
               "unavailable" if production and name not in enabled else
               "synthetic" if _flag(values, f"{name.upper()}_USE_STUBS", global_stub) else
               "live_enabled")
        for name in EVIDENCE_PROVIDERS
    }


def require_live_provider(provider: str) -> None:
    if runtime_environment() == "production" and provider_modes().get(provider) != "live_enabled":
        raise RuntimeConfigurationError(f"{provider} live integration is unavailable in production.")


def evidence_uses_stubs(provider: str) -> bool:
    stub = _flag(os.environ, f"{provider.upper()}_USE_STUBS",
                 _flag(os.environ, "CHARGEGUARD_USE_STUBS"))
    if runtime_environment() == "production":
        if stub:
            raise RuntimeConfigurationError("Synthetic evidence is forbidden in production.")
        # Live availability is checked by each client, inside its independent
        # failure handler, so one disabled source cannot hide another source.
    return stub


def contains_synthetic_data(value: object) -> bool:
    """Recognize our reserved simulator identifiers and evidence provenance."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"simulate_evidence_degraded", "simulation_scenario_id", "chargeguard_simulator"} and child:
                return True
            if key == "source" and isinstance(child, str) and ("stub" in child.lower() or "simulation" in child.lower()):
                return True
            if contains_synthetic_data(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(contains_synthetic_data(child) for child in value)
    elif isinstance(value, str):
        return (value.startswith(("disp_SIM_", "pay_SIM_", "order_SIM_", "cb_demo_"))
                or value in {"merchant_reviewer_demo", "acc_REVIEWERDEMO", "demo_simulation"})
    return False


def assert_workflow_environment(state: Mapping, *, environment: str | None = None) -> None:
    if (environment or runtime_environment()) != "production":
        return
    if state.get("data_environment") != "production" or contains_synthetic_data(state):
        raise RuntimeConfigurationError("Cross-environment or synthetic workflow state is forbidden in production.")
