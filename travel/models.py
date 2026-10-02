from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel
from strands.types.content import Messages

Status = Literal["chatting"]


@dataclass
class ConciergeInput:
    messages: Messages = field(default_factory=list)


class ChatRequest(BaseModel):
    text: str


class ChatResponse(BaseModel):
    message: str
    status: Status


class ConversationState(BaseModel):
    status: Status
    turns: int
