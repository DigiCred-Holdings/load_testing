FROM locustio/locust:2.15.1

# Install additional dependencies
RUN pip install prometheus-client faker

RUN pip install redis

# Set working directory
WORKDIR /mnt/locust

# Copy your test files
COPY ./locust-scripts /mnt/locust/
