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
- AWS credentials (any standard source: `aws login`, `aws sso login`, a profile, or env vars) that can call
  Amazon Bedrock with an Anthropic Claude model (default `global.anthropic.claude-sonnet-4-6`).
  Only the worker needs them; the chat client and the tests do not.
- From `v2-agents-as-tools`: a Duffel **test** token and a Google Places API (New) key (see SPEC §8.3)
- From `v7-agentcore`: AWS CLI v2, Node 20+ with `@aws/agentcore`, CDK bootstrapped, and a Temporal Cloud namespace enabled for Serverless Workers

```bash
uv sync
```

## Run locally

```bash
# terminal 1: Temporal dev server, with the Temporal Web UI at http://localhost:8233
temporal server start-dev

# terminal 2: the worker, in a shell with AWS credentials (set AWS_PROFILE if they are not the default profile)
uv run python -m travel.worker

# terminal 3: once per build id, after the worker has started polling
temporal worker deployment set-current-version --deployment-name travel-concierge --build-id local --yes
uv run python chat.py
```

The chat is a plain terminal prompt (`you>` / `concierge>`). The Temporal Web UI is where you watch
what happens underneath: each conversation is a workflow whose history shows every `chat` Update and
every `invoke_model` activity.

The region is `AWS_REGION` if set, else your AWS profile's region, else `us-east-1`. Override the
model with `MODEL_ID` (for example `us.anthropic.claude-sonnet-4-6` where the global profile is blocked).

The worker uses Worker Versioning even locally, for parity with AgentCore; that is why the
`set-current-version` step exists. If that command fails (for example with a gRPC deadline error right after
the worker starts), wait a few seconds and run it again. Until it succeeds, chat messages wait
without a reply; they go through as soon as the version is current. After switching tags, start a new conversation: open
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
