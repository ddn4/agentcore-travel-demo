"""MOCK activity provider. The places are real (Google Places); these PRICES ARE NOT."""

from decimal import Decimal

RATE_CARD = {  # per traveller, in the trip's currency, keyed by Google primaryType. MOCK.
    "museum": Decimal("18"), "art_gallery": Decimal("15"), "tourist_attraction": Decimal("20"),
    "historical_landmark": Decimal("12"), "church": Decimal("5"), "park": Decimal("0"),
    "restaurant": Decimal("55"), "tour_agency": Decimal("75"), "amusement_park": Decimal("45"),
    "night_club": Decimal("30"), "spa": Decimal("90"),
}
DEFAULT = Decimal("25")


def quote(place_id: str, category: str | None, travelers: int) -> dict:
    return {"offer_id": place_id, "amount": RATE_CARD.get(category or "", DEFAULT) * travelers, "mock": True}
