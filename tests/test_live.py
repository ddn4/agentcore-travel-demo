"""Opt-in smoke test of the real providers: uv run pytest -m live -q -s

Needs DUFFEL_ACCESS_TOKEN (duffel_test_...) and GOOGLE_PLACES_API_KEY. Everything runs in Duffel test mode.
"""

import os
from datetime import date, timedelta

import pytest
from temporalio.testing import ActivityEnvironment

from travel import activities as act
from travel.models import PriceRequest, TripProposal

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("DUFFEL_ACCESS_TOKEN", "").startswith("duffel_test_")
        or not os.environ.get("GOOGLE_PLACES_API_KEY"),
        reason="needs DUFFEL_ACCESS_TOKEN=duffel_test_... and GOOGLE_PLACES_API_KEY",
    ),
]


async def test_real_providers(monkeypatch):
    monkeypatch.setenv("DUFFEL_TEST_HOTEL", "1")  # the documented test hotel always has inventory
    env = ActivityEnvironment()
    start = date.today() + timedelta(days=30)
    end = start + timedelta(days=3)

    flights = await env.run(act.search_flights, "JFK", "LIS", start.isoformat(), end.isoformat())
    assert flights, "no flight offers"
    hotels = await env.run(act.search_hotels, "Lisbon", start.isoformat(), end.isoformat())
    assert hotels and hotels[0]["test_fallback"] and hotels[0]["rate_id"]
    places = await env.run(act.search_places, "Lisbon", ["museums"])
    assert places, "no places"

    proposal = TripProposal(destination="Lisbon", start_date=start.isoformat(), end_date=end.isoformat(),
                            flight_offer_id=flights[0]["offer_id"], hotel_rate_id=hotels[0]["rate_id"],
                            activity_place_ids=[places[0]["place_id"]])
    priced = await env.run(act.price_quote, PriceRequest(proposal=proposal))
    assert priced.problems == []
    print(f"\ncurrencies: flight={flights[0]['currency']} hotel={hotels[0]['currency']} totals={priced.totals}")
