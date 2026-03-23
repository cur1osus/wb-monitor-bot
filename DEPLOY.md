# Deployment Guide

This guide covers deployment options for the WB Monitor Bot with Next.js frontend.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Telegram      │────▶│  Backend         │────▶│  PostgreSQL     │
│   Bot           │     │  (FastAPI)       │     │  Database       │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                              │
                              ▼
                        ┌──────────────────┐
                        │  Frontend        │
                        │  (Next.js)       │
                        └──────────────────┘
```

## Prerequisites

- Docker & Docker Compose
- Domain name (optional, for production)
- SSL certificate (via Let's Encrypt)

## Quick Deploy (Docker Compose)

### 1. Clone and Configure

```bash
git clone https://github.com/cur1osus/wb-monitor-bot.git
cd wb-monitor-bot
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# Bot
BOT_TOKEN=your_telegram_bot_token

# Database
DATABASE_URL=postgresql://user:password@db:5432/wb_monitor

# Redis
REDIS_URL=redis://redis:6379/0

# Frontend
NEXT_PUBLIC_API_URL=https://your-domain.com
```

### 2. Deploy with Docker Compose

```bash
docker compose up -d
```

This starts:
- PostgreSQL database
- Redis cache
- FastAPI backend
- Next.js frontend

### 3. Run Migrations

```bash
docker compose exec backend uv run alembic upgrade head
```

### 4. Verify

Check logs:
```bash
docker compose logs -f
```

Access the dashboard at `http://localhost:3000`

## Production Deployment

### Option 1: VPS (Recommended)

Deploy on a VPS (Hetzner, DigitalOcean, Linode, etc.):

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh

# Clone repository
git clone https://github.com/cur1osus/wb-monitor-bot.git
cd wb-monitor-bot

# Configure environment
cp .env.example .env
nano .env  # Edit with your values

# Deploy
docker compose up -d

# Run migrations
docker compose exec backend uv run alembic upgrade head
```

### Option 2: Separate Frontend (Vercel) + Backend (VPS)

#### Backend (VPS)

```bash
# On your VPS
docker run -d \
  --name wb-backend \
  -p 8000:8000 \
  -e DATABASE_URL=postgresql://... \
  -e REDIS_URL=redis://... \
  -e BOT_TOKEN=... \
  ghcr.io/cur1osus/wb-monitor-bot:latest
```

#### Frontend (Vercel)

1. Push code to GitHub
2. Go to [Vercel](https://vercel.com)
3. Import your repository
4. Set environment variable:
   ```
   NEXT_PUBLIC_API_URL=https://your-backend-domain.com
   ```
5. Deploy

### Option 3: Railway

1. Create account on [Railway](https://railway.app)
2. Create new project from GitHub
3. Add PostgreSQL and Redis services
4. Configure environment variables
5. Deploy

## Environment Variables

### Backend

| Variable | Description | Required |
|----------|-------------|----------|
| `BOT_TOKEN` | Telegram bot token | ✅ |
| `DATABASE_URL` | PostgreSQL connection string | ✅ |
| `REDIS_URL` | Redis connection string | ✅ |
| `AGENTPLATFORM_API_KEY` | LLM API key for reviews analysis | ❌ |
| `AGENTPLATFORM_MODEL` | LLM model name | ❌ |
| `AGENTPLATFORM_BASE_URL` | LLM API base URL | ❌ |

### Frontend

| Variable | Description | Required |
|----------|-------------|----------|
| `NEXT_PUBLIC_API_URL` | Backend API URL | ✅ |

## Docker Compose Example

```yaml
version: '3.8'

services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: wb_monitor
      POSTGRES_PASSWORD: your_password
      POSTGRES_DB: wb_monitor
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U wb_monitor"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  backend:
    build:
      context: .
      dockerfile: Dockerfile.backend
    environment:
      DATABASE_URL: postgresql://wb_monitor:your_password@db:5432/wb_monitor
      REDIS_URL: redis://redis:6379/0
      BOT_TOKEN: ${BOT_TOKEN}
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    ports:
      - "8000:8000"

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    environment:
      NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL}
    depends_on:
      - backend
    ports:
      - "3000:3000"

volumes:
  postgres_data:
  redis_data:
```

## SSL/HTTPS Setup

Use Nginx as reverse proxy with Let's Encrypt:

```bash
# Install certbot
apt install certbot python3-certbot-nginx

# Get certificate
certbot --nginx -d your-domain.com

# Nginx config for backend
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location /api/ {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## Monitoring

### Health Checks

Backend health: `GET https://your-domain.com/health`
Frontend: Check if page loads at `https://your-domain.com`

### Logs

```bash
# Backend logs
docker compose logs backend

# Frontend logs
docker compose logs frontend

# Database logs
docker compose logs db
```

## Backup

### Database Backup

```bash
# Backup
docker compose exec db pg_dump -U wb_monitor wb_monitor > backup.sql

# Restore
docker compose exec -T db psql -U wb_monitor wb_monitor < backup.sql
```

## Troubleshooting

### Frontend can't connect to backend

1. Check `NEXT_PUBLIC_API_URL` is correct
2. Ensure backend is running: `docker compose ps`
3. Check backend logs: `docker compose logs backend`

### Database connection errors

1. Verify `DATABASE_URL` format
2. Check database is healthy: `docker compose ps db`
3. Run migrations: `docker compose exec backend uv run alembic upgrade head`

### Bot not responding

1. Verify `BOT_TOKEN` is correct
2. Check bot logs: `docker compose logs backend`
3. Ensure bot is running: `docker compose ps backend`

## Updates

```bash
# Pull latest changes
git pull origin main

# Rebuild and restart
docker compose up -d --build

# Run new migrations
docker compose exec backend uv run alembic upgrade head
```

## Support

For issues, create a ticket in the GitHub repository or contact support.
