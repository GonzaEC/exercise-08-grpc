"""gRPC server — NodeRegistry service.

Exposes:
  - NodeRegistry (Register / List / Get / Delete)
  - gRPC Health-check (grpc.health.v1.Health)
  - Server reflection (grpc.reflection.v1alpha.ServerReflection)

Storage: PostgreSQL via SQLAlchemy 2.x (DATABASE_URL env-var).
"""

import logging
import os
import time
import uuid
from concurrent import futures

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection
from sqlalchemy import Column, Integer, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session

# Stubs are generated inside this container at build time (see Dockerfile).
import node_registry_pb2
import node_registry_pb2_grpc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://noderegistry:noderegistry@db:5432/noderegistry",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


# ---------------------------------------------------------------------------
# ORM model
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


class NodeModel(Base):
    __tablename__ = "nodes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    address = Column(String, nullable=False)
    port = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="active")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def wait_for_db(max_retries: int = 15, delay: float = 2.0) -> None:
    """Block until the database is reachable."""
    for attempt in range(1, max_retries + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database is ready.")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("DB not ready (%d/%d): %s", attempt, max_retries, exc)
            time.sleep(delay)
    raise RuntimeError("Could not connect to the database after retries.")


def _node_proto(node: NodeModel) -> node_registry_pb2.Node:
    return node_registry_pb2.Node(
        id=node.id,
        name=node.name,
        address=node.address,
        port=node.port,
        status=node.status,
    )


# ---------------------------------------------------------------------------
# Service implementation
# ---------------------------------------------------------------------------


class NodeRegistryServicer(node_registry_pb2_grpc.NodeRegistryServicer):

    def Register(self, request, context):
        with Session(engine) as session:
            node = NodeModel(
                id=str(uuid.uuid4()),
                name=request.name,
                address=request.address,
                port=request.port,
                status="active",
            )
            session.add(node)
            session.commit()
            session.refresh(node)
            logger.info("Registered node id=%s name=%s", node.id, node.name)
            return node_registry_pb2.NodeResponse(node=_node_proto(node))

    def List(self, request, context):
        with Session(engine) as session:
            nodes = session.query(NodeModel).all()
            return node_registry_pb2.NodeList(nodes=[_node_proto(n) for n in nodes])

    def Get(self, request, context):
        with Session(engine) as session:
            node = session.get(NodeModel, request.id)
            if node is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Node {request.id!r} not found",
                )
            return node_registry_pb2.NodeResponse(node=_node_proto(node))

    def Delete(self, request, context):
        with Session(engine) as session:
            node = session.get(NodeModel, request.id)
            if node is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Node {request.id!r} not found",
                )
            session.delete(node)
            session.commit()
            logger.info("Deleted node id=%s", request.id)
            return node_registry_pb2.Empty()


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------


def serve() -> None:
    wait_for_db()
    Base.metadata.create_all(engine)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    # --- NodeRegistry service ---
    node_registry_pb2_grpc.add_NodeRegistryServicer_to_server(
        NodeRegistryServicer(), server
    )

    # --- Health-check ---
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_servicer.set(
        "node_registry.NodeRegistry",
        health_pb2.HealthCheckResponse.SERVING,
    )

    # --- Server reflection ---
    service_names = (
        node_registry_pb2.DESCRIPTOR.services_by_name["NodeRegistry"].full_name,
        health_pb2.DESCRIPTOR.services_by_name["Health"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)

    server.add_insecure_port("[::]:50051")
    server.start()
    logger.info("gRPC server listening on port 50051")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
