import json
import logging
import os
from pathlib import Path
from typing import Literal, TypedDict


logger = logging.getLogger(__name__)
DEFAULT_MAPPING_PATH = Path(__file__).with_name("razorpay_reason_mappings.json")


class VerifiedReasonMapping(TypedDict):
    provider: Literal["razorpay"]
    network: Literal["VISA", "MASTERCARD", "RUPAY", "AMEX"]
    provider_reason_code: str
    network_reason_code: str
    version: str
    source: str


def resolve_reason_mapping(
    *,
    network: str | None,
    provider_reason_code: str,
) -> VerifiedReasonMapping | None:
    if not network or not provider_reason_code:
        return None
    configured = os.getenv("RAZORPAY_REASON_MAPPING_PATH")
    path = Path(configured) if configured else DEFAULT_MAPPING_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        version = str(payload["version"]).strip()
        mappings = payload["mappings"]
        if not version or not isinstance(mappings, list):
            raise ValueError
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        logger.error("Razorpay reason mapping configuration is unavailable or invalid")
        return None

    matches = []
    for item in mappings:
        if not isinstance(item, dict):
            continue
        if (
            item.get("provider") == "razorpay"
            and item.get("network") == network
            and item.get("provider_reason_code") == provider_reason_code
            and isinstance(item.get("network_reason_code"), str)
            and item["network_reason_code"].strip()
            and isinstance(item.get("source"), str)
            and item["source"].strip()
        ):
            matches.append(item)
    if len(matches) != 1:
        if len(matches) > 1:
            logger.error("Duplicate verified Razorpay reason mappings were ignored")
        return None
    match = matches[0]
    return {
        "provider": "razorpay",
        "network": network,
        "provider_reason_code": provider_reason_code,
        "network_reason_code": match["network_reason_code"].strip(),
        "version": version,
        "source": match["source"].strip(),
    }
