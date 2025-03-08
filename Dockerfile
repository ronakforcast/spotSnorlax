FROM python:3.9-slim

WORKDIR /app

# Copy requirements first to leverage Docker caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the script
COPY spot_blacklist.py .

# Set environment variables (can be overridden at runtime)
ENV PYTHONUNBUFFERED=1

# Run as non-root user for better security
RUN adduser --disabled-password --gecos "" appuser
USER appuser

# Command to run when the container starts
ENTRYPOINT ["python", "spot_blacklist.py"]