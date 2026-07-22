FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY config ./config
COPY data ./data

RUN pip install --no-cache-dir .

CMD ["python", "-m", "edge.shadow.runner"]
