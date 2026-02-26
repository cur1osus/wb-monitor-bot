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
    agentplatform_api_key: str = os.environ.get("AGENTPLATFORM_API_KEY", "")
    agentplatform_model: str = os.environ.get("AGENTPLATFORM_MODEL", "qwen/qwen3-32b")
    agentplatform_base_url: str = os.environ.get(
        "AGENTPLATFORM_BASE_URL",
        "https://litellm.tokengate.ru/v1",
    )

    psql: PostgresSettings = PostgresSettings()
    redis: RedisSettings = RedisSettings()

    @property
    def admin_ids_list(self) -> set[int]:
        raw = self.admin_ids_str.strip()
        return {int(p.strip()) for p in raw.split(",") if p.strip().isdigit()}

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
