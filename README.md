# Alpha Harness

A local app for generating Alphas on [WorldQuant BRAIN](https://platform.worldquantbrain.com). It runs entirely on your machine.

## Requirements

- [uv](https://docs.astral.sh/uv/) 0.12 or newer (installs Python 3.14 for you)
- [Node.js](https://nodejs.org) 22.12 or newer
- [pnpm](https://pnpm.io) 12 or newer
- [Docker](https://www.docker.com/) (optional, for containerized development)

## Quick Start (Unified UI)

### Option 1: Single Command (Recommended - macOS/Linux)

```bash
./run.sh
```

This starts both backend and frontend automatically in one process.

### Option 2: Single Command (Windows)

```powershell
.\run.ps1
```

### Option 3: Docker Compose (Platform Independent)

```bash
docker-compose up
```

The application will open at: **http://localhost:5173**

The backend API will be available at: **http://localhost:8000**

---

## Setup

```bash
git clone <repo-url> alpha-harness
cd alpha-harness

cd backend && uv sync
cd ../frontend && pnpm install
```

## Run (Manual - Two Terminals)

If you prefer to run components separately:

**Terminal 1 - Backend:**
```bash
cd backend && uv run uvicorn alpha_harness.main:app --port 8000
```

**Terminal 2 - Frontend:**
```bash
cd frontend && pnpm dev
```

Then open http://localhost:5173 and sign in with your BRAIN account.

## Architecture

### Unified UI Integration

The frontend and backend are now integrated to work seamlessly as one complete application:

- **Frontend** communicates with backend through a unified API client (`frontend/src/api/client.ts`)
- **Backend** serves the REST API on port 8000 with proper CORS configuration
- **Automatic startup scripts** handle service orchestration
- **Docker support** for containerized development and deployment

### Environment Configuration

Create `frontend/.env` for custom API configuration:

```bash
VITE_API_URL=http://localhost:8000
VITE_API_TIMEOUT=30000
VITE_ENV=development
```

See `frontend/.env.example` for all available options.

## Local Data

Your login, API keys, simulations and synced data are stored in `~/.alpha-harness/`. To use another folder, create a `.env` file in the repository root:

```bash
AH_DATA_DIR=/path/to/folder
```

## Key Files for Integration

- **`run.sh`** - Unix/Linux/macOS startup script
- **`run.ps1`** - Windows PowerShell startup script
- **`docker-compose.yml`** - Docker orchestration
- **`frontend/src/api/client.ts`** - Unified API client
- **`frontend/src/hooks/useApi.ts`** - React hooks for API calls
- **`DEVELOPMENT.md`** - Detailed development guide

## API Integration Example

### Using the API in Your Pages

The frontend includes ready-to-use hooks for seamless backend integration:

```typescript
import { useGet, useApiCall } from '@/hooks/useApi';

export function MyAlphaPage() {
  // Fetch data automatically
  const { data: alphas, loading, error, refetch } = useGet('/api/alphas');

  // Manual API calls
  const { call, loading: saving } = useApiCall();

  const handleCreate = async () => {
    await call('POST', '/api/alphas', { 
      name: 'New Alpha',
      code: 'your_alpha_code_here'
    });
    refetch(); // Refresh the data
  };

  if (loading) return <div>Loading...</div>;
  if (error) return <div>Error: {error}</div>;

  return (
    <div>
      <button onClick={handleCreate} disabled={saving}>
        {saving ? 'Creating...' : 'Create Alpha'}
      </button>
      {alphas?.map(alpha => (
        <div key={alpha.id}>{alpha.name}</div>
      ))}
    </div>
  );
}
```

## Troubleshooting

### Backend won't connect
- Ensure backend is running on port 8000
- Check `VITE_API_URL` in `frontend/.env`
- Verify CORS is enabled in backend
- Run health check: `curl http://localhost:8000/health`

### Frontend won't load
- Check if port 5173 is available
- Clear `node_modules`: `rm -rf frontend/node_modules && cd frontend && pnpm install`
- Check browser console for API errors

### Docker issues
- Ensure Docker daemon is running
- Rebuild images: `docker-compose up --build`
- View logs: `docker-compose logs -f`

## Documentation

For detailed setup, development workflow, and advanced configuration, see:
- **[DEVELOPMENT.md](./DEVELOPMENT.md)** - Complete development guide
- **[frontend/.env.example](./frontend/.env.example)** - Frontend environment variables
- **Backend API docs** - Available at http://localhost:8000/docs (Swagger UI)

## Project Links

- **Repository**: https://github.com/baby-droid/ahmedsirmaquant
- **Live Demo**: https://ahmedsirmaquant.vercel.app
- **WorldQuant BRAIN**: https://platform.worldquantbrain.com
