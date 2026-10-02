# Travel Concierge: Strands agents on Temporal, local and on AgentCore

A chat-based travel concierge built with [Strands Agents](https://strandsagents.com) running inside
[Temporal](https://temporal.io) workflows through the official `temporalio.contrib.strands` plugin.
A concierge agent calls flight, hotel, itinerary and budget/policy agents as tools, asks permission
before using your location, waits for your approval, and books the trip as a saga that rolls back on failure.
The same worker code runs against a local Temporal dev server and as a Temporal Serverless Worker on
Amazon Bedrock AgentCore Runtime.

[`SPEC.md`](SPEC.md) is the full design.

## Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- [Temporal CLI](https://docs.temporal.io/cli) 1.8.3 or later
- AWS credentials with Amazon Bedrock access to an Anthropic Claude model (default `global.anthropic.claude-sonnet-4-6`)
- From `v2-agents-as-tools`: a Duffel **test** token and a Google Places API (New) key (see SPEC §8.3)
- From `v7-agentcore`: AWS CLI v2, Node 20+ with `@aws/agentcore`, CDK bootstrapped, and a Temporal Cloud namespace enabled for Serverless Workers

```bash
uv sync
```

## Run locally

```bash
# terminal 1: Temporal dev server (UI at http://localhost:8233)
temporal server start-dev

# terminal 2: the worker (needs AWS credentials with Bedrock access)
export AWS_REGION=us-west-2 AWS_PROFILE=<profile>
uv run python -m travel.worker

# terminal 3: once per build id, after the worker has started polling
temporal worker deployment set-current-version --deployment-name travel-concierge --build-id local --yes
uv run python chat.py
```

The worker uses Worker Versioning even locally, for parity with AgentCore; that is why the
`set-current-version` step exists. After switching tags, start a new conversation: open
conversations are pinned to the code that started them.

Tests run against a local dev server that the test fixture starts, with a scripted model, so they
need no AWS credentials: `uv run pytest -q`.

## Walkthrough

The repository is built one capability at a time. Each tag below runs and passes its tests;
`git diff <previous-tag>..<tag>` shows exactly what that step added.

| Tag | What you see | Command |
|---|---|---|
| `v0-spec` | the spec and a toolchain that installs | `uv sync` |
| `v1-durable-chat` | a chat reply that survives a worker restart; `invoke_model` activities in the UI | `uv run python -m travel.worker` · `uv run python chat.py` |
