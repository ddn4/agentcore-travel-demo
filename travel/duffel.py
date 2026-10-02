"""Thin Duffel REST client (test mode only). Called from activities, never from workflow code."""

import logging
import os

import httpx
from temporalio.exceptions import ApplicationError

TOKEN = os.environ.get("DUFFEL_ACCESS_TOKEN", "")
TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests install an httpx.MockTransport
_CLIENT: httpx.AsyncClient | None = None
log = logging.getLogger(__name__)


def _client() -> httpx.AsyncClient:  # lazy: one per worker process
    global _CLIENT
    if not TOKEN.startswith("duffel_test_"):
        raise ApplicationError("DUFFEL_ACCESS_TOKEN must be a duffel_test_ token", type="Config", non_retryable=True)
    if _CLIENT is None:
        _CLIENT = httpx.AsyncClient(
            base_url="https://api.duffel.com",
            timeout=130,
            transport=TRANSPORT,
            headers={"Authorization": f"Bearer {TOKEN}", "Duffel-Version": "v2", "Accept": "application/json"},
        )
    return _CLIENT


async def _req(method: str, path: str, **kw) -> dict | list:
    """Unwrap {"data": ...}; map Duffel errors to ApplicationError(type=<Duffel error code>)."""
    r = await _client().request(method, path, **kw)
    if r.status_code < 400:
        return r.json()["data"] if r.content else {}
    err = (r.json().get("errors") or [{}])[0] if "json" in r.headers.get("content-type", "") else {}
    code = err.get("code", f"http_{r.status_code}")
    log.warning("duffel %s %s -> %s %s (x-request-id %s)", method, path, r.status_code, code,
                r.headers.get("x-request-id"))
    raise ApplicationError(f"{code}: {err.get('message', '')}", type=code,
                           non_retryable=400 <= r.status_code < 500 and r.status_code != 429)


async def place(query: str) -> tuple[str, float, float]:
    """City or airport name -> (IATA code, latitude, longitude)."""
    found = await _req("GET", "/places/suggestions", params={"query": query})
    if not found:
        raise ApplicationError(f"no Duffel place matches {query!r}", type="PlaceNotFound", non_retryable=True)
    airports = [p for p in found if p["type"] == "airport"]
    cities = [p for p in found if p["type"] == "city"]
    wants_airport = "airport" in query.lower() or any(query.lower() in a["name"].lower() for a in airports)
    p = (airports if wants_airport and airports else cities or airports or found)[0]
    return p["iata_code"], p["latitude"], p["longitude"]


async def search_flights(origin: str, destination: str, depart: str, back: str, travelers: int, cabin: str) -> list:
    body = {"data": {
        "slices": [{"origin": origin, "destination": destination, "departure_date": depart},
                   {"origin": destination, "destination": origin, "departure_date": back}],
        "passengers": [{"type": "adult"}] * travelers,
        "cabin_class": cabin,
        "max_connections": 1,
    }}
    data = await _req("POST", "/air/offer_requests", params={"return_offers": "true", "supplier_timeout": 15000},
                      json=body)
    return data["offers"]


async def get_offer(offer_id: str) -> dict:
    return await _req("GET", f"/air/offers/{offer_id}")


async def search_stays(lat: float, lng: float, radius_km: float, check_in: str, check_out: str,
                       travelers: int) -> list:
    body = {"data": {
        "location": {"radius": radius_km, "geographic_coordinates": {"latitude": lat, "longitude": lng}},
        "check_in_date": check_in,
        "check_out_date": check_out,
        "rooms": 1,
        "guests": [{"type": "adult"}] * travelers,
    }}
    return (await _req("POST", "/stays/search", json=body))["results"]


async def fetch_rates(search_result_id: str) -> dict:
    return await _req("POST", f"/stays/search_results/{search_result_id}/actions/fetch_all_rates")


async def create_stays_quote(rate_id: str) -> dict:
    return await _req("POST", "/stays/quotes", json={"data": {"rate_id": rate_id}})
