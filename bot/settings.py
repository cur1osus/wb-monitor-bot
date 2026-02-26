import os
import urllib.parse

from dotenv import load_dotenv
from sqlalchemy import URL

load_dotenv()


class RedisSettings:
    def __init__(self) -> None:
        self.host = os.environ.get("REDIS_HOST", "localhost")
        self.port = int(os.environ.get("REDIS_PORT", 6379))
        self.db = int(os.environ.get("REDIS_DB", 0))
        self.user = os.environ.get("REDIS_USER", "")
        self.password = os.environ.get("REDIS_PASSWORD", "")


class PostgresSettings:
    def __init__(self) -> None:
        self.host = os.environ.get("PSQL_HOST", "localhost")
        self.port = int(os.environ.get("PSQL_PORT", 5432))
        self.db = os.environ.get("PSQL_DB", "database")
        self.user = os.environ.get("PSQL_USER", "user")
        self.password = os.environ.get("PSQL_PASSWORD", "password")


class Settings:
    bot_token: str = os.environ.get("BOT_TOKEN", "")
    developer_id: int = int(os.environ.get("DEVELOPER_ID", 0))
    admin_ids_str: str = os.environ.get("ADMIN_IDS", "")
    dev: bool = os.environ.get("DEV", "false").lower() == "true"
    groq_api_key: str = os.environ.get("GROQ_API_KEY", "")
    groq_model: str = os.environ.get("GROQ_MODEL", "qwen/qwen3-32b")
    groq_fallback_models_str: str = os.environ.get(
        "GROQ_FALLBACK_MODELS", "openai/gpt-oss-120b"
    )

    psql: PostgresSettings = PostgresSettings()
    redis: RedisSettings = RedisSettings()

    @property
    def admin_ids_list(self) -> set[int]:
        raw = self.admin_ids_str.strip()
        return {int(p.strip()) for p in raw.split(",") if p.strip().isdigit()}

    @property
    def groq_fallback_models(self) -> list[str]:
        raw = self.groq_fallback_models_str.strip()
        if not raw:
            return []

        seen: set[str] = set()
        out: list[str] = []
        for item in raw.split(","):
            model = item.strip()
            if not model or model in seen:
                continue
            seen.add(model)
            out.append(model)
        return out

    def psql_dsn(self, is_migration: bool = False) -> URL:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.psql.user,
            password=self.psql.password,
            host=self.psql.host,
            port=self.psql.port,
            database=self.psql.db,
        )

    def redis_url(self) -> str:
        r = self.redis
        if r.user and r.password:
            password = urllib.parse.quote(r.password)
            return f"redis://{r.user}:{password}@{r.host}:{r.port}/{r.db}"
        elif r.password:
            password = urllib.parse.quote(r.password)
            return f"redis://:{password}@{r.host}:{r.port}/{r.db}"
        return f"redis://{r.host}:{r.port}/{r.db}"


se = Settings()
