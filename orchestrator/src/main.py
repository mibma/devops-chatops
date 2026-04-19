import logging
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from .adapters.docker_adapter import DockerAdapter
from .adapters.jenkins_adapter import JenkinsAdapter
from .adapters.kubernetes_adapter import KubernetesAdapter
from .config import settings
from .db.postgres import Postgres
from .db.redis_client import RedisClient
from .engine.command_parser import CommandParser
from .engine.orchestrator import Orchestrator
from .engine.permission_checker import PermissionChecker
from .models.intents import Action
from .notifications.slack_notifier import SlackNotifier
from .utils.logger import configure_logging

configure_logging()
log = logging.getLogger(__name__)


class ExecuteRequest(BaseModel):
    raw_text: str = ""
    action: str
    user_id: str
    channel_id: str
    team_id: str | None = None


class ApprovalRequest(BaseModel):
    approved_by: str | None = None
    rejected_by: str | None = None


def _build_jenkins() -> JenkinsAdapter | None:
    if not settings.jenkins_token:
        log.warning("JENKINS_TOKEN not set; Jenkins adapter disabled")
        return None
    try:
        return JenkinsAdapter(settings.jenkins_url, settings.jenkins_user, settings.jenkins_token)
    except Exception:  # noqa: BLE001
        log.exception("failed to initialize Jenkins adapter")
        return None


def _build_docker() -> DockerAdapter | None:
    try:
        return DockerAdapter()
    except Exception:  # noqa: BLE001
        log.warning("Docker adapter unavailable (no docker socket?)")
        return None


def _build_k8s() -> KubernetesAdapter | None:
    try:
        return KubernetesAdapter(
            in_cluster=settings.in_cluster,
            kubeconfig_path=settings.kubeconfig,
        )
    except Exception:  # noqa: BLE001
        log.warning("Kubernetes adapter unavailable; running without cluster access")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    postgres = Postgres(settings.database_url)
    redis_client = RedisClient(settings.redis_url)
    await postgres.connect()
    await redis_client.connect()

    parser = CommandParser()
    permissions = PermissionChecker(settings.permissions_file)
    slack = SlackNotifier(settings.slack_bot_token)

    jenkins = _build_jenkins()
    docker_ = _build_docker()
    k8s = _build_k8s()

    app.state.orch = Orchestrator(
        parser=parser,
        permissions=permissions,
        postgres=postgres,
        redis_client=redis_client,
        slack=slack,
        jenkins=jenkins,
        docker=docker_,
        k8s=k8s,
    )
    app.state.redis = redis_client

    log.info("orchestrator ready")
    try:
        yield
    finally:
        await redis_client.close()
        await postgres.close()


app = FastAPI(title="ChatOps Orchestrator", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready(request: Request):
    if not hasattr(request.app.state, "orch"):
        raise HTTPException(status_code=503, detail="not ready")
    return {"status": "ready"}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/commands/execute")
async def execute(req: ExecuteRequest, request: Request):
    redis_client: RedisClient = request.app.state.redis

    if await redis_client.hit_rate_limit(req.user_id, settings.rate_limit_per_minute):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    try:
        action = Action(req.action.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")

    orch: Orchestrator = request.app.state.orch
    result = await orch.execute(req.raw_text, action, req.user_id, req.channel_id)
    return JSONResponse(result)


@app.post("/operations/{tracking_id}/approve")
async def approve(tracking_id: UUID, req: ApprovalRequest, request: Request):
    orch: Orchestrator = request.app.state.orch
    return await orch.approve(tracking_id, req.approved_by or "")


@app.post("/operations/{tracking_id}/reject")
async def reject(tracking_id: UUID, req: ApprovalRequest, request: Request):
    orch: Orchestrator = request.app.state.orch
    return await orch.reject(tracking_id, req.rejected_by or "")
