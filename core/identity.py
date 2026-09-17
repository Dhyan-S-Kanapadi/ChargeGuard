"""Supabase identity configuration, separate from local operator/demo access."""
import os
import re

from core.runtime import RuntimeConfigurationError, runtime_environment


def auth_mode(values=None):
    values = os.environ if values is None else values
    production = runtime_environment(values) == "production"
    mode = values.get("CHARGEGUARD_AUTH_MODE", "supabase" if production else "operator").strip()
    if mode not in {"operator", "supabase"} or (production and mode != "supabase"):
        raise RuntimeConfigurationError("CHARGEGUARD_AUTH_MODE must be supabase in production.")
    return mode


def supabase_url(values=None):
    values = os.environ if values is None else values
    url = values.get("SUPABASE_URL", "").rstrip("/")
    # No caller-selected issuer, arbitrary JWKS host, redirect or embedded credentials.
    if not re.fullmatch(r"https://[a-z0-9-]+\.supabase\.co", url):
        raise RuntimeConfigurationError("SUPABASE_URL must be a hosted Supabase HTTPS project URL.")
    return url


def validate_identity_configuration(values=None):
    values = os.environ if values is None else values
    if auth_mode(values) == "supabase":
        supabase_url(values)
        if values.get("CHARGEGUARD_STORE_BACKEND", "postgres" if runtime_environment(values) == "production" else "local") != "postgres":
            raise RuntimeConfigurationError("Supabase authentication requires CHARGEGUARD_STORE_BACKEND=postgres.")
        # Publishable keys are intentionally public. Never send a secret/service-role key to a browser.
        if not values.get("SUPABASE_PUBLISHABLE_KEY", "").startswith("sb_publishable_"):
            raise RuntimeConfigurationError("SUPABASE_PUBLISHABLE_KEY must be a publishable key, not a secret or service-role key.")
