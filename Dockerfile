FROM node:22.23-slim AS web
WORKDIR /web
COPY src/web/package.json src/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/web/ ./
RUN npx tsc -b && npx vite build --outDir /out

FROM python:3.12-slim AS app
RUN apt-get update && apt-get install -y --no-install-recommends libseccomp2 tini && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.11.2
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 DD_DATA_DIR=/data DD_DATASETS_DIR=/datasets DD_PROCEDURES_DIR=/procedures
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ src/
COPY --from=web /out src/decision_diff/web
RUN uv sync --frozen --no-dev && useradd --system --uid 10001 --home /app dd && mkdir -p /data/tmp && chown -R dd /data
COPY procedures/ /procedures/
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "decision_diff.api:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "5", "--proxy-headers"]
