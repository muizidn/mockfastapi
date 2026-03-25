# Use a lightweight Python image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install uv
RUN pip install --no-cache-dir uv

# Copy requirements and install dependencies
COPY requirements.txt .
RUN uv pip install --system -r requirements.txt

# Copy the backend code and the UI
COPY main.py .
COPY index.html .

# Create the data and schema directories (it will be overridden by the volume,
# but this ensures correct permissions)
RUN mkdir -p /app/data /app/data/schema

# Expose the port FastAPI runs on
EXPOSE 8000

# Start the application with auto-reload enabled for development
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]