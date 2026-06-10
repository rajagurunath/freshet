FROM python:3.11-slim

# Install build dependencies for native extensions (pyarrow, lancedb)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY contexthub/ ./contexthub/

# Install the package (production deps only)
RUN pip install --no-cache-dir -e .

# Create default data directories
RUN mkdir -p /app/data/lancedb /app/data/blobs

# Expose API port
EXPOSE 8787

# Run with uvicorn
CMD ["uvicorn", "contexthub.main:app", "--host", "0.0.0.0", "--port", "8787"]
