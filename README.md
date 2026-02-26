# wb-monitor-bot

Telegram bot for monitoring Wildberries products.

## Run with uv

1. Create env file:

```bash
cp .env.example .env
```

For reviews analysis via LLM, set `AGENTPLATFORM_API_KEY`,
`AGENTPLATFORM_MODEL` and `AGENTPLATFORM_BASE_URL` in `.env`.

Optional similar-products browser provider:

- Set `WB_SIMILAR_PROVIDER=browser` (or `auto`) in `.env`.
- Install browser once: `uv run playwright install chromium`.

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
