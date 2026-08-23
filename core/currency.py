import logging
import os


logger = logging.getLogger(__name__)

_DEFAULT_FX_RATES: dict[str, float] = {
    "USD": 1.0,
    "INR": 83.0,
}


def _normalize_currency(currency: str) -> str:
    return currency.strip().upper()


def fx_rates() -> dict[str, float]:
    """Return currency units per USD, optionally overridden by env config."""
    rates = dict(_DEFAULT_FX_RATES)
    raw_rates = os.getenv("RESPONSE_COST_FX_RATES")
    if raw_rates is None:
        return rates

    for raw_entry in raw_rates.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue

        try:
            raw_currency, raw_rate = entry.split(":", 1)
            currency = _normalize_currency(raw_currency)
            rate = float(raw_rate)
        except ValueError:
            logger.warning("Invalid RESPONSE_COST_FX_RATES entry %r, ignoring it", entry)
            continue

        if not currency or rate <= 0:
            logger.warning("Invalid RESPONSE_COST_FX_RATES entry %r, ignoring it", entry)
            continue

        rates[currency] = rate

    return rates


def convert_currency(amount: float, source_currency: str, target_currency: str) -> float:
    """Convert amount using static env-configured rates.

    Rates are expressed as currency units per USD. If either currency is not
    configured, return the original amount so callers can fail open.
    """
    source = _normalize_currency(source_currency)
    target = _normalize_currency(target_currency)
    if source == target:
        return amount

    rates = fx_rates()
    source_rate = rates.get(source)
    target_rate = rates.get(target)
    if source_rate is None or target_rate is None:
        missing = source if source_rate is None else target
        logger.warning(
            "Missing FX rate for %s in RESPONSE_COST_FX_RATES; treating %.2f %s as %.2f %s",
            missing,
            amount,
            source,
            amount,
            target,
        )
        return amount

    return (amount / source_rate) * target_rate
