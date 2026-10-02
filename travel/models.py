from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field
from strands.types.content import Messages

Status = Literal["chatting"]


# --- produced by the LLM: provider offer IDs and dates only, never prices ---
class TripProposal(BaseModel):
    destination: str
    start_date: str = Field(description="YYYY-MM-DD; outbound flight date and hotel check-in")
    end_date: str = Field(description="YYYY-MM-DD; return flight date and hotel check-out")
    travelers: int = Field(1, ge=1, le=2)  # two fixed test traveller profiles (TEST_TRAVELLERS)
    budget_usd: float | None = None
    flight_offer_id: str = Field(description="Duffel offer id (off_...) exactly as search_flights returned it")
    hotel_rate_id: str = Field(description="Duffel Stays rate id (rat_...) exactly as search_hotels returned it")
    activity_place_ids: list[str] = Field(
        default_factory=list, description="Google place ids exactly as search_places returned them"
    )
    itinerary: list[str] = Field(default_factory=list, description="one line per day")


class ConciergeReply(BaseModel):  # the concierge's structured output, every turn
    message: str = Field(description="what to say to the user")
    proposal: TripProposal | None = Field(None, description="set ONLY when presenting a complete, bookable trip")


# --- built by the price_quote ACTIVITY from provider responses ---
class LineItem(BaseModel):
    kind: Literal["flight", "hotel", "activity"]
    item_id: str  # the ID the LLM proposed (off_ / rat_ / place id)
    book_id: str  # what booking will take: off_ (flight), quo_ (Stays quote), place id
    label: str
    amount: Decimal
    currency: str
    mock: bool = False  # True for activity_provider prices


class PriceRequest(BaseModel):
    proposal: TripProposal


class PricedTrip(BaseModel):
    line_items: list[LineItem] = Field(default_factory=list)
    totals: dict[str, Decimal] = Field(default_factory=dict)  # per currency; never converted
    expires_at: str | None = None  # the flight offer's expiry
    problems: list[str] = Field(default_factory=list)  # empty = bookable


# --- minted by WORKFLOW code from a PricedTrip ---
class Quote(BaseModel):
    quote_id: str
    proposal: TripProposal
    line_items: list[LineItem]
    totals: dict[str, Decimal]
    travellers: list[str]
    expires_at: str | None
    over_budget: bool | None  # None: no budget, or not every line is in USD


TEST_TRAVELLERS = [  # fixed FICTIONAL test-mode profiles; Duffel flights require full passenger details
    {"title": "ms", "gender": "f", "given_name": "Ada", "family_name": "Lovelace", "born_on": "1985-12-10",
     "email": "ada@example.com", "phone_number": "+442080160508"},
    {"title": "mr", "gender": "m", "given_name": "Alan", "family_name": "Turing", "born_on": "1982-06-23",
     "email": "alan@example.com", "phone_number": "+442080160509"},
]


@dataclass
class ConciergeInput:
    messages: Messages = field(default_factory=list)


class ChatRequest(BaseModel):
    text: str


class ChatResponse(BaseModel):
    message: str
    status: Status
    quote: Quote | None = None


class ConversationState(BaseModel):
    status: Status
    turns: int
    quote: Quote | None = None
