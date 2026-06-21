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
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
