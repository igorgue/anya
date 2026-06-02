FROM python:3.14-slim

WORKDIR /app

# Install system deps for cryptography and sqlite
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY router/ router/

# Install dependencies
RUN pip install --no-cache-dir .

# Expose port (matches ROUTER_PORT default)
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8080/health').raise_for_status()" || exit 1

# Run with uvicorn
CMD ["python", "-m", "router.main"]
