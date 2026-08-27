import logging
import os


logger = logging.getLogger(__name__)

RESPONSE_COST_BY_CURRENCY: dict[str, float] = {
    "USD": 15.0,
    "INR": 1200.0,
}


def _float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    try:
        return float(raw_value)
    except ValueError:
        logger.warning("Invalid %s=%r; using %.2f", name, raw_value, default)
        return default


def response_cost_for_currency(currency: str) -> float:
    """Return the configured operational response cost in native currency."""
    normalized_currency = currency.strip().upper()
    configured_cost = RESPONSE_COST_BY_CURRENCY.get(normalized_currency)
    if configured_cost is None:
        fallback = _float_env("RESPONSE_COST_DEFAULT", RESPONSE_COST_BY_CURRENCY["USD"])
        logger.warning(
            "No response cost configured for currency %s; using fallback %.2f in native currency",
            normalized_currency or "<empty>",
            fallback,
        )
        return fallback

    return _float_env(f"RESPONSE_COST_{normalized_currency}", configured_cost)
