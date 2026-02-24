# wb-monitor-bot

Telegram bot for monitoring Wildberries products.

## Run with uv

1. Create env file:

```bash
cp .env.example .env
```

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
