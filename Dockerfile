# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y ffmpeg curl wget git && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .
COPY cookies.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app code
COPY . .

# Create uploads directory
RUN mkdir -p /app/temp

# Expose port
EXPOSE 5000

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=app.py

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Run the application
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--timeout", "300", "--workers", "1", "--max-requests", "100", "app:app"]