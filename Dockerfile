FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if any are needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Ensure the src directory is in PYTHONPATH
ENV PYTHONPATH=/app/src

# Default port for web app
EXPOSE 5001

# The entrypoint will be overridden by docker-compose for different services
ENTRYPOINT ["python", "main.py"]
