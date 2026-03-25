# Use a lightweight Python image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements and install them
# We only need fastapi and uvicorn for this project
RUN pip install --no-cache-dir fastapi uvicorn

# Copy the backend code and the UI
COPY main.py .
COPY index.html .

# Create the data directory (it will be overridden by the volume, 
# but this ensures correct permissions)
RUN mkdir -p /app/data

# Expose the port FastAPI runs on
EXPOSE 8000

# Start the application with auto-reload enabled for development
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]