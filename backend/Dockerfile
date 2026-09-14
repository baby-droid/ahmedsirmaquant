FROM python:3.14-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy project files
COPY . .

# Sync dependencies
RUN uv sync

# Expose port
EXPOSE 8000

# Run the application
CMD ["uv", "run", "uvicorn", "alpha_harness.main:app", "--host", "0.0.0.0", "--port", "8000"]
