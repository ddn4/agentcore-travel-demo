import asyncio
import copy

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from strands.types.exceptions import EventLoopException, StructuredOutputException

    from travel.agents import build_agents  # imports the activities (httpx); they never run in the sandbox
    from travel.models import ChatRequest, ChatResponse, ConciergeInput, ConciergeReply, ConversationState, Status


@workflow.defn
class ConciergeWorkflow:
    """One conversation. Each user message is a `chat` Update that returns the concierge's reply."""

    @workflow.init
    def __init__(self, inp: ConciergeInput) -> None:
        self._concierge = self._build(list(inp.messages))
        self._lock = asyncio.Lock()
        self._status: Status = "chatting"
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
            snapshot = copy.deepcopy(self._concierge.messages)
            try:
                result = await self._concierge.invoke_async(req.text, structured_output_model=ConciergeReply)
                reply: ConciergeReply = result.structured_output or ConciergeReply(message=str(result).strip())
            except (ActivityError, EventLoopException, StructuredOutputException) as e:
                if isinstance(e, EventLoopException) and not isinstance(e.original_exception, ActivityError):
                    raise  # a bug, not a model or provider failure: fail the workflow task so it is visible
                self._concierge = self._build(snapshot)  # drop the half-finished turn
                raise ApplicationError(f"concierge turn failed: {e}", type="TurnFailed") from e
            self._turns += 1
            return ChatResponse(message=reply.message, status=self._status)

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
        return ConversationState(status=self._status, turns=self._turns)

    @workflow.run
    async def run(self, inp: ConciergeInput) -> None:
        await workflow.wait_condition(lambda: self._done)
        await workflow.wait_condition(workflow.all_handlers_finished)
