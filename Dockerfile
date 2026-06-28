FROM python:3.11-slim

# Install system dependencies for geospatial libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgdal-dev \
    libspatialindex-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces require running as a non-root user
RUN useradd -m -u 1000 user
USER user

# Set environment variables
ENV PATH="/home/user/.local/bin:$PATH"
ENV PYTHONUNBUFFERED=1

WORKDIR /home/user/app

# Copy requirements and install
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=user . .

# Expose the default port for Hugging Face Spaces
EXPOSE 7860

# Start FastAPI and the background scheduler
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "7860"]
