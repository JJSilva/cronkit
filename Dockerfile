# cronkit - container image for Railway.
# Runs every configured tool on its own schedule and serves /health.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip && pip install .

ENV PORT=8000
EXPOSE 8000

CMD ["cronkit", "serve"]
