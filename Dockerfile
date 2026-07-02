# Dockerfile for SIGAP Backend
# Hugging Face Spaces runs this to build and serve our FastAPI app

# Use Python 3.11 slim — smaller image, faster build
FROM python:3.11-slim

# Set working directory inside the container
WORKDIR /app

# Copy requirements first (Docker caches this layer — faster rebuilds)
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all backend code into the container
COPY . .

# Hugging Face Spaces uses port 7860 by default
# We expose it and tell uvicorn to use it
EXPOSE 7860

# Start the FastAPI server
# Using 'python -m uvicorn' instead of just 'uvicorn'
# because in Docker, Python module calls are always reliable
# regardless of how PATH is configured in the container.
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
