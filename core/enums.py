from enum import StrEnum


class CardNetwork(StrEnum):
    VISA = "VISA"
    MASTERCARD = "MASTERCARD"
    RUPAY = "RUPAY"
    AMEX = "AMEX"


class Decision(StrEnum):
    FIGHT = "FIGHT"
    ACCEPT = "ACCEPT"


class Outcome(StrEnum):
    WIN = "WIN"
    LOSS = "LOSS"
    PENDING = "PENDING"


class Vertical(StrEnum):
    ECOMMERCE = "ecommerce"
    FOOD_DELIVERY = "food_delivery"
    QUICK_COMMERCE = "quick_commerce"


class ReasonCode(StrEnum):
    VISA_OTHER_FRAUD_CARD_ABSENT = "10.4"
    VISA_MERCHANDISE_NOT_RECEIVED = "13.1"
    VISA_NOT_AS_DESCRIBED = "13.3"
    MASTERCARD_CARDHOLDER_DISPUTE = "4853"
    RUPAY_UNAUTHORIZED = "UA02"
