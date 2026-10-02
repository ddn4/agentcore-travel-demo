import asyncio
import copy
import json
from datetime import timedelta
from decimal import Decimal

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from strands.types.exceptions import EventLoopException, StructuredOutputException

    from travel import activities as act  # httpx is imported but never used in the sandbox
    from travel.agents import build_agents
    from travel.models import (TEST_TRAVELLERS, ChatRequest, ChatResponse, ConciergeInput, ConciergeReply,
                               ConversationState, PricedTrip, PriceRequest, Quote, Status, TripProposal)

PRICE = dict(start_to_close_timeout=timedelta(seconds=60), retry_policy=RetryPolicy(maximum_attempts=3))


@workflow.defn
class ConciergeWorkflow:
    """One conversation. Each user message is a `chat` Update that returns the concierge's reply."""

    @workflow.init
    def __init__(self, inp: ConciergeInput) -> None:
        self._concierge = self._build(list(inp.messages))
        self._lock = asyncio.Lock()
        self._status: Status = "chatting"
        self._quote: Quote | None = None  # the latest priced proposal
        self._quote_seq = 0
        self._note: str | None = None  # a pricing problem the concierge has not been told about yet
        self._turns = 0
        self._done = False

    def _build(self, messages: list):
        """The agent is a projection of workflow state: built at start and rebuilt after a failed turn."""
        return build_agents(messages=messages, today=workflow.now().date().isoformat())

    @workflow.update
    async def chat(self, req: ChatRequest) -> ChatResponse:
        async with self._lock:  # one turn at a time
            if self._done:  # re-check: the validator ran before we waited for the lock
                raise ApplicationError("conversation closed")
            note, self._note = self._note, None
            text = f"[system: {note}] {req.text}" if note else req.text
            snapshot = copy.deepcopy(self._concierge.messages)
            try:
                result = await self._concierge.invoke_async(text, structured_output_model=ConciergeReply)
                reply: ConciergeReply = result.structured_output or ConciergeReply(message=str(result).strip())
                priced = None
                if reply.proposal and (invented := self._invented_ids(reply.proposal)):
                    priced = PricedTrip(problems=[f"{i} was not returned by any search" for i in invented])
                elif reply.proposal:  # the model proposed offer IDs; the providers price them
                    priced = await workflow.execute_activity(
                        act.price_quote, PriceRequest(proposal=reply.proposal), summary="price_quote", **PRICE)
            except (ActivityError, EventLoopException, StructuredOutputException) as e:
                if isinstance(e, EventLoopException) and not isinstance(e.original_exception, ActivityError):
                    raise  # a bug, not a model or provider failure: fail the workflow task so it is visible
                self._concierge = self._build(snapshot)  # drop the half-finished turn
                self._note = note
                raise ApplicationError(f"concierge turn failed: {e}", type="TurnFailed") from e
            self._turns += 1
            message = reply.message
            if priced is not None:  # a new proposal always replaces the previous quote
                self._quote = None
                if priced.problems:
                    problems = "; ".join(priced.problems)
                    self._note = f"price_quote rejected the proposal: {problems}"
                    message += f"\n(The providers could not confirm this trip: {problems}. Ask me to search again.)"
                else:
                    self._quote = self._make_quote(reply.proposal, priced)
            return ChatResponse(message=message, status=self._status, quote=self._quote)

    def _invented_ids(self, p: TripProposal) -> list[str]:
        """Proposed IDs that appear in no tool result the concierge received: the model may not invent offers."""
        results = json.dumps([block["toolResult"]["content"] for m in self._concierge.messages
                              for block in m["content"] if "toolResult" in block])
        return [i for i in (p.flight_offer_id, p.hotel_rate_id, *p.activity_place_ids) if i not in results]

    def _make_quote(self, p: TripProposal, priced: PricedTrip) -> Quote:
        """Amounts come from price_quote (providers and the mock rate card), never from the model."""
        self._quote_seq += 1
        over = (priced.totals["USD"] > Decimal(str(p.budget_usd))
                if p.budget_usd is not None and set(priced.totals) == {"USD"} else None)
        names = [f"{t['given_name']} {t['family_name']}" for t in TEST_TRAVELLERS[:p.travelers]]
        return Quote(quote_id=f"q{self._quote_seq}", proposal=p, line_items=priced.line_items,
                     totals=priced.totals, travellers=names, expires_at=priced.expires_at, over_budget=over)

    @chat.validator
    def _validate_chat(self, req: ChatRequest) -> None:
        if self._done:
            raise ValueError("conversation closed")
        if not req.text.strip():
            raise ValueError("empty message")

    @workflow.signal
    def end_chat(self) -> None:
        self._done = True

    @workflow.query
    def state(self) -> ConversationState:
        return ConversationState(status=self._status, turns=self._turns, quote=self._quote)

    @workflow.run
    async def run(self, inp: ConciergeInput) -> None:
        await workflow.wait_condition(lambda: self._done)
        await workflow.wait_condition(workflow.all_handlers_finished)
