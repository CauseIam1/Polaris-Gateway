FROM python:3.11-slim

WORKDIR /app

# Install SSH client, Docker CLI, and build dependencies
RUN apt-get update && apt-get install -y \
    openssh-client \
    docker.io \
    curl \
    procps \
    adb \
    && rm -rf /var/lib/apt/lists/*

# Install base Python dependencies first (lightweight packages)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir flask>=3.0.0 flask-cors>=4.0.0 flask-socketio>=5.3.0 requests>=2.31.0 python-socketio>=5.0.0 watchdog>=3.0.0 gunicorn>=23.0.0 gevent>=23.9.0 gevent-websocket>=0.10.1

# Install chromadb separately with extended timeout (heavy dependency)
RUN pip install --no-cache-dir --default-timeout=1000 chromadb==0.5.23

# Copy application code
COPY *.py .
COPY *.json .
COPY *.html .
COPY run_gateway.sh .
COPY sandbox_static/ ./sandbox_static/

# Create data directory for persistence
RUN mkdir -p /app/data

# Create directories for mounted volumes
RUN mkdir -p /playground/www /livecharts

# Make script executable
RUN chmod +x run_gateway.sh

# Expose gateway ports (Chat Gateway on 5000, Burst Receiver on 5001, Sandbox on 7007)
EXPOSE 5000
EXPOSE 5001
EXPOSE 7007

# Run the Unified Polaris Gateway launcher (starts Chat Gateway, Burst Receiver, and Sandbox Gateway)
CMD ["./run_gateway.sh"]