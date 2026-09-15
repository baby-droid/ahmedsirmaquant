# A-SIRMA QUANT on Replit

## Running the app

The app uses one coordinated workflow:

- `Alpha Harness` starts the FastAPI backend on internal port 8000, waits for its health check,
  then serves the Vite/React UI on preview port 5000.

The frontend proxies `/api`, `/ws`, and `/openapi.json` to the backend. The backend stores local operational data under `~/.alpha-harness/`. The frontend reconnects its WebSocket and retries safe reads after a transient backend restart.

Open the Replit preview and sign in through the A-SIRMA QUANT UI with the WorldQuant BRAIN account you want to use. BRAIN credentials are entered in the app and are not configured as Replit environment variables.

## Manual commands

```bash
cd backend
uv sync --frozen
uv run uvicorn alpha_harness.main:app --host 0.0.0.0 --port 8000
```

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev --host 0.0.0.0 --port 5000
```

The backend workflow exports the system C++ library path required by DuckDB in this environment before starting Uvicorn.