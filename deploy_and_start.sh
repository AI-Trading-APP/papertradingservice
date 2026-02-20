#!/bin/bash

# Automated deployment and startup script for this repo

# Create virtual environment if not exists
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

# Activate virtual environment
source .venv/bin/activate

# Install dependencies
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
fi

# Start the main service (update the command as needed)
if [ -f "main.py" ]; then
    nohup python main.py > service.log 2>&1 &
fi

# For FastAPI/Uvicorn services
if [ -f "userservice/main.py" ]; then
    nohup python userservice/main.py > userservice.log 2>&1 &
fi
if [ -f "prediction-engine/main.py" ]; then
    nohup python prediction-engine/main.py > prediction-engine.log 2>&1 &
fi

# Add more service startup commands as needed

echo "Deployment and startup complete."
