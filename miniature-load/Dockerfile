FROM locustio/locust:2.15.1

# Switch to root to install packages
USER root

# Install additional dependencies
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
RUN pip install --no-cache-dir \
    prometheus-client \
    faker

# Set working directory
WORKDIR /mnt/locust

# Create necessary directories
RUN mkdir -p /mnt/locust/stats && \
    mkdir -p /mnt/locust/loadtest && \
    chown -R locust:locust /mnt/locust

# Copy your test files
COPY --chown=locust:locust ./locust-scripts/* /mnt/locust/loadtest/

# Add the locust-scripts directory to Python path
ENV PYTHONPATH=/mnt/locust/loadtest:$PYTHONPATH

# Switch back to locust user
USER locust