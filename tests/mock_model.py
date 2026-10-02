"""A scripted Strands model for tests: each call plays the next turn of the script.

A str is a text turn. A {"name": ..., "input": {...}} dict is a tool-use turn with toolUseId "t<n>",
n being the call's position in the script. Structured output is a tool-use named after the model class,
e.g. {"name": "ConciergeReply", "input": {"message": "..."}}.
"""

import json
from collections.abc import AsyncIterable
from typing import Any

from strands.models import Model
from strands.types.streaming import StreamEvent


class MockModel(Model):
    def __init__(self, script: list[str | dict]) -> None:
        self.script = list(script)
        self.calls = 0
        self.seen: list[list] = []  # the messages each call received

    def update_config(self, **_: Any) -> None:
        pass

    def get_config(self) -> dict:
        return {}

    def structured_output(self, *_: Any, **__: Any) -> Any:
        raise NotImplementedError

    async def stream(self, messages: list, *_: Any, **__: Any) -> AsyncIterable[StreamEvent]:
        self.seen.append(messages)
        turn = self.script.pop(0)
        n, self.calls = self.calls, self.calls + 1
        yield {"messageStart": {"role": "assistant"}}
        if isinstance(turn, str):
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": turn}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        else:
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"t{n}", "name": turn["name"]}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(turn["input"])}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}


def reply(message: str, proposal: dict | None = None) -> dict:
    """A concierge turn that ends with its structured ConciergeReply."""
    return {"name": "ConciergeReply", "input": {"message": message, "proposal": proposal}}
