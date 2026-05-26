.PHONY: proto up down logs

# Generate gRPC stubs locally (requires grpcio-tools installed).
# Stubs are written directly into each service directory so they are
# importable when running services outside Docker.
proto:
	python -m grpc_tools.protoc -I proto \
		--python_out=grpc_server \
		--grpc_python_out=grpc_server \
		proto/node_registry.proto
	python -m grpc_tools.protoc -I proto \
		--python_out=gateway \
		--grpc_python_out=gateway \
		proto/node_registry.proto

up:
	docker compose up --build -d

down:
	docker compose down -v

logs:
	docker compose logs -f
