# Starts the API on http://localhost:8000 (run from the project root, venv activated).
$ErrorActionPreference = "Stop"
uvicorn app.main:app --host 127.0.0.1 --port 8000
