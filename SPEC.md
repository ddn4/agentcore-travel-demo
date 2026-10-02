# Travel Concierge on Temporal, Strands and AgentCore: Implementation Spec

**Status:** ready to implement (rev 7: a failed turn rebuilds the agent from workflow state instead of resetting Strands internals; consent survives a failed turn) · **Date:** 2026-10-02

This spec describes a lean demo. A user chats with a travel concierge, which is a Strands orchestrator agent. The concierge treats four specialist Strands agents as tools. The **Temporal Strands plugin** (`temporalio.contrib.strands`) makes every LLM call and every I/O tool call durable. The specialists search **real providers** for any destination: **Duffel Flights and Duffel Stays in test mode** for flights and hotels, and **Google Places API (New)** for things to do. When the user approves a quote with an explicit **human approval Update**, the workflow re-prices the offers and books the trip as a **saga** with compensations. Flight and hotel bookings are real Duffel test-mode orders, and compensation really cancels them. Activities go through a small **mock provider**, and payment is a **mock**. The trip's origin is unknown until the user states it or allows the **permissioned `locate_user` tool**, which estimates the nearest airport from the user's public IP. Permission is asked **once per conversation** and enforced by workflow code, not the LLM (§5.5). The **same worker code** runs on a laptop against `temporal server start-dev` and as a **Temporal Serverless Worker on Amazon Bedrock AgentCore Runtime** against Temporal Cloud. Only environment variables (and the hosting shell file) change between the two.

> **Rule this spec is built on:** LLM agents *propose provider offer IDs*; deterministic code *prices and books*. Every price in a Quote comes from a `price_quote` activity that re-fetches the offers from the providers (or, for activities, from the labelled mock rate card), never from model output. Booking, compensation, approval and payment never run as LLM tool calls. Tool permissions are checked by a hook in workflow code; the LLM can ask for a permissioned tool but cannot run it without the user's recorded consent.

This spec describes the **final state**. §13 describes how to get there as ten annotated git tags (`v0-spec` … `v9-hardening`), each adding one capability with its tests.

---

## 1. Goals and non-goals

### Goals
1. **Chat as the primary interface.** A CLI client talks to one long-lived `ConciergeWorkflow` per conversation. Every user message is an Update-with-Start (`USE_EXISTING`) that returns the agent's reply. Example prompt: "Plan a four-day trip to Lisbon in March under $3K". Lisbon is only an example: any destination works, and the budget is optional.
2. **The official Temporal Strands plugin.** `StrandsPlugin` and `TemporalAgent` from `temporalio.contrib.strands`, plus `activity_as_tool` from `temporalio.contrib.strands.workflow`.
3. **Strands agents-as-tools.** The `concierge` orchestrator gets `flight_agent`, `hotel_agent`, `itinerary_agent` and `budget_policy_agent` through `TemporalAgent(...).as_tool(name=..., description=...)`.
4. **Real provider data, any destination.**
   - Flights: Duffel Flights, test mode.
   - Hotels: Duffel Stays, test mode.
   - Bookings are real test-mode orders and bookings, and compensation really cancels them.
   - Things to do: real places from Google Places API (New).
   - Activities stay in the saga through a small **mock** provider (`travel/activity_provider.py`). Its prices come from a labelled category rate card.
   - Payment is a **mock**.
5. **Deterministic pricing.** Specialists return provider offer IDs. The `price_quote` activity re-fetches those offers to build the Quote. It runs again on approve. An offer that has expired or changed price is never booked: the conversation goes back to chat with a `[system: ...]` note and the concierge re-plans.
6. **Booking saga.** Flight, then hotel, then activities, then payment, with LIFO compensation on any failure.
   - Primary demo: `[fail:activities]`. The activities step fails, so two real Duffel test bookings are rolled back (hotel, then flight).
   - Simpler variant: `[fail:hotel]`.
7. **Human approval before any booking or payment.** An Update with a validator, and a timeout.
8. **A permissioned tool (§5.5).** `locate_user` (an `activity_as_tool`) turns the user's public IP into a city and the nearest airports.
   - It needs the user's permission, asked **once per conversation**. The decision is carried across continue-as-new, and `/permissions` (a `set_permission` Update) can deny it or reset it to "ask again".
   - Enforcement is deterministic: a Strands `BeforeToolCallEvent` hook in workflow code pauses the agent with a Strands **interrupt** until the user answers through a `grant_permission` Update.
   - The IP is the **user's**. The CLI looks it up (`api.ipify.org`) only after the user answers yes, and sends it with the grant. No IP leaves the laptop before consent, and the worker never uses its own IP, which on AgentCore would be an AWS address.
9. **Local first, then AgentCore with the same code.** Develop with `temporal server start-dev`, then deploy the same `travel/` package to AgentCore Runtime with Temporal Cloud Serverless Workers. Switching is env vars only, through `temporalio.envconfig`.
10. **Tests.**
    - Dev-server workflow tests with a scripted mock LLM and **mocked activities** registered under the real activity names (no network) (§10).
    - `ActivityEnvironment` tests run the real `price_quote` and Duffel booking/cancel code against a small `httpx.MockTransport` fake, because idempotency and pricing are the parts that must be right.
    - One opt-in live smoke test (`pytest -m live`) against test keys.
11. **Tagged, incremental history (§13).** Ten annotated tags; each one adds one capability with its tests, all green, so `git diff <prev>..<tag>` tells the story.

### Non-goals (we are NOT building)
- **Live-mode bookings or real money.** Only Duffel test tokens (`duffel_test_…`) are accepted. The client refuses any other token.
- **Real payments.** `charge_payment` and `refund_payment` are mocks that take a fake token.
- **Real activity bookings or prices.** The places are real. Activity prices come from a mock rate card and are labelled "mock" wherever they appear.
- **Currency conversion.** Each line item keeps its provider's currency, and a quote's total is a sum per currency. The optional `budget_usd` is compared only when every line is in USD (§4.2).
- **A web UI or token streaming.** CLI only.
- **Auth or multi-tenant identity.** The approver is a free-text field. Traveller details come from fixed fictional test profiles (`TEST_TRAVELLERS`, §4.2). The concierge never asks for them, and the CLI prints the names on every quote.
- **Provider state of our own.** We keep no database. Idempotency comes from provider lookups (Duffel) and from deterministic references (mocks).
- **Strands session managers, AgentCore Memory, MCP, Code Interpreter, Secrets Manager.** The Duffel and Google keys are env vars, just like the Temporal API key.
- **Child workflows, multiple task queues, Nexus.** One workflow on one task queue. If specialists ever need their own fleets, `TemporalAgent(task_queue=...)` can move their LLM calls. Use child workflows only if a sub-agent must be independently addressable.
- **Real authorize/capture, partial refunds, payment splits.** Also out of scope: multi-room stays, more than 2 travellers, identity documents, and seat or bag extras.
- **Precise location, or a default home airport.** There is no GPS and no hard-coded origin. IP geolocation is city-level at best, so the concierge states the airport it assumed.
- **Payload encryption.** Workflow history is plain text. A client-side PayloadCodec plus a codec server is the production fix (§5.5) and is not built.

---

## 2. Architecture overview

```
 chat.py ──Update-with-Start / Update / Query / Signal──► Temporal (local dev server │ Temporal Cloud)
                                                              │ task queue: travel-concierge
                                         (cloud only) WCI ──► InvokeAgentRuntime (named endpoint per build)
 Worker = travel.worker.build_worker()  in  python -m travel.worker   │  agentcore_worker.py (BedrockAgentCoreApp)
   ConciergeWorkflow
     @update chat ─► concierge TemporalAgent ─► as_tool: flight │ hotel │ itinerary │ budget_policy (TemporalAgents)
                       │                          └► activity_as_tool(search_flights │ search_hotels │ search_places)
                       │                                                         budget_policy └► pure @tool check_*
                       ├► activity_as_tool(locate_user) ◄── PermissionHook (BeforeToolCallEvent, workflow code)
                       │     undecided → interrupt → ChatResponse.permission_request → CLI [y/N] (+ ipify on y)
                       │     @update grant_permission(interrupt_id, decision, client_ip) ─► resume the same tool call
                    ─► ConciergeReply(message, proposal of offer IDs) ─► price_quote activity ─► Quote
     run(): awaiting_approval ─(@update approve)─► price_quote (re-check) ─┬─ changed/expired ─► back to chat
                                                                           └─ SAGA book_flight → book_hotel → book_activities → charge_payment
                                                                                on failure: LIFO cancel_* / refund_payment
```
The full state machine is in §4.3.

### Providers (only activities talk to them)

| Provider | Mode | Used by | Module |
|---|---|---|---|
| Duffel Flights (`/air/*`) | real, **test mode** | `search_flights`, `price_quote`, `book_flight`, `cancel_flight` | `travel/duffel.py` |
| Duffel Places (`/places/suggestions`) | real | geocoding inside `search_flights` and `search_hotels` (city → IATA code and coordinates); `locate_user` (lat/lng → nearby airports) | `travel/duffel.py` |
| ipinfo.io (`/{ip}/json`, Legacy Free API) | real, read-only, optional `IPINFO_TOKEN` | `locate_user` (IP → city, lat/lng) | `travel/activities.py` |
| ipify (`api.ipify.org`) | real, **client side only** | `chat.py`, after the user allows location | `chat.py` |
| Duffel Stays (`/stays/*`) | real, **test mode** (access must be requested, §8.3) | `search_hotels`, `price_quote`, `book_hotel`, `cancel_hotel` | `travel/duffel.py` |
| Google Places API (New) | real, read-only | `search_places`, `price_quote` (name and category of each place) | `travel/activities.py` (one `_google()` helper) |
| Activity provider | **mock** (stateless) | `price_quote`, `book_activities`, `cancel_activities` | `travel/activity_provider.py` |
| Payment | **mock** (stateless) | `charge_payment`, `refund_payment` | `travel/activities.py` |

### What becomes an activity (verified from plugin source and README)
- **Every model call** of every agent is an `invoke_model` activity, registered by the plugin, with `summary` set to the agent name.
- **Every `activity_as_tool` tool** is an activity: `search_flights`, `search_hotels`, `search_places`, `locate_user`.
- **`price_quote`** is an ordinary activity that the workflow calls. It is not an LLM tool. It runs once after each proposal and once more on approve.
- **Every saga step** is an explicit `workflow.execute_activity` call.
- **What runs inside the workflow, deterministically:**
  - the Strands agent loop;
  - the `_AgentAsTool` wrapper;
  - the pure `@tool` functions `check_budget` and `check_policy`;
  - the `PermissionHook` that gates `locate_user`, and the Strands interrupt it raises;
  - building the Quote from `price_quote`'s result (quote ID, budget flag, failure knob).

