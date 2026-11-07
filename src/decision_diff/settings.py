"""Configuration from environment variables (see ``.env.example``)."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name, default):
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    pg_url: str = field(default_factory=lambda: _env("DD_PG_URL", "postgresql://dd:dd@localhost:5432/dd"))
    amqp_url: str = field(default_factory=lambda: _env("DD_AMQP_URL", "amqp://dd:dd@localhost:5672/dd"))
    data_dir: Path = field(default_factory=lambda: Path(_env("DD_DATA_DIR", "./data")))
    datasets_dir: Path = field(default_factory=lambda: Path(_env("DD_DATASETS_DIR", "./datasets")))
    procedures_dir: Path = field(default_factory=lambda: Path(_env("DD_PROCEDURES_DIR", "./procedures")))
    chunk_apps: int = field(default_factory=lambda: int(_env("DD_CHUNK_APPS", "250")))
    run_window: int = field(default_factory=lambda: int(_env("DD_USER_WINDOW", _env("DD_WORKER_PROCS", "4"))))
    worker_procs: int = field(default_factory=lambda: int(_env("DD_WORKER_PROCS", "4")))
    delivery_limit: int = field(default_factory=lambda: int(_env("DD_DELIVERY_LIMIT", "5")))
    sandbox: str = field(default_factory=lambda: _env("DD_SANDBOX", "strict"))
    sandbox_uid: int | None = field(default_factory=lambda: int(_env("DD_SANDBOX_UID", "20000")))
    sandbox_mem_mb: int = field(default_factory=lambda: int(_env("DD_SANDBOX_MEM_MB", "4096")))
    sandbox_wall_s: int = field(default_factory=lambda: int(_env("DD_SANDBOX_WALL_S", "300")))
    llm_base_url: str = field(default_factory=lambda: _env("DD_LLM_BASE_URL", ""))
    llm_api_key: str = field(default_factory=lambda: _env("DD_LLM_API_KEY", ""), repr=False)
    llm_model: str = field(default_factory=lambda: _env("DD_LLM_MODEL", ""))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("DD_LLM_TIMEOUT_S", "300")))

    @property
    def llm_enabled(self):
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)

    def run_dir(self, run_id):
        return self.data_dir / "runs" / str(run_id)


settings = Settings()
