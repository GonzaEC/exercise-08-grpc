"""FastAPI REST gateway — translates HTTP requests to gRPC calls.

Endpoints:
  POST   /nodes           → NodeRegistry.Register
  GET    /nodes           → NodeRegistry.List
  GET    /nodes/{id}      → NodeRegistry.Get
  DELETE /nodes/{id}      → NodeRegistry.Delete
  GET    /health          → liveness probe
"""

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import grpc
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

# Stubs are generated inside this container at build time (see Dockerfile).
import node_registry_pb2
import node_registry_pb2_grpc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

GRPC_HOST = os.getenv("GRPC_HOST", "grpc-server")
GRPC_PORT = os.getenv("GRPC_PORT", "50051")
GRPC_TARGET = f"{GRPC_HOST}:{GRPC_PORT}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_for_grpc(max_retries: int = 30, delay: float = 2.0) -> None:
    """Block until the gRPC server is reachable."""
    for attempt in range(1, max_retries + 1):
        channel = grpc.insecure_channel(GRPC_TARGET)
        try:
            grpc.channel_ready_future(channel).result(timeout=5)
            channel.close()
            logger.info("gRPC server at %s is ready.", GRPC_TARGET)
            return
        except grpc.FutureTimeoutError:
            channel.close()
            logger.warning(
                "gRPC server not ready (%d/%d), retrying in %.0fs…",
                attempt,
                max_retries,
                delay,
            )
            time.sleep(delay)
    raise RuntimeError(f"Could not connect to gRPC server at {GRPC_TARGET}")


def _node_to_dict(node: node_registry_pb2.Node) -> dict[str, Any]:
    return {
        "id": node.id,
        "name": node.name,
        "address": node.address,
        "port": node.port,
        "status": node.status,
    }


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

_channel: grpc.Channel | None = None
_stub: node_registry_pb2_grpc.NodeRegistryStub | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _channel, _stub
    _wait_for_grpc()
    _channel = grpc.insecure_channel(GRPC_TARGET)
    _stub = node_registry_pb2_grpc.NodeRegistryStub(_channel)
    yield
    _channel.close()


app = FastAPI(title="Node Registry Gateway", lifespan=lifespan)


class RegisterNodeRequest(BaseModel):
    name: str
    address: str
    port: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/nodes", status_code=201)
def register_node(body: RegisterNodeRequest):
    try:
        resp = _stub.Register(
            node_registry_pb2.RegisterRequest(
                name=body.name,
                address=body.address,
                port=body.port,
            )
        )
        return _node_to_dict(resp.node)
    except grpc.RpcError as exc:
        raise HTTPException(status_code=500, detail=exc.details()) from exc


@app.get("/nodes")
def list_nodes():
    try:
        resp = _stub.List(node_registry_pb2.Empty())
        return [_node_to_dict(n) for n in resp.nodes]
    except grpc.RpcError as exc:
        raise HTTPException(status_code=500, detail=exc.details()) from exc


@app.get("/nodes/{node_id}")
def get_node(node_id: str):
    try:
        resp = _stub.Get(node_registry_pb2.GetRequest(id=node_id))
        return _node_to_dict(resp.node)
    except grpc.RpcError as exc:
        if exc.code() == grpc.StatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail=exc.details()) from exc
        raise HTTPException(status_code=500, detail=exc.details()) from exc


@app.delete("/nodes/{node_id}", status_code=204, response_class=Response)
def delete_node(node_id: str):
    try:
        _stub.Delete(node_registry_pb2.DeleteRequest(id=node_id))
    except grpc.RpcError as exc:
        if exc.code() == grpc.StatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail=exc.details()) from exc
        raise HTTPException(status_code=500, detail=exc.details()) from exc
