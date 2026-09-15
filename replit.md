# Alpha Harness on Replit

## Running the app

The app uses two workflows:

- `Alpha Harness frontend` serves the Vite/React UI on port 5000.
- `Alpha Harness backend` serves the FastAPI API on port 8000.

The frontend proxies `/api`, `/ws`, and `/openapi.json` to the backend. The backend stores local operational data under `~/.alpha-harness/`.

Open the Replit preview and sign in through the Alpha Harness UI with the WorldQuant BRAIN account you want to use. BRAIN credentials are entered in the app and are not configured as Replit environment variables.

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