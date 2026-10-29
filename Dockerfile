FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" VIGIA_MODO=prod
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY conocimiento ./conocimiento
COPY evals ./evals
RUN uv sync --frozen --no-dev && useradd --system app
USER app
EXPOSE 8000
CMD ["uvicorn", "vigia.app:app", "--host", "0.0.0.0", "--port", "8000"]
