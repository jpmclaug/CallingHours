# Use official lightweight Python image
FROM python:3.12-slim

# Allow statements and log messages to immediately appear in Cloud Run logs
ENV PYTHONUNBUFFERED=1

# Create and set working directory
ENV APP_HOME=/app
WORKDIR $APP_HOME

# Install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy local application code to container image
COPY . ./

# Create a non-root user for container security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser $APP_HOME
USER appuser

# Cloud Run injects PORT (default 8080)
ENV PORT=8080
ENV HOST=0.0.0.0
EXPOSE 8080

# Start Calling Hours
CMD ["python", "calling_hours.py"]
