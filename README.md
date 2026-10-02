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

## Walkthrough

The repository is built one capability at a time. Each tag below runs and passes its tests;
`git diff <previous-tag>..<tag>` shows exactly what that step added.

| Tag | What you see | Command |
|---|---|---|
| `v0-spec` | the spec and a toolchain that installs | `uv sync` |