### Why one workflow (no child workflows)
- Sub-agents are bounded, in-process orchestration, and the user asked for Strands agents-as-tools.
- Temporal guidance says not to split into child workflows just to organize code (https://docs.temporal.io/evaluate/features/child-workflows).
- The saga runs inside `run()`, never inside an update handler. Continue-as-new is gated so it cannot cut off a saga, a turn or a pending approval.

---

## 3. Repo layout and dependencies

```
agentcore-travel-demo/
├── SPEC.md                      # this file
├── README.md                    # runbook: §8.3 commands, §9.3 deploy, §11 demo script, §13 tag walkthrough table
├── .gitignore                   # .venv/, __pycache__/, agentcore/cdk/, agentcore/.cache/
├── pyproject.toml
├── uv.lock                      # committed from v0; every tag is checked with `uv sync --locked`
├── agentcore_worker.py          # AgentCore Runtime entrypoint (hosting shell only)
├── chat.py                      # CLI chat client
├── iam-role-for-temporal-agentcore-invoke.yaml   # copied verbatim from the sample
├── travel/
│   ├── __init__.py
│   ├── models.py                # Pydantic models + ConciergeInput dataclass
│   ├── duffel.py                # thin httpx client: places, flights, stays (~90 lines)
│   ├── activity_provider.py     # MOCK activity provider: rate card, quote/book/cancel (~40 lines)
│   ├── activities.py            # search_*, locate_user, price_quote, book_*/cancel_*, mock charge/refund, Google + ipinfo calls
│   ├── agents.py                # prompts, pure @tools, PermissionHook, build_agents()
│   ├── workflow.py              # ConciergeWorkflow (chat + permission + approval + re-check + saga + CAN)
│   └── worker.py                # bedrock_models(), connect(), build_worker(), local main
├── agentcore/
│   ├── agentcore.json
│   └── aws-targets.json
├── bin/
│   ├── create-runtime.sh        # copied from samples-python/bedrock_agentcore/strands_agent
│   └── mk-invoke-role.sh        # copied from the same sample
└── tests/
    ├── mock_model.py            # scripted strands Model
    ├── fake_duffel.py           # ~40-line httpx.MockTransport fake: offers, orders, cancellations, stays quotes/bookings, place details
    ├── test_workflow.py         # workflow tests (mocked activities) + ActivityEnvironment tests, no network (§10.4)
    ├── test_agentcore_shell.py  # 2 tests of the hosting shell (added at v7)
    ├── test_versioning.py       # 1 upgrade-on-CAN test with two build ids on the dev server (added at v8)
    └── test_live.py             # 1 opt-in smoke test (pytest -m live)
```
That is nine Python source files, with no helper packages and no base classes. The permission hook is ~15 lines in `agents.py`, so it gets no module of its own. We do not use the official Duffel Python SDK: `duffel-api` 0.6.2 was last released in 2023-10, its repo is archived, and it predates the v1 retirement (VERIFIED, PyPI and GitHub). `travel/duffel.py` calls the REST API directly.

### `pyproject.toml`
```toml
[project]
name = "agentcore-travel-demo"
version = "0.1.0"
requires-python = ">=3.12"            # AgentCore runtimeVersion PYTHON_3_12
dependencies = [
  "temporalio[strands-agents,pydantic]>=1.34,<2",  # PyPI 1.34.0; extra brings strands-agents + pydantic
  "strands-agents>=1.53,<2",                       # tested with 1.57.2
  "bedrock-agentcore>=1.22",                       # tested with 1.24.0; BedrockAgentCoreApp
  "httpx>=0.27",                                   # Duffel + Google Places; MockTransport in tests
]

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24"]

[build-system]                         # mirrors the sample; AgentCore CodeZip builds with uv
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["travel"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = ["live: calls Duffel and Google with real test keys"]
addopts = "-m 'not live'"              # `pytest -m live` overrides (the last -m wins)
```
boto3 comes in through `strands-agents`, and pydantic through `temporalio[pydantic]`. httpx is listed explicitly because our own code imports it.

**Tooling:**
- Python 3.12 and `uv`.
- Temporal CLI 1.8.3 or later. It has the `worker deployment` commands, the `--aws-agentcore-*` flags, and a dev server with Worker Deployment support.
- AWS CLI v2.
- Node 20+ with `npm i -g @aws/agentcore`.
- AWS CDK bootstrapped in the target account and region.

**Accounts:** see §8.3 for the Duffel test token, the Duffel Stays access request and the Google Places key.

---
## 4. Workflow design

### 4.1 Identity and messages

| Kind | Name | Input → Output | Notes |
|---|---|---|---|
| Workflow | `ConciergeWorkflow` | `ConciergeInput` → `BookingOutcome \| None` | `workflow_id = "concierge-<uuid>"` (conversation id). PINNED via the worker's default versioning behavior; upgrades on continue-as-new (§4.4). |
| Update | `chat` | `ChatRequest` → `ChatResponse` | Every message is Update-with-Start, `USE_EXISTING`, `id=<message uuid>`. |
| Update validator | `chat` | | Rejects: empty text; conversation closed; status `booking`/`confirmed`; an approval decision already recorded; **a permission request pending** (Strands cannot take new text while interrupted). |
| Update | `grant_permission` | `PermissionAnswer` → `ChatResponse` | Answers the pending permission request and **resumes the interrupted turn**, returning that turn's reply. Client uses `id=f"perm-{interrupt_id}"`. |
| Update validator | `grant_permission` | | Rejects: conversation closed; nothing pending; `interrupt_id` not the pending request's (stale or duplicate answer); `granted` without a public `client_ip`; a `client_ip` with `denied`. The handler re-checks the same rules under the lock. |
| Update | `set_permission` | `PermissionSetting` → `dict[str, Decision]` | `/permissions deny` / `reset`: records `denied` or `undecided` (ask again). Never grants and never carries an IP. |
| Update validator | `set_permission` | | Rejects: unknown permission; conversation closed; a request pending (answer it instead). Re-checked under the lock. |
| Update | `approve` | `ApprovalDecision` → `BookingOutcome` | Human gate. Client uses `id=f"approve-{quote_id}"`. Returns once the re-check (`price_quote`) and the saga, or the cancellation, have finished. |
| Update validator | `approve` | | Rejects unless status is `awaiting_approval`, no turn is open (none running, no permission pending), `quote_id` matches the pending quote, and no decision is recorded. |
| Query | `state` | → `ConversationState` | Status, pending quote, last outcome, turns, permission decisions, pending permission request. Never the IP. |
| Signal | `end_chat` | → None | Close; workflow completes after handlers drain (a running saga finishes first). |

**Why `approve` is an Update:** the validator rejects stale/mismatched approvals without writing history, and the approver gets the booking result synchronously (https://docs.temporal.io/design-patterns/approval, https://docs.temporal.io/develop/python/workflows/message-passing#updates).

### 4.2 Models (`travel/models.py`)
```python
import ipaddress
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, Field
from strands.types.content import Messages

Status = Literal["chatting", "awaiting_approval", "booking", "confirmed", "compensated", "cancelled"]
FailStep = Literal["none", "flight", "hotel", "activities", "payment"]
Decision = Literal["undecided", "granted", "denied"]

# --- tool permissions (§5.5) ---
class PermissionRequest(BaseModel):       # built from the Strands interrupt(s) the PermissionHook raised
    interrupt_id: str                     # identifies the request: the first interrupt's deterministic id,
                                          # v1:before_tool_call:<toolUseId>:<uuid5(name)>
    interrupt_ids: list[str]              # every ip_location interrupt of this pause (one message may call locate_user twice)
    permission: str                       # "ip_location" (the only one)
    prompt: str | None                    # what the CLI asks (names ipify and ipinfo.io); None = consent already
                                          # given, the CLI just sends the IP again without asking

class PermissionAnswer(BaseModel):        # grant_permission payload: answers the pending request
    interrupt_id: str                     # must equal the pending request's interrupt_id
    decision: Literal["granted", "denied"]
    client_ip: str | None = None          # the USER's public IP: required with "granted", rejected otherwise; never kept

class PermissionSetting(BaseModel):       # set_permission payload: /permissions deny | reset, only with nothing pending
    permission: str = "ip_location"       # str, not Literal: the validator rejects unknown names cleanly
    decision: Literal["denied", "undecided"]

def is_public_ip(s: str | None) -> bool:  # stdlib + deterministic: used by the validator and by locate_user
    try: return ipaddress.ip_address(s or "").is_global
    except ValueError: return False

# --- produced by the LLM: provider offer IDs and dates only, never prices ---
class TripProposal(BaseModel):
    destination: str; start_date: str; end_date: str              # hotel check-in / check-out
    travelers: int = Field(1, ge=1, le=2)                         # 2 fixed test traveller profiles (TEST_TRAVELLERS)
    budget_usd: float | None = None                               # optional
    flight_offer_id: str = Field(description="Duffel offer id (off_...) exactly as search_flights returned it")
    hotel_rate_id: str = Field(description="Duffel Stays rate id (rat_...) exactly as search_hotels returned it")
    activity_place_ids: list[str] = Field(default_factory=list, description="Google place ids exactly as search_places returned them")
    itinerary: list[str] = Field(default_factory=list, description="one line per day")

class ConciergeReply(BaseModel):          # structured_output_model for each concierge turn
    message: str = Field(description="what to say to the user")
    proposal: TripProposal | None = Field(None, description="set ONLY when presenting a complete, bookable trip")

# --- built by the price_quote ACTIVITY from provider responses ---
class LineItem(BaseModel):
    kind: Literal["flight", "hotel", "activity"]
    item_id: str                          # the ID the LLM proposed (off_ / rat_ / place id)
    book_id: str                          # what the booking call takes: off_ (flight), quo_ (Stays quote), place id
    label: str; amount: Decimal; currency: str
    mock: bool = False                    # True for activity_provider prices; the CLI prints "(mock price)"

class PriceRequest(BaseModel):
    proposal: TripProposal
    expected: list[LineItem] | None = None   # set on the approve re-check: compare amounts and currencies

class PricedTrip(BaseModel):
    line_items: list[LineItem] = Field(default_factory=list)
    totals: dict[str, Decimal] = Field(default_factory=dict)   # per currency, e.g. {"GBP": ..., "EUR": ...}
    expires_at: str | None = None         # the Duffel flight offer's expires_at (Stays quotes carry none)
    problems: list[str] = Field(default_factory=list)   # expired / unavailable / changed / already used; empty = bookable

# --- minted by WORKFLOW code from a PricedTrip ---
class Quote(BaseModel):
    quote_id: str                         # f"q{seq}"
    proposal: TripProposal
    line_items: list[LineItem]
    totals: dict[str, Decimal]
    travellers: list[str]                 # names from TEST_TRAVELLERS, shown by the CLI
    expires_at: str | None
    over_budget: bool | None              # None when there is no budget or any line is not in USD
    simulate_failure: FailStep = "none"

TEST_TRAVELLERS = [   # fixed FICTIONAL test-mode profiles; Duffel flights require full passenger details
    {"title": "ms", "gender": "f", "given_name": "Ada", "family_name": "Lovelace", "born_on": "1985-12-10",
     "email": "ada@example.com", "phone_number": "+442080160508"},
    {"title": "mr", "gender": "m", "given_name": "Alan", "family_name": "Turing", "born_on": "1982-06-23",
     "email": "alan@example.com", "phone_number": "+442080160509"},
]

@dataclass                                # dataclass + Messages, exactly like samples-python/strands_plugin/continue_as_new
class ConciergeInput:
    messages: Messages = field(default_factory=list)   # carried across continue-as-new
    quote_seq: int = 0
    permissions: dict[str, Decision] = field(default_factory=lambda: {"ip_location": "undecided"})  # carried; ask once
    approval_timeout_s: int = 900
    # no origin / home airport: unknown until the user states it or locate_user runs (§5.5)

class ChatRequest(BaseModel):  text: str
class ChatResponse(BaseModel):
    message: str; status: Status; quote: Quote | None = None
    outcome: "BookingOutcome | None" = None   # set when a booking outcome was reported this turn
    permission_request: PermissionRequest | None = None   # set: the turn is paused; answer with grant_permission

class ApprovalDecision(BaseModel):
    quote_id: str; approved: bool; approver: str = "demo-user"; reason: str = ""

class BookingOutcome(BaseModel):
    quote_id: str; status: Status
    flight_ref: str | None = None; hotel_ref: str | None = None      # Duffel ord_... / bok_...
    activities_ref: str | None = None; payment_ref: str | None = None  # mock ACT-... / PY-...
    failed_step: str | None = None; error: str | None = None         # failed_step "price_quote" = re-check rejected
    compensations: list[str] = Field(default_factory=list)   # execution order; "<name>:FAILED" if it gave up

ChatResponse.model_rebuild()

class ConversationState(BaseModel):
    status: Status; quote: Quote | None; last_outcome: BookingOutcome | None; turns: int
    permissions: dict[str, Decision]; pending_permission: PermissionRequest | None

# saga step payloads
class BookRequest(BaseModel):
    idempotency_key: str; book_ids: list[str]; amount: Decimal; currency: str
    travelers: int = 1; simulate_failure: FailStep = "none"
class CancelRequest(BaseModel):
    idempotency_key: str; book_ids: list[str]
    ref: str | None = None           # None if the forward step never reported
    maybe_booked: bool = True        # v9 (§6.4): False when the forward step failed non-retryably (cannot have booked)
class ChargeRequest(BaseModel):
    idempotency_key: str; amounts: dict[str, Decimal]; payment_token: str = "tok_demo_visa"
    simulate_failure: FailStep = "none"
```

- **Money is `Decimal`.** Duffel returns amounts as strings such as `"90.80"`. Pydantic serializes `Decimal` losslessly. `book_flight` must send back exactly the offer's amount (§6.1).
- **Currency.** Duffel prices flights in the org billing currency (VERIFIED), which may be GBP rather than USD. The Stays currency is UNVERIFIED (§12 Q15). So a trip may mix currencies, and that is allowed:
  - Each `LineItem` keeps its provider's currency; `totals` sums per currency. There is no conversion.
  - Mock activity prices take the flight's currency.
  - `over_budget` is computed only when `totals` has the single key `"USD"`. Otherwise it is `None` and the CLI says the budget could not be compared.
  - The mock `charge_payment` takes the per-currency amounts.
- **No search-result models.** The `search_*` tools return plain dicts (§5.4), which is the verified tool-result path. The only structured LLM output is `ConciergeReply`.

**Serialization:** `StrandsPlugin` installs `pydantic_data_converter` plus `StrandsFailureConverter`. **Do not pass `data_converter=`** anywhere: the plugin only replaces a converter whose payload converter is the default (`_plugin.py`). The pydantic converter handles both the `BaseModel`s and the `ConciergeInput` dataclass.

### 4.3 State machine

```
            turn ends with a valid proposal                 approve(approved=True)
 chatting ─────────────────────────────────► awaiting_approval ──────────────────► booking (saga)
   ▲   ▲                                      │   ▲     │                             │
   │   │ approve(approved=False) / timeout    │   │     │ chat turn (timer paused;    ├─ any step fails ─► compensated ─┐
   │   └──────────── cancelled ◄──────────────┘   └─────┘  new proposal replaces quote)│                                 │
   │                    │ next chat turn                                               └─ all ok ─► confirmed → workflow completes
   └────────────────────┴──────────────────────────────── next chat turn ◄──────────────────────────────────────────────┘
```
- "Valid proposal" means `price_quote` priced it with no problems. A proposal that has problems leaves the status at `chatting` and records a system note.
- `approve(approved=True)` first re-runs `price_quote`. If anything changed or expired, the result is `cancelled` with `failed_step="price_quote"`, and nothing is booked (§7).
- `compensated` and `cancelled` are resting states: the chat stays open ("try a different hotel").
- `confirmed` is terminal, and **a conversation ends there**: `run()` drains handlers and returns the `BookingOutcome`, and the CLI switches to a new conversation id (printed). The new conversation starts with empty history and default permissions, so it asks for location again.
- `end_chat` in any non-booking state completes the workflow.
- **A pending permission request is part of the turn.** The status does not change, but the turn counts as open (`_turn_open()`, §4.5) until `grant_permission` resumes it: `chat` and `approve` are rejected, the approval timer stays paused, and continue-as-new waits.

### 4.4 Continue-as-new and versioning
The worker defaults to `PINNED` (required by Serverless Workers). For a chat entity the docs recommend **"PINNED + upgrade on Continue-as-New"** (https://docs.temporal.io/production-deployment/worker-deployments/worker-versioning#choosing-behavior; https://docs.temporal.io/production-deployment/worker-deployments/worker-versioning/upgrade-on-continue-as-new). By default the pinned version is inherited across CAN (https://docs.temporal.io/worker-versioning#inheritance-semantics), so we opt in explicitly.

CAN happens only when all hold:
- `workflow.info().is_continue_as_new_suggested()` **or** `workflow.info().is_target_worker_deployment_version_changed()` (verified in temporalio 1.34 `_context.py`; experimental / Public Preview);
- status ∈ `{chatting, compensated, cancelled}`, no turn is open (no turn holds the lock **and no permission request is pending**), not closed;
- after `await workflow.wait_condition(workflow.all_handlers_finished)`, the same conditions are **re-checked** (a chat accepted during the drain may have minted a quote).

Then: `workflow.continue_as_new(ConciergeInput(messages=self._concierge.messages, quote_seq=..., permissions=..., approval_timeout_s=...), initial_versioning_behavior=AUTO_UPGRADE if version changed else None)` using `temporalio.workflow.ContinueAsNewVersioningBehavior` (verified: `workflow/_exceptions.py`, `continue_as_new(..., initial_versioning_behavior=...)`).

- Sub-agents are stateless (`preserve_context=False`), so only concierge messages carry over.
- `quote_seq` carries over, so idempotency keys stay unique across runs.
- `permissions` carries over, so the user is asked **once per conversation**, not once per run. The IP does not carry over (it never enters workflow state), and the earlier `locate_user` result is already in `messages` (VERIFIED by experiment, §5.5).
- Because CAN never happens while a request is pending, the agent's in-memory interrupt state is always inactive at the hand-off, so nothing about it needs carrying. (`TemporalAgent` disables Strands snapshots anyway.)
- The turn that notices a version change still runs on the old build, so its AgentCore endpoint must still exist (§9.4).
- Update-ID dedup is per run; a client retry spanning a CAN could re-process one message (acceptable for the demo).

### 4.5 Workflow skeleton (`travel/workflow.py`)
```python
import asyncio, re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Awaitable, Callable
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from strands.types.exceptions import EventLoopException, StructuredOutputException
    from travel.models import (ApprovalDecision, BookingOutcome, BookRequest, CancelRequest, ChargeRequest,
                               ChatRequest, ChatResponse, ConciergeInput, ConciergeReply, ConversationState,
                               Decision, FailStep, PermissionAnswer, PermissionRequest, PermissionSetting,
                               PricedTrip, PriceRequest, Quote, Status, TEST_TRAVELLERS, TripProposal, is_public_ip)
    from travel import activities as act          # activities import httpx; passed through, never run in the sandbox
    from travel.agents import PermissionHook, build_agents

FWD = dict(start_to_close_timeout=timedelta(seconds=150),    # > Duffel's 130 s client-timeout guidance for orders
           heartbeat_timeout=timedelta(seconds=15),           # activities heartbeat every 5 s while waiting on HTTP
           retry_policy=RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=1),
                                    non_retryable_error_types=["FlightUnavailable", "HotelUnavailable",
                                                               "ActivitySoldOut", "CardDeclined"]))
PRICE = dict(start_to_close_timeout=timedelta(seconds=60), retry_policy=RetryPolicy(maximum_attempts=3))
COMP = dict(start_to_close_timeout=timedelta(seconds=200),            # v9: a cancel may wait up to 140 s for an in-flight booking (§6.4)
            heartbeat_timeout=timedelta(seconds=15), schedule_to_close_timeout=timedelta(hours=1),
            retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=30)))
FAIL_TAG = re.compile(r"\[fail:(flight|hotel|activities|payment)\]", re.I)
RESTING = {"chatting", "compensated", "cancelled"}

@workflow.defn
class ConciergeWorkflow:
    @workflow.init
    def __init__(self, inp: ConciergeInput) -> None:
        self._inp = inp
        self._permissions: dict[str, Decision] = {"ip_location": "undecided", **inp.permissions}   # read by PermissionHook
        self._pending: PermissionRequest | None = None   # turn paused by the hook's interrupt
        self._grant_ip: str | None = None             # user's IP, set only while grant_permission resumes a turn
        self._turn_ctx: tuple = ()                    # (messages snapshot, reported outcome, note) of the open turn
        self._concierge = self._build(list(inp.messages))
        self._lock = asyncio.Lock()
        self._status: Status = "chatting"
        self._quote: Quote | None = None              # pending quote
        self._decision: ApprovalDecision | None = None
        self._last_outcome: BookingOutcome | None = None
        self._unreported: BookingOutcome | None = None  # outcome the concierge hasn't been told about
        self._note: str | None = None                 # pricing problem the concierge hasn't been told about
        self._quote_seq = inp.quote_seq
        self._fail_next: FailStep = "none"
        self._turns = 0
        self._done = False

    def _build(self, messages: list):
        """The concierge is a projection of workflow state: built at start, after CAN, and after a failed turn."""
        return build_agents(messages=messages, today=workflow.now().date().isoformat(), hook=PermissionHook(self))

    # ---------------- chat ----------------
    @workflow.update
    async def chat(self, req: ChatRequest) -> ChatResponse:
        async with self._lock:                        # serialize turns
            if self._decision is not None or self._status in ("booking", "confirmed"):
                raise ApplicationError("booking in progress", type="Busy")   # re-check: validators ran at admission
            if self._pending is not None:             # a racing chat paused on a permission while this one queued
                raise ApplicationError("answer the pending permission request first", type="Busy")
            text = req.text
            if m := FAIL_TAG.search(text):            # deterministic demo knob
                self._fail_next = m.group(1).lower()  # type: ignore[assignment]
                text = FAIL_TAG.sub("", text).strip() or "Please continue."
            reported, self._unreported = self._unreported, None
            note, self._note = self._note, None
            sys_notes = ([f"quote {reported.quote_id} {reported.status}; failed_step={reported.failed_step}; "
                          f"error={reported.error}"] if reported else []) + ([note] if note else [])
            if sys_notes:                              # tell the concierge what happened since its last turn
                text = f"[system: {' | '.join(sys_notes)}] {text}"
            self._turn_ctx = (list(self._concierge.messages), reported, note)
            return await self._turn(text)

    async def _turn(self, prompt: str | list) -> ChatResponse:
        """Run (or resume) one concierge turn. Caller holds the lock. prompt is text, or interruptResponses."""
        snapshot, reported, note = self._turn_ctx
        try:
            res = await self._concierge.invoke_async(prompt, structured_output_model=ConciergeReply)
            if res.stop_reason == "interrupt":         # PermissionHook paused the turn before locate_user (§5.5)
                ours = [i for i in res.interrupts if i.name == "ip_location"]
                self._pending = PermissionRequest(interrupt_id=ours[0].id, interrupt_ids=[i.id for i in ours],
                                                  permission="ip_location", prompt=ours[0].reason["prompt"])
                return ChatResponse(message="", status=self._status, quote=self._quote,
                                    permission_request=self._pending)
            self._pending = None
            reply: ConciergeReply = res.structured_output or ConciergeReply(message=str(res).strip())
            priced = None
            if reply.proposal:                         # the LLM proposed IDs; the providers price them
                priced = await workflow.execute_activity(act.price_quote, PriceRequest(proposal=reply.proposal),
                                                         summary="price_quote", **PRICE)
        except (ActivityError, EventLoopException, StructuredOutputException) as e:   # fail only this update
            if isinstance(e, EventLoopException) and not isinstance(e.original_exception, ActivityError):
                raise                                  # a bug, not a model/provider failure: fail the workflow task
            self._concierge = self._build(snapshot)   # rebuild from workflow-owned state (public API only); this
            self._pending = None                      # also discards any half-finished interrupt state
            self._unreported, self._note = reported, note
            raise ApplicationError(f"concierge turn failed: {e}", type="TurnFailed") from e
        self._turns += 1
        message = reply.message
        if priced is not None:
            self._quote = None                         # a new proposal always replaces the old quote
            if priced.problems:
                self._note = "price_quote rejected the proposal: " + "; ".join(priced.problems)
                message += f"\n(The providers could not confirm this trip: {'; '.join(priced.problems)}. Ask me to search again.)"
            else:
                self._quote = self._make_quote(reply.proposal, priced)
        self._status = "awaiting_approval" if self._quote else "chatting"
        return ChatResponse(message=message, status=self._status, quote=self._quote, outcome=reported)

    @chat.validator
    def _validate_chat(self, req: ChatRequest) -> None:
        if self._done: raise ValueError("conversation closed")
        if self._pending is not None: raise ValueError("answer the pending permission request first")
        if self._status in ("booking", "confirmed") or self._decision is not None:
            raise ValueError(f"cannot chat while {self._status}")
        if not req.text.strip(): raise ValueError("empty message")

    def _turn_open(self) -> bool:                      # a turn is running, or paused on a permission request
        return self._lock.locked() or self._pending is not None

    # ---------------- permissions (§5.5) ----------------
    @workflow.update
    async def grant_permission(self, a: PermissionAnswer) -> ChatResponse:
        async with self._lock:
            self._check_answer(a)                      # re-check under the lock: a racing answer may have resumed it
            p = self._pending.permission
            self._permissions[p] = a.decision          # ask once: persists, carried across CAN, and is kept even
            self._grant_ip = a.client_ip               # if the resumed turn fails (consent was given; the turn failed)
            try:                                       # the IP lives for this resumed turn only (None when denied)
                return await self._turn([{"interruptResponse": {"interruptId": i, "response": a.decision}}
                                         for i in self._pending.interrupt_ids])
            finally:
                self._grant_ip = None                  # never kept, never carried

    @grant_permission.validator
    def _validate_grant(self, a: PermissionAnswer) -> None:
        self._check_answer(a)

    def _check_answer(self, a: PermissionAnswer) -> None:      # ApplicationError: safe in validator and handler
        if self._done: raise ApplicationError("conversation closed")
        if self._pending is None or a.interrupt_id != self._pending.interrupt_id:
            raise ApplicationError("no such permission request pending", type="Busy")
        if (a.decision == "granted") != (a.client_ip is not None):
            raise ApplicationError("client_ip goes with granted, and only with granted")
        if a.client_ip is not None and not is_public_ip(a.client_ip):
            raise ApplicationError("client_ip must be a public IP address")

    @workflow.update
    async def set_permission(self, s: PermissionSetting) -> dict[str, Decision]:   # /permissions deny | reset
        async with self._lock:                         # never changes a decision under a running turn
            self._check_setting(s)                     # re-check: a turn may have paused while this queued
            self._permissions[s.permission] = s.decision
            return dict(self._permissions)

    @set_permission.validator
    def _validate_setting(self, s: PermissionSetting) -> None:
        self._check_setting(s)

    def _check_setting(self, s: PermissionSetting) -> None:
        if self._done: raise ApplicationError("conversation closed")
        if s.permission not in self._permissions: raise ApplicationError(f"unknown permission {s.permission}")
        if self._pending is not None:
            raise ApplicationError("answer the pending permission request first", type="Busy")

    def _make_quote(self, p: TripProposal, priced: PricedTrip) -> Quote:
        """Prices come from price_quote (providers + mock rate card). The LLM never supplies amounts."""
        self._quote_seq += 1
        over = (priced.totals["USD"] > Decimal(str(p.budget_usd))
                if p.budget_usd is not None and set(priced.totals) == {"USD"} else None)
        names = [f"{t['given_name']} {t['family_name']}" for t in TEST_TRAVELLERS[:p.travelers]]
        q = Quote(quote_id=f"q{self._quote_seq}", proposal=p, line_items=priced.line_items, totals=priced.totals,
                  travellers=names, expires_at=priced.expires_at, over_budget=over,
                  simulate_failure=self._fail_next)
        self._fail_next = "none"
        return q

    # ---------------- approval ----------------
    @workflow.update
    async def approve(self, d: ApprovalDecision) -> BookingOutcome:
        if self._turn_open() or self._decision is not None:     # handler may start after a turn grabbed the lock
            raise ApplicationError("concierge is updating the proposal; retry", type="Busy")
        self._decision = d                                       # set synchronously, before any await
        if d.approved:
            self._status = "booking"
        await workflow.wait_condition(lambda: self._last_outcome is not None
                                      and self._last_outcome.quote_id == d.quote_id)
        return self._last_outcome  # type: ignore[return-value]

    @approve.validator
    def _validate_approve(self, d: ApprovalDecision) -> None:
        if self._turn_open(): raise ValueError("concierge is updating the proposal (or waiting on a permission); retry")
        if self._status != "awaiting_approval" or self._quote is None: raise ValueError("nothing awaiting approval")
        if d.quote_id != self._quote.quote_id: raise ValueError(f"stale quote {d.quote_id}; current is {self._quote.quote_id}")
        if self._decision is not None: raise ValueError("decision already recorded")

    @workflow.signal
    def end_chat(self) -> None: self._done = True

    @workflow.query
    def state(self) -> ConversationState:
        return ConversationState(status=self._status, quote=self._quote, last_outcome=self._last_outcome,
                                 turns=self._turns, permissions=self._permissions, pending_permission=self._pending)

    # ---------------- main loop ----------------
    @workflow.run
    async def run(self, inp: ConciergeInput) -> BookingOutcome | None:
        while True:
            await workflow.wait_condition(lambda: self._done or self._decision is not None
                                          or (self._quote is not None and not self._turn_open())
                                          or self._should_continue_as_new())
            if self._decision is None and self._quote is not None and not self._done:
                q = self._quote
                wait = timedelta(seconds=inp.approval_timeout_s)
                if q.expires_at:   # never wait past the flight offer's expiry (deterministic: workflow.now() + activity output)
                    wait = min(wait, max(timedelta(0), datetime.fromisoformat(q.expires_at) - workflow.now()))
                try:   # approval timer; paused while a turn is open (exit + re-arm), restarts after each turn
                    await workflow.wait_condition(
                        lambda: self._decision is not None or self._done or self._turn_open(), timeout=wait)
                except asyncio.TimeoutError:
                    expired = wait < timedelta(seconds=inp.approval_timeout_s)
                    self._finish(BookingOutcome(quote_id=q.quote_id, status="cancelled",
                                                error="offer expired" if expired else "approval timed out"))
                    continue
            if self._decision is not None and self._quote is not None:
                q, d = self._quote, self._decision
                if d.approved:
                    outcome = await self._recheck_and_book(q)
                else:
                    outcome = BookingOutcome(quote_id=q.quote_id, status="cancelled",
                                             error=f"rejected by {d.approver}: {d.reason}")
                self._finish(outcome)
                if outcome.status == "confirmed":
                    break
                continue
            if self._done:
                break
            if self._should_continue_as_new():
                await workflow.wait_condition(workflow.all_handlers_finished)
                if not self._should_continue_as_new():   # re-check after drain
                    continue
                upgrade = workflow.info().is_target_worker_deployment_version_changed()
                workflow.continue_as_new(
                    ConciergeInput(messages=self._concierge.messages, quote_seq=self._quote_seq,
                                   permissions=self._permissions, approval_timeout_s=inp.approval_timeout_s),
                    initial_versioning_behavior=workflow.ContinueAsNewVersioningBehavior.AUTO_UPGRADE if upgrade else None)
        await workflow.wait_condition(workflow.all_handlers_finished)
        return self._last_outcome

    def _should_continue_as_new(self) -> bool:
        i = workflow.info()
        return ((i.is_continue_as_new_suggested() or i.is_target_worker_deployment_version_changed())
                and self._status in RESTING and self._quote is None and self._decision is None
                and not self._turn_open() and not self._done)       # never CAN with a permission request pending

    def _finish(self, o: BookingOutcome) -> None:
        self._last_outcome = self._unreported = o
        self._status, self._quote, self._decision = o.status, None, None

    # ---------------- re-check, then saga (see §6, §7) ----------------
    async def _recheck_and_book(self, q: Quote) -> BookingOutcome:
        """Re-price right before booking. A changed or expired offer is never booked: back to chat."""
        try:
            priced = await workflow.execute_activity(act.price_quote, PriceRequest(proposal=q.proposal, expected=q.line_items),
                                                     summary="price_quote (re-check)", **PRICE)
            problems = priced.problems
        except ActivityError as e:
            problems = [f"providers unreachable: {e.cause}"]
        if problems:
            return BookingOutcome(quote_id=q.quote_id, status="cancelled", failed_step="price_quote",
                                  error="; ".join(problems))
        return await self._book(q.model_copy(update={"line_items": priced.line_items}))   # fresh Stays quo_ id

    async def _book(self, q: Quote) -> BookingOutcome: ...
```
**Concurrency notes** (https://docs.temporal.io/develop/python/workflows/message-passing#concurrent-handlers):
- Validators run at admission, so `chat`, `grant_permission` and `set_permission` re-check after acquiring the lock (the permission checks are shared functions that raise `ApplicationError`, which rejects in a validator and fails only the Update in a handler), and `approve` re-checks before its first await.
- Exactly one of a racing chat/approve pair wins. The other fails with `Busy`, and the CLI tells the user to retry.
- **Permission pause.** A turn paused on a permission request releases the lock (the Update returns), but `_turn_open()` stays true until `grant_permission` resumes it. `grant_permission` takes the same lock, so a resumed turn is serialized like any other. The resumed `invoke_async` must get interruptResponses, never text (Strands raises `TypeError` otherwise), which is why `chat` is rejected while a request is pending.
- **Ask once.** The decision is recorded before the resume and kept for the conversation. Later `locate_user` calls are decided by the hook from that state with no prompt (§5.5). If the resumed turn fails, the messages are rolled back and the previous decision is restored, so the next `locate_user` asks again.
- **What fails a turn:** `ActivityError` (including a `price_quote` that runs out of retries), Strands `StructuredOutputException`, and Strands `EventLoopException` whose `original_exception` is an `ActivityError`. VERIFIED by experiment: a failure of the turn's first model call arrives as a bare `ActivityError`, but a model call that fails *after a tool result* arrives wrapped in `EventLoopException`. Anything else (sandbox violations, non-determinism) fails the workflow task, so bugs are visible and are retried after a fix.
- While the re-check runs, the status is already `booking`, so `chat` is rejected.

---

## 5. Agent design (`travel/agents.py`)

### 5.1 Model names used by agents
Agents reference two plugin model names: `"concierge"` and `"specialist"`. The factories live in `travel/worker.py` (§8.1). The two names exist only so tests can script the concierge and the specialists separately. Because we pass our own `models=`, every `TemporalAgent` must set `model=` (verified, `_plugin.py`).

### 5.2 Agent wiring (agents-as-tools with the plugin)
```python
from datetime import timedelta
from strands import tool
from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry
from temporalio.common import RetryPolicy
from temporalio.contrib.strands import TemporalAgent
from temporalio.contrib.strands.workflow import activity_as_tool
from travel import activities as act

LLM = dict(start_to_close_timeout=timedelta(seconds=180),   # > boto read_timeout (120 s)
           retry_policy=RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=2), backoff_coefficient=2.0),
           callback_handler=None)
SEARCH = dict(start_to_close_timeout=timedelta(seconds=60),  # Duffel supplier_timeout is 20 s; hotels do 1 + 3 calls
              retry_policy=RetryPolicy(maximum_attempts=3))

POLICY = {"max_hotel_nightly": 300, "max_activity": 150, "no_red_eyes": True}   # in the quote currency; warn, never block

@tool
def check_budget(line_items: list[float], budget_usd: float | None = None) -> dict:
    """Sum line-item prices. If a budget is given, compare against it. Returns total, and remaining/over_budget when budgeted."""
    total = round(sum(line_items), 2)
    if budget_usd is None:
        return {"total": total, "budget": None}
    return {"total": total, "remaining": round(budget_usd - total, 2), "over_budget": total > budget_usd}

@tool
def check_policy(hotel_nightly: float, flight_red_eye: bool, activity_prices: list[float]) -> dict:
    """Check the trip against company travel policy. Returns warnings (empty list means compliant). Warnings never block."""
    w = []
    if hotel_nightly > POLICY["max_hotel_nightly"]: w.append(f"hotel nightly {hotel_nightly} > {POLICY['max_hotel_nightly']}")
    if POLICY["no_red_eyes"] and flight_red_eye: w.append("red-eye flight")
    w += [f"activity {p} > {POLICY['max_activity']}" for p in activity_prices if p > POLICY["max_activity"]]
    return {"warnings": w, "compliant": not w}

def build_agents(messages: list, today: str, hook: HookProvider) -> TemporalAgent:
    flight = TemporalAgent(model="specialist", summary="flight_agent", name="flight_agent",
        system_prompt=FLIGHT_PROMPT, tools=[activity_as_tool(act.search_flights, **SEARCH)], **LLM)
    hotel = TemporalAgent(model="specialist", summary="hotel_agent", name="hotel_agent",
        system_prompt=HOTEL_PROMPT, tools=[activity_as_tool(act.search_hotels, **SEARCH)], **LLM)
    itinerary = TemporalAgent(model="specialist", summary="itinerary_agent", name="itinerary_agent",
        system_prompt=ITINERARY_PROMPT, tools=[activity_as_tool(act.search_places, **SEARCH)], **LLM)
    budget = TemporalAgent(model="specialist", summary="budget_policy_agent", name="budget_policy_agent",
        system_prompt=BUDGET_PROMPT, tools=[check_budget, check_policy], **LLM)
    return TemporalAgent(model="concierge", summary="concierge", name="concierge",
        system_prompt=CONCIERGE_PROMPT.format(today=today),
        messages=messages,
        hooks=[hook],                                       # PermissionHook gates locate_user (§5.5)
        tools=[  # Strands agents-as-tools: each sub-agent's LLM calls are invoke_model activities
            activity_as_tool(act.locate_user, **SEARCH),    # permissioned; owned by the concierge, not a specialist
            flight.as_tool(name="flight_agent", description="Find round-trip flight offers. Input: origin and destination (city names or IATA codes), dates, travelers, preferences."),
            hotel.as_tool(name="hotel_agent", description="Find hotel rates. Input: city, check-in/out dates, travelers, preferences."),
            itinerary.as_tool(name="itinerary_agent", description="Pick real places to visit and draft a day-by-day itinerary. Input: city, dates, travelers, interests, optional budget."),
            budget.as_tool(name="budget_policy_agent", description="Total the line-item prices, compare to the optional budget, check travel policy, suggest cheaper swaps."),
        ], **LLM)
```

**Wiring facts:**
- **Verified by local test** (dev server, fake model, temporalio 1.34.0, strands 1.57.2): orchestrator → `sub.as_tool(...)` → sub-agent `activity_as_tool` tool → structured output, all inside the workflow sandbox.
- **Always `await agent.invoke_async(...)`**, never `agent(...)`, which spawns a thread the sandbox blocks.
- **Every agent needs an explicit `name=`.** The default `"Strands Agents"` fails the tool-name pattern.
- **`preserve_context=False`** (the default): sub-agents are stateless per call.
- **Parallel calls.**
  - The default `ConcurrentToolExecutor` uses `asyncio.create_task`, which is safe on the workflow loop, so flight and hotel can run in parallel.
  - A second concurrent call to the *same* sub-agent returns the tool error "already processing a request" (`_agent_as_tool.py`). The prompt therefore says "call each specialist at most once per step".
- **Sub-agent output** is compact JSON text. Sub-agents have no `structured_output_model`; the concierge gets `ConciergeReply` on each turn.
- **Retries** use `retry_policy=` only. A non-None `retry_strategy=` raises `ValueError`.
- **Prices the LLM sees** come from the tool results, and it can mention them in chat and pass them to `budget_policy_agent` for advice. The **Quote** never uses them: `price_quote` re-fetches every amount.
- **`locate_user` sits on the concierge**, because only the concierge decides the origin. The hook is registered on the agent that owns the tool. (A hook on a specialist also works: the agent-as-tool wrapper passes the interrupt up and the resume back down; VERIFIED by experiment.)

### 5.3 Prompts (sketches)

**CONCIERGE_PROMPT**
```
You are a travel concierge. Today is {today}. You do not know where the traveler lives or flies from.
Any destination is possible. Goal: turn the request into ONE complete, bookable trip proposal.
Process:
1. Extract origin, destination, dates (resolve "March" to the next March after today; a 4-day trip = 3 nights,
   end_date = start_date + 3 days), travelers (default 1, max 2), optional budget (USD), interests.
   Origin: if the user has not named one in this conversation, call locate_user ONCE (no arguments needed).
   The app may ask the user for permission first. If it returns airports, use the main one, and say in your
   message: "Looks like you're near <city>, so I searched from <IATA>; tell me if you fly from elsewhere."
   If locate_user is declined or fails, ask the user for their departure city and stop there.
   If it says the location was already looked up, reuse an earlier locate_user result if there is one; otherwise ask.
   Ask ONE concise clarifying question only if the destination or the dates are missing. Never ask for a budget.
   Never ask for passenger names, birth dates or contact details: the app uses fixed test profiles.
2. Call flight_agent and hotel_agent (in parallel is fine), then itinerary_agent.
   Pass city names or IATA codes; the tools resolve cities to airports and coordinates.
   If the user names an airport or gives a 3-letter code, pass the IATA code unchanged.
   Call each specialist at most once per step; pass ALL requirements in one input string.
3. Call budget_policy_agent with every line-item price, the hotel nightly rate, whether the flight is a red-eye,
   and the budget if one was given. If it reports over_budget or policy warnings, ask the relevant specialist
   ONCE for a cheaper or compliant swap. Warnings never block: mention them and continue.
4. Respond with `message` (short, friendly summary) and `proposal` containing flight_offer_id, hotel_rate_id
   and activity_place_ids EXACTLY as the specialists returned them. Never invent or edit IDs.
   Do not state a final total or whether the trip is over budget: the app prints the verified,
   provider-priced quote (and its budget check) under your message.
   If a hotel result has test_fallback=true, say the hotel is a Duffel test-mode placeholder.
Messages starting with "[system: ...]" report what happened since your last turn (booked, compensated,
rejected, timed out, which step failed, or that price_quote found an offer expired, changed or already used).
Acknowledge briefly. After ANY outcome other than confirmed (compensated, cancelled, timed out, or a
price_quote problem), call flight_agent and hotel_agent again before proposing: offers are single-use and
expire, so never reuse an offer_id or rate_id from an earlier quote. Avoid an item that failed during booking.
Rules: You cannot book or charge anything. Booking happens only after the user approves the quote in the app.
Never say a trip is booked. If the user asks to change something, produce a new full proposal.
```

**FLIGHT_PROMPT**
```
You find flights. Always call search_flights (origin/destination may be city names or IATA codes;
pass 3-letter IATA codes through unchanged).
Return the 1–2 best offers as JSON [{offer_id, airline, total_amount, currency, outbound, inbound, red_eye}],
cheapest reasonable first, copying offer_id exactly. Prefer red_eye=false.
```
**HOTEL_PROMPT:**
- Same shape, with `search_hotels`.
- Return `[{rate_id, hotel, nightly, total_amount, currency, board_type, refundable, test_fallback}]`, copying `rate_id` exactly.
- Prefer nightly ≤ 300 and honor neighborhood requests by name.

**ITINERARY_PROMPT:**
- Call `search_places` with the city and 1–3 interest phrases.
- Pick at most 1 place per day. Respect the budget if one is given, using `mock_price`.
- Return `{places:[{place_id,name,category,mock_price}], itinerary:["Day 1: ...", ...]}`, copying `place_id` exactly.
- Mention the flight and hotel only if they were given in the input.

**BUDGET_PROMPT:**
- Always call `check_budget`, then `check_policy`.
- Return `{total, over_budget, remaining, warnings:[...], suggestions:[...]}`.
- Add suggestions only when over budget or when there are warnings.
- Do not change prices.
- This agent's `over_budget` is advice for picking swaps only. Users see only the deterministic `Quote.over_budget`, which the CLI prints, so the two can never visibly disagree.

### 5.4 Provider activities (`travel/activities.py`, `travel/duffel.py`, `travel/activity_provider.py`)
The three `search_*` activities are exposed to the specialists with `activity_as_tool`, and `locate_user` to the concierge (§5.5). Their docstrings become the tool specs. `price_quote` is called only by workflow code. Every one of them is an `async def` with `@activity.defn` and returns plain dicts or pydantic models.

| Activity | Signature | Provider calls | Returns |
|---|---|---|---|
| `search_flights` | `(origin: str, destination: str, depart_date: str, return_date: str, travelers: int = 1, cabin_class: str = "economy") -> list[dict]` | `GET /places/suggestions?query=` for each non-IATA input; `POST /air/offer_requests?return_offers=true&supplier_timeout=15000` with 2 slices, `max_connections: 1` and `passengers: [{"type":"adult"}] * travelers` (Duffel prefers `age` to avoid passenger-type mismatches; the test profiles are adults, so `type` is kept) | up to 5 `{offer_id, airline, total_amount, currency, expires_at, outbound, inbound, stops, red_eye}`, cheapest first |
| `search_hotels` | `(city: str, check_in: str, check_out: str, travelers: int = 1) -> list[dict]` | `GET /places/suggestions` (coordinates); `POST /stays/search` (radius 5 km, `rooms: 1`, `guests: [{"type":"adult"}] * travelers`); `POST /stays/search_results/{srr}/actions/fetch_all_rates` for the 3 cheapest results, in parallel | up to 3 `{rate_id, hotel, nightly, total_amount, currency, board_type, refundable, expires_at, test_fallback}` |
| `search_places` | `(city: str, interests: list[str]) -> list[dict]` | Google `POST places:searchText` once per interest (max 3), `textQuery=f"{interest} in {city}"`, `pageSize: 5` | deduplicated `{place_id, name, category, address, mock_price}` |
| `locate_user` | `(ip: str = "") -> dict` (the hook always overwrites `ip`, §5.5) | ipinfo `GET /{ip}/json`; Duffel `GET /places/suggestions?lat=&lng=&rad=100000` (widen to 250 km if empty) | `{city, region, country, lat, lng, airports: [{iata, name, city, km}]}`: top 3 by haversine distance, lat/lng rounded to 2 dp, **no IP**. An empty or non-public `ip` raises `GeoUnavailable` before any call |
| `price_quote` | `(req: PriceRequest) -> PricedTrip` | `GET /air/offers/{off}`; `GET /air/orders?offer_id=` (spent check); `POST /stays/quotes {rate_id}`; Google `GET places/{id}` (name and `primaryType`); `activity_provider.quote(...)` | `PricedTrip` (§4.2) |

**Search details**
- **City to IATA code and coordinates:** `duffel.place(query)` calls `GET /places/suggestions?query=<city>` (VERIFIED). It takes the first `type=="airport"` match when the query contains "airport" or matches an airport name; otherwise the first `type=="city"` match, or failing that the first airport. It returns `(iata_code, latitude, longitude)`. City codes such as `NYC` are valid flight origins and destinations (VERIFIED). Inputs that are already three letters (any case) skip the lookup for flights; hotels and places still need coordinates, so they look the code up.
- **Test-mode airline:**
  - If any offer has `owner.iata_code == "ZZ"` (Duffel Airways), keep only ZZ offers. Duffel Airways is the reliable airline for book and cancel in test mode. Other sandboxes "might be used up" (VERIFIED).
  - The ZZ fares and schedules are not realistic (VERIFIED).
  - UNVERIFIED: whether ZZ returns offers for every real airport pair.
- **`red_eye`:** true if any segment's `departing_at` hour is 22:00–05:59. It is computed deterministically in the activity. That `departing_at` is in local time is UNVERIFIED (§12).
- **Hotel fallback:** Stays test mode may return nothing for real cities (UNVERIFIED, §12). If `results` is empty, or the worker env sets `DUFFEL_TEST_HOTEL=1` (used by the live test and as a demo safety switch), `search_hotels` searches the documented **Duffel Test Hotel** instead, at lat −24.38, lng −128.32, radius 2. Each result is marked `test_fallback: true`, and the activity prefers the rate whose room name contains "Successful Booking" (room names are scenario names, VERIFIED; exact strings UNVERIFIED).
- **`nightly`** is `total_amount / nights`, for the policy check only.
- **`mock_price`** comes from `activity_provider.RATE_CARD[category] × travelers`. It is labelled mock in the tool docstring.

**`price_quote` (the only place a Quote's amounts come from)**
```python
@activity.defn
async def price_quote(req: PriceRequest) -> PricedTrip:
    """Re-fetch every proposed offer from its provider. Problems are returned as data; transport errors raise (retry)."""
    p, items, problems, expiry = req.proposal, [], [], None
    def gone(what: str, e: ApplicationError) -> None:
        if not e.non_retryable or e.type == "Config": raise e   # 429/5xx: Temporal retries; bad token: fail the turn visibly
        problems.append(f"{what} unavailable ({e.type})")     # any other 4xx = gone / bad ID
    try:                                                     # flight: GET /air/offers/{id} (Duffel's pre-booking check)
        o = await duffel.get_offer(p.flight_offer_id)
        expiry = o["expires_at"]
        if datetime.fromisoformat(o["expires_at"]) <= datetime.now(timezone.utc): problems.append("flight offer expired")
        if len(o["passengers"]) != p.travelers: problems.append("flight offer is for a different number of travelers")
        dates = (o["slices"][0]["segments"][0]["departing_at"][:10], o["slices"][-1]["segments"][0]["departing_at"][:10])
        if dates != (p.start_date, p.end_date): problems.append("flight dates differ from the trip dates")
        if await duffel.orders_for_offer(o["id"]):           # single-use: any order, even a cancelled one, spends it
            problems.append("flight offer already used; search again")
        items.append(LineItem(kind="flight", item_id=o["id"], book_id=o["id"], label=_flight_label(o),
                              amount=Decimal(o["total_amount"]), currency=o["total_currency"]))
    except ApplicationError as e:
        gone(f"flight offer {p.flight_offer_id}", e)
    try:                                                     # hotel: a fresh Stays quote for the rate
        h = await duffel.create_stays_quote(p.hotel_rate_id)
        if (h["check_in_date"], h["check_out_date"]) != (p.start_date, p.end_date): problems.append("hotel dates differ")
        if len(h.get("guests") or [None] * p.travelers) != p.travelers:   # `guests` on the quote is UNVERIFIED; tolerate absence
            problems.append("hotel rate is for a different number of guests")
        name = h["accommodation"]["name"]
        label = f"{name} (Duffel test-mode placeholder)" if name.startswith("Duffel Test Hotel") else _hotel_label(h)
        items.append(LineItem(kind="hotel", item_id=p.hotel_rate_id, book_id=h["id"], label=label,
                              amount=Decimal(h["total_amount"]), currency=h["total_currency"]))
    except ApplicationError as e:
        gone(f"hotel rate {p.hotel_rate_id}", e)
    currency = items[0].currency if items else "USD"        # mock activities take the flight's currency
    for pid in p.activity_place_ids:                         # real place, MOCK price
        try:
            place = await google_place(pid)                  # displayName + primaryType; 4xx (not 429) is non-retryable
        except ApplicationError as e:
            gone(f"place {pid}", e); continue
        q = activity_provider.quote(pid, place.get("primaryType"), p.travelers)
        items.append(LineItem(kind="activity", item_id=pid, book_id=pid, mock=True, currency=currency,
                              label=f"{place['displayName']['text']} (mock price)", amount=q["amount"]))
    was = {(e.kind, e.item_id): e for e in req.expected or []}
    for i in items:                                          # approve re-check: any change blocks booking
        if (e := was.get((i.kind, i.item_id))) and (e.amount, e.currency) != (i.amount, i.currency):
            problems.append(f"{i.kind} price changed {e.amount} {e.currency} → {i.amount} {i.currency}")
    totals: dict[str, Decimal] = {}
    for i in items: totals[i.currency] = totals.get(i.currency, Decimal(0)) + i.amount
    return PricedTrip(line_items=items, totals=totals, expires_at=expiry, problems=problems)
```
- **Flight re-check** uses `GET /air/offers/{id}`. Duffel documents it as the way to get "the complete, up-to-date information about an offer… you may see changes to the offer (e.g a changed total_amount)" right before booking (VERIFIED). It "does not guarantee that the offer will be available at the time of booking", so `book_flight` can still fail, and that triggers compensation.
- **Hotel re-check** creates a fresh Stays quote (`POST /stays/quotes`), because quotes carry no expiry field (VERIFIED). The rate's `expires_at` governs. The fresh `quo_` id becomes `book_id`, and the workflow books with the re-check's line items. Whether creating a new quote is the intended re-check, rather than a GET, is UNVERIFIED.
- **Mock activities never change price**, so they never trip the re-check.
- **Spent offers.** Duffel allows one order per offer request (VERIFIED), and a re-plan after compensation could otherwise reuse a spent `off_` that `GET /air/offers/{id}` still returns. The `orders_for_offer` check turns that into a chat note instead of a doomed booking.
- **Expiry.** `expires_at` is the flight offer's. Stays quotes have none (VERIFIED), and the rate's `expires_at` is not re-read here; a stale rate shows up as a failed `POST /stays/quotes` instead.
- **Bad IDs are data.** An unknown or mistyped place, offer or rate id is a non-retryable 4xx, which becomes a `problems` entry, so the turn stays in `chatting` (§4.3) instead of failing with `TurnFailed`. Only a `Config` error (wrong token) fails the turn.

**`travel/duffel.py` (thin client, ~90 lines)**
```python
import asyncio, os, logging, httpx
from temporalio.exceptions import ApplicationError

TOKEN = os.environ.get("DUFFEL_ACCESS_TOKEN", "")
TRANSPORT: httpx.AsyncBaseTransport | None = None      # tests install httpx.MockTransport
_CLIENT: httpx.AsyncClient | None = None
POLL_S = 5                                              # v9 (§6.4): in-flight polling interval (tests: 0)
log = logging.getLogger(__name__)

def _client() -> httpx.AsyncClient:                     # lazy: one per worker process
    global _CLIENT
    if not TOKEN.startswith("duffel_test_"):
        raise ApplicationError("DUFFEL_ACCESS_TOKEN must be a duffel_test_ token", type="Config", non_retryable=True)
    if _CLIENT is None:
        _CLIENT = httpx.AsyncClient(base_url="https://api.duffel.com", timeout=130, transport=TRANSPORT,
            headers={"Authorization": f"Bearer {TOKEN}", "Duffel-Version": "v2",
                     "Accept": "application/json", "Accept-Encoding": "gzip"})
    return _CLIENT

async def _req(method: str, path: str, **kw) -> dict:
    """Unwraps {"data": ...}. Maps Duffel errors to ApplicationError(type=<duffel code>)."""
    r = await _client().request(method, path, **kw)
    if r.status_code < 400:
        return r.json()["data"] if r.content else {}
    err = (r.json().get("errors") or [{}])[0] if "json" in r.headers.get("content-type", "") else {}
    code = err.get("code", f"http_{r.status_code}")
    log.warning("duffel %s %s -> %s %s (x-request-id %s)", method, path, r.status_code, code, r.headers.get("x-request-id"))
    raise ApplicationError(f"{code}: {err.get('message', '')}", type=code,
                           non_retryable=400 <= r.status_code < 500 and r.status_code != 429)
```
Helpers, each a few lines over `_req` (all paths VERIFIED unless marked):

| Helper | Call |
|---|---|
| `place(query)` | `GET /places/suggestions?query=` |
| `airports_near(lat, lng, rad_m)` | `GET /places/suggestions?lat=&lng=&rad=` (VERIFIED in the docs: no `query` needed, `rad` in metres). Keeps `type == "airport"` with an `iata_code` and sorts by distance itself: result order, result count and whether cities can appear are UNVERIFIED. Not run live yet (no token in the repo). |
| `search_flights(o, d, out, back, travelers, cabin)` | `POST /air/offer_requests` (returns `data.offers`) |
| `get_offer(id)` | `GET /air/offers/{id}` |
| `orders_for_offer(offer_id)` | `GET /air/orders?offer_id=` → all orders for the offer, cancelled or not |
| `order_for_offer(offer_id)` | first of `orders_for_offer` with `cancelled_at is None`, else `None` |
| `create_order(offer_id, amount, currency, travellers, metadata)` | `order_for_offer` first, then `get_offer` (for the passenger `pas_` ids), then `POST /air/orders`; on a duplicate-type 422, polls `order_for_offer` (§6.1) |
| `cancel_order(order_id)` | `GET /air/orders/{id}`, then `POST /air/order_cancellations`, then `POST /air/order_cancellations/{ore}/actions/confirm` |
| `search_stays(lat, lng, radius_km, check_in, check_out, travelers)` | `POST /stays/search` |
| `fetch_rates(srr_id)` | `POST /stays/search_results/{id}/actions/fetch_all_rates` |
| `create_stays_quote(rate_id)` | `POST /stays/quotes` |
| `booking_for_key(idempotency_key)` | `GET /stays/bookings?limit=200`, following the `after` cursor; returns the first booking with `metadata.idempotency_key == key` and `status != "cancelled"`, else `None`. VERIFIED: the list exists (filters `after`/`before`/`limit`/`user_id` only) and bookings carry `metadata` but no quote id. Filtering is client-side, which is fine for a test account. |
| `create_booking(quote_id, travellers, metadata)` | `booking_for_key` first, then `POST /stays/bookings` with `metadata={"idempotency_key": key}` |
| `get_order(id)` | `GET /air/orders/{id}` |
| `get_booking(id)` / `cancel_booking(id)` | `GET /stays/bookings/{id}`, then `POST /stays/bookings/{id}/actions/cancel` |

- Activities call `duffel.get_offer(...)` and the other helpers through the module attribute, never with `from travel.duffel import ...`. That way tests can swap `TRANSPORT` and reset `_CLIENT`.
- The Google calls in `activities.py` use the same `TRANSPORT` hook and the `X-Goog-Api-Key` header.
  - Text Search sends the field mask `places.id,places.displayName,places.primaryType,places.types,places.formattedAddress`. These are all Text Search **Pro** SKU fields; `places.rating` is deliberately left out because it triggers the **Enterprise** SKU (VERIFIED, Text Search SKU lists, 2026-10-02). A demo stays inside the 5,000 free Pro calls a month.
  - Place Details: `GET https://places.googleapis.com/v1/places/{id}` with field mask `id,displayName,primaryType`, which is the Place Details Pro SKU (VERIFIED).
  - Both go through one `_google(method, url, mask, **kw)` helper that maps errors like `duffel._req`: a 4xx other than 429 raises `ApplicationError(type=f"google_{status}", non_retryable=True)`; 429/5xx stay retryable. The exact status for an unknown place id is UNVERIFIED (any 4xx counts).
  - The Google helper builds an `httpx.AsyncClient(transport=duffel.TRANSPORT)` per call, so there is no cached client bound to an old event loop in tests.

**`travel/activity_provider.py` (MOCK, ~40 lines; same quote/book/cancel shape as the Duffel helpers)**
```python
"""MOCK activity provider. Places are real (Google Places); these PRICES and BOOKINGS ARE NOT."""
import hashlib
from decimal import Decimal

RATE_CARD = {   # per traveller, in the trip's currency, keyed by Google primaryType. MOCK.
    "museum": Decimal("18"), "art_gallery": Decimal("15"), "tourist_attraction": Decimal("20"),
    "historical_landmark": Decimal("12"), "church": Decimal("5"), "park": Decimal("0"),
    "restaurant": Decimal("55"), "tour_agency": Decimal("75"), "amusement_park": Decimal("45"),
    "night_club": Decimal("30"), "spa": Decimal("90"),
}
DEFAULT = Decimal("25")

def quote(place_id: str, category: str | None, travelers: int) -> dict:
    return {"offer_id": place_id, "amount": RATE_CARD.get(category or "", DEFAULT) * travelers, "mock": True}

def _ref(key: str) -> str:
    return "ACT-" + hashlib.sha256(key.encode()).hexdigest()[:8].upper()

async def book(idempotency_key: str, place_ids: list[str]) -> str:
    return _ref(idempotency_key)        # stateless: same key → same ref on any worker or microVM

async def cancel(idempotency_key: str) -> None:
    return None                         # always succeeds, idempotent by construction
```

### 5.5 Permissioned tool: `locate_user`

The rule is that **workflow code enforces the permission and the LLM only asks for the tool**. The model can call `locate_user` whenever it likes. A `BeforeToolCallEvent` hook, which runs in workflow context, decides from workflow state whether the call goes ahead.

**Flow** (state is `_permissions["ip_location"]`: `undecided` | `granted` | `denied`)
1. The concierge calls `locate_user` because it has no origin yet.
2. If the decision is **undecided**, the hook calls `event.interrupt("ip_location", reason={"prompt": PROMPT})`.
   - The agent stops with `stop_reason == "interrupt"`, and `_turn` returns `ChatResponse(message="", permission_request=...)`.
   - No activity runs, and no IP exists anywhere yet.
3. The CLI prints the prompt and reads `[y/N]`.
   - On **y** it fetches the user's public IP from `https://api.ipify.org?format=json`, then sends `grant_permission(PermissionAnswer(interrupt_id=..., decision="granted", client_ip=ip))`.
   - On anything else it sends `decision="denied"` and makes no network call.
4. `grant_permission` records the decision, then resumes the paused tool call(s) with one `interruptResponse` per id in `interrupt_ids`. Strands re-runs the hook with the same `toolUseId`; the decision is no longer `undecided`, so the hook skips `interrupt()` and decides from the recorded state. Strands discards the answered interrupt at the end of the tool cycle.
   - **Granted:** the hook replaces the tool input with the consented IP, and `locate_user` runs. Every `locate_user` call in the resumed turn uses the same IP; it is cleared when the Update returns.
   - **Denied:** the hook sets `event.cancel_tool = DENIED_MSG`. Strands skips the tool and gives the model an error tool result with that text, so the concierge asks for the departure city.
   - The Update returns the rest of the turn (a reply, or a quote).
5. **Ask once.** The decision is kept for the conversation and carried across continue-as-new (§4.4).
   - Denied: later calls are cancelled with no prompt.
   - Granted: the IP is never stored, so a later call pauses again with `prompt=None`. The CLI sends a fresh IP without asking; the user sees no second question.
   - Two `locate_user` calls in one model message raise two interrupts but give one request; one answer resumes both (VERIFIED by experiment).
   - **If the resumed turn fails** (`TurnFailed`, after Temporal's retries are exhausted), the workflow discards the agent and rebuilds it from the turn's message snapshot with the same `build_agents` factory used at start and after continue-as-new. This is the Strands-idiomatic way to restore an agent (construct it from saved state, as session managers do) and uses no private API. The `grant_permission` Update fails with `TurnFailed`; the workflow keeps running, the decision stays `granted`, and the user resends the message.
   - `/permissions deny` and `/permissions reset` send `set_permission`, which records `denied` or `undecided` (ask again). Granting only happens by answering a request, because only then is there a call to run.
   - A confirmed booking ends the conversation (§4.3). The next one starts with empty messages and asks again.

**Hook (`travel/agents.py`)**
```python
PROMPT = ("Allow the concierge to estimate your location from your public IP? Your IP is read from "
          "api.ipify.org and sent once to ipinfo.io to find your city. [y/N]")
DENIED_MSG = "The user declined location access. Ask the user for their departure city instead."

class PermissionHook(HookProvider):
    """Deterministic gate for locate_user. Runs in workflow code and reads only workflow fields."""
    def __init__(self, wf) -> None: self._wf = wf           # ConciergeWorkflow: _permissions, _grant_ip

    def register_hooks(self, registry: HookRegistry, **_) -> None:
        registry.add_callback(BeforeToolCallEvent, self._gate)

    def _gate(self, event: BeforeToolCallEvent) -> None:
        if event.selected_tool is None or event.selected_tool.tool_name != "locate_user":   # the tool that will run
            return
        if self._wf._permissions["ip_location"] == "denied":
            event.cancel_tool = DENIED_MSG; return
        ip = self._wf._grant_ip
        if not ip:   # undecided: ask the user. granted: consent exists but the IP is never stored, so ask the
                     # client for it again without a prompt. Raises; on resume the IP is set or the user denied.
            undecided = self._wf._permissions["ip_location"] == "undecided"
            event.interrupt("ip_location", reason={"prompt": PROMPT if undecided else None})
        event.tool_use = {**event.tool_use, "input": {"ip": ip}}   # COPY: never write the IP into agent.messages
```
- **Assign a copy of `tool_use`; don't mutate it.** VERIFIED by experiment: `event.tool_use["input"] = ...` mutates the assistant message in `agent.messages`. The IP would then be sent to the LLM on every later call and copied into each continue-as-new input. With the copy, the message keeps the model's own (ignored) arguments, and the executor still runs the tool with the replaced input (`_executor.py:256`).
- **Model-supplied arguments are always ignored.** `activity_as_tool` builds the tool spec from the signature, so the model sees an `ip` parameter. The docstring says to leave it empty. Hiding it from the spec would need a custom `@tool` wrapper, which is not needed.
- **The gate keys on `selected_tool`**, the tool that will actually run, not the model's string. Strands' lookup is an exact registry name match with no normalization (VERIFIED, `tools/executors/_executor.py`).

**Activity (`travel/activities.py`)**
```python
@activity.defn
async def locate_user(ip: str = "") -> dict:
    """Estimate the traveler's city and nearest airports. Leave `ip` empty: the app fills it in after the
    user allows it. Returns city, region, country and up to 3 nearby airports (iata, name, km)."""
    if not is_public_ip(ip):            # never the worker's own IP: ipinfo's /json with no IP describes the caller
        raise ApplicationError("no consented client IP", type="GeoUnavailable", non_retryable=True)
    params = {"token": t} if (t := os.environ.get("IPINFO_TOKEN")) else {}
    try:
        async with httpx.AsyncClient(timeout=5, transport=duffel.TRANSPORT) as c:
            r = await c.get(f"https://ipinfo.io/{ip}/json", params=params)
    except httpx.HTTPError as e:        # httpx messages may include the URL (the IP): keep it out of failure history
        raise ApplicationError(f"ipinfo unreachable: {type(e).__name__}") from None
    if r.status_code == 429 or r.status_code >= 500:
        raise ApplicationError(f"ipinfo {r.status_code}")                         # retryable
    d = r.json() if r.status_code < 400 else {}
    if d.get("bogon") or "loc" not in d:                                         # private/reserved IP, bad input
        raise ApplicationError("cannot geolocate this IP", type="GeoUnavailable", non_retryable=True)
    lat, lng = map(float, d["loc"].split(","))
    airports = await duffel.airports_near(lat, lng, 100_000) or await duffel.airports_near(lat, lng, 250_000)
    activity.logger.info("locate_user: %s, %s (%d airports)", d.get("city"), d.get("country"), len(airports))  # never the IP
    return {"city": d.get("city"), "region": d.get("region"), "country": d.get("country"),
            "lat": round(lat, 2), "lng": round(lng, 2), "airports": airports[:3]}
```
- **ipinfo.io Legacy Free API** (VERIFIED live, 2026-10-02): `/{ip}/json` works with no token (1,000/day), or 50,000/month with a free `IPINFO_TOKEN`. It returns `city`, `region`, `country` and `loc: "lat,lng"`. ipinfo **Lite** can't be used, because it has country and ASN only.
  - UNVERIFIED: how long the legacy endpoint keeps running, since ipinfo now points to Lite. The swap is one function: ipwho.is `/{ip}` (1,000/day, commercial use allowed, numeric `latitude`/`longitude`).
  - UNVERIFIED: the `bogon: true` field for private IPs. The `"loc" not in d` check covers it either way.
  - Rejected: ip-api.com (no HTTPS on the free tier, no commercial use) and ipapi.co (returned 429 on the first live call).
- **Failures become tool text.** After its retries, a `locate_user` failure reaches the concierge as an error tool result (the `activity_as_tool` path), and the prompt says to ask for the departure city.
- **Accuracy.** IP geolocation is city or metro level, and a VPN or mobile carrier can place the user elsewhere. That's why it returns the top 3 airports and why the concierge states the airport it assumed.

**Privacy (IP addresses are personal data under GDPR and CCPA)**
- **Consent comes first.** The CLI makes no ipify call until the user answers y. The prompt names both third parties.
- **The IP is recorded in exactly two places:** the `grant_permission` Update argument and the `locate_user` activity input.
  - It is never in workflow state that outlives the Update, `ConciergeInput`, a query, `ChatResponse`, the activity result, `agent.messages`, failure messages or the logs.
  - VERIFIED by experiment: the first run's decoded history holds the IP exactly twice, and the run after continue-as-new holds none.
  - The validator rejects a `client_ip` on a denial, so no IP enters history without a recorded grant (rejected Updates write nothing).
  - The activity result keeps only derived data: city, region, country, rounded lat/lng and candidate airports.
- **History is still plain text.** Anyone with namespace read access can see those two payloads for the retention period. The production fix is a client-side **PayloadCodec** (encryption data converter) plus a codec server for the UI. It's out of scope (non-goals).
- **AgentCore:** the worker only receives the IP the client sent, and `locate_user` itself refuses an empty or non-public IP, so no refactor can make it geolocate its own (AWS) address.

**Verified by experiment** (temporalio 1.34.0, strands-agents 1.57.2, dev server, scripted fake model, `max_cached_workflows=0`, `Replayer` on every history): hook interrupts on the top-level agent and on a specialist behind `as_tool()`; resume; eviction while pending; deny via `cancel_tool`; interrupt plus `structured_output_model`; CAN with the decision and no IP; two `locate_user` calls in one message answered together. Interrupt IDs are deterministic (`strands/hooks/events.py`). The v3 tests re-run these checks. To verify at v3: rebuilding the agent after a failed resume replays cleanly (`test_failed_resume_rebuilds_agent`).

---

## 6. Saga (`travel/activities.py` and `ConciergeWorkflow._book`)

§6.1–6.3 describe the core saga that arrives in `v5-booking-saga`. §6.4 is the delta that `v9-hardening` adds for the in-flight Duffel window. The final code is §6.1–6.3 plus §6.4.

### 6.1 Activities

| Activity | Provider | Input | Returns | Business failures (non-retryable) |
|---|---|---|---|---|
| `book_flight` | Duffel `POST /air/orders` (test) | `BookRequest(book_ids=[off_])` | `ord_...` | knob `FlightUnavailable`; Duffel 422 `offer_no_longer_available`, `offer_expired`, `price_changed`, `payment_amount_does_not_match_order_amount`, `insufficient_balance`, `order_not_created`, `invalid_*` |
| `cancel_flight` | Duffel order cancellation create + confirm | `CancelRequest(book_ids=[off_], ref=ord_ or None)` | `None` | `order_not_cancellable` → recorded `cancel_flight:FAILED` |
| `book_hotel` | Duffel `POST /stays/bookings` (test) | `BookRequest(book_ids=[quo_])` | `bok_...` | knob `HotelUnavailable`; any Stays 4xx (e.g. "Rate Unavailable") |
| `cancel_hotel` | Duffel `POST /stays/bookings/{id}/actions/cancel` | `CancelRequest(ref=bok_ or None)`; lookup by `idempotency_key` | `None` | none expected; a 4xx is recorded as `:FAILED` |
| `book_activities` | **mock** `activity_provider.book` | `BookRequest(book_ids=[place ids])` | `ACT-xxxxxxxx` | knob `ActivitySoldOut` |
| `cancel_activities` | **mock** `activity_provider.cancel` | `CancelRequest` | `None` | none |
| `charge_payment` | **mock** | `ChargeRequest` | `PY-xxxxxxxx` | knob `CardDeclined` |
| `refund_payment` | **mock** | `CancelRequest` | `None` | none |

- `ALL_ACTIVITIES` is the three `search_*` activities, `locate_user`, `price_quote`, and these eight.
- All of them log with `activity.logger`, including the Duffel `booking_reference` (PNR) and the hotel `reference`.
- Duffel bookings are paid from the test **balance** (`payments: [{"type": "balance", ...}]` for flights; Stays omits `payment`). The mock `charge_payment` then "charges the traveller" for the total. A failed charge therefore cancels real test bookings, and the refunds go back to the Duffel balance.

```python
from travel.models import TEST_TRAVELLERS   # §4.2

def _maybe_fail(step: str, requested: str, err_type: str) -> None:   # runs BEFORE any provider call
    if requested == step:                                           # from the [fail:<step>] chat tag
        raise ApplicationError(f"{step} provider rejected booking", type=err_type, non_retryable=True)
    if os.environ.get("FLAKY_STEP") == step and activity.info().attempt < 3:      # local Demo C only
        raise RuntimeError(f"transient {step} provider timeout (attempt {activity.info().attempt})")

async def _delay() -> None:                                         # BOOKING_DELAY_S, local Demo C only
    for _ in range(int(os.environ.get("BOOKING_DELAY_S", "0"))):
        activity.heartbeat(); await asyncio.sleep(1)

async def _hb(coro):                                                # heartbeat every 5 s while an HTTP call runs
    task = asyncio.ensure_future(coro)
    try:
        while not task.done():
            activity.heartbeat(); await asyncio.wait({task}, timeout=5)
        return task.result()
    finally:                                                        # activity cancelled: don't leave the HTTP call running
        if not task.done(): task.cancel()                           # a cancelled POST is resolved by the next lookup

@activity.defn
async def book_flight(req: BookRequest) -> str:
    await _delay(); _maybe_fail("flight", req.simulate_failure, "FlightUnavailable")
    order = await _hb(duffel.create_order(req.book_ids[0], req.amount, req.currency, TEST_TRAVELLERS[:req.travelers],
                                          metadata={"idempotency_key": req.idempotency_key}))
    activity.logger.info("flight %s PNR %s", order["id"], order["booking_reference"])
    return order["id"]

@activity.defn
async def cancel_flight(req: CancelRequest) -> None:                # cancel-if-exists
    if not (order_id := req.ref):                                   # forward step never reported: look it up
        order = await duffel.order_for_offer(req.book_ids[0])       # v9 waits here for an in-flight order (§6.4)
        order_id = order and order["id"]
    if order_id:
        await _hb(duffel.cancel_order(order_id))

@activity.defn
async def book_activities(req: BookRequest) -> str:                 # MOCK provider
    await _delay(); _maybe_fail("activities", req.simulate_failure, "ActivitySoldOut")
    return await activity_provider.book(req.idempotency_key, req.book_ids)
```
`book_hotel`/`cancel_hotel` follow the `book_flight`/`cancel_flight` pattern:
- `book_hotel` calls `duffel.create_booking(quote_id, travellers, metadata={"idempotency_key": key})` with `email` and `phone_number` from `TEST_TRAVELLERS[0]` and `guests=[{given_name, family_name} for t in TEST_TRAVELLERS[:travelers]]`. No `born_on` and no `payment` (omitted = paid from balance). VERIFIED body fields: `quote_id`, `email`, `phone_number`, `guests`, optional `metadata`, `payment`, `users`, `loyalty_programme_account_number`, `accommodation_special_requests`.
- `cancel_hotel` gets the booking id from `req.ref`, or else from `duffel.booking_for_key(req.idempotency_key)` (v9 waits for an in-flight booking, §6.4). It GETs the booking: if `status == "cancelled"` it returns; otherwise it POSTs the cancel. A 4xx on the cancel counts as success if a follow-up GET shows `status == "cancelled"`.

`charge_payment` and `refund_payment` are mocks:
- `charge_payment` runs `_maybe_fail("payment", ..., "CardDeclined")`, logs the per-currency `amounts`, and returns `"PY-" + sha256(key)[:8]`.
- `refund_payment` only logs.

**`duffel.create_order` and `duffel.cancel_order` (the two idempotency-critical helpers)**
```python
async def create_order(offer_id, amount, currency, travellers, metadata) -> dict:
    if existing := await order_for_offer(offer_id):          # a retry after a lost response: return what exists
        return existing
    offer = await get_offer(offer_id)                        # passenger ids (pas_...) come from the offer
    body = {"type": "instant", "selected_offers": [offer_id], "metadata": metadata,
            "payments": [{"type": "balance", "currency": currency, "amount": f"{amount:.2f}"}],   # must equal the approved amount
            "passengers": [{"id": p["id"], **t} for p, t in zip(offer["passengers"], travellers)]}
    return await _req("POST", "/air/orders", json={"data": body})   # a duplicate-type 422 raises non-retryable (v9: §6.4)

async def cancel_order(order_id: str) -> None:
    if (await _req("GET", f"/air/orders/{order_id}"))["cancelled_at"]:
        return
    try:
        c = await _req("POST", "/air/order_cancellations", json={"data": {"order_id": order_id}})
        await _req("POST", f"/air/order_cancellations/{c['id']}/actions/confirm")
    except ApplicationError as e:
        if e.type != "already_cancelled":
            raise
```
- `amount` is the **approved** amount. If Duffel's price moved after the re-check, Duffel returns 422 (`price_changed` or `payment_amount_does_not_match_order_amount`), the step fails without retry, and the saga compensates. We never pay more than the user approved.
- Duffel says to give order creation a client timeout of at least 130 s (VERIFIED). Hence `httpx` `timeout=130` and the 150 s `start_to_close_timeout` in `FWD`.
- **The core covers the common retry**: a lost response is found by the lookup that runs before every POST. The rarer case, where attempt 1 is *still being created* at Duffel when attempt 2 starts, is handled in v9 (§6.4).

### 6.2 Ordering, keys, idempotency and the saga loop
**Ordering:**
1. Approval (§7).
2. `price_quote` re-check.
3. `book_flight`.
4. `book_hotel`.
5. `book_activities` (skipped if there are none).
6. `charge_payment`, last, so a booking failure never needs a refund.

Amounts come from the re-checked `Quote.line_items`, never from model output.

**Idempotency key:** `f"{workflow_id}:{quote_id}:{step}"`. It is stable across retries, replay, worker crashes and CAN (`quote_seq` is carried over). The forward step and its compensation share the key.

| Provider | Forward step idempotency | Compensation idempotency |
|---|---|---|
| Duffel Flights | No idempotency header exists (VERIFIED). `create_order` looks up `GET /air/orders?offer_id=` before POST (§6.1); v9 also polls that lookup after a duplicate-type 422 (§6.4). Duffel also allows only one booking per offer request (VERIFIED). The key goes in `metadata.idempotency_key` for tracing only, since there is no metadata filter on the orders list (VERIFIED). | GET the order: if `cancelled_at` is set, done. Treat `already_cancelled` as success. Without a ref, find the order by `offer_id`; if there is none, there is nothing to cancel (v9 waits for an in-flight one). |
| Duffel Stays | No documented idempotency key. `create_booking` sends `metadata.idempotency_key` and, on every attempt, first calls `booking_for_key(key)` over `GET /stays/bookings` (VERIFIED: list exists, bookings expose `metadata`). The key is stable across retries; the `quo_` id is not (each re-check mints a new one). | Without a ref, `booking_for_key(key)` (v9 waits for an in-flight one). GET the booking first and return if `status == "cancelled"`; a 4xx on cancel counts as success if a GET then shows `cancelled`. |
| Activities (mock) | `ACT-` + sha256(key): the same ref on any worker or AgentCore microVM | `cancel` always succeeds |
| Payment (mock) | `PY-` + sha256(key) | `refund` always succeeds |

```python
async def _book(self, q: Quote) -> BookingOutcome:
    self._status = "booking"
    wf = workflow.info().workflow_id
    key = lambda step: f"{wf}:{q.quote_id}:{step}"
    items = lambda kind: [i for i in q.line_items if i.kind == kind]
    out = BookingOutcome(quote_id=q.quote_id, status="booking")
    comps: list[tuple[str, Callable[[], Awaitable[None]]]] = []

    def req(step: str, kind: str) -> BookRequest:
        its = items(kind)
        return BookRequest(idempotency_key=key(step), book_ids=[i.book_id for i in its],
                           amount=sum((i.amount for i in its), Decimal(0)), currency=its[0].currency,
                           travelers=q.proposal.travelers, simulate_failure=q.simulate_failure)

    def compensate_with(name: str, fn, r: BookRequest | ChargeRequest, ref: Callable[[], str | None]) -> None:
        # register BEFORE the forward step; ref() is read when the compensation runs (None if the step never reported)
        comps.append((name, lambda: workflow.execute_activity(
            fn, CancelRequest(idempotency_key=r.idempotency_key, book_ids=getattr(r, "book_ids", []), ref=ref()),
            summary=name, **COMP)))

    async def run_compensations() -> None:
        for name, c in reversed(comps):                     # LIFO
            try:
                await c(); out.compensations.append(name)
            except ActivityError as e:                      # non-retryable, or gave up after schedule_to_close (1 h)
                workflow.logger.error("compensation %s failed: %s", name, e)
                out.compensations.append(f"{name}:FAILED")

    async def step(name: str, fn, r) -> str:
        return await workflow.execute_activity(fn, r, summary=name, **FWD)

    try:
        flight = req("flight", "flight")
        compensate_with("cancel_flight", act.cancel_flight, flight, lambda: out.flight_ref)
        out.flight_ref = await step("book_flight", act.book_flight, flight)
        hotel = req("hotel", "hotel")
        compensate_with("cancel_hotel", act.cancel_hotel, hotel, lambda: out.hotel_ref)
        out.hotel_ref = await step("book_hotel", act.book_hotel, hotel)
        if items("activity"):
            acts = req("activities", "activity")
            compensate_with("cancel_activities", act.cancel_activities, acts, lambda: out.activities_ref)
            out.activities_ref = await step("book_activities", act.book_activities, acts)
        pay = ChargeRequest(idempotency_key=key("payment"), amounts=q.totals, simulate_failure=q.simulate_failure)
        compensate_with("refund_payment", act.refund_payment, pay, lambda: out.payment_ref)
        out.payment_ref = await step("charge_payment", act.charge_payment, pay)
        out.status = "confirmed"
        return out
    except ActivityError as e:                              # business failure → compensate, keep chatting
        out.failed_step = e.activity_type                   # e.g. "book_activities"
        cause = e.cause
        out.error = f"{getattr(cause, 'type', '')}: {getattr(cause, 'message', str(cause))}"
        await run_compensations()
        out.status = "compensated"
        return out
    except asyncio.CancelledError:                          # workflow cancelled mid-saga
        await asyncio.shield(asyncio.ensure_future(run_compensations()))
        raise
```

**Saga rules** (https://docs.temporal.io/design-patterns/saga-pattern, https://docs.temporal.io/develop/python/best-practices/error-handling#implement-saga-pattern, temporal-developer skill `references/python/patterns.md`):
- Register each compensation **before** its forward step. Every compensation is cancel-if-exists, because a step can succeed at the provider and time out before reporting. Flights find the order by `offer_id`; hotels find the booking by `metadata.idempotency_key`. (v9 adds a wait for a booking that may still be in flight, §6.4.)
- **Retry policies:**

| Options | Applies to | Timeouts | Retries | Non-retryable |
|---|---|---|---|---|
| `FWD` | forward steps | `start_to_close` 150 s, heartbeat 15 s | up to 5 attempts | 4 knob types (listed **and** raised `non_retryable=True`); every Duffel 4xx except 429, raised `non_retryable=True` with `type=<duffel code>` |
| `PRICE` | `price_quote` | `start_to_close` 60 s | 3 attempts | none: business problems come back as `problems` data |
| `COMP` | compensations | `start_to_close` 200 s (60 s before v9), heartbeat 15 s, `schedule_to_close` 1 h | unlimited, `maximum_interval` 30 s | Duffel 4xx such as `order_not_cancellable` fails at once; recorded `"<name>:FAILED"`, surfaced in the outcome |
| `SEARCH` | search tools (§5.2) | `start_to_close` 60 s | 3 attempts | after retries the tool error becomes tool-result text for the LLM |

- **Retryable failures:** 429 `rate_limit_exceeded` (the limit is 60 requests per 60 s, VERIFIED), 5xx and network errors. Duffel advises against blindly retrying a 500/502 on order creation (VERIFIED), but our retry always runs the `offer_id` lookup first, so it is safe.
- Business failures are `ApplicationError`s raised **from activities**, never from workflow code.
- Workflow cancellation runs compensations under `asyncio.shield`, then re-raises.
- The saga is **never** an LLM tool. `activity_as_tool` turns failures into tool-result text, so the workflow would never see an exception to compensate on.

### 6.3 Failure-injection knobs

| Knob | Where | Effect |
|---|---|---|
| **`[fail:activities]`** (primary) in a chat message | Parsed deterministically in `chat` and stored on the next `Quote.simulate_failure` | `book_flight` and `book_hotel` create **real** Duffel test bookings. Then `book_activities` raises `ActivitySoldOut` (non-retryable). `cancel_activities`, `cancel_hotel` and `cancel_flight` run in LIFO order, and both Duffel bookings show as cancelled in the Duffel dashboard. |
| `[fail:hotel]` (also `flight` / `payment`) | same | Simpler variant: one real flight order, cancelled. `[fail:payment]` rolls back all three. The knob raises **before** the provider call, so the failing step never books anything. Works on AgentCore with no redeploy, and is visible in history. |
| Duffel scenario routes (Duffel Airways) | IATA codes in the chat (city names resolve to `LON`, which skips the scenario) | Real provider failures (VERIFIED in the docs): `LGW→LHR` offer no longer available; `LHR→STN` price changes; `LHR→LGW` order creation error; `LGW→STN` insufficient balance. These fail at the flight step, so they show a non-retryable provider error, not a multi-step rollback. UNVERIFIED: whether the scenario still triggers on a round-trip search (we send both slices). Optional; see §11 F. |
| `FLAKY_STEP=hotel` | Worker env, **local Demo C only** | Attempts 1–2 raise a retryable `RuntimeError` before the provider call; attempt 3 books. |
| `BOOKING_DELAY_S=5` | Worker env, **local Demo C only** | Each booking activity sleeps 5 s before its provider call, heartbeating each second, so you can kill the worker mid-saga. |

### 6.4 Hardening: the in-flight window (added in `v9-hardening`)
**Problem.** Attempt 1 can still be creating the order at Duffel when attempt 2 starts, for example because the worker died or missed a heartbeat. Duffel allows up to 130 s for this. Attempt 2's lookup finds nothing, and its POST gets a duplicate-type 422. Likewise, a compensation with no `ref` can look up too early and conclude there is nothing to cancel, while the booking appears a minute later.

**Delta (all in `travel/duffel.py`, `travel/activities.py`, `travel/models.py`, `travel/workflow.py`):**
```python
# duffel.py
POLL_S = 5
DUPLICATE = {"offer_request_already_booked", "order_creation_already_attempted", "duplicate_booking"}

async def create_order(offer_id, amount, currency, travellers, metadata) -> dict:   # §6.1 body; the POST becomes:
    try:
        return await _req("POST", "/air/orders", json={"data": body})
    except ApplicationError as e:
        if e.type not in DUPLICATE:
            raise
    for _ in range(24):                                      # an earlier attempt may still be creating it (up to 130 s)
        if existing := await order_for_offer(offer_id):     # caller wraps this in _hb, so heartbeats continue
            return existing
        await asyncio.sleep(POLL_S)
    raise ApplicationError("order creation already attempted but no live order exists", type="OrderNotFound",
                           non_retryable=True)               # the offer is spent: compensate and re-plan

# activities.py
async def _find(lookup, wait_s: int):                        # poll a provider lookup, heartbeating
    for _ in range(0, wait_s, 5):
        if found := await lookup(): return found
        activity.heartbeat(); await asyncio.sleep(duffel.POLL_S)
    return await lookup()
# cancel_flight: order = await _find(lambda: duffel.order_for_offer(off), 140 if req.maybe_booked else 0)
# cancel_hotel:  bok   = await _find(lambda: duffel.booking_for_key(key), 140 if req.maybe_booked else 0)

# models.py: CancelRequest.maybe_booked: bool = True
# workflow.py (_book): definite = False; on ActivityError:
#     definite = isinstance(e.cause, ApplicationError) and e.cause.non_retryable   # it cannot have booked
#   and each CancelRequest gets maybe_booked=not definite (read when the compensation runs)
# workflow.py: COMP start_to_close 60 s → 200 s (a 140 s wait plus the cancel itself)
```
- `create_order` polls every 5 s for up to 120 s, which covers Duffel's 130 s ceiling counted from attempt 1. The polling runs inside `_hb`, so heartbeats continue, and it fits the 150 s `FWD` budget.
- A compensation waits only when the forward step **may** have booked, meaning it timed out or ran out of retries. A knob or a Duffel 4xx is non-retryable and happens before anything is booked, so no wait is needed and Demo B stays fast.
- UNVERIFIED (§12 Q19): that `order_creation_already_attempted` is what Duffel returns while an earlier creation is still running.

---

## 7. Human approval

- **Mechanism:** `@workflow.update approve(ApprovalDecision) -> BookingOutcome`.
  - The validator checks: status is `awaiting_approval`; no turn is open (running, or paused on a permission request); `quote_id` matches the pending quote (this rejects stale quotes after a re-proposal); no prior decision.
  - Rejected updates write nothing to history.
  - The handler records the decision synchronously, before awaiting (§4.5).
- **Gate placement:** in `run()`, which is deterministic code, so the LLM cannot skip it. Strands interrupts are deliberately not used for payment approval. They *are* used for the location permission (§5.5): that gate pauses one tool call in the middle of a turn, whereas approval gates the saga, which is never a tool.
- **Re-validation on approve:** this is the second `price_quote`, with `expected=quote.line_items`.
  - If an offer has expired, disappeared, changed price or changed currency, nothing is booked. The outcome is `cancelled` with `failed_step="price_quote"`, and the approver gets it immediately.
  - The next chat turn starts with `[system: quote qN cancelled; failed_step=price_quote; error=flight price changed 412.30 USD → 431.10 USD]`, and the concierge searches again. Duffel offer requests are single-use and offers expire, so the old IDs are never reused.
  - If the re-check passes, the saga books exactly the re-checked amounts. For the hotel, that is the fresh Stays quote.
- **Timeout:** `ConciergeInput.approval_timeout_s`, default 900 s, on `wait_condition`.
  - It measures **idle time since the last turn**. The timer pauses while a chat turn runs and re-arms after it, so a quote is never cancelled mid-turn.
  - On timeout the outcome is `BookingOutcome(status="cancelled", error="approval timed out")`. The quote is cleared, and the next turn tells the concierge.
  - Duffel offers typically expire 15–30 min after search (VERIFIED). The wait is `min(approval_timeout_s, expires_at − workflow.now())`, so a quote is cancelled with `error="offer expired"` as soon as its flight offer lapses, even if turns keep re-arming the timer. The CLI shows `Quote.expires_at`. The approve re-check still catches anything the timer cannot see (a hotel rate that lapsed, a price change).
- **Reject:** `approved=False` gives `cancelled` with the approver and reason. Nothing is booked, so there is nothing to compensate.
- **Approve:** the handler waits for the re-check and the saga, and returns `confirmed`, `compensated` or `cancelled` (re-check) in one call.
- **On AgentCore:** waiting holds no compute ("human approval waits … do not require compute to remain running", https://docs.temporal.io/serverless-workers/agentcore). The worker drains after `AGENTCORE_DEBOUNCE_SECONDS`. The next `approve` creates a workflow task, and the WCI invokes the runtime. See §9.6, and §12 Q4 for the client-side retry.
- **References:** https://docs.temporal.io/design-patterns/approval, https://docs.temporal.io/ai/cookbook/human-in-the-loop-python.

---

## 8. Local development

### 8.1 Worker factory (`travel/worker.py`): the single switch between local and cloud
```python
import asyncio, os
from datetime import timedelta
from botocore.config import Config as BotocoreConfig
from strands.models.bedrock import BedrockModel
from temporalio.client import Client
from temporalio.common import VersioningBehavior
from temporalio.contrib.strands import StrandsPlugin
from temporalio.envconfig import ClientConfig
from temporalio.worker import Worker, WorkerDeploymentConfig, WorkerDeploymentVersion
from travel.activities import ALL_ACTIVITIES
from travel.workflow import ConciergeWorkflow

TASK_QUEUE = os.environ.get("TEMPORAL_TASK_QUEUE", "travel-concierge")
DEPLOYMENT = os.environ.get("TEMPORAL_DEPLOYMENT_NAME", "travel-concierge")
BUILD_ID   = os.environ.get("TEMPORAL_BUILD_ID", "local")      # AgentCore shell asserts it is set (§9.1)

_NO_RETRY = BotocoreConfig(retries={"max_attempts": 0}, read_timeout=120)   # Temporal owns retries

def bedrock_models() -> dict:
    region = os.environ.get("AWS_REGION", "us-west-2")
    model_id = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-6")   # Strands' default model
    make = lambda: BedrockModel(model_id=model_id, region_name=region, boto_client_config=_NO_RETRY)
    return {"concierge": make, "specialist": make}       # lazy factories; never invoked by chat.py

async def connect(models: dict | None = None) -> Client:
    cfg = ClientConfig.load_client_connect_config()     # TEMPORAL_ADDRESS / _NAMESPACE / _API_KEY / _PROFILE
    cfg.setdefault("target_host", "localhost:7233")     # no env → dev server; API key present → TLS on
    return await Client.connect(**cfg, plugins=[StrandsPlugin(models=models or bedrock_models())])

def build_worker(client: Client, interceptors: list = ()) -> Worker:
    return Worker(client, task_queue=TASK_QUEUE, workflows=[ConciergeWorkflow], activities=ALL_ACTIVITIES,
        interceptors=list(interceptors),
        deployment_config=WorkerDeploymentConfig(
            version=WorkerDeploymentVersion(deployment_name=DEPLOYMENT, build_id=BUILD_ID),
            use_worker_versioning=True, default_versioning_behavior=VersioningBehavior.PINNED),
        graceful_shutdown_timeout=timedelta(seconds=120))

async def main() -> None:
    await build_worker(await connect()).run()

if __name__ == "__main__":
    asyncio.run(main())
```
- **`StrandsPlugin` goes on the Client** (worker and `chat.py`); workers inherit it, and it supplies the pydantic and failure converters.
- **Versioning stays on locally** for parity with AgentCore; the cost is one `set-current-version` command.

### 8.2 Chat CLI (`chat.py`)
`chat.py` does `from travel.worker import connect, TASK_QUEUE` and `client = await connect()` (the Bedrock factories are lazy and never run in the CLI). It imports `ConciergeWorkflow` and models for typed calls.

- **Flags (argparse):** `--conversation concierge-<id>` (default: new uuid; printed at start), `--approval-timeout <s>` (default 900, passed in `ConciergeInput`).
- **REPL loop:** read a line, dispatch per the table, print, repeat.

```python
async def send(text: str) -> ChatResponse:
    upd_id = str(uuid4())
    for attempt in range(5):                                   # cold start on AgentCore can exceed the RPC deadline
        start = WithStartWorkflowOperation(ConciergeWorkflow.run, ConciergeInput(approval_timeout_s=args.approval_timeout),
                    id=conv_id, id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING, task_queue=TASK_QUEUE)
        try:                                                   # a WithStartWorkflowOperation is single-use: rebuild per try
            return await client.execute_update_with_start_workflow(
                ConciergeWorkflow.chat, ChatRequest(text=text), start_workflow_operation=start, id=upd_id)
        except WorkflowUpdateRPCTimeoutOrCancelledError:       # same update id → deduplicated within the run
            print(f"(waiting for a worker… retry {attempt + 1})")
    raise RuntimeError("no worker picked up the message")

handle = client.get_workflow_handle_for(ConciergeWorkflow.run, conv_id)   # rebuilt when conv_id changes

# /approve and /reject: same retry loop around
await handle.execute_update(ConciergeWorkflow.approve, ApprovalDecision(quote_id=q.quote_id, approved=True),
                            id=f"approve-{q.quote_id}")

async def fetch_public_ip() -> str:                            # ONLY after the user answered y
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get("https://api.ipify.org", params={"format": "json"})   # IPv4 host; no key, no logging (ipify)
        r.raise_for_status()
        return r.json()["ip"]

async def answer(pr: PermissionRequest) -> ChatResponse:      # a reply, or state(), has a pending request
    ip = None
    if pr.prompt is None or input(f"{pr.prompt} ").strip().lower() in ("y", "yes"):   # None: already consented
        try: ip = await fetch_public_ip()
        except httpx.HTTPError: print("(could not get your public IP; answering no; /permissions reset to be asked again)")
    a = PermissionAnswer(interrupt_id=pr.interrupt_id, decision="granted" if ip else "denied", client_ip=ip)
    return await handle.execute_update(ConciergeWorkflow.grant_permission, a, id=f"perm-{pr.interrupt_id}")
```
- `answer` uses the same retry loop as `send`. Its reply is the rest of the paused turn, and is displayed like any chat reply. That reply could in principle carry another request; the CLI loops while `permission_request` is set.
- **Recovering a pending request.** At startup with `--conversation`, and whenever `chat` is rejected with "answer the pending permission request first", the CLI calls `state()`; if `pending_permission` is set it runs `answer(pending_permission)`. Without this, a CLI restarted mid-request could never unblock the conversation.
- **After `confirmed`** the CLI sets `conv_id` to a new `concierge-<uuid>` and prints "trip booked; new conversation concierge-<id>" (§4.3).
- `httpx` is already a dependency.

| Command | Action |
|---|---|
| any text | `chat` Update-with-Start; if the reply has `permission_request`, run `answer` |
| `/approve` | `approve` Update with the current `quote_id` |
| `/reject <reason>` | `approve` Update with `approved=False` |
| `/permissions` | prints `state().permissions` (e.g. `ip_location: granted`); if a request is pending, re-runs its prompt (`answer`) |
| `/permissions deny` · `/permissions reset` | `set_permission` with `decision="denied"` / `"undecided"` (reset = ask again next time); no IP is sent; rejected while a request is pending |
| `/status` | `state` Query; on `RPCError` / `WorkflowQueryFailedError` prints the last cached `ChatResponse` and "worker may be scaled to zero; send a message to wake it" |
| `/quit` | `end_chat` Signal |

- **Display:** If `outcome` is set, print it first. Then print:
  - the message;
  - "Travellers: Ada Lovelace (test profile)" from `Quote.travellers`;
  - the line items with amount and currency, printing each `label` as-is and adding "(mock price)" for `mock=True`;
  - the total per currency (one line per key of `totals`);
  - "over budget" when `over_budget` is true, or "budget not compared (not all USD)" when it is `None` and a budget was given. This is the only budget verdict shown; the budget agent's opinion is never printed;
  - "offer expires <expires_at>";
  - the `quote_id`, and "type /approve to book".
- **Errors:** `WorkflowUpdateFailedError` (validator rejection, `Busy`, `TurnFailed`) prints the cause and continues.
- Update-with-Start: https://docs.temporal.io/develop/python/workflows/message-passing#update-with-start.

### 8.3 Commands
**Provider accounts (one-time; start the Stays request first, because it has a lead time):**
1. **Duffel:**
   - Sign up, then **request access to Duffel Stays** through Duffel's contact form, before you create test tokens (VERIFIED, getting-started guide; §12 Q11).
   - Create a **test** access token (`duffel_test_…`).
   - Optionally set the org billing currency to USD so the budget can be compared (§12 Q15).
2. **Google:**
   - Create a Cloud project with a **billing account**, which is required even for the free tier.
   - Enable **Places API (New)** and create an API key. Restrict the key to that API.

```bash
cd agentcore-travel-demo
git checkout v9-hardening       # or any tag from §13; the repo is built tag by tag
uv sync

# terminal 1
temporal server start-dev                       # UI http://localhost:8233

# terminal 2 — worker (needs AWS creds with Bedrock access to the Anthropic model)
export AWS_REGION=us-west-2 AWS_PROFILE=<profile>
export DUFFEL_ACCESS_TOKEN=duffel_test_... GOOGLE_PLACES_API_KEY=...
uv run python -m travel.worker

# terminal 3 — one-time per build id, after the worker has polled once
# (if it errors with "deployment not found", the worker hasn't polled yet; wait and retry)
temporal worker deployment set-current-version --deployment-name travel-concierge --build-id local --yes
uv run python chat.py
```
The first local run also confirms Bedrock model access for the account before AgentCore needs it.

**Switching tags:** with `PINNED` and build id `local` on every tag, a worker restarted on another tag would replay open conversations against different code (non-determinism errors). After `git checkout <tag>`, start a new conversation, or `temporal workflow terminate` the old ones.

**Optional: run the exact AgentCore shell locally.**
```bash
TEMPORAL_DEPLOYMENT_NAME=travel-concierge TEMPORAL_BUILD_ID=local TEMPORAL_ADDRESS=localhost:7233 TEMPORAL_NAMESPACE=default \
AGENTCORE_DEBOUNCE_SECONDS=3600 DUFFEL_ACCESS_TOKEN=$DUFFEL_ACCESS_TOKEN GOOGLE_PLACES_API_KEY=$GOOGLE_PLACES_API_KEY uv run python agentcore_worker.py                      # serves :8080
curl -X POST localhost:8080/invocations -H 'Content-Type: application/json' -d '{}'   # what the WCI does
```

### 8.4 Env vars

| Var | Local default | AgentCore |
|---|---|---|
| `TEMPORAL_ADDRESS` | unset → `localhost:7233` | `<ns>.<acct>.tmprl.cloud:7233` (if the namespace has both mTLS and API-key auth, use the regional endpoint `us-west-2.aws.api.temporal.io:7233`) |
| `TEMPORAL_NAMESPACE` | unset → `default` | `<ns>.<acct>` |
| `TEMPORAL_API_KEY` | unset (no TLS) | API key; turns TLS on automatically |
| `TEMPORAL_TASK_QUEUE` | `travel-concierge` | `travel-concierge` |
| `TEMPORAL_DEPLOYMENT_NAME` | `travel-concierge` | `travel-concierge` (required) |
| `TEMPORAL_BUILD_ID` | `local` | `1.0.0`, bumped every deploy (required) |
| `AWS_REGION` | `us-west-2` | `us-west-2` |
| `MODEL_ID` | `global.anthropic.claude-sonnet-4-6` | same |
| `AGENTCORE_DEBOUNCE_SECONDS` | n/a | `300` for live demos (sample default 60) |
| `DUFFEL_ACCESS_TOKEN` | `duffel_test_…` (required by the worker; any other prefix is refused) | same test token |
| `GOOGLE_PLACES_API_KEY` | Places API (New) key (required by the worker) | same |
| `IPINFO_TOKEN` | unset (ipinfo free tier, 1,000/day); a free token raises it to 50,000/month | optional; add to `envVars` if set |
| `DUFFEL_TEST_HOTEL` | unset; `1` forces the Duffel Test Hotel (live test, demo safety switch) | unset |
| `FLAKY_STEP` / `BOOKING_DELAY_S` | unset (Demo C only) | unset |

- Only the worker reads the provider keys. `chat.py` never needs them; its only non-Temporal call is ipify, after consent.
- The chat client and the `temporal` CLI read the same `TEMPORAL_*` vars (or a `temporal.toml` profile via `TEMPORAL_PROFILE`; https://docs.temporal.io/references/client-environment-configuration).

---

## 9. AgentCore deployment (Temporal Cloud Serverless Workers)

**Pre-release** for AgentCore; needs an AWS-hosted Temporal Cloud namespace enabled for it (support ticket or account team). Docs: https://docs.temporal.io/serverless-workers/agentcore, https://docs.temporal.io/develop/python/workers/serverless-workers/agentcore, https://docs.temporal.io/production-deployment/worker-deployments/serverless-workers/agentcore, https://docs.temporal.io/guides/durable-agent-on-agentcore.

### 9.1 Entrypoint (`agentcore_worker.py`): the hosting shell only
Copied from `samples-python/bedrock_agentcore/strands_agent/agentcore_worker.py`, with Worker construction replaced by `travel.worker`.
```python
import asyncio, os
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput, Interceptor
from travel.worker import TASK_QUEUE, build_worker, connect

app = BedrockAgentCoreApp()
log = app.logger
DEBOUNCE = float(os.environ.get("AGENTCORE_DEBOUNCE_SECONDS", "60"))
REQUIRED = ("TEMPORAL_ADDRESS", "TEMPORAL_NAMESPACE", "TEMPORAL_DEPLOYMENT_NAME", "TEMPORAL_BUILD_ID")
_worker: asyncio.Task[None] | None = None

class ActivityTracker(Interceptor):            # verbatim from sample: drain after DEBOUNCE s with no activity
    def __init__(self) -> None: self.inflight, self.changed = 0, asyncio.Event()
    def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:
        return _Tracked(next, self)
    async def wait_until_idle(self, debounce: float) -> None:
        while True:
            self.changed.clear()
            try: await asyncio.wait_for(self.changed.wait(), timeout=debounce)
            except asyncio.TimeoutError:
                if self.inflight == 0: return

class _Tracked(ActivityInboundInterceptor):
    def __init__(self, next, t): super().__init__(next); self._t = t
    async def execute_activity(self, input: ExecuteActivityInput):
        self._t.inflight += 1; self._t.changed.set()
        try: return await self.next.execute_activity(input)
        finally: self._t.inflight -= 1; self._t.changed.set()

async def run_worker() -> None:
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:                                  # a defaulted build id would strand workflows on an unpolled version
        raise RuntimeError(f"missing env: {missing}")
    tracker = ActivityTracker()
    async with build_worker(await connect(), interceptors=[tracker]):
        await tracker.wait_until_idle(DEBOUNCE)

async def _run_until_idle(task_id: int) -> None:
    try: await run_worker()
    except Exception: log.exception("worker failed")
    finally: app.complete_async_task(task_id)    # ALWAYS, or the session stays HealthyBusy until maxLifetime

@app.entrypoint
async def invoke(payload: dict) -> dict:        # capacity request from the WCI, not a prompt; never block here
    global _worker
    if _worker is not None and not _worker.done():
        return {"message": "worker already polling", "task_queue": TASK_QUEUE}
    task_id = app.add_async_task("temporal-worker")
    _worker = asyncio.create_task(_run_until_idle(task_id))
    return {"message": "worker starting", "task_queue": TASK_QUEUE}

if __name__ == "__main__":
    app.run()
```
Only the hosting shell differs; `travel/` (workflow, agents, activities, plugin config, versioning) is byte-identical to local.

### 9.2 `agentcore/agentcore.json`
The sample's file with our values. **Endpoint names: one per build**, and they must match `^[a-zA-Z][a-zA-Z0-9_]{0,47}$` (AgentCore schema), so use underscores (the docs' `temporal-v1` example would not validate).
```json
{ "$schema": "https://schema.agentcore.aws.dev/v1/agentcore.json",
  "name": "TravelConcierge", "version": 1, "managedBy": "CDK",
  "runtimes": [{
    "name": "travel_concierge_worker", "build": "CodeZip",
    "entrypoint": "agentcore_worker.py", "codeLocation": ".",
    "runtimeVersion": "PYTHON_3_12", "networkMode": "PUBLIC",
    "protocol": "HTTP", "authorizerType": "AWS_IAM",
    "envVars": [
      {"name": "TEMPORAL_ADDRESS",          "value": "<ns>.<acct>.tmprl.cloud:7233"},
      {"name": "TEMPORAL_NAMESPACE",        "value": "<ns>.<acct>"},
      {"name": "TEMPORAL_API_KEY",          "value": "<your-api-key>"},
      {"name": "TEMPORAL_TASK_QUEUE",       "value": "travel-concierge"},
      {"name": "TEMPORAL_DEPLOYMENT_NAME",  "value": "travel-concierge"},
      {"name": "TEMPORAL_BUILD_ID",         "value": "1.0.0"},
      {"name": "AWS_REGION",                "value": "us-west-2"},
      {"name": "MODEL_ID",                  "value": "global.anthropic.claude-sonnet-4-6"},
      {"name": "AGENTCORE_DEBOUNCE_SECONDS","value": "300"},
      {"name": "DUFFEL_ACCESS_TOKEN",       "value": "duffel_test_<token>"},
      {"name": "GOOGLE_PLACES_API_KEY",     "value": "<places-api-key>"}
    ],
    "lifecycleConfiguration": { "idleRuntimeSessionTimeout": 120, "maxLifetime": 28800 },
    "endpoints": { "b1_0_0": { "version": 1, "description": "WDV travel-concierge/1.0.0" } }
  }],
  "memories": [], "knowledgeBases": [], "credentials": [] }
```
- This is the file as committed at v7. v8 bumps the build id to `1.1.0` and adds the `b1_1_0` endpoint next to `b1_0_0` (§9.4).
- `aws-targets.json`: `[{"name":"default","account":"<id>","region":"us-west-2"}]`.
- **Keys:** `TEMPORAL_API_KEY`, `DUFFEL_ACCESS_TOKEN` and `GOOGLE_PLACES_API_KEY` all go in `envVars`, exactly as the sample does for the Temporal key. Secrets Manager stays a non-goal. Fill them in locally, and **do not commit the populated values**. Changing any of them creates a new runtime version (§9.4). For production, load it from Secrets Manager in the entrypoint (https://docs.temporal.io/production-deployment/worker-deployments/serverless-workers/agentcore#configure-worker-runtime).
- **Execution role:** created by `agentcore deploy`; the sample needs no extra policy for Bedrock model calls (its only `additionalPolicies` entry is Code Interpreter, which we don't use). If Bedrock calls are denied, see §12 Q6.
- `idleRuntimeSessionTimeout: 120` (range 60–28800): while the async task is registered `/ping` is HealthyBusy regardless of the idle timer; after drain nothing runs, so a short idle timeout is fine (sample README).

### 9.3 Deploy (first build)
```bash
# 0. Prereqs: Bedrock Anthropic model access + Duffel/Google keys confirmed by a local run (§8.3);
#    keys filled into agentcore.json envVars (not committed); CDK bootstrapped; uv on PATH
export AWS_REGION=us-west-2
# 1. Scaffold (first run) + agentcore validate + agentcore deploy — the script does all three
./bin/create-runtime.sh
# 2. Capture ARNs (runtime name = <project>_<runtime>)
RUNTIME_ARN=$(aws bedrock-agentcore-control list-agent-runtimes \
  --query "agentRuntimes[?agentRuntimeName=='TravelConcierge_travel_concierge_worker'].agentRuntimeArn|[0]" --output text)
RUNTIME_ENDPOINT_ARN=$(aws bedrock-agentcore-control list-agent-runtime-endpoints --agent-runtime-id "${RUNTIME_ARN##*/}" \
  --query "runtimeEndpoints[?name=='b1_0_0'].agentRuntimeEndpointArn|[0]" --output text)   # …/runtime-endpoint/b1_0_0
# 3. Invocation role Temporal Cloud assumes (stack name ≤ 31 chars; external id 5–45 chars)
EXTERNAL_ID=travel-demo-$(openssl rand -hex 6)
./bin/mk-invoke-role.sh travel-invoke "$EXTERNAL_ID" "${RUNTIME_ARN}*"
aws cloudformation wait stack-create-complete --stack-name travel-invoke
INVOCATION_ROLE_ARN=$(aws cloudformation describe-stacks --stack-name travel-invoke \
  --query 'Stacks[0].Outputs[?OutputKey==`RoleARN`].OutputValue' --output text)
# 4. Worker Deployment Version → AgentCore endpoint (CLI reads TEMPORAL_ADDRESS/NAMESPACE/API_KEY from env)
temporal worker deployment create --name travel-concierge
temporal worker deployment create-version --deployment-name travel-concierge --build-id 1.0.0 \
  --aws-agentcore-endpoint-arn "$RUNTIME_ENDPOINT_ARN" \
  --aws-agentcore-assume-role-arn "$INVOCATION_ROLE_ARN" \
  --aws-agentcore-assume-role-external-id "$EXTERNAL_ID"      # Temporal invokes the runtime and waits for registration
temporal worker deployment set-current-version --deployment-name travel-concierge --build-id 1.0.0 --yes
# 5. Chat from the laptop against Cloud (same chat.py)
TEMPORAL_ADDRESS=... TEMPORAL_NAMESPACE=... TEMPORAL_API_KEY=... uv run python chat.py
# 6. Observe
temporal workflow show -w concierge-<id>
agentcore logs --runtime travel_concierge_worker --since 1h
temporal workflow list --query 'TemporalNamespaceDivision = "TemporalWorkerControllerInstance"'
```
- **Invocation role (from the sample):** trusts `arn:aws:iam::{902542641901,160190466495,819232936619,829909441867,354116250941}:role/wci-lambda-invoke` with `sts:ExternalId`; allows `bedrock-agentcore:InvokeAgentRuntime` and `GetAgentRuntimeEndpoint` on `<runtime-arn>*`.
- **Never** configure a Worker Deployment Version with AgentCore's `DEFAULT` endpoint, and never pass the runtime ARN as the endpoint ARN.
- **Cloud UI alternative:** Workers → Create Worker Deployment → Compute Provider "Amazon Bedrock AgentCore Runtime" → Actions → Validate Connection.

### 9.4 Redeploys: one named endpoint per Worker Deployment Version
Per https://docs.temporal.io/serverless-workers/agentcore#worker-versioning: each WDV points at its own named endpoint pinned to one immutable AgentCore runtime version; keep old endpoints while pinned workflows may need them. Updating code behind a live WDV causes non-determinism errors.

1. Bump `TEMPORAL_BUILD_ID` (e.g. `1.1.0`) in `agentcore.json`.
2. **Add** an endpoint, keeping the old one: `"b1_1_0": {"version": 2, "description": "WDV travel-concierge/1.1.0"}`. The number is the runtime version this deploy creates: every runtime change (code, env, lifecycle) increments it, so 2 holds only if nothing else was deployed since v7. Check with `aws bedrock-agentcore-control list-agent-runtime-versions --agent-runtime-id <id>`.
3. `./bin/create-runtime.sh`.
4. `temporal worker deployment create-version --build-id 1.1.0 --aws-agentcore-endpoint-arn <RUNTIME_ARN>/runtime-endpoint/b1_1_0 …` (same role and external id).
5. `set-current-version --build-id 1.1.0 --yes`.
6. Open conversations finish their current turn on 1.0.0, then continue-as-new with `AUTO_UPGRADE` onto 1.1.0 (§4.4). Delete `b1_0_0` only once 1.0.0 shows no open workflows (`temporal worker deployment describe-version`).

### 9.5 Network and identity
- **`networkMode: PUBLIC`.**
  - Outbound HTTPS goes over the internet to Temporal Cloud, `api.duffel.com`, `places.googleapis.com` and `ipinfo.io`. The worker geolocates only the IP the client sent; its own egress IP (AWS) is never used. Bedrock is reached through AWS.
  - AWS documents that in PUBLIC mode agents use public IPs to reach external APIs, and that outbound traffic is not subject to VPC security groups or NACLs (VERIFIED, AgentCore networking blog and the security best-practices page). No NAT or VPC setup is needed.
  - VPC mode would need a NAT gateway for these calls, which is a non-goal.
  - Nothing comes inbound except the WCI's `InvokeAgentRuntime`.
- Execution role created by `agentcore deploy`; invocation role from §9.3.
- CodeZip: no ECR/Docker; managed Python 3.12 runtime (ARM64). `codeLocation` must contain the entrypoint, `travel/` and `pyproject.toml`; `.git`, `.venv`, `__pycache__`, `node_modules` are excluded from the zip.

### 9.6 Lifecycle behaviour to expect in the demo
- Temporal polling does **not** reset AgentCore's idle timer. `add_async_task` keeps `/ping` HealthyBusy; the `ActivityTracker` drains the worker after `AGENTCORE_DEBOUNCE_SECONDS` with no activities, then `complete_async_task` lets the session idle out.
- Approval waits and idle chats hold no compute. The next `chat`/`approve` Update creates a workflow task; the WCI invokes the runtime on sync-match failure/backlog (https://docs.temporal.io/serverless-workers#how-invocation-works); a fresh worker replays and continues. The CLI's retry loop covers a cold start that exceeds the Update RPC deadline.
- Queries are not workflow tasks and likely don't wake a drained worker; `/status` falls back to the cached reply.
- `maxLifetime` (8 h) kills without a drain; activity timeouts and retries cover it.

---

## 10. Testing

### 10.1 `tests/mock_model.py`
A scripted `strands.models.Model`, modelled on the plugin's `tests/contrib/strands/mock_model.py`.
- `MockModel(script: list[str | dict])`:
  - a `str` yields a text turn (`stopReason: end_turn`);
  - a `{"name": ..., "input": {...}}` dict yields a tool-use turn (`stopReason: tool_use`) with a deterministic `toolUseId` (`f"t{n}"`, the script position). Interrupt IDs are derived from it, so the permission tests can assert them.
  - It records every `messages` list it receives (`model.seen`), so tests can assert what the concierge was told, such as the `[system: …]` note or the denial tool result.
  - `MockModel.FAIL = True` makes every call raise `ApplicationError("model down", non_retryable=True)`, so `invoke_model` fails at once without waiting out the `LLM` retries (UNVERIFIED that the plugin's model activity keeps `non_retryable`; otherwise the test waits ~30 s).
- Events follow the Converse stream shape: `messageStart`, `contentBlockStart`, `contentBlockDelta`, `contentBlockStop`, `messageStop`.
- **Structured output:** a tool-use named after the model class (`"ConciergeReply"`) carrying the JSON payload (verified with a fake model).
- **Per-name scripts:** `StrandsPlugin(models={"concierge": lambda: MockModel(concierge_script), "specialist": lambda: MockModel(spec_script)})`.
  - Factories are cached per worker, so all four specialists share **one** specialist script, consumed in call order.
  - Build a fresh plugin, client and worker for each test.
- **Saga tests** use a single concierge turn: `{"name": "ConciergeReply", "input": {"message": "Here's your trip", "proposal": PROPOSAL}}`. `PROPOSAL` uses the mock provider IDs `off_fake_zz`, `rat_fake_1` and `ChIJmuseum`/`ChIJfado` (§10.2), with dates Mar 10–13 and a budget of 3000.

### 10.2 Mocked activities (in `tests/test_workflow.py`, no network)
Per decision 9, workflow tests **mock the provider activities**. Each mock is an `@activity.defn(name="<real name>")` function with the real signature, registered in place of the real one, and it records its calls in a shared `CALLS` dict.
- `search_flights`, `search_hotels`, `search_places`: return canned dicts with the IDs `off_fake_zz`, `rat_fake_1`, `ChIJmuseum` and `ChIJfado`.
- `locate_user`: records the `ip` it got and returns `{"city": "Mountain View", "country": "US", "airports": [{"iata": "SFO", ...}, {"iata": "SJC", ...}]}`. The tests use `USER_IP = "8.8.4.4"` as the user's IP (it must pass `is_public_ip`; documentation ranges such as 203.0.113.0/24 do not), and the scripted model always passes `{"ip": "6.6.6.6"}`, so a test can prove the model's argument was ignored.
- `price_quote`: returns a `PricedTrip` built from a module-level `PRICES` dict (`off_fake_zz` 412.30 USD, `rat_fake_1` → `quo_fake_1` 465.00 USD, the two places 18 and 55 USD, `mock=True`). It applies the same `expected` comparison as the real activity, so `PRICES["off_fake_zz"] = Decimal("431.10")` simulates a price change on approve.
- `book_flight` / `book_hotel` / `cancel_flight` / `cancel_hotel`: return `ord_fake` / `bok_fake` and record the call. They honour `simulate_failure` through the real `_maybe_fail`.
- The **real** `book_activities`, `cancel_activities`, `charge_payment` and `refund_payment` are registered as-is, because they are mocks with no network.

### 10.3 `tests/fake_duffel.py` (no network; ~40 lines)
Only for the two `ActivityEnvironment` tests that exercise the real `travel/duffel.py` and `price_quote`.
- `FakeDuffel.handle(request) -> httpx.Response` routes:
  - v2: `GET /air/offers/{id}`, `POST /stays/quotes`, Google `GET places/{id}` (an unknown id returns 404);
  - v5 adds: `GET /air/orders?offer_id=`, `POST /air/orders`, `GET /air/orders/{id}`, `POST /air/order_cancellations` and `…/actions/confirm`, single-use offers;
  - v9 adds: the in-flight mode (below).
- Its offer's slices depart on the `PROPOSAL` dates, so the flight-date check passes. From v9 the fixture also sets `duffel.POLL_S = 0` so the in-flight polling loops finish at once.
- It models single-use offers: a `POST /air/orders` for an offer that already has an order (even a cancelled one) returns 422 `offer_request_already_booked`.
- Responses use the documented `{"data": ...}` and error shapes.
- **Fixture:** `monkeypatch.setattr(duffel, "TRANSPORT", httpx.MockTransport(fake.handle))`, `duffel._CLIENT = None`, `duffel.TOKEN = "duffel_test_fake"`. The Google helper reads `duffel.TRANSPORT` and builds a client per call, so there is nothing else to reset.

### 10.4 `tests/test_workflow.py`
**Fixture:**
- `env = await WorkflowEnvironment.start_local()`. This is a real dev server: it supports Update-with-Start, follows the same path as `chat.py`, and needs no Rosetta.
- `client = Client(**{**env.client.config(), "plugins": [StrandsPlugin(models=MOCKS)]})`.
- `Worker(client, task_queue=..., workflows=[ConciergeWorkflow], activities=MOCK_ACTIVITIES)`, built directly **without** `deployment_config`.
- Messages are sent with Update-with-Start, exactly like the CLI.

The expected trip total is 412.30 + 465.00 + 18 + 55 = **950.30 USD**.

Each test is added at the tag named in its row and only uses features that exist at that tag (§13). Later tags add rows or add cases to an `ActivityEnvironment` test. The one deliberate change of an existing expectation is v9's `OrderNotFound`, and it lands in the same commit as the behaviour change.

| Tag | Test | Steps | Assertions |
|---|---|---|---|
| v1 | `test_chat_is_a_durable_update` | two `chat` Update-with-Starts on one id; then `Replayer` on the history | same run; two replies from the scripted concierge; `state().turns == 2`; history has `invoke_model` × 2; replay passes |
| v1 | `test_chat_validator` | empty text; then `end_chat` and another message | both rejected by the validator, nothing written to history |
| v2 | `test_agents_as_tools_wiring` | concierge calls `flight_agent` → specialist calls `search_flights` → specialist text → concierge `ConciergeReply` | history has `invoke_model` × 4, `search_flights` × 1, `price_quote` × 1 |
| v2 | `test_proposal_is_priced_by_activity` | one concierge turn with `PROPOSAL` | `quote.totals == {"USD": Decimal("950.30")}`, `over_budget is False`, `travellers == ["Ada Lovelace"]`, two activity line items `mock=True`; every amount equals `PRICES`, none comes from the model |
| v2 | `test_price_quote_problems` (ActivityEnvironment + `FakeDuffel`) | real `price_quote` with a good proposal, then an unknown place id. **v4 adds** a changed flight price against `expected`; **v5 adds** an offer that already has a cancelled order | first returns `problems == []` and the 950.30 USD total; then "place … unavailable", "price changed" and "already used" respectively, each **without** raising |
| v3 | `test_permission_granted_resumes_turn` | concierge calls `locate_user` (`{"ip": "6.6.6.6"}`); `chat` while pending; `grant_permission(granted, USER_IP)`. Second case: one model message with two `locate_user` toolUses | 1st reply has `permission_request` (id starts `v1:before_tool_call:t`), no `locate_user` scheduled; the pending `chat` is rejected; the grant reply is the finished turn; mock `locate_user` got `[USER_IP]`; `state()` contains no IP; the IP is in no `invoke_model` input (`model.seen`). Two-call case: one prompt, `interrupt_ids` has 2 ids, both calls run with `USER_IP` |
| v3 | `test_permission_denied_asks_for_city` | as above, then `grant_permission(denied)`; a later turn calls `locate_user` again | no `locate_user` scheduled, ever; `model.seen` has an error tool result containing `DENIED_MSG`; the later turn gets no `permission_request` |
| v3 | `test_permission_asked_once` | grant; a later turn calls `locate_user`; `set_permission(undecided)`; a later call | the 2nd call pauses with `prompt=None` (no question); answering with an IP runs it; after reset the next call prompts again. Validators reject: an answer with nothing pending; a stale `interrupt_id`; `granted` without `client_ip`; `client_ip` with `denied`; `client_ip="10.0.0.1"`; `set_permission` while a request is pending |
| v3 | `test_failed_resume_rebuilds_agent` | `locate_user` pauses; set `MockModel.FAIL`; grant | grant fails with `TurnFailed`; workflow still running; `state()`: `pending_permission is None`, `permissions == {"ip_location": "granted"}`; clear `FAIL`; resending the message pauses with `prompt=None` (no question) and completes; replay passes |
| v3 | `test_locate_user_refuses_non_public_ip` (ActivityEnvironment) | real `locate_user("")` and `locate_user("10.0.0.1")` with a `MockTransport` that fails on any request | both raise non-retryable `GeoUnavailable`; no HTTP request made |
| v4 | `test_price_change_on_approve_returns_to_chat` | chat → `PRICES["off_fake_zz"] = Decimal("431.10")` → approve | outcome `cancelled`, `failed_step == "price_quote"`, error contains `412.30 USD → 431.10 USD`; no `book_*`; next chat's prompt starts with `[system: quote q1 cancelled; failed_step=price_quote` |
| v4 | `test_reject_and_stale_quote` | two proposals (`q1`, `q2`); approve `q1`; then reject `q2` | `q1` rejected by the validator (`stale quote q1; current is q2`); reject gives `cancelled`, nothing booked |
| v4 | `test_approval_timeout` | start with `ConciergeInput(approval_timeout_s=2)`, chat, poll `state` | `status == "cancelled"`, `last_outcome.error == "approval timed out"` |
| v4 | `test_approve_blocked_by_pending_permission` | get `q1`; next message triggers `locate_user`; approve `q1` while the request is pending; answer it | approve rejected by the validator; quote not cancelled by the timer while pending (`approval_timeout_s=2`, wait 3 s) |
| v5 | `test_happy_path` | chat → approve | approve returns `confirmed` with `ord_`/`bok_`/`ACT-`/`PY-` refs; provider activities in order `price_quote, price_quote, book_flight, book_hotel, book_activities, charge_payment`; mock `book_flight` called once, no `cancel_*` |
| v5 | `test_activities_failure_compensates_hotel_and_flight` | chat `"Plan Lisbon [fail:activities]"` → approve | `compensated`, `failed_step == "book_activities"`, `compensations == ["cancel_activities","cancel_hotel","cancel_flight"]`; mock `cancel_hotel` got `ref="bok_fake"` and `cancel_flight` got `ref="ord_fake"`; no `charge_payment`; next chat's prompt starts with `[system: quote q1 compensated` |
| v5 | `test_retry_does_not_double_book` (ActivityEnvironment + `FakeDuffel`) | `book_flight` twice with the same key; `cancel_flight` twice; then `create_order` again on the cancelled offer | same `ord_` id and 1 `POST /air/orders` for the pair; 1 confirmed cancellation, the second cancel is a no-op; the re-book raises non-retryable `offer_request_already_booked` (v9 changes this case to `OrderNotFound`) |
| v6 | `test_continue_as_new_keeps_conversation` | dev server started with a low CAN threshold (§12 Q21); grant location, get `q1`, reject; chat until CAN; chat again | a new run id; `messages` length carried; `permissions == {"ip_location": "granted"}` and no prompt in the new run; next quote is `q2`; the new run's decoded history contains no `USER_IP`; `Replayer` passes on both runs |
| v7 | `test_agentcore_shell.py` × 2 | `run_worker()` with `TEMPORAL_BUILD_ID` unset; `ActivityTracker.wait_until_idle(0.1)` with 0 then 1 in-flight activity | raises `missing env`; returns only once in-flight is 0 |
| v8 | `test_versioning.py::test_upgrade_on_continue_as_new` | two `Worker`s built directly with `WorkerDeploymentConfig` build ids `b1` and `b2` (`build_worker` reads `TEMPORAL_BUILD_ID` once at import), same code; current = `b1`; chat; set current = `b2`; chat | the 2nd turn runs on `b1`, then the workflow continues-as-new onto `b2` (`describe()` shows `b2`); messages carried. UNVERIFIED that `start_local()` supports this end to end (§12 Q3) |
| v9 | `test_inflight_order_is_found_not_duplicated` (ActivityEnvironment + `FakeDuffel`) | `FakeDuffel` answers the 1st `POST /air/orders` with 422 `order_creation_already_attempted` and makes the order visible on the 2nd lookup | `book_flight` returns that `ord_`; 1 POST; re-book of a cancelled offer now raises `OrderNotFound` |
| v9 | `test_cancel_waits_for_inflight_booking` (ActivityEnvironment + `FakeDuffel`) | `cancel_flight(ref=None, maybe_booked=True)` with the order appearing on the 3rd lookup; then `maybe_booked=False` with no order | 1st cancels that order; 2nd does a single lookup and returns |

- Helper: `provider_activities(handle)` collects `activity_task_scheduled_event_attributes.activity_type.name` from `handle.fetch_history_events()` and drops `invoke_model` entries.
- Run with `uv run pytest -q`. This never touches the network.
- The v3 workflow permission tests also run each scenario once with `max_cached_workflows=0`, so every Update replays from history (the eviction check from §5.5).

### 10.5 `tests/test_live.py` (opt-in: `uv run pytest -m live -q`)
One smoke test of the real providers. It drives the activities directly with `temporalio.testing.ActivityEnvironment`, with no LLM and no workflow.
- **Skip unless** `DUFFEL_ACCESS_TOKEN` starts with `duffel_test_` and `GOOGLE_PLACES_API_KEY` is set. The test sets `DUFFEL_TEST_HOTEL=1` so the hotel step always hits the documented test hotel.
- **Steps** (`d = today + 30`, a 3-night trip, so flight and hotel dates match):
  0. `locate_user("8.8.8.8")` (a public resolver IP, not a person's). Assert `city` is set and `airports` is non-empty with IATA codes (expect SFO/SJC/OAK). This answers the Duffel lat/lng questions in §12 Q20. Added at v3.
  1. `search_flights("JFK", "LIS", d, d+3)`. Assert at least one offer.
  2. `search_hotels("Lisbon", d, d+3)`. Assert `test_fallback` and a `rate_id`.
  3. `search_places("Lisbon", ["museums"])`. Assert at least one result.
  4. `price_quote` on the resulting proposal. Assert `problems == []`, and **record whether `totals` has one currency** (flight and hotel `total_currency` equal). Print the currencies, so the Stays currency question (§12 Q15) is answered on day one.
  5. `book_flight`, then `book_hotel`. **In `finally`**, `cancel_hotel` and `cancel_flight` with `ref` if known, otherwise by `idempotency_key` / `offer_id` lookup, so a half-finished run still cleans up.
  6. Assert `duffel.get_order(...)["cancelled_at"]` is set and `get_booking(...)["status"] == "cancelled"`.
- **Tags:** steps 1–4 arrive at v2, step 0 at v3, steps 5–6 at v5.
- **Cost:** about 11 Duffel calls, 3 Google Pro calls and 1 ipinfo call. Everything runs in Duffel test mode, so no money moves. The orders are paid from, and refunded to, the test balance.

---

## 11. Demo script

**Prep:**
- **Local:** three terminals (§8.3), the Temporal UI at `http://localhost:8233`, and the **Duffel dashboard in test mode** (Orders and Stays bookings) in a browser tab.
- **AgentCore:** the Cloud UI plus `agentcore logs` tailing. **Rehearse one cold-start turn** (drain, then send a message) before presenting.
- **Numbers vary on every run:**
  - Duffel Airways test fares and schedules are not realistic.
  - Hotels may be the Duffel Test Hotel fallback.
  - Activity prices are the mock rate card.
- Approve within ~15 min of a quote, or show the re-check instead (D3).
- **Check the Duffel test balance** in the dashboard before presenting. Every A/B run debits it (refunded on cancel), and a drained balance fails Demo A with `insufficient_balance`. Whether the dashboard lets you top it up is UNVERIFIED.
- Traveller details are the fixed fictional profiles (Ada Lovelace, Alan Turing). The CLI prints them on the quote, so the audience isn't surprised to see them in the Duffel dashboard.
- If a real city returns no hotels during rehearsal, restart the worker with `DUFFEL_TEST_HOTEL=1`.
- **Location:** a VPN or conference Wi-Fi moves the IP-estimated city (often to the VPN exit or the carrier's hub). Rehearse on the venue network. If the city is wrong, that is a good moment to say why the concierge states its assumption.

### A. Happy path with the permission moment (about 3–4 min)
1. `uv run python chat.py`: **"Plan a four-day trip to Lisbon in March under $3K"**. Any city works; Lisbon is just the example. The message names no origin.
2. **Permission moment.** The CLI prints *"Allow the concierge to estimate your location from your public IP? Your IP is read from api.ipify.org and sent once to ipinfo.io to find your city. [y/N]"*.
   - Before answering, show the UI: the turn stopped after the concierge's first `invoke_model`, and there is **no** `locate_user` activity. The Update returned with a pending request. The workflow is just waiting; on AgentCore no compute is held.
   - Make the point: the model asked for the tool, and workflow code (the hook) decided to pause. The LLM can't skip the gate.
   - Answer **y**. Only now does the laptop call ipify. The `grant_permission` Update resumes the same tool call, `locate_user` runs, and the concierge says "Looks like you're near <city>, so I searched from <IATA>…".
   - In the UI, the `grant_permission` Update and the `locate_user` input are the only two places the IP appears. The result holds only the city and airports.
   - Optional: `/permissions` prints `ip_location: granted`. Later turns never ask again, even after continue-as-new.
   - Variant (about 30 s, new conversation): answer **N**. No network call happens, `locate_user` never runs, and the concierge asks "Which city are you flying from?".
3. In the UI, open `concierge-<id>` and point out:
   - `invoke_model` activities labelled `concierge`, `flight_agent`, `hotel_agent`, `itinerary_agent` and `budget_policy_agent`: agents-as-tools, with every LLM call durable;
   - the `search_flights`, `search_hotels` and `search_places` activities, which hold real Duffel and Google payloads;
   - **`price_quote`** after the concierge's last model call.
4. The CLI shows quote `q1`:
   - a Duffel Airways flight, a hotel and 2–3 real places marked "(mock price)";
   - the total per currency (one line if flight and hotel share a currency), the test traveller names, and the offer expiry.
   - Point out that the amounts came from `price_quote` re-fetching the offers, not from the model.
5. Refine: **"Switch to a cheaper hotel."** You get a new quote, `q2`. Show the stale-quote validator rejection: `temporal workflow update execute -w concierge-<id> --name approve --input '{"quote_id":"q1","approved":true}'` prints `stale quote q1; current is q2`, and nothing is written to history.
6. `/approve`.
   - The UI shows `price_quote (re-check)`, then `book_flight`, `book_hotel`, `book_activities` and `charge_payment`.
   - The CLI prints `confirmed` with an `ord_…` and a `bok_…`.
   - Show both in the Duffel dashboard (test mode). The workflow completes, and the CLI moves to a new conversation id.

### B. Compensation path: `[fail:activities]` (primary, about 2 min)
1. New conversation: **"Plan a four-day trip from London to Lisbon in March [fail:activities]"**, then `/approve`. Naming the origin skips the permission prompt.
2. In the UI:
   - `book_flight` ✓ and `book_hotel` ✓: two **real** Duffel test bookings.
   - `book_activities` ✗ with `ActivitySoldOut`, non-retryable, 1 attempt.
   - Then `cancel_activities`, `cancel_hotel` and `cancel_flight`, in LIFO order.
3. In the CLI: `compensated`, failed step `book_activities`, compensations `cancel_activities, cancel_hotel, cancel_flight`. No payment was taken.
4. In the Duffel dashboard, the order shows as cancelled (refunded to the balance) and the stay booking shows as cancelled.
5. **"Book it again without activities."** The concierge is told what failed through the system note. The prompt makes it **search again** after any non-confirmed outcome, because the old offer request is spent. If the model reused the old `off_` anyway, `price_quote` would report "flight offer already used" and nothing would be booked. It proposes `q2`. `/approve` → `confirmed`.

**B′ (simpler):** `[fail:hotel]` gives `book_flight` ✓, `book_hotel` ✗, then `cancel_hotel` (a no-op) and `cancel_flight`.

### C. Durability and retries (optional, local)
1. Restart the worker with `FLAKY_STEP=hotel BOOKING_DELAY_S=5`.
2. `/approve` a quote. During `book_hotel`, **hard-kill** the worker with `kill -9 <pid>` or by closing the terminal. Ctrl+C is a graceful shutdown, which would just let the activity finish.
3. Restart it. Within about 15 s (the heartbeat timeout), `book_hotel` is retried on the new worker and succeeds on a later attempt; 5 are allowed.
4. Nothing is double-booked:
   - the flight booking is protected by the `offer_id` lookup;
   - the kill happens during the pre-call delay, so no hotel booking had started;
   - the mock refs are deterministic.

### D. Reject, timeout and re-check (optional)
1. `/reject too pricey` → `cancelled`, with no bookings in history.
2. `chat.py --approval-timeout 60`. Get a quote, wait 60 s, then `/status` → `cancelled`, "approval timed out".
3. **Re-check (price change):**
   1. Say **"Fly LHR to STN next week, 2 nights"**. `LHR→STN` is Duffel Airways' price-change route (VERIFIED in the docs).
   2. `/approve` → `cancelled`, `failed_step=price_quote` ("flight price changed …"), and nothing is booked. It is UNVERIFIED whether the change already appears on the first `price_quote`; if so, the proposal itself comes back with the problem note, which shows the same mechanism.
   3. The next message makes the concierge search again.
   - Footnote: the expiry path needs a 15–30 min wait. With `--approval-timeout 3600`, the timer fires at `expires_at` and the quote is cancelled with "offer expired".

### E. The same code on AgentCore
1. Use the same CLI with `TEMPORAL_*` pointing at Cloud. `git diff --stat v6-long-conversations v7-agentcore -- travel/` and `git diff --stat v7-agentcore v8-versioned-redeploy -- travel/` are both empty: only env vars and the `agentcore/agentcore.json` env values differ. Don't show that file's diff, because it holds the keys.
2. The first message makes the WCI invoke the runtime ("worker starting" in `agentcore logs`). The worker calls Duffel and Google over PUBLIC egress, and the workflow history looks identical to local. The permission prompt still appears on the laptop, and the located city is the **laptop's**, not us-west-2's: the worker only geolocates the IP the client sent.
3. Leave the approval pending for longer than `AGENTCORE_DEBOUNCE_SECONDS`. The logs show the worker draining (scale to zero).
4. `/approve` → the runtime is invoked again (the CLI may print "waiting for a worker…"). The workflow replays, the re-check runs, and the saga completes. A flight offer that expired while the worker was scaled to zero takes the D3 path instead.

### F. Real provider failure (optional; deterministic Duffel Airways test routes)
- Say **"Fly LGW to STN next week, 2 nights"**, using IATA codes (city names resolve to `LON` and skip the scenario). Hotel and places searches target Stansted, which is harmless.
- On `LGW→STN`, Duffel returns `insufficient_balance` on order creation (VERIFIED in the docs). `book_flight` fails non-retryably on attempt 1 with type `insufficient_balance`, and `cancel_flight` is a no-op. This shows a real provider failure surfacing in Temporal, not a multi-step rollback (that is Demo B). No `[fail:…]` tag is needed.
- UNVERIFIED: whether the scenario triggers on a round-trip search.

---

## 12. Open questions and UNVERIFIED assumptions

1. **Agents-as-tools with real Bedrock.** Verified only with a fake model (temporalio 1.34.0, strands 1.57.2).
   - UNVERIFIED: whether Sonnet 4.6 thinking/streaming events serialize through `invoke_model`.
   - UNVERIFIED: whether parallel sub-agent calls under `ConcurrentToolExecutor` replay deterministically.
   - Fallback: put `tool_executor=SequentialToolExecutor()` (`strands.tools.executors`) on the concierge.
2. **Same-sub-agent concurrency.** A second parallel call to one sub-agent returns "already processing a request". This was read in the source but not run. The prompt mitigates it.
3. **Redeploy mechanics.**
   - UNVERIFIED: whether one `agentcore deploy` can both create runtime version N and an endpoint pointing at N. Fallback: deploy twice (code and env first, then the endpoint entry).
   - Upgrade-on-CAN is experimental/Public Preview. `test_versioning.py` (v8) covers it with two in-process builds on the dev server. UNVERIFIED: that `WorkflowEnvironment.start_local()` reports `is_target_worker_deployment_version_changed()` end to end. If it doesn't, v8 replaces the test with a scripted manual check in the README and says so in the tag message.
4. **Serverless wake-up.**
   - Updates and Signals create workflow tasks and should trigger the WCI. This has not been demonstrated with Update-driven workflows.
   - AgentCore cold-start latency is undocumented.
   - Mitigation: the CLI retries `WorkflowUpdateRPCTimeoutOrCancelledError` with the same update id; rehearse it.
   - Queries likely do not wake a drained worker.
5. **AgentCore Pre-release access** for the namespace. The UI and CLI flags may change.
6. **Bedrock permissions of the auto-created execution role.** The sample relies on them (default `BedrockModel()`, no extra policy). If calls are denied:
   - Add a root-level `bedrock-policy.json` to `additionalPolicies`, allowing `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` on:
     - `arn:aws:bedrock:us-west-2:<acct>:inference-profile/global.anthropic.*`
     - `arn:aws:bedrock:us-west-2::foundation-model/anthropic.*`
     - `arn:aws:bedrock:::foundation-model/anthropic.*` (global cross-region inference)
   - An org region-deny SCP must allow `aws:RequestedRegion = unspecified`.
7. **Model availability.** `global.anthropic.claude-sonnet-4-6` is Strands' default, and access is per account and region. `claude-sonnet-5-5` is untested with the plugin. Haiku 4.5 is in its EOL window.
8. **Strands exception surface.** Resolved by experiment (fake model): a failed first model call reaches `_turn` as a bare `ActivityError`; a model call that fails after a tool result arrives as `strands.types.exceptions.EventLoopException` with `original_exception` an `ActivityError`. `_turn` handles both and re-raises any other `EventLoopException` (§4.5). UNVERIFIED with real Bedrock errors, which should take the same path.
9. **CodeZip install of `travel/`.** Imports work because the entrypoint runs from the zip root. UNVERIFIED: how the CDK/uv build treats `[tool.hatch.build.targets.wheel] packages = ["travel"]` compared with the sample's `["."]`.
10. **`agentcore dev`** exists (default port 8080), but whether it honours `envVars` is UNVERIFIED. Use `python agentcore_worker.py` + `curl` instead.
11. **Duffel Stays access (blocker; start early).**
    - Stays must be requested from Duffel through a contact form before test tokens can use it (VERIFIED in the getting-started guide; public projects report waiting on it). The lead time is unknown.
    - If access is not granted in time, the only lean fallback is a temporary hotel mock shaped like `activity_provider.py`. That would break decision 2, so it needs a user decision; it is not built by default.
    - **Co-blocker:** the Stays currency (Q15). It no longer blocks quoting (totals are per currency), but it decides whether the budget can ever be compared.
12. **Stays test inventory.**
    - Documented: the Duffel Test Hotel, whose rooms are named after scenarios such as "Successful Booking with Balance" and "Rate Unavailable at Booking".
    - UNVERIFIED: whether test searches for real cities return hotels. The test-hotel fallback (§5.4) covers the gap.
    - UNVERIFIED: the full scenario list and the exact room-name strings.
13. **Stays booking details.**
    - VERIFIED (bookings docs, 2026-10-02): `GET /stays/bookings` exists with only `after`/`before`/`limit` (1–200)/`user_id`; bookings expose `metadata` but no quote id; `POST /stays/bookings` takes `quote_id`, `email`, `phone_number`, `guests` (`given_name`, `family_name`), and optional `metadata`, `payment` (omit for balance), `users`, `loyalty_programme_account_number`, `accommodation_special_requests`. No `born_on`. Hence `booking_for_key` (§5.4).
    - UNVERIFIED: an idempotency key on `POST /stays/bookings` (none documented), and whether a second booking of the same quote is rejected.
    - UNVERIFIED: the list's sort order (we scan all pages) and whether `metadata` round-trips on the list response.
    - UNVERIFIED: the behaviour of cancelling an already-cancelled booking. We GET first, and treat a 4xx followed by `status == "cancelled"` as success.
    - UNVERIFIED: whether a fresh `POST /stays/quotes` is the right re-check, and whether the quote carries `guests` (the guest-count check tolerates its absence).
    - UNVERIFIED: the common headers for Stays. We assume the same as Flights.
14. **Offer expiry compared with approval.**
    - Duffel offers expire per `expires_at`, typically 15–30 min. The approval timeout default is 900 s.
    - The approval timer fires at the flight offer's `expires_at` ("offer expired"); the re-check on approve catches anything else, including a long AgentCore cold start.
    - UNVERIFIED: which exact status and code `GET /air/offers/{id}` returns for an expired offer. We treat any non-retryable 4xx as "gone".
    - The pending order-cancellation object has its own `expires_at`. We confirm it right away.
15. **Currency.**
    - Duffel flight amounts are in the org billing currency.
    - UNVERIFIED: whether a test org can set its billing currency to USD.
    - UNVERIFIED: whether Stays amounts use the same currency. The live test prints both currencies (§10.5).
    - Decided: mixed currencies are allowed. Lines keep their own currency, `totals` sums per currency, and `over_budget` is `None` unless everything is USD. No conversion.
16. **Duffel Airways coverage and balance.**
    - UNVERIFIED: whether ZZ returns offers for every real airport pair and date. Widely observed, but not stated.
    - UNVERIFIED: the test balance amount, and whether it can be topped up. `insufficient_balance` is non-retryable and compensates. Check it before each demo (§11 Prep).
17. **`departing_at` time zone.** We assume it is local airport time for the red-eye flag (UNVERIFIED). If it turns out to be UTC, the flag is only approximate; it is advisory only.
18. **Google Places.**
    - VERIFIED: Text Search requires a field mask; the free usage for the Pro tier is 5,000 a month; the account needs a billing account.
    - VERIFIED (2026-10-02): `places.rating` triggers the Text Search **Enterprise** SKU, so it is not in our mask; `displayName`, `formattedAddress`, `types`, `primaryType` are Pro. Place Details `GET /v1/places/{id}` with `displayName,primaryType` is Place Details Pro.
    - UNVERIFIED: the exact HTTP status for an unknown place id (we treat any 4xx except 429 as "not found").
    - The exact `locationBias` radius field is not used, because we put the city in `textQuery` instead.
19. **Heartbeats and the in-flight window (v9, §6.4).** The `_hb` wrapper heartbeats every 5 s while a Duffel call (up to 130 s) is in flight, and cancels the HTTP task if the activity is cancelled. A worker killed mid-POST is retried, and the retry runs the lookup first. If Duffel is still creating the order, the retry's POST gets a duplicate-type 422 and `create_order` polls up to 120 s; compensations with no ref wait up to 140 s (§6.4, v9). UNVERIFIED: that `order_creation_already_attempted` is what Duffel returns while an earlier creation is still running, and that the order shows up in `GET /air/orders?offer_id=` once it completes.

20. **Permissioned tool (§5.5).**
    - A failed resume rebuilds the agent from the message snapshot (public `Agent(messages=...)` path, same as start and CAN). UNVERIFIED until v3: that constructing a new `TemporalAgent` mid-workflow replays cleanly (expected: construction does no I/O); `test_failed_resume_rebuilds_agent` proves it.
    - UNVERIFIED: ipinfo's `/json` with an empty IP describing the caller (documented semantics, not tested); irrelevant now that `locate_user` refuses non-public IPs.
    - UNVERIFIED: whether some httpx exception strings include the request URL; `locate_user` drops them (`from None`) either way.
    - UNVERIFIED: how long ipinfo's Legacy Free API keeps running (swap to ipwho.is), and its `bogon` field.
    - UNVERIFIED (docs only, no live call yet): Duffel `places/suggestions` with `lat`/`lng`/`rad` and no `query`. The result order, the count, and whether city objects appear are all undocumented. We filter to airports and sort by distance ourselves. The v3 live test step 0 settles it.
    - UNVERIFIED with real Bedrock: that Sonnet calls `locate_user` before asking for an origin, and calls it only once. Extra calls cost one silent IP round trip each.
    - Accepted: the model sees an `ip` parameter in the tool spec, and the hook ignores it.
21. **Forcing continue-as-new in tests.** `test_continue_as_new_keeps_conversation` starts the dev server with `dev_server_extra_args=["--dynamic-config-value", "limit.historyCount.suggestContinueAsNew=50"]`. UNVERIFIED: the key name and that the dev server honours it. Fallback: a test-only `ConciergeInput.max_turns_per_run: int | None = None` that also makes `_should_continue_as_new` true. It is one line, but it adds a field to production input, so it's used only if the dynamic config fails.

### Key references
- Strands plugin: https://docs.temporal.io/develop/python/integrations/strands-agents · https://github.com/temporalio/sdk-python/tree/main/temporalio/contrib/strands · https://python.temporal.io/temporalio.contrib.strands.TemporalAgent.html
- Samples: https://github.com/temporalio/samples-python/tree/main/strands_plugin (`continue_as_new`, `tools`, `structured_output`, `human_in_the_loop`) · https://github.com/temporalio/samples-python/tree/main/bedrock_agentcore/strands_agent
- Strands agents-as-tools: https://strandsagents.com/docs/user-guide/concepts/multi-agent/agents-as-tools/ · interrupts: https://strandsagents.com/docs/user-guide/concepts/interrupts/ (UNVERIFIED URL; the plugin README section "Human-in-the-loop interrupts" is the verified source)
- Location: https://www.ipify.org/ · https://ipinfo.io/developers · https://support.ipinfo.io/hc/en-us/articles/34121895556242-Legacy-Free-API-vs-IPinfo-Lite · https://duffel.com/docs/api/places/get-place-suggestions · https://duffel.com/docs/guides/finding-airports-within-an-area
- Saga: https://docs.temporal.io/design-patterns/saga-pattern · https://docs.temporal.io/develop/python/best-practices/error-handling#implement-saga-pattern
- Messages, approval, CAN: https://docs.temporal.io/develop/python/workflows/message-passing · https://docs.temporal.io/design-patterns/approval · https://docs.temporal.io/develop/python/workflows/continue-as-new
- Versioning: https://docs.temporal.io/production-deployment/worker-deployments/worker-versioning · https://docs.temporal.io/production-deployment/worker-deployments/worker-versioning/upgrade-on-continue-as-new
- Serverless Workers: https://docs.temporal.io/serverless-workers/agentcore · https://docs.temporal.io/production-deployment/worker-deployments/serverless-workers/agentcore · https://docs.temporal.io/guides/durable-agent-on-agentcore
- AgentCore: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agent-runtime-versioning.html · https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-settings.html · https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-long-run.html · https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-security-best-practices.html · https://aws.amazon.com/blogs/networking-and-content-delivery/network-connectivity-patterns-for-agents-deployed-on-amazon-bedrock-agentcore-runtime/
- Duffel Flights: https://duffel.com/docs/api/overview/making-requests · https://duffel.com/docs/api/overview/response-handling · https://duffel.com/docs/api/overview/test-mode · https://duffel.com/docs/api/v2/offer-requests · https://duffel.com/docs/api/v2/offers · https://duffel.com/docs/api/v2/orders · https://duffel.com/docs/api/v2/order-cancellations · https://duffel.com/docs/api/v2/places
- Duffel Stays: https://duffel.com/docs/api/v2/bookings · https://duffel.com/docs/guides/getting-started-with-stays · https://duffel.com/docs/guides/test-hotels · https://duffel.com/docs/api/v2/search/stays-search · https://duffel.com/docs/api/v2/quotes/create-quote · https://duffel.com/docs/api/v2/bookings/create-booking · https://duffel.com/docs/api/v2/bookings/cancel-booking
- Google Places (New): https://developers.google.com/maps/documentation/places/web-service/text-search · https://developers.google.com/maps/documentation/places/web-service/place-details · https://developers.google.com/maps/billing-and-pricing/pricing
- Client config: https://docs.temporal.io/references/client-environment-configuration
- Testing: https://docs.temporal.io/develop/python/best-practices/testing-suite

---

## 13. Build milestones (tagged history)

The repo is built as ten **annotated git tags**. Each tag adds one capability together with its tests, and the suite is green at every commit. `git diff <prev>..<tag>` tells that capability's story. The sections above describe the final state (`v9-hardening`). This section says which part of it lands where.

### 13.1 Commit and tag conventions
- **Conventional commits** with a scope. Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`. Scopes: `models`, `workflow`, `agents`, `activities`, `duffel`, `cli`, `worker`, `agentcore`, `readme`, `spec`, `deps`. A test commit uses the scope of the code it tests (`test(workflow): …`).
- **Several small commits per tag** (typically 3–6), in this order: `refactor` (pure moves or extracts, such as v3's `_turn`) → `feat` commits (models → provider client/activities → workflow/agents → CLI) → `docs(readme)` (the walkthrough row).
- **Each `feat` commit includes the tests for what it adds.** A `test(<scope>)` commit only adds tests for behaviour that already exists.
- **Every commit is runnable.** `uv run pytest -q` is green, and `uv run python -c "import travel.worker, chat"` succeeds.
- **No cross-tag fixups.** Before tagging, fold review fixes into the tag's own commits (`git commit --fixup` + `git rebase -i --autosquash` on the unpushed range). Once a tag is cut it is never moved. A bug found later is fixed forward with a `fix(<scope>)` commit in the current tag, and its tag message mentions it.
- **Annotated tags with summary messages:** `git tag -a v3-permissioned-tool -F .git/TAG_MSG`. Message format:
  ```
  v3-permissioned-tool: locate_user behind a deterministic permission gate

  Adds: locate_user (ipinfo + Duffel nearby airports), PermissionHook + Strands interrupt, grant_permission and
        set_permission Updates, CLI [y/N] prompt (ipify only after yes), /permissions.
  Tests: test_permission_granted_resumes_turn, test_permission_denied_asks_for_city, test_permission_asked_once,
         test_failed_resume_rebuilds_agent, test_locate_user_refuses_non_public_ip.
  Demo: uv run python chat.py → "Plan a trip to Lisbon in March" → answer y / N.
  ```
- **Check every tag before pushing:**
  ```bash
  for t in $(git tag -l 'v*' --sort=creatordate); do
    git checkout -q "$t" || { echo "FAIL $t checkout"; continue; }
    uv sync --locked -q && uv run pytest -q; rc=$?
    [ "$t" = v0-spec ] && [ $rc -eq 5 ] && rc=0                       # v0: "no tests collected"
    [ "$t" = v0-spec ] || uv run python -c "import travel.worker, chat" || rc=1
    [ $rc -eq 0 ] || echo "FAIL $t"
  done; git checkout -q main
  ```

### 13.2 Tags

**`v0-spec`: the plan**
- Files: `SPEC.md`, `README.md` (title, prerequisites, an empty walkthrough table), `pyproject.toml` (deps from §3 except `bedrock-agentcore`, which arrives at v7), `uv.lock`, `.gitignore`.
- Tests: none. Check with `uv sync && uv run python -c "import temporalio.contrib.strands, strands"`.
- Diff shows: the spec and the toolchain only.

**`v1-durable-chat`: one durable agent behind a chat Update**
- Files: `travel/__init__.py`; `travel/models.py` (`Status = Literal["chatting"]`, `ConciergeInput(messages)`, `ChatRequest`, `ChatResponse(message, status)`, `ConversationState(status, turns)`); `travel/agents.py` (`LLM` options, a short `CONCIERGE_PROMPT` with no tools, `build_agents(messages, today)`); `travel/workflow.py` (`chat` Update with lock, snapshot, `TurnFailed` (catching `EventLoopException` too), validator; `state`; `end_chat`; `run` waits for `_done`); `travel/activities.py` (`ALL_ACTIVITIES: list = []`, filled from v2); `travel/worker.py` (complete §8.1); `chat.py` (`send` with the retry loop, `/status`, `/quit`); `tests/mock_model.py`; `tests/test_workflow.py`.
- **Everything v7 needs is in v1:** `connect()` via `ClientConfig.load_client_connect_config()` (an API key turns TLS on), `build_worker(client, interceptors=())`, `TASK_QUEUE`, and `PINNED` versioning with `TEMPORAL_BUILD_ID` (default `local`). Serverless Workers require versioning and v7 must not touch `travel/`. The local run needs `set-current-version` from here on (§8.3).
- Tests: `test_chat_is_a_durable_update`, `test_chat_validator`.
- Demo: §8.3 terminals 1–3, then chat; only AWS creds are needed until v2. Kill the worker during a reply and restart it: the Update completes once the lost `invoke_model` attempt times out (up to 180 s, no heartbeat) and retries, while the CLI prints "waiting for a worker…". The UI shows `invoke_model` activities.
- Diff shows: `StrandsPlugin` on the client, `TemporalAgent`, and an Update-with-Start chat. No tools yet.

**`v2-agents-as-tools`: specialists, real search and deterministic pricing**
- Files: `travel/duffel.py` (client, `_req`, `place`, `search_flights`, `get_offer`, `search_stays`, `fetch_rates`, `create_stays_quote`); `travel/activity_provider.py` (`RATE_CARD`, `quote`); `travel/activities.py` (`search_*`, `_google`, `price_quote` without `expected` or the spent check); `travel/agents.py` (four specialists via `as_tool`, `check_budget`/`check_policy`, all §5.3 prompts; the concierge **asks** for the origin, since there is no default airport); `travel/models.py` (`TripProposal`, `ConciergeReply`, `LineItem`, `PriceRequest(proposal)`, `PricedTrip`, `Quote` without `simulate_failure`, `TEST_TRAVELLERS`, `ChatResponse.quote`); `travel/workflow.py` (structured output, `price_quote` after a proposal, `_make_quote`, problem note); `chat.py` (quote display, without "/approve"); `tests/fake_duffel.py` (`GET /air/offers/{id}`, `POST /stays/quotes`, Google `GET places/{id}`); mocked activities; `tests/test_live.py` steps 1–4; README provider accounts.
- Tests: `test_agents_as_tools_wiring`, `test_proposal_is_priced_by_activity`, `test_price_quote_problems` (good proposal and unknown place).
- Demo: "Plan a four-day trip from New York to Lisbon in March under $3K" → quote `q1` with real offers. The UI shows the agent tree and `price_quote`.
- Diff shows: agents-as-tools wiring and the "LLM proposes IDs, an activity prices them" rule.

**`v3-permissioned-tool`: `locate_user` behind a deterministic permission gate**
- Files: `travel/models.py` (`Decision`, `PermissionRequest`, `PermissionAnswer`, `PermissionSetting`, `is_public_ip`, `ChatResponse.permission_request`, `ConciergeInput.permissions`, `ConversationState.permissions`/`pending_permission`); `travel/duffel.py` (`airports_near`); `travel/activities.py` (`locate_user` with the public-IP guard); `travel/agents.py` (`PermissionHook`, `PROMPT`/`DENIED_MSG`, `locate_user` on the concierge, origin rule in the prompt); `travel/workflow.py` (`refactor`: extract `_turn`, with `_turn_ctx = (snapshot, note)` and no `outcome=` in its return; then `_pending`, `_grant_ip`, `grant_permission`/`set_permission` + validators, `_check_answer`/`_check_setting`, `_build` rebuild on a failed turn, `chat` pending checks); `chat.py` (`answer`, `fetch_public_ip`, pending-request recovery, `/permissions`); `tests/mock_model.py` (`FAIL`); mocked `locate_user`; `test_live.py` step 0; README `IPINFO_TOKEN`.
- Needs nothing from v4+. `chat` and the validators test `_pending` directly; `_turn_open()` arrives with approval in v4.
- Tests: `test_permission_granted_resumes_turn`, `test_permission_denied_asks_for_city`, `test_permission_asked_once`, `test_failed_resume_rebuilds_agent`, `test_locate_user_refuses_non_public_ip`.
- Demo: §11 A step 2 (y, then N in a new conversation).
- Diff shows: a ~15-line hook, one Update, and no LLM in the enforcement path.

**`v4-human-approval`: approve/reject, timeout and re-price; nothing is booked yet**
- Files: `travel/models.py` (`Status` + `awaiting_approval`, `booking`, `confirmed`, `cancelled`; `ApprovalDecision`; `BookingOutcome(quote_id, status, failed_step, error)`; `ChatResponse.outcome`; `ConciergeInput.approval_timeout_s`; `PriceRequest.expected`); `travel/activities.py` (`expected` comparison in `price_quote`); `travel/workflow.py` (`_turn_open()`; `approve` + validator using it; the timer with offer expiry, paused while a turn is open; `_recheck_and_book`, `_finish`, `[system: …]` notes, so `_turn_ctx` gains `reported` and `_turn` returns `outcome=`; and `_book` as a **stub** that returns `BookingOutcome(quote_id, status="confirmed")` with every ref `None`); `chat.py` (`/approve`, `/reject`, `--approval-timeout`, outcome display printing "confirmed (dry run: nothing booked)" when `flight_ref is None`, and the new conversation id after `confirmed`).
- Why it makes sense before the saga: the gate, stale-quote rejection, timeout and re-price are complete and testable on their own. The stub makes it obvious that nothing is booked.
- Tests: `test_price_change_on_approve_returns_to_chat`, `test_reject_and_stale_quote`, `test_approval_timeout`, `test_approve_blocked_by_pending_permission`, plus the "price changed" case in `test_price_quote_problems`.
- Demo: §11 A steps 1–6 ending in "confirmed (dry run)"; §11 D1–D3.
- Diff shows: an Update-based approval gate in `run()`, outside any LLM tool.

**`v5-booking-saga`: real test bookings with LIFO compensation**
- Files: `travel/duffel.py` (`orders_for_offer`, `order_for_offer`, core `create_order` with lookup first, `cancel_order`, `booking_for_key`, `create_booking`, `get_order`, `get_booking`, `cancel_booking`); `travel/activity_provider.py` (`book`, `cancel`); `travel/activities.py` (`book_*`/`cancel_*` with a single lookup, mock charge/refund, `_maybe_fail`, `_delay`, `_hb`, the spent-offer check in `price_quote`); `travel/models.py` (`FailStep`, `Status` + `compensated`, `BookRequest`, `CancelRequest` without `maybe_booked`, `ChargeRequest`, refs and compensations on `BookingOutcome`, `Quote.simulate_failure`); `travel/workflow.py` (`FWD`/`COMP` (60 s), `FAIL_TAG`, `_fail_next`, `_book` saga replacing the stub); concierge prompt (search again after any non-confirmed outcome); `chat.py` (refs, compensations; the dry-run branch removed); `tests/fake_duffel.py` (+ `GET/POST /air/orders`, `GET /air/orders/{id}`, cancellations, single-use 422); `test_live.py` steps 5–6.
- Tests: `test_happy_path`, `test_activities_failure_compensates_hotel_and_flight`, `test_retry_does_not_double_book`, plus the "already used" case.
- Demo: §11 A step 6 for real, B, B′, C and F.
- Diff shows: the stub replaced by §6.2's saga, and compensations registered before each forward step.

**`v6-long-conversations`: continue-as-new**
- Files: `travel/workflow.py` (`RESTING`, `_should_continue_as_new` with `is_continue_as_new_suggested() or is_target_worker_deployment_version_changed()`, drain + re-check, CAN input, `initial_versioning_behavior=AUTO_UPGRADE` when the version changed), `travel/models.py` (`ConciergeInput.quote_seq`).
- The upgrade trigger lands here, not in v8, because the *old* build's code must notice the version change: build 1.0.0 is deployed at v7 with v6 code. Tests run without versioning, so the flag is always false there and no v6 test depends on it.
- The CAN condition includes `not self._turn_open()` (no turn running **and no permission pending**), and the input carries `permissions`, so "ask once" holds across runs.
- Tests: `test_continue_as_new_keeps_conversation`.
- Demo: in the UI, the conversation chain continues under the same workflow id with a new run id. The next turn neither re-prompts nor reuses a quote id.
- Diff shows: ~30 lines in `workflow.py`.

**`v7-agentcore`: the same code on AgentCore**
- Files: `agentcore_worker.py`, `agentcore/agentcore.json` (placeholders, endpoint `b1_0_0`), `agentcore/aws-targets.json`, `bin/create-runtime.sh`, `bin/mk-invoke-role.sh`, `iam-role-for-temporal-agentcore-invoke.yaml`, `pyproject.toml` (+ `bedrock-agentcore`) and `uv.lock`, `.gitignore` (+ `agentcore/cdk/`, `agentcore/.cache/`), README §9.3, `tests/test_agentcore_shell.py`.
- **Zero changes under `travel/`.** `git diff --stat v6-long-conversations v7-agentcore -- travel/` prints nothing. The README shows that command, and it is Demo E's proof.
- Tests: the two shell tests.
- Demo: §9.3, then §11 E.

**`v8-versioned-redeploy`: a named endpoint per build, and upgrade on continue-as-new**
- Files: config, docs and tests only: `agentcore/agentcore.json` (build id `1.1.0`, endpoint `b1_1_0` added next to `b1_0_0`; placeholders only), README §9.4, `tests/test_versioning.py`.
- **Zero changes under `travel/`.** `git diff --stat v7-agentcore v8-versioned-redeploy -- travel/` prints nothing; the upgrade-on-CAN code has been there since v6.
- Why it's separate from v7: v7 deploys one immutable build. v8 is about changing code under open conversations without non-determinism errors.
- Tests: `test_upgrade_on_continue_as_new`.
- Demo: start a conversation on 1.0.0, deploy 1.1.0 (§9.4), `set-current-version`, and send one message. The turn finishes on 1.0.0, then the workflow continues-as-new onto 1.1.0.

**`v9-hardening`: the in-flight Duffel window**
- Files: §6.4 exactly: `travel/duffel.py` (`POLL_S`, `DUPLICATE`, the polling `create_order`), `travel/activities.py` (`_find`, waits in `cancel_*`), `travel/models.py` (`CancelRequest.maybe_booked`), `travel/workflow.py` (`definite`, `COMP` 200 s), `tests/fake_duffel.py` (in-flight mode, `POLL_S = 0`).
- The core v5 saga keeps only the cheap lookup-before-POST and lookup-before-cancel.
- Tests: `test_inflight_order_is_found_not_duplicated`, `test_cancel_waits_for_inflight_booking`. `test_retry_does_not_double_book`'s re-book case now expects `OrderNotFound`, in the same commit as the change.
- Demo: none new. The tag message explains the 130 s Duffel window.
- Diff shows: robustness only. There is no new user-visible behaviour, which is the point.

### 13.3 Ordering checks (each tag builds only on earlier ones)
- **v1** imports no v2 models: `ChatResponse` has no `quote`, and the agent has no tools or structured output.
- **v2** quotes are never approvable. Status stays `chatting`, and the CLI prints no "/approve".
- **v1** already contains `travel/activities.py` (empty `ALL_ACTIVITIES`), so `import travel.worker` works at every commit.
- **v3** depends only on v1–v2. `ConciergeInput.permissions` is a start input here and is first *carried* by v6. `_turn_ctx` has no `reported` and `_turn` no `outcome=` until v4.
- **v4**'s `_book` stub returns `confirmed` with no refs, and the CLI labels it a dry run. Approval, timeout and re-check don't need the saga to be meaningful. v4 is also the first tag where a conversation can end (`confirmed`), so the CLI's new-conversation switch arrives here.
- **v5** needs v4's re-checked line items. The in-flight polling and `maybe_booked` are deliberately left to v9.
- **v6** is the first tag where CAN can happen. No earlier test depends on it, and earlier tags simply never continue-as-new (fine at demo history sizes).
- **v6** also carries the version-changed trigger, because 1.0.0 (deployed at v7) must contain it for v8's upgrade to work.
- **v7** changes nothing under `travel/`. `connect()`, `build_worker(interceptors=)`, `TASK_QUEUE` and `PINNED` versioning have been in `travel/worker.py` since v1 for that reason.
- **v8** changes nothing under `travel/` either.
- **v9** changes no public behaviour except `OrderNotFound` on a spent offer.

### 13.4 README walkthrough table
The README carries this table, filled in tag by tag, each in its tag's `docs(readme)` commit.

| Tag | What you see | Command |
|---|---|---|
| `v0-spec` | the spec and a toolchain that installs | `uv sync` |
| `v1-durable-chat` | a chat reply that survives a worker kill; `invoke_model` in the UI | `uv run python -m travel.worker` · `uv run python chat.py` |
| `v2-agents-as-tools` | a real Duffel/Google quote priced by `price_quote`; the agent tree in the UI | `chat.py` → "Plan a four-day trip from New York to Lisbon in March under $3K" |
| `v3-permissioned-tool` | the `[y/N]` location prompt; no `locate_user` until yes; asked once | `chat.py` → "Plan a trip to Lisbon in March" → `y` (or `N`) · `/permissions` |
| `v4-human-approval` | `/approve` re-prices and returns "confirmed (dry run)"; stale, reject and timeout paths | `chat.py --approval-timeout 60` → `/approve` · `/reject too pricey` |
| `v5-booking-saga` | real test orders; `[fail:activities]` rolls back hotel then flight | `chat.py` → "… [fail:activities]" → `/approve` |
| `v6-long-conversations` | a new run id under the same conversation; no re-prompt | `uv run pytest -q -k continue_as_new` |
| `v7-agentcore` | the same chat against Temporal Cloud with the worker on AgentCore; empty `travel/` diff | `./bin/create-runtime.sh` · `git diff --stat v6-long-conversations v7-agentcore -- travel/` |
| `v8-versioned-redeploy` | an open conversation upgrading from build 1.0.0 to 1.1.0 at its next turn; empty `travel/` diff | §9.4 steps · `temporal worker deployment describe-version …` · `git diff --stat v7-agentcore v8-versioned-redeploy -- travel/` |
| `v9-hardening` | the same demos; extra tests for the in-flight window | `uv run pytest -q -k inflight` |
