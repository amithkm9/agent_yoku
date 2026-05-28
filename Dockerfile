# Multi-stage build for agent_yoku. Pattern lifted from asato-svc:
# builder installs poetry deps into a venv; runtime stage copies only the venv
# + source so the final image stays small and rootless.

FROM python:3.12-slim AS builder

ENV POETRY_VERSION=1.8.4 \
    POETRY_HOME=/opt/poetry \
    POETRY_NO_INTERACTION=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_PREFER_BINARY=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    POETRY_REQUESTS_TIMEOUT=300

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="${POETRY_HOME}/bin:${PATH}"

WORKDIR /app
COPY pyproject.toml poetry.lock ./
# Install runtime deps INTO /opt/venv. Setting VIRTUAL_ENV makes poetry target
# that venv rather than its own interpreter — without it the runtime stage
# (which copies /opt/venv) ends up empty and every import fails at startup.
RUN python -m venv /opt/venv
ENV VIRTUAL_ENV="/opt/venv" \
    PATH="/opt/venv/bin:${PATH}"
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --without dev \
    && /opt/venv/bin/pip uninstall -y pip setuptools wheel 2>/dev/null || true \
    && find /opt/venv -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true \
    && find /opt/venv -name "*.pyc" -o -name "*.pyo" -delete 2>/dev/null || true


# ---------- Runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

# Create a non-root user.
RUN groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY agent_yoku /app/agent_yoku
COPY scripts /app/scripts

RUN mkdir -p /app/logs && chown -R app:app /app
USER app

EXPOSE 8000

CMD ["python", "-m", "agent_yoku.cli", "api", "--host", "0.0.0.0", "--port", "8000"]
