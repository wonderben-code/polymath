FROM node:24-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm@sha256:6dd8b6dab6b3adc7812bab69eca5dbdc1a5cc690ad28306d234ffb4ef8ac9753 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_CACHE_DIR=/tmp/uv-cache
WORKDIR /app
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir --disable-pip-version-check uv==0.12.23
COPY backend/ /app/backend/
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    uv sync --project backend --locked --no-dev --no-editable
COPY --from=web /build/web/dist /app/web/dist
COPY scripts/polymath-client.py /app/scripts/polymath-client.py
RUN groupadd --gid 10001 polymath && useradd --uid 10001 --gid polymath --no-create-home polymath \
    && chmod -R a+rX /app
USER 10001:10001
ENV STATIC_DIR=/app/web/dist
EXPOSE 8000
CMD ["/app/backend/.venv/bin/uvicorn", "polymath.main:app", "--host", "0.0.0.0", "--port", "8000"]
