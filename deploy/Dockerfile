FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY data ./data
COPY evals ./evals
RUN uv sync --frozen --no-dev
ENV PATH=/opt/venv/bin:$PATH
RUN useradd -u 10001 -m closeout && mkdir -p /app/var/uploads && chown -R closeout /app/var
USER closeout
