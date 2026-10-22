"""运行时配置（环境变量优先，支持 .env）。"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # PostgreSQL + pgvector（与 docker-compose 的 postgres 服务一致）
    database_url: str = "postgresql+asyncpg://tes:tes123@postgres:5432/tes_edu"

    # Embedding（P2 接入；当前未启用，course_kb.embedding 保持 NULL）
    embed_model: str = "text-embedding-3-small"
    embed_api_base: str = ""
    embed_api_key: str = ""

    # Kafka 异步管道（course_kb_ingest 入库链路）
    # 137 上的 kafka 在 opt_default 网络，broker 宣告地址是容器主机名，
    # 因此本服务必须同时接入 opt_default 才解析得到；容器内用 kafka:9092 引导
    kafka_bootstrap: str = ""
    kafka_topic_ingest: str = "course_kb_ingest"
    kafka_topic_event: str = "student_event"
    kafka_group: str = "course-kb-ingest"
    kafka_group_event: str = "student-event-consumer"

    @property
    def kafka_enabled(self) -> bool:
        """是否启用异步管道；未配置时降级为同步直写。"""
        return bool(self.kafka_bootstrap)

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
