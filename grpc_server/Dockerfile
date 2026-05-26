FROM python:3.12-slim

WORKDIR /app

# Install Python dependencies first (better layer caching).
COPY grpc_server/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy proto definition and generate stubs into /app.
COPY proto/ proto/
RUN python -m grpc_tools.protoc \
      -I proto \
      --python_out=. \
      --grpc_python_out=. \
      proto/node_registry.proto

# Copy application source.
COPY grpc_server/ .

EXPOSE 50051

CMD ["python", "server.py"]
