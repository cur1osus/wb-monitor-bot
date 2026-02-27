# wb-monitor-bot

Telegram bot for monitoring Wildberries products.

## Run with uv

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

## Similar products CLI

Fetch similar products by Wildberries nmId via Selenium (headless Chrome):

```bash
uv run python -m bot.wb_similar_parser --nm-id 12345678 --limit 20 --timeout 20
```

To open a visible browser window, add `--no-headless`.
