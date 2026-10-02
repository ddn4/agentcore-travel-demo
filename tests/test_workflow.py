import uuid

import pytest
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WithStartWorkflowOperation, WorkflowUpdateFailedError
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.contrib.strands import StrandsPlugin
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from tests.mock_model import MockModel
from travel.models import ChatRequest, ConciergeInput
from travel.workflow import ConciergeWorkflow

TASK_QUEUE = "test-concierge"


@pytest.fixture(scope="session")
async def env():
    async with await WorkflowEnvironment.start_local() as env:
        yield env


def plugin_for(concierge_script: list[str]) -> StrandsPlugin:
    return StrandsPlugin(models={"concierge": lambda: MockModel(concierge_script)})


async def chat(client: Client, conv_id: str, text: str):
    """Exactly what chat.py does: Update-with-Start, reusing the running conversation."""
    start = WithStartWorkflowOperation(
        ConciergeWorkflow.run,
        ConciergeInput(),
        id=conv_id,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        task_queue=TASK_QUEUE,
    )
    reply = await client.execute_update_with_start_workflow(
        ConciergeWorkflow.chat, ChatRequest(text=text), start_workflow_operation=start
    )
    return reply, await start.workflow_handle()


async def test_chat_is_a_durable_update(env: WorkflowEnvironment):
    plugin = plugin_for(["Lisbon in March is lovely. How many travelers?", "Two travelers, noted."])
    client = Client(**{**env.client.config(), "plugins": [plugin]})
    conv_id = f"concierge-{uuid.uuid4()}"
    async with Worker(client, task_queue=TASK_QUEUE, workflows=[ConciergeWorkflow]):
        first, h1 = await chat(client, conv_id, "Plan a four-day trip to Lisbon in March")
        second, h2 = await chat(client, conv_id, "Two of us")
        assert h1.first_execution_run_id == h2.first_execution_run_id  # same conversation, same run
        assert first.message == "Lisbon in March is lovely. How many travelers?"
        assert second.message == "Two travelers, noted."
        assert (await h1.query(ConciergeWorkflow.state)).turns == 2

        history = await h1.fetch_history()
        model_calls = [
            e for e in history.events
            if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
            and e.activity_task_scheduled_event_attributes.activity_type.name == "invoke_model"
        ]
        assert len(model_calls) == 2
        await h1.signal(ConciergeWorkflow.end_chat)
        await h1.result()

    await Replayer(workflows=[ConciergeWorkflow], plugins=[plugin]).replay_workflow(await h1.fetch_history())


async def test_chat_validator(env: WorkflowEnvironment):
    client = Client(**{**env.client.config(), "plugins": [plugin_for(["Hello! Where would you like to go?"])]})
    conv_id = f"concierge-{uuid.uuid4()}"
    async with Worker(client, task_queue=TASK_QUEUE, workflows=[ConciergeWorkflow]):
        await chat(client, conv_id, "hi")
        handle = client.get_workflow_handle_for(ConciergeWorkflow.run, conv_id)
        with pytest.raises(WorkflowUpdateFailedError) as rejected:
            await handle.execute_update(ConciergeWorkflow.chat, ChatRequest(text="   "))
        assert "empty message" in str(rejected.value.cause)

        history = await handle.fetch_history()
        accepted = [e for e in history.events if e.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_ACCEPTED]
        assert len(accepted) == 1  # the rejected update left no trace in history

        await handle.signal(ConciergeWorkflow.end_chat)
        await handle.result()  # end_chat completes the conversation
