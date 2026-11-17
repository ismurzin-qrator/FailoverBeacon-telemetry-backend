FROM python:3.13.18-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv PATH=/app/.venv/bin:$PATH
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY ingest/gunicorn.conf.py ./
RUN uv sync --locked --no-dev
# A new volume takes the owner of this directory, so the geo update can write as nobody.
RUN mkdir -p /var/lib/beacon/geo && chown nobody /var/lib/beacon/geo

USER nobody
EXPOSE 8080
CMD ["gunicorn", "--config", "gunicorn.conf.py", "beacon.app:create_app"]
