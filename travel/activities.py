"""Activities: the only code that talks to the outside world.

search_* are exposed to the specialist agents as tools (their docstrings become the tool specs);
price_quote is called by workflow code and is the only source of a quote's amounts.
"""

import asyncio
import os
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from travel import activity_provider, duffel
from travel.models import LineItem, PricedTrip, PriceRequest

TEST_HOTEL = (-24.38, -128.32, 2)  # the documented Duffel Test Hotel: lat, lng, radius km
PLACES_URL = "https://places.googleapis.com/v1"


def _iata(code_or_city: str) -> str | None:
    s = code_or_city.strip()
    return s.upper() if len(s) == 3 and s.isalpha() else None


def _red_eye(offer: dict) -> bool:
    return any(int(seg["departing_at"][11:13]) >= 22 or int(seg["departing_at"][11:13]) < 6
               for sl in offer["slices"] for seg in sl["segments"])


def _leg(sl: dict) -> str:
    first, last = sl["segments"][0], sl["segments"][-1]
    return (f"{first['origin']['iata_code']} {first['departing_at'][:16].replace('T', ' ')} → "
            f"{last['destination']['iata_code']} {last['arriving_at'][:16].replace('T', ' ')}")


@activity.defn
async def search_flights(origin: str, destination: str, depart_date: str, return_date: str,
                         travelers: int = 1, cabin_class: str = "economy") -> list[dict]:
    """Search round-trip flight offers (Duffel test mode).

    Args:
        origin: departure city name or 3-letter IATA code
        destination: destination city name or 3-letter IATA code
        depart_date: YYYY-MM-DD
        return_date: YYYY-MM-DD
        travelers: number of adult travelers (1 or 2)
        cabin_class: economy, premium_economy, business or first

    Returns up to 5 offers, cheapest first: offer_id, airline, total_amount, currency, expires_at,
    outbound, inbound, stops, red_eye. Copy offer_id exactly.
    """
    o = _iata(origin) or (await duffel.place(origin))[0]
    d = _iata(destination) or (await duffel.place(destination))[0]
    offers = await duffel.search_flights(o, d, depart_date, return_date, travelers, cabin_class)
    if any(x["owner"]["iata_code"] == "ZZ" for x in offers):  # Duffel Airways books reliably in test mode
        offers = [x for x in offers if x["owner"]["iata_code"] == "ZZ"]
    offers.sort(key=lambda x: Decimal(x["total_amount"]))
    return [{
        "offer_id": x["id"], "airline": x["owner"]["name"],
        "total_amount": x["total_amount"], "currency": x["total_currency"], "expires_at": x["expires_at"],
        "outbound": _leg(x["slices"][0]), "inbound": _leg(x["slices"][-1]),
        "stops": max(len(sl["segments"]) - 1 for sl in x["slices"]), "red_eye": _red_eye(x),
    } for x in offers[:5]]


def _pick_rate(rates_result: dict, test_fallback: bool) -> tuple[dict, dict]:
    rooms = rates_result["accommodation"]["rooms"]
    pairs = [(room, rate) for room in rooms for rate in room["rates"]]
    if test_fallback:  # the test hotel's room names are scenarios; prefer the one that books
        ok = [p for p in pairs if "successful booking" in p[0].get("name", "").lower()]
        pairs = ok or pairs
    return min(pairs, key=lambda p: Decimal(p[1]["total_amount"]))


