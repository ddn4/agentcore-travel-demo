"""Chat with the concierge. Each message is a `chat` Update-with-Start on the conversation's workflow."""

import argparse
import asyncio
import uuid

from temporalio.client import (
    WithStartWorkflowOperation,
    WorkflowUpdateFailedError,
    WorkflowUpdateRPCTimeoutOrCancelledError,
)
from temporalio.common import WorkflowIDConflictPolicy

from travel.models import ChatRequest, ChatResponse, ConciergeInput
from travel.worker import TASK_QUEUE, connect
from travel.workflow import ConciergeWorkflow

HELP = "Type a message, /status, or /quit."


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conversation", help="resume an existing conversation id")
    args = parser.parse_args()

    client = await connect()  # Bedrock factories are lazy; the CLI never builds a model
    conv_id = args.conversation or f"concierge-{uuid.uuid4()}"
    handle = client.get_workflow_handle_for(ConciergeWorkflow.run, conv_id)
    print(f"conversation {conv_id}\n{HELP}")

    async def send(text: str) -> ChatResponse:
        update_id = str(uuid.uuid4())  # same id on every retry, so the workflow processes the message once
        for attempt in range(5):
            start = WithStartWorkflowOperation(  # single-use: rebuilt for each attempt
                ConciergeWorkflow.run,
                ConciergeInput(),
                id=conv_id,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                task_queue=TASK_QUEUE,
            )
            try:
                return await client.execute_update_with_start_workflow(
                    ConciergeWorkflow.chat, ChatRequest(text=text), start_workflow_operation=start, id=update_id
                )
            except WorkflowUpdateRPCTimeoutOrCancelledError:
                print(f"(waiting for a worker… retry {attempt + 1})")
        raise RuntimeError("no worker picked up the message")

    while True:
        try:
            line = (await asyncio.to_thread(input, "you> ")).strip()
        except EOFError:
            line = "/quit"
        if not line:
            continue
        if line == "/quit":
            try:
                await handle.signal(ConciergeWorkflow.end_chat)
            except Exception:
                pass  # never started, or already closed
            return
        if line == "/status":
            try:
                print(await handle.query(ConciergeWorkflow.state))
            except Exception as e:  # not started yet, or no worker to answer the query
                print(f"(no status: {e})")
            continue
        try:
            reply = await send(line)
        except WorkflowUpdateFailedError as e:
            print(f"(not sent: {e.cause})")
            continue
        print(f"concierge> {reply.message}")


if __name__ == "__main__":
    asyncio.run(main())
