#!/bin/bash

# Alpha Harness - Unified Start Script
# Starts both backend and frontend services

set -e

echo "==================================="
echo "Alpha Harness - Unified Start"
echo "==================================="
echo ""

# Check if running on macOS or Linux
OS_TYPE=$(uname -s)

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "Shutting down services..."
    kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
}

trap cleanup EXIT

# Start backend
echo "Starting backend..."
cd backend
uv run uvicorn alpha_harness.main:app --port 8000 &
BACKEND_PID=$!
echo "Backend started (PID: $BACKEND_PID)"
cd ..

# Wait a moment for backend to start
sleep 2

# Start frontend
echo "Starting frontend..."
cd frontend
pnpm dev &
FRONTEND_PID=$!
echo "Frontend started (PID: $FRONTEND_PID)"
cd ..

echo ""
echo "==================================="
echo "Services Running"
echo "==================================="
echo "Backend:  http://localhost:8000"
echo "Frontend: http://localhost:5173"
echo ""
echo "Press Ctrl+C to stop all services"
echo "==================================="

# Wait for both processes
wait $BACKEND_PID $FRONTEND_PID
