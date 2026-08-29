from typing import Literal


ShippingStatusCategory = Literal[
    "CONFIRMED_DELIVERED",
    "IN_TRANSIT",
    "LOST",
    "RETURNED",
    "UNKNOWN",
]


def categorize_shipping_status(status: str) -> ShippingStatusCategory:
    normalized = status.strip().upper().replace("-", "_").replace(" ", "_")
    if any(marker in normalized for marker in ("RETURN", "RTO")):
        return "RETURNED"
    if any(marker in normalized for marker in ("LOST", "MISSING")):
        return "LOST"
    if normalized in {"DELIVERED", "DELIVERY_COMPLETED"}:
        return "CONFIRMED_DELIVERED"
    if any(
        marker in normalized
        for marker in (
            "IN_TRANSIT",
            "OUT_FOR_DELIVERY",
            "SHIPPED",
            "PICKED",
            "MANIFEST",
            "PENDING",
        )
    ):
        return "IN_TRANSIT"
    return "UNKNOWN"
