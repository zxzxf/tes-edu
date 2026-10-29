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

    # M2-10 判分护栏：同一标答累计多少次**有效改判**就冻结该题自动判分。
    # 调小 = 更保守（更容易转人工，老师更累）；调大 = 更容易被错答案批量判错。
    # 环境变量 GRADING_FREEZE_THRESHOLD 可覆盖。
    grading_freeze_threshold: int = 3

    # M2-9 / G1：答案双源校验时是否调用租户 LLM 给出「独立求解建议」。
    # **默认关**：LLM 只给建议、绝不作为发布依据，开着只是让教师多一条参考；
    # 端点额度紧张（403/429）时它还会拖慢发布流程，所以按需开。
    answer_verify_llm: bool = False

    @property
    def kafka_enabled(self) -> bool:
        """是否启用异步管道；未配置时降级为同步直写。"""
        return bool(self.kafka_bootstrap)

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