@activity.defn
async def search_hotels(city: str, check_in: str, check_out: str, travelers: int = 1) -> list[dict]:
    """Search hotel rates near a city (Duffel Stays test mode).

    Args:
        city: city name or IATA code
        check_in: YYYY-MM-DD
        check_out: YYYY-MM-DD
        travelers: number of adult guests (1 or 2)

    Returns up to 3 rates: rate_id, hotel, nightly, total_amount, currency, board_type, refundable,
    expires_at, test_fallback. Copy rate_id exactly. test_fallback=true means a Duffel test-mode placeholder hotel.
    """
    test_fallback = os.environ.get("DUFFEL_TEST_HOTEL") == "1"
    results = []
    if not test_fallback:
        _, lat, lng = await duffel.place(city)
        results = await duffel.search_stays(lat, lng, 5, check_in, check_out, travelers)
    if not results:  # test mode may have no inventory for real cities
        test_fallback = True
        results = await duffel.search_stays(*TEST_HOTEL, check_in, check_out, travelers)
    results.sort(key=lambda r: Decimal(r["cheapest_rate_total_amount"]))
    rated = await asyncio.gather(*(duffel.fetch_rates(r["id"]) for r in results[:3]))
    nights = (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days
    out = []
    for rr in rated:
        room, rate = _pick_rate(rr, test_fallback)
        total = Decimal(rate["total_amount"])
        out.append({
            "rate_id": rate["id"], "hotel": f"{rr['accommodation']['name']} ({room.get('name', 'room')})",
            "nightly": str(round(total / max(nights, 1), 2)), "total_amount": rate["total_amount"],
            "currency": rate["total_currency"], "board_type": rate.get("board_type"),
            "refundable": bool(rate.get("cancellation_timeline")), "expires_at": rate.get("expires_at"),
            "test_fallback": test_fallback,
        })
    return out


async def _google(method: str, url: str, mask: str, **kw) -> dict:
    key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not key:
        raise ApplicationError("GOOGLE_PLACES_API_KEY is not set", type="Config", non_retryable=True)
    async with httpx.AsyncClient(timeout=20, transport=duffel.TRANSPORT) as c:
        r = await c.request(method, url, headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": mask}, **kw)
    if r.status_code >= 400:
        raise ApplicationError(f"google places {r.status_code}", type=f"google_{r.status_code}",
                               non_retryable=r.status_code < 500 and r.status_code != 429)
    return r.json()


async def google_place(place_id: str) -> dict:
    return await _google("GET", f"{PLACES_URL}/places/{place_id}", "id,displayName,primaryType")


@activity.defn
async def search_places(city: str, interests: list[str]) -> list[dict]:
    """Find real places to visit (Google Places).

    Args:
        city: city name
        interests: 1 to 3 short interest phrases, e.g. ["museums", "food markets"]

    Returns place_id, name, category, address and mock_price (a MOCK per-traveller price from the
    demo's rate card, in the trip's currency). Copy place_id exactly.
    """
    mask = "places.id,places.displayName,places.primaryType,places.types,places.formattedAddress"  # Pro SKU only
    seen: dict[str, dict] = {}
    for interest in interests[:3]:
        found = await _google("POST", f"{PLACES_URL}/places:searchText", mask,
                              json={"textQuery": f"{interest} in {city}", "pageSize": 5})
        for p in found.get("places", []):
            seen.setdefault(p["id"], {
                "place_id": p["id"], "name": p["displayName"]["text"], "category": p.get("primaryType"),
                "address": p.get("formattedAddress"),
                "mock_price": str(activity_provider.quote(p["id"], p.get("primaryType"), 1)["amount"]),
            })
    return list(seen.values())


def _flight_label(o: dict) -> str:
    out, back = o["slices"][0]["segments"], o["slices"][-1]["segments"]
    return (f"{o['owner']['name']} {out[0]['origin']['iata_code']}→{out[-1]['destination']['iata_code']} "
            f"{out[0]['departing_at'][:10]}, return {back[0]['departing_at'][:10]}")


def _hotel_label(h: dict) -> str:
    return f"{h['accommodation']['name']}, {h['check_in_date']} to {h['check_out_date']}"


@activity.defn
async def price_quote(req: PriceRequest) -> PricedTrip:
    """Re-fetch every proposed offer from its provider. Problems are data; transport errors raise (and retry)."""
    p, items, problems, expiry = req.proposal, [], [], None

    def gone(what: str, e: ApplicationError) -> None:
        if not e.non_retryable or e.type == "Config":
            raise e  # 429/5xx: Temporal retries; a bad key fails the turn visibly
        problems.append(f"{what} unavailable ({e.type})")  # any other 4xx: gone, or a bad ID

    try:  # flight: GET /air/offers/{id} is Duffel's pre-booking check
        o = await duffel.get_offer(p.flight_offer_id)
        expiry = o["expires_at"]
        if datetime.fromisoformat(o["expires_at"]) <= datetime.now(timezone.utc):
            problems.append("flight offer expired")
        if len(o["passengers"]) != p.travelers:
            problems.append("flight offer is for a different number of travelers")
        dates = (o["slices"][0]["segments"][0]["departing_at"][:10], o["slices"][-1]["segments"][0]["departing_at"][:10])
        if dates != (p.start_date, p.end_date):
            problems.append("flight dates differ from the trip dates")
        items.append(LineItem(kind="flight", item_id=o["id"], book_id=o["id"], label=_flight_label(o),
                              amount=Decimal(o["total_amount"]), currency=o["total_currency"]))
    except ApplicationError as e:
        gone(f"flight offer {p.flight_offer_id}", e)

    try:  # hotel: a fresh Stays quote for the rate
        h = await duffel.create_stays_quote(p.hotel_rate_id)
        if (h["check_in_date"], h["check_out_date"]) != (p.start_date, p.end_date):
            problems.append("hotel dates differ from the trip dates")
        name = h["accommodation"]["name"]
        label = f"{name} (Duffel test-mode placeholder)" if name.startswith("Duffel Test Hotel") else _hotel_label(h)
        items.append(LineItem(kind="hotel", item_id=p.hotel_rate_id, book_id=h["id"], label=label,
                              amount=Decimal(h["total_amount"]), currency=h["total_currency"]))
    except ApplicationError as e:
        gone(f"hotel rate {p.hotel_rate_id}", e)

    currency = items[0].currency if items else "USD"  # mock activities take the flight's currency
    for pid in p.activity_place_ids:  # real place, MOCK price
        try:
            place = await google_place(pid)
        except ApplicationError as e:
            gone(f"place {pid}", e)
            continue
        q = activity_provider.quote(pid, place.get("primaryType"), p.travelers)
        items.append(LineItem(kind="activity", item_id=pid, book_id=pid, mock=True, currency=currency,
                              label=f"{place['displayName']['text']} (mock price)", amount=q["amount"]))

    totals: dict[str, Decimal] = {}
    for i in items:
        totals[i.currency] = totals.get(i.currency, Decimal(0)) + i.amount
    return PricedTrip(line_items=items, totals=totals, expires_at=expiry, problems=problems)


ALL_ACTIVITIES: list = [search_flights, search_hotels, search_places, price_quote]
