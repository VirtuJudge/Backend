FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH="/app" \
    PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir . "psycopg[binary]==3.3.5" "boto3==1.43.88"

COPY app/ ./app/
COPY --from=local_stack . ./local_stack/

CMD ["python", "-m", "local_stack.worker"]
