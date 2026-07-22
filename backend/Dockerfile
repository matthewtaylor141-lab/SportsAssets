FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY sportsassets ./sportsassets
COPY migrations ./migrations
COPY start.sh ./

RUN pip install --no-cache-dir . && chmod +x start.sh

CMD ["sh", "start.sh"]
