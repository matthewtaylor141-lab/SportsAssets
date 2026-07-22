FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY sportsassets ./sportsassets
COPY migrations ./migrations

RUN pip install --no-cache-dir .

CMD ["uvicorn", "sportsassets.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
