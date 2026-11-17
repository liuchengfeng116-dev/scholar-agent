"""全局配置：从项目根目录的 .env 读取，所有密钥只通过环境变量注入。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 大模型
    dashscope_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    llm_model_strong: str = "qwen-max"
    llm_model_fast: str = "qwen-flash"
    vlm_model: str = "qwen3-vl-plus"
    ocr_model: str = "qwen-vl-ocr"
    embedding_model: str = "text-embedding-v3"
    rerank_model: str = "gte-rerank"

    # 外部学术服务
    semantic_scholar_api_key: str = ""
    github_token: str = ""
    crossref_mailto: str = ""

    # 服务
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    jwt_secret: str = "please-change-me"
    jwt_expire_hours: int = 72
    database_url: str = ""
    data_dir: str = "./data"
    cors_origins: str = "http://localhost:5173"

    reviewer_username: str = "reviewer"
    reviewer_password: str = "change-me"

    arxiv_track_categories: str = "cs.CL,cs.LG,cs.AI"
    arxiv_track_cron: str = "0 6 * * *"

    @property
    def data_path(self) -> Path:
        """数据目录（相对路径以项目根目录为基准）。"""
        p = Path(self.data_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_url(self) -> str:
        if self.database_url and not self.database_url.startswith("sqlite:///./"):
            return self.database_url
        return f"sqlite:///{(self.data_path / 'scholar.db').as_posix()}"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
