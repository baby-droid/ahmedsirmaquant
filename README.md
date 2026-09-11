# Alpha Harness

A local app for generating Alphas on [WorldQuant BRAIN](https://platform.worldquantbrain.com). It runs entirely on your machine.

## Requirements

- [uv](https://docs.astral.sh/uv/) 0.12 or newer (installs Python 3.14 for you)
- [Node.js](https://nodejs.org) 22.12 or newer
- [pnpm](https://pnpm.io) 12 or newer

## Setup

```bash
git clone <repo-url> alpha-harness
cd alpha-harness

cd backend && uv sync
cd ../frontend && pnpm install
```

## Run

Start the backend and the frontend in two terminals:

```bash
# Terminal 1
cd backend && uv run uvicorn alpha_harness.main:app --port 8000
```

```bash
# Terminal 2
cd frontend && pnpm dev
```

Open http://localhost:5173 and sign in with your BRAIN account.

## Local data

Your login, API keys, simulations and synced data are stored in `~/.alpha-harness/`. To use another folder, create a `.env` file in the repository root:

```bash
AH_DATA_DIR=/path/to/folder
```
