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
from typing import Any

import grpc
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# gRPC target
# ---------------------------------------------------------------------------

GRPC_HOST = os.getenv("GRPC_HOST", "grpc-server")
GRPC_PORT = os.getenv("GRPC_PORT", "50051")
GRPC_TARGET = f"{GRPC_HOST}:{GRPC_PORT}"

# ---------------------------------------------------------------------------
# Lazy stub loader — defers import until first use so that a missing or
# incompatible grpcio installation never prevents the app from starting.
# ---------------------------------------------------------------------------

_stub = None


def _get_stub():
    global _stub
    if _stub is not None:
        return _stub

    # Deferred import: grpc stubs are generated at Docker build-time or by
    # running `make proto` locally. We import them here, not at module level,
    # so that any version mismatch / missing file does not crash the app on
    # startup and block route registration.
    import node_registry_pb2_grpc  # noqa: PLC0415

    channel = grpc.insecure_channel(GRPC_TARGET)
    _stub = node_registry_pb2_grpc.NodeRegistryStub(channel)
    logger.info("gRPC stub initialised → %s", GRPC_TARGET)
    return _stub


def _pb2():
    """Return the pb2 message module (also deferred)."""
    import node_registry_pb2  # noqa: PLC0415

    return node_registry_pb2


# ---------------------------------------------------------------------------
# FastAPI app — all routes are registered unconditionally at import time.
# ---------------------------------------------------------------------------

app = FastAPI(title="Node Registry Gateway")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class RegisterNodeRequest(BaseModel):
    name: str
    address: str
    port: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node_to_dict(node: Any) -> dict:
    return {
        "id": node.id,
        "name": node.name,
        "address": node.address,
        "port": node.port,
        "status": node.status,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/nodes", status_code=201)
def register_node(body: RegisterNodeRequest):
    pb2 = _pb2()
    try:
        resp = _get_stub().Register(
            pb2.RegisterRequest(
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
    pb2 = _pb2()
    try:
        resp = _get_stub().List(pb2.Empty())
        return [_node_to_dict(n) for n in resp.nodes]
    except grpc.RpcError as exc:
        raise HTTPException(status_code=500, detail=exc.details()) from exc


@app.get("/nodes/{node_id}")
def get_node(node_id: str):
    pb2 = _pb2()
    try:
        resp = _get_stub().Get(pb2.GetRequest(id=node_id))
        return _node_to_dict(resp.node)
    except grpc.RpcError as exc:
        if exc.code() == grpc.StatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail=exc.details()) from exc
        raise HTTPException(status_code=500, detail=exc.details()) from exc


@app.delete("/nodes/{node_id}", status_code=204, response_class=Response)
def delete_node(node_id: str):
    pb2 = _pb2()
    try:
        _get_stub().Delete(pb2.DeleteRequest(id=node_id))
    except grpc.RpcError as exc:
        if exc.code() == grpc.StatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail=exc.details()) from exc
        raise HTTPException(status_code=500, detail=exc.details()) from exc
