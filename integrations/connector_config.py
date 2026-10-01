import re
from collections.abc import Mapping


CONNECTOR_REF_PATTERN = r"[A-Z][A-Z0-9_]{0,63}"


def connector_env_value(
    values: Mapping[str, str],
    connector_ref: str | None,
    name: str,
) -> str | None:
    """Resolve a connector-scoped value, falling back to the pilot global value."""
    if connector_ref:
        if re.fullmatch(CONNECTOR_REF_PATTERN, connector_ref) is None:
            raise ValueError(
                "Connector reference must contain only uppercase letters, numbers, and underscores."
            )
        scoped = values.get(f"CHARGEGUARD_CONNECTOR_{connector_ref}_{name}")
        if scoped:
            return scoped
    return values.get(name)


def redact_credential_echoes(value, *secrets: str):
    """Prevent a provider echoing authentication material into evidence state."""
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {redact_credential_echoes(k, *secrets): redact_credential_echoes(v, *secrets)
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact_credential_echoes(v, *secrets) for v in value]
    return value
