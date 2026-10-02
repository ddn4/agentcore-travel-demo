"""A scripted Strands model for tests: each call plays the next turn of the script."""

from collections.abc import AsyncIterable
from typing import Any

from strands.models import Model
from strands.types.streaming import StreamEvent


class MockModel(Model):
    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.seen: list[list] = []  # the messages each call received

    def update_config(self, **_: Any) -> None:
        pass

    def get_config(self) -> dict:
        return {}

    def structured_output(self, *_: Any, **__: Any) -> Any:
        raise NotImplementedError

    async def stream(self, messages: list, *_: Any, **__: Any) -> AsyncIterable[StreamEvent]:
        self.seen.append(messages)
        text = self.script.pop(0)
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {}}}
        yield {"contentBlockDelta": {"delta": {"text": text}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
