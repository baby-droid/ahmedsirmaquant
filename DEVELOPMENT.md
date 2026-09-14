# Alpha Harness - Development Guide

This guide explains how to run the combined backend and frontend application as a unified UI.

## Quick Start

### Option 1: Using Docker Compose (Recommended)

```bash
docker-compose up
```

The application will be available at:
- **Frontend UI:** http://localhost:5173
- **Backend API:** http://localhost:8000

### Option 2: Using Startup Scripts

#### macOS / Linux

```bash
chmod +x run.sh
./run.sh
```

#### Windows (PowerShell)

```powershell
.\run.ps1
```

### Option 3: Manual Setup (Two Terminals)

**Terminal 1 - Backend:**
```bash
cd backend
uv sync
uv run uvicorn alpha_harness.main:app --port 8000
```

**Terminal 2 - Frontend:**
```bash
cd frontend
pnpm install
pnpm dev
```

Then open http://localhost:5173

## Architecture

### Backend
- **Framework:** FastAPI (Python)
- **Port:** 8000
- **Purpose:** REST API for Alpha generation and data management
- **Entry Point:** `backend/alpha_harness/main:app`

### Frontend
- **Framework:** Vite + React/TypeScript
- **Port:** 5173
- **Purpose:** Web UI for the application
- **API Integration:** Uses `src/api/client.ts` for backend communication

## API Communication

### Environment Configuration

Create a `.env` file in the `frontend/` directory:

```bash
VITE_API_URL=http://localhost:8000
VITE_API_TIMEOUT=30000
VITE_ENV=development
```

### Using the API Client

The frontend includes a unified API client in `src/api/client.ts` with hooks in `src/hooks/useApi.ts`.

**Example Usage:**

```typescript
import { useGet, useApiCall } from '@/hooks/useApi';

function MyComponent() {
  // Fetch data
  const { data, loading, error, refetch } = useGet('/api/alphas');

  // Make API calls
  const { call, loading: calling } = useApiCall();

  const handleCreate = async () => {
    await call('POST', '/api/alphas', { name: 'My Alpha' });
    refetch();
  };

  return (
    <div>
      {loading && <p>Loading...</p>}
      {error && <p>Error: {error}</p>}
      {data && <p>{JSON.stringify(data)}</p>}
      <button onClick={handleCreate} disabled={calling}>Create</button>
    </div>
  );
}
```

## Backend API Integration

### CORS Configuration

Ensure your FastAPI backend has CORS properly configured to allow requests from the frontend:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Health Check Endpoint

Add a health check endpoint to your backend:

```python
@app.get("/health")
async def health():
    return {"status": "ok"}
```

## Directory Structure

```
.
├── backend/
│   ├── alpha_harness/
│   │   ├── main.py          # FastAPI application
│   │   └── ...              # Backend modules
│   ├── Dockerfile           # Backend container
│   └── pyproject.toml       # Python dependencies
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   │   └── client.ts    # API client
│   │   ├── hooks/
│   │   │   └── useApi.ts    # API hooks
│   │   ├── pages/           # Page components
│   │   ├── components/      # Reusable components
│   │   └── App.tsx          # Main app component
│   ├── Dockerfile           # Frontend container
│   ├── vite.config.ts       # Vite configuration
│   └── package.json         # Node dependencies
├── docker-compose.yml       # Docker compose setup
├── run.sh                   # Unix startup script
├── run.ps1                  # Windows startup script
└── DEVELOPMENT.md           # This file
```

## Troubleshooting

### Backend won't start
- Check if port 8000 is already in use: `lsof -i :8000` (macOS/Linux) or `netstat -ano | findstr :8000` (Windows)
- Ensure Python 3.14+ is installed: `python --version`
- Run `uv sync` in the backend directory to update dependencies

### Frontend won't start
- Check if port 5173 is already in use: `lsof -i :5173` (macOS/Linux) or `netstat -ano | findstr :5173` (Windows)
- Ensure Node.js 22.12+ is installed: `node --version`
- Clear node modules and reinstall: `rm -rf node_modules && pnpm install`

### API calls failing (CORS errors)
- Verify backend is running: `curl http://localhost:8000/health`
- Check CORS configuration in backend `main.py`
- Verify `VITE_API_URL` environment variable is set correctly

### API calls timing out
- Check backend performance and logs
- Increase `VITE_API_TIMEOUT` in frontend `.env` file

## Development Workflow

1. Make changes to backend code
   - Backend auto-reloads with uvicorn when using `--reload` flag
   - Restart if needed: `uv run uvicorn alpha_harness.main:app --reload`

2. Make changes to frontend code
   - Frontend auto-refreshes with Vite HMR
   - Changes appear instantly in browser

3. Test API integration
   - Use the `useGet` and `useApiCall` hooks in React components
   - Check browser DevTools Network tab for API requests
   - Check backend logs for errors

## Deployment

### Docker Deployment

```bash
docker-compose -f docker-compose.yml up --build
```

### Vercel / Netlify (Frontend Only)

Set environment variables in deployment platform:
```
VITE_API_URL=https://your-backend-api.com
```

### Production Checklist

- [ ] Set `VITE_ENV=production` in frontend
- [ ] Configure CORS for production domain in backend
- [ ] Set secure cookie flags in backend
- [ ] Enable HTTPS/SSL
- [ ] Configure error logging and monitoring
- [ ] Set appropriate API rate limits

## Additional Resources

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Vite Documentation](https://vitejs.dev/)
- [React Documentation](https://react.dev/)
- [pnpm Documentation](https://pnpm.io/)
- [uv Documentation](https://docs.astral.sh/uv/)
