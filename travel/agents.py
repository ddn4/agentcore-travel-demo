from datetime import timedelta

from temporalio.common import RetryPolicy
from temporalio.contrib.strands import TemporalAgent

# Every model call is an `invoke_model` activity; Temporal owns timeouts and retries.
LLM = dict(
    start_to_close_timeout=timedelta(seconds=180),  # longer than the Bedrock read timeout (120 s)
    retry_policy=RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=2), backoff_coefficient=2.0),
    callback_handler=None,
)

CONCIERGE_PROMPT = """You are a friendly travel concierge. Today is {today}.
Help the traveler shape a trip: destination, dates, number of travelers, budget and interests.
You cannot search for or book anything yet, so never invent flights, hotels or prices.
Keep replies short and ask at most one question at a time."""


def build_agents(messages: list, today: str) -> TemporalAgent:
    return TemporalAgent(
        model="concierge",
        summary="concierge",
        name="concierge",
        system_prompt=CONCIERGE_PROMPT.format(today=today),
        messages=messages,
        **LLM,
    )
