from datetime import datetime, timedelta


RUPAY_RESPONSE_WINDOW_WORKING_DAYS = 7


def add_working_days(start: datetime, working_days: int) -> datetime:
    if working_days < 0:
        raise ValueError("working_days must be non-negative")

    deadline = start
    days_added = 0
    while days_added < working_days:
        deadline += timedelta(days=1)
        if deadline.weekday() < 5:
            days_added += 1
    return deadline


def filing_deadline_for_network(
    card_network: str,
    *,
    received_at: datetime,
    provided_deadline: datetime,
) -> datetime:
    """Apply locally defined network windows and preserve provider deadlines otherwise."""
    if card_network.upper() == "RUPAY":
        return add_working_days(received_at, RUPAY_RESPONSE_WINDOW_WORKING_DAYS)
    return provided_deadline
