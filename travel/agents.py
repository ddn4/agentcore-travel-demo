from datetime import timedelta

from strands import tool
from temporalio.common import RetryPolicy
from temporalio.contrib.strands import TemporalAgent
from temporalio.contrib.strands.workflow import activity_as_tool

from travel import activities as act

# Every model call is an `invoke_model` activity; every activity tool is its own activity.
LLM = dict(
    start_to_close_timeout=timedelta(seconds=180),  # longer than the Bedrock read timeout (120 s)
    retry_policy=RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=2), backoff_coefficient=2.0),
    callback_handler=None,
)
SEARCH = dict(start_to_close_timeout=timedelta(seconds=60), retry_policy=RetryPolicy(maximum_attempts=3))

POLICY = {"max_hotel_nightly": 300, "max_activity": 150, "no_red_eyes": True}  # warn, never block

CONCIERGE_PROMPT = """You are a travel concierge. Today is {today}. Any destination is possible.
Goal: turn the request into ONE complete trip proposal.
1. Extract origin, destination, dates (resolve "March" to the next March after today; a 4-day trip is
   3 nights, end_date = start_date + 3 days), travelers (default 1, max 2), optional budget (USD), interests.
   You do not know where the traveler flies from: if they have not said, ask for their departure city.
   Ask ONE short clarifying question only if the origin, destination or dates are missing. Never ask for a budget.
   Never ask for names, birth dates or contact details: the app uses fixed test traveller profiles.
2. Call flight_agent and hotel_agent (in parallel is fine), then itinerary_agent. Pass city names or IATA codes.
   Call each specialist at most once per step and pass ALL requirements in one input string.
3. Call budget_policy_agent with every price, the hotel nightly rate, whether the flight is a red-eye, and the
   budget if one was given. If it reports over_budget or warnings, ask the relevant specialist ONCE for a
   cheaper or compliant swap. Warnings never block: mention them and continue.
4. Reply with `message` (a short, friendly summary) and `proposal` holding flight_offer_id, hotel_rate_id and
   activity_place_ids EXACTLY as the specialists returned them. Never invent or edit IDs.
   Do not state a final total or a budget verdict: the app prints the provider-priced quote under your message.
   If a hotel has test_fallback=true, say it is a Duffel test-mode placeholder.
Messages starting with "[system: ...]" report that the app could not price your last proposal. Acknowledge
briefly and search again; never reuse an offer_id or rate_id that was rejected.
You cannot book anything yet. Never say a trip is booked."""

FLIGHT_PROMPT = """You find flights. Always call search_flights (origin and destination may be city names or
IATA codes; pass 3-letter codes through unchanged). Return the 1-2 best offers as JSON
[{offer_id, airline, total_amount, currency, outbound, inbound, red_eye}], cheapest reasonable first,
copying offer_id exactly. Prefer red_eye=false."""

HOTEL_PROMPT = """You find hotels. Always call search_hotels. Return the 1-2 best rates as JSON
[{rate_id, hotel, nightly, total_amount, currency, board_type, refundable, test_fallback}], copying rate_id
exactly. Prefer nightly <= 300 and honor neighborhood requests by name."""

ITINERARY_PROMPT = """You plan things to do. Call search_places with the city and 1-3 interest phrases.
Pick at most 1 place per day, respecting the budget if one is given (mock_price is per traveller).
Return JSON {places: [{place_id, name, category, mock_price}], itinerary: ["Day 1: ...", ...]},
copying place_id exactly."""

BUDGET_PROMPT = """You check budgets and travel policy. Always call check_budget, then check_policy.
Return JSON {total, over_budget, remaining, warnings: [...], suggestions: [...]}. Add suggestions only when
over budget or when there are warnings. Never change prices."""


@tool
def check_budget(line_items: list[float], budget_usd: float | None = None) -> dict:
    """Sum line-item prices. If a budget is given, compare against it.

    Args:
        line_items: every price in the trip
        budget_usd: the traveler's budget, if they gave one
    """
    total = round(sum(line_items), 2)
    if budget_usd is None:
        return {"total": total, "budget": None}
    return {"total": total, "remaining": round(budget_usd - total, 2), "over_budget": total > budget_usd}


@tool
def check_policy(hotel_nightly: float, flight_red_eye: bool, activity_prices: list[float]) -> dict:
    """Check the trip against travel policy. Returns warnings (empty means compliant); warnings never block.

    Args:
        hotel_nightly: the hotel's nightly rate
        flight_red_eye: whether any flight leg departs between 22:00 and 06:00
        activity_prices: the price of each planned activity
    """
    w = []
    if hotel_nightly > POLICY["max_hotel_nightly"]:
        w.append(f"hotel nightly {hotel_nightly} > {POLICY['max_hotel_nightly']}")
    if POLICY["no_red_eyes"] and flight_red_eye:
        w.append("red-eye flight")
    w += [f"activity {p} > {POLICY['max_activity']}" for p in activity_prices if p > POLICY["max_activity"]]
    return {"warnings": w, "compliant": not w}


def _specialist(name: str, prompt: str, tools: list) -> TemporalAgent:
    return TemporalAgent(model="specialist", summary=name, name=name, system_prompt=prompt, tools=tools, **LLM)


def build_agents(messages: list, today: str) -> TemporalAgent:
    """The concierge, with four specialist agents as its tools (Strands agents-as-tools)."""
    flight = _specialist("flight_agent", FLIGHT_PROMPT, [activity_as_tool(act.search_flights, **SEARCH)])
    hotel = _specialist("hotel_agent", HOTEL_PROMPT, [activity_as_tool(act.search_hotels, **SEARCH)])
    itinerary = _specialist("itinerary_agent", ITINERARY_PROMPT, [activity_as_tool(act.search_places, **SEARCH)])
    budget = _specialist("budget_policy_agent", BUDGET_PROMPT, [check_budget, check_policy])
    return TemporalAgent(
        model="concierge",
        summary="concierge",
        name="concierge",
        system_prompt=CONCIERGE_PROMPT.format(today=today),
        messages=messages,
        tools=[
            flight.as_tool(name="flight_agent", description="Find round-trip flight offers. Input: origin and "
                           "destination (city names or IATA codes), dates, travelers, preferences."),
            hotel.as_tool(name="hotel_agent", description="Find hotel rates. Input: city, check-in and "
                          "check-out dates, travelers, preferences."),
            itinerary.as_tool(name="itinerary_agent", description="Pick real places to visit and draft a "
                              "day-by-day itinerary. Input: city, dates, travelers, interests, optional budget."),
            budget.as_tool(name="budget_policy_agent", description="Total the prices, compare to the optional "
                           "budget, check travel policy, suggest cheaper swaps."),
        ],
        **LLM,
    )
