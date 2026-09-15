FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY config ./config
COPY data ./data

RUN pip install --no-cache-dir .

# Run from the source tree so repo-relative paths (config/, data/) resolve;
# the pip install above is for dependencies.
ENV PYTHONPATH=/app/src
ENV EDGE_CONFIG_DIR=/app/config
ENV EDGE_DATA_DIR=/app/data

CMD ["python", "-m", "edge.shadow.runner"]
