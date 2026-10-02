"""Real activity code against the fake Duffel/Google transport (no network, no workflow)."""

from decimal import Decimal

import httpx
import pytest
from temporalio.testing import ActivityEnvironment

from tests.fake_duffel import END, START, FakeDuffel
from travel import activities as act
from travel import duffel
from travel.models import PriceRequest, TripProposal

PROPOSAL = TripProposal(destination="Lisbon", start_date=START, end_date=END, budget_usd=3000,
                        flight_offer_id="off_fake_zz", hotel_rate_id="rat_fake_1",
                        activity_place_ids=["ChIJmuseum", "ChIJfado"])


@pytest.fixture
def fake(monkeypatch) -> FakeDuffel:
    fake = FakeDuffel()
    monkeypatch.setattr(duffel, "TRANSPORT", httpx.MockTransport(fake.handle))
    monkeypatch.setattr(duffel, "_CLIENT", None)
    monkeypatch.setattr(duffel, "TOKEN", "duffel_test_fake")
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "fake")
    return fake


async def test_price_quote_problems(fake: FakeDuffel):
    env = ActivityEnvironment()

    good = await env.run(act.price_quote, PriceRequest(proposal=PROPOSAL))
    assert good.problems == []
    assert good.totals == {"USD": Decimal("950.30")}  # 412.30 + 465.00 + mock 18 + mock 55
    assert [i.book_id for i in good.line_items] == ["off_fake_zz", "quo_fake_1", "ChIJmuseum", "ChIJfado"]
    assert [i.mock for i in good.line_items] == [False, False, True, True]

    unknown = PROPOSAL.model_copy(update={"activity_place_ids": ["ChIJnope"]})
    bad = await env.run(act.price_quote, PriceRequest(proposal=unknown))  # a problem, not an exception
    assert bad.problems == ["place ChIJnope unavailable (google_404)"]
