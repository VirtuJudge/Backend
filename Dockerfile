FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --locked

COPY . .

RUN chmod +x scripts/start.sh

EXPOSE 8000

CMD ["scripts/start.sh"]
