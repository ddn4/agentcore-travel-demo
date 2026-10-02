import uuid
from collections import Counter

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WithStartWorkflowOperation, WorkflowHandle, WorkflowUpdateFailedError
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.contrib.strands import StrandsPlugin
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from tests.mock_model import MockModel, reply
from travel.models import ChatRequest, ConciergeInput
from travel.workflow import ConciergeWorkflow

TASK_QUEUE = "test-concierge"
CALLS: Counter = Counter()  # mocked activity name -> times called


# --- mocked provider activities, registered under the real names (no network) ---
@activity.defn(name="search_flights")
async def mock_search_flights(origin: str, destination: str, depart_date: str, return_date: str,
                              travelers: int = 1, cabin_class: str = "economy") -> list[dict]:
    CALLS["search_flights"] += 1
    return [{"offer_id": "off_fake_zz", "airline": "Duffel Airways", "total_amount": "412.30", "currency": "USD",
             "outbound": "JFK → LIS", "inbound": "LIS → JFK", "red_eye": False}]


@activity.defn(name="search_hotels")
async def mock_search_hotels(city: str, check_in: str, check_out: str, travelers: int = 1) -> list[dict]:
    CALLS["search_hotels"] += 1
    return [{"rate_id": "rat_fake_1", "hotel": "Memmo Alfama", "nightly": "155.00", "total_amount": "465.00",
             "currency": "USD", "test_fallback": False}]


@activity.defn(name="search_places")
async def mock_search_places(city: str, interests: list[str]) -> list[dict]:
    CALLS["search_places"] += 1
    return [{"place_id": "ChIJmuseum", "name": "Museu Nacional do Azulejo", "category": "museum", "mock_price": "18"}]


MOCK_ACTIVITIES = [mock_search_flights, mock_search_hotels, mock_search_places]


@pytest.fixture(scope="session")
async def env():
    async with await WorkflowEnvironment.start_local() as env:
        yield env


@pytest.fixture(autouse=True)
def reset_calls():
    CALLS.clear()


def connect(env: WorkflowEnvironment, concierge: list, specialist: list | None = None) -> tuple[Client, StrandsPlugin]:
    """A client whose plugin plays scripted models: one script for the concierge, one shared by the specialists."""
    plugin = StrandsPlugin(models={"concierge": lambda: MockModel(concierge),
                                   "specialist": lambda: MockModel(specialist or [])})
    return Client(**{**env.client.config(), "plugins": [plugin]}), plugin


def worker(client: Client) -> Worker:
    return Worker(client, task_queue=TASK_QUEUE, workflows=[ConciergeWorkflow], activities=MOCK_ACTIVITIES)


async def chat(client: Client, conv_id: str, text: str):
    """Exactly what chat.py does: Update-with-Start, reusing the running conversation."""
    start = WithStartWorkflowOperation(
        ConciergeWorkflow.run,
        ConciergeInput(),
        id=conv_id,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        task_queue=TASK_QUEUE,
    )
    response = await client.execute_update_with_start_workflow(
        ConciergeWorkflow.chat, ChatRequest(text=text), start_workflow_operation=start
    )
    return response, await start.workflow_handle()


async def scheduled(handle: WorkflowHandle) -> Counter:
    """Activity type -> how many times the workflow scheduled it."""
    history = await handle.fetch_history()
    return Counter(
        e.activity_task_scheduled_event_attributes.activity_type.name
        for e in history.events
        if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    )


async def test_chat_is_a_durable_update(env: WorkflowEnvironment):
    client, plugin = connect(env, [reply("Lisbon in March is lovely. Where are you flying from?"),
                                   reply("New York, noted.")])
    conv_id = f"concierge-{uuid.uuid4()}"
    async with worker(client):
        first, h1 = await chat(client, conv_id, "Plan a four-day trip to Lisbon in March")
        second, h2 = await chat(client, conv_id, "From New York")
        assert h1.first_execution_run_id == h2.first_execution_run_id  # same conversation, same run
        assert first.message == "Lisbon in March is lovely. Where are you flying from?"
        assert second.message == "New York, noted."
        assert (await h1.query(ConciergeWorkflow.state)).turns == 2
        assert (await scheduled(h1))["invoke_model"] == 2
        await h1.signal(ConciergeWorkflow.end_chat)
        await h1.result()

    await Replayer(workflows=[ConciergeWorkflow], plugins=[plugin]).replay_workflow(await h1.fetch_history())


async def test_chat_validator(env: WorkflowEnvironment):
    client, _ = connect(env, [reply("Hello! Where would you like to go?")])
    conv_id = f"concierge-{uuid.uuid4()}"
    async with worker(client):
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


async def test_agents_as_tools_wiring(env: WorkflowEnvironment):
    concierge = [{"name": "flight_agent", "input": {"input": "JFK to Lisbon, 2027-03-10 to 2027-03-13, 1 adult"}},
                 reply("Duffel Airways has a fare for 412.30 USD.")]
    specialist = [{"name": "search_flights", "input": {"origin": "JFK", "destination": "Lisbon",
                                                       "depart_date": "2027-03-10", "return_date": "2027-03-13"}},
                  '[{"offer_id": "off_fake_zz", "total_amount": "412.30", "currency": "USD"}]']
    client, plugin = connect(env, concierge, specialist)
    conv_id = f"concierge-{uuid.uuid4()}"
    async with worker(client):
        response, handle = await chat(client, conv_id, "Flights from JFK to Lisbon in March")
        assert response.message == "Duffel Airways has a fare for 412.30 USD."
        # concierge -> flight_agent (a sub-agent) -> search_flights (an activity) -> back to the concierge
        assert await scheduled(handle) == Counter({"invoke_model": 4, "search_flights": 1})
        assert CALLS == Counter({"search_flights": 1})
        await handle.signal(ConciergeWorkflow.end_chat)
        await handle.result()

    await Replayer(workflows=[ConciergeWorkflow], plugins=[plugin]).replay_workflow(await handle.fetch_history())
