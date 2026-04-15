# Apex Terminal Unified

Unified monorepo for:
- FastAPI backend (`/backend`)
- React/Vite frontend (`/frontend`)
- local Docker Compose
- Render Blueprint deploy (`render.yaml`)
- Railway per-service config (`backend/railway.json`, `frontend/railway.json`)

## Local run

```bash
docker compose up --build
```

Open:
- Frontend: http://localhost:8080
- API docs: http://localhost:8000/docs

## Render one-click style deploy

1. Push this repo to GitHub.
2. In Render, create a new Blueprint from the repo.
3. Render will read `render.yaml` and provision:
   - `apex-api`
   - `apex-terminal`
   - `apex-db`
4. After the first deploy, open the backend shell and run the seed if needed:

```bash
python seed_demo.py
```

### Deploy button

Replace `YOUR_REPO_URL` in this markdown after uploading the repo:

```md
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=YOUR_REPO_URL)
```

## Railway deploy

1. Create a Railway project.
2. Add PostgreSQL.
3. Add two services from the same repo:
   - backend with root directory `/backend`
   - frontend with root directory `/frontend`
4. Set the Railway config file path explicitly:
   - `/backend/railway.json`
   - `/frontend/railway.json`
5. Set variables:
   - backend: `DATABASE_URL`, `SECRET_KEY`, `DATA_PROVIDER`
   - frontend: `BACKEND_UPSTREAM=http://<backend-private-host>:10000`

## Notes

- Production frontend serves built static files with Nginx.
- `/api/*` is proxied internally to the backend.
- In local dev, the same frontend container also proxies `/api/*` to the backend.
- If you want real market data, change `DATA_PROVIDER=demo` to `yfinance`.
