# Production image for the Gatekeeper AI / Flare Control System backend.
FROM python:3.11-slim

# Predictable, quiet Python; no .pyc clutter; no pip cache bloat.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Install dependencies first so this layer is cached across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY app ./app

# Documented default port. Hosts (Render/Railway/etc.) inject their own $PORT,
# which the start command below respects.
EXPOSE 8000

# Shell form so ${PORT} is expanded at runtime. Falls back to 8000 locally.
#
# --workers 1 is load-bearing, not decoration: the control loop is a single
# background task owning one shared ControllerState (app/services/controller.py).
# A second worker means a second controller fighting over the same fleet. Passing
# the flag explicitly also beats uvicorn's $WEB_CONCURRENCY fallback, which would
# otherwise fork silently if that env var were ever set on the host.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
