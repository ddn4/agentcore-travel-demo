"""The single switch between local and cloud: everything is read from TEMPORAL_* and AWS_* env vars."""

import asyncio
import os
from datetime import timedelta

import boto3
from botocore.config import Config as BotocoreConfig
from strands.models.bedrock import BedrockModel
from temporalio.client import Client
from temporalio.common import VersioningBehavior
from temporalio.contrib.strands import StrandsPlugin
from temporalio.envconfig import ClientConfig
from temporalio.worker import Worker, WorkerDeploymentConfig, WorkerDeploymentVersion

from travel.activities import ALL_ACTIVITIES
from travel.workflow import ConciergeWorkflow

TASK_QUEUE = os.environ.get("TEMPORAL_TASK_QUEUE", "travel-concierge")
DEPLOYMENT = os.environ.get("TEMPORAL_DEPLOYMENT_NAME", "travel-concierge")
BUILD_ID = os.environ.get("TEMPORAL_BUILD_ID", "local")

_NO_RETRY = BotocoreConfig(retries={"max_attempts": 0}, read_timeout=120)  # Temporal owns retries


def aws_region() -> str:
    """AWS_REGION, else the region of the active AWS profile, else us-east-1."""
    return os.environ.get("AWS_REGION") or boto3.Session().region_name or "us-east-1"


def bedrock_models() -> dict:
    model_id = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-6")
    make = lambda: BedrockModel(model_id=model_id, region_name=aws_region(), boto_client_config=_NO_RETRY)  # noqa: E731
    return {"concierge": make, "specialist": make}  # lazy: only called on the worker, never by chat.py


async def connect(models: dict | None = None) -> Client:
    cfg = ClientConfig.load_client_connect_config()  # TEMPORAL_ADDRESS / _NAMESPACE / _API_KEY / _PROFILE
    cfg.setdefault("target_host", "localhost:7233")  # no env: local dev server; an API key turns TLS on
    return await Client.connect(**cfg, plugins=[StrandsPlugin(models=models or bedrock_models())])


def build_worker(client: Client, interceptors: list = ()) -> Worker:
    return Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ConciergeWorkflow],
        activities=ALL_ACTIVITIES,
        interceptors=list(interceptors),
        deployment_config=WorkerDeploymentConfig(
            version=WorkerDeploymentVersion(deployment_name=DEPLOYMENT, build_id=BUILD_ID),
            use_worker_versioning=True,
            default_versioning_behavior=VersioningBehavior.PINNED,
        ),
        graceful_shutdown_timeout=timedelta(seconds=120),
    )


async def main() -> None:
    await build_worker(await connect()).run()


if __name__ == "__main__":
    asyncio.run(main())
