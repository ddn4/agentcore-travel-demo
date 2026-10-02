"""Chat with the concierge. Each message is a `chat` Update-with-Start on the conversation's workflow."""

import argparse
import asyncio
import contextlib
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

HELP = "Type a message, /status, or /quit (ends the conversation). Ctrl+C leaves it open to resume later."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conversation", help="resume an existing conversation id")
    args = parser.parse_args()

    # One event loop for the whole session; input() stays on the main thread, so Ctrl+C is a clean
    # KeyboardInterrupt both at the prompt and while waiting for a reply.
    with asyncio.Runner() as runner:
        client = runner.run(connect())  # Bedrock factories are lazy; the CLI never builds a model
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
                    if asyncio.current_task().cancelling():  # Ctrl+C, not a slow worker: stop retrying
                        with contextlib.suppress(BaseException):
                            await start.workflow_handle()  # consume the operation's (failed) result
                        raise asyncio.CancelledError from None
                    print(f"(waiting for a worker… retry {attempt + 1})")
            raise RuntimeError("no worker picked up the message")

        async def status() -> None:
            try:
                print(await handle.query(ConciergeWorkflow.state))
            except Exception as e:  # not started yet, or no worker to answer the query
                print(f"(no status: {e})")

        async def quit_chat() -> None:
            try:
                await handle.signal(ConciergeWorkflow.end_chat)
            except Exception:
                pass  # never started, or already closed

        try:
            while True:
                try:
                    line = input("you> ").strip()
                except EOFError:  # Ctrl+D ends the conversation, like /quit
                    line = "/quit"
                if not line:
                    continue
                if line == "/quit":
                    runner.run(quit_chat())
                    return
                if line == "/status":
                    runner.run(status())
                    continue
                try:
                    reply = runner.run(send(line))
                except WorkflowUpdateFailedError as e:
                    print(f"(not sent: {e.cause})")
                    continue
                print(f"concierge> {reply.message}")
        except KeyboardInterrupt:  # leave without ending the conversation: it is durable
            print(f"\n(left conversation {conv_id} open; resume with: uv run python chat.py --conversation {conv_id})")


if __name__ == "__main__":
    main()
