# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install dependencies
COPY papertradingservice/requirements.txt ./requirements.txt
COPY ai-trading-common /tmp/ai-trading-common
RUN grep -v '^git+https://github.com/AI-Trading-APP/ai-trading-common.git$' requirements.txt > requirements.docker.txt && \
    pip install --no-cache-dir /tmp/ai-trading-common && \
    pip install --no-cache-dir -r requirements.docker.txt

# Copy application code
COPY papertradingservice/main.py papertradingservice/database.py papertradingservice/models.py papertradingservice/price_cache.py papertradingservice/repository.py papertradingservice/storage.py papertradingservice/__init__.py ./

# Create data directory
RUN mkdir -p /app/data

# Create non-root user
RUN useradd --create-home --shell /bin/bash --uid 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8005/health')" || exit 1

# Expose port
EXPOSE 8005

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005"]

