# wb-monitor-bot

Telegram bot for monitoring Wildberries products with a modern web dashboard.

## Features

- 🤖 Telegram bot for product monitoring
- 📊 Web dashboard with price history charts
- 📱 Telegram Web App integration
- 🔔 Price, stock, and rating alerts
- 🧠 AI-powered reviews analysis

## Backend (Python/FastAPI)

### Run with uv

1. Create env file:

```bash
cp .env.example .env
```

For reviews analysis via LLM, set `AGENTPLATFORM_API_KEY`,
`AGENTPLATFORM_MODEL` and `AGENTPLATFORM_BASE_URL` in `.env`.

2. Install dependencies:

```bash
uv sync
```

3. Apply migrations:

```bash
uv run --package migrations alembic -c migrations/alembic.ini upgrade head
```

4. Start bot:

```bash
uv run python -m bot
```

### Similar products CLI

Fetch similar products by Wildberries nmId via Selenium (headless Chrome):

```bash
uv run python -m bot.wb_similar_parser --nm-id 12345678 --limit 20 --timeout 20
```

To open a visible browser window, add `--no-headless`.

## Frontend (Next.js/React)

Modern dashboard built with Next.js 16, TypeScript, Tailwind CSS, and Recharts.

### Setup

1. Navigate to frontend directory:
```bash
cd frontend
```

2. Install dependencies:
```bash
npm install
```

3. Create environment file:
```bash
cp .env.example .env.local
```

4. Configure backend URL in `.env.local`:
```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### Development

Run the development server:

```bash
npm run dev
```

The dashboard will be available at [http://localhost:3000](http://localhost:3000).

### Production Build

```bash
npm run build
npm start
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Telegram Bot                          │
│  (aiogram-based, Python)                                │
├─────────────────────────────────────────────────────────┤
│  bot/                                                   │
│  ├── handlers/     - Telegram message handlers          │
│  ├── services/     - Business logic (WB API, DB)        │
│  ├── db/           - SQLAlchemy models                  │
│  └── web/          - FastAPI for Web App                │
│      ├── app.py    - API endpoints                      │
│      └── auth.py   - Telegram auth validation           │
└─────────────────────────────────────────────────────────┘
                          │
                          │ REST API
                          ▼
┌─────────────────────────────────────────────────────────┐
│              Next.js Dashboard (frontend/)               │
│  ├── React components with Recharts charts              │
│  ├── Tailwind CSS styling                               │
│  └── Telegram Web App integration                       │
└─────────────────────────────────────────────────────────┘
```

## Database

The bot uses PostgreSQL for data storage. Make sure to set `DATABASE_URL` in your `.env` file.

Redis is used for task queue management and caching.
