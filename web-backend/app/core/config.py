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

    # M3-8 定时采集调度器：随 web-backend 启动，每分钟检查到期 cron 任务。
    # 单测/本地不想起后台循环时设 CRAWL_SCHEDULER_ENABLED=false。
    crawl_scheduler_enabled: bool = True
    crawl_scheduler_interval: int = 60

    # ---- M5-2 非结构化源（url/web）的「网页 -> 题目」LLM 抽取 ----
    # **默认关**：LLM 抽取有 token 成本，且必须显式确认模型通道已就绪；
    # 关闭时非结构化源仍如实记录「需 LLM 抽取」原因，不静默 0 条、绝不编造题目。
    crawl_llm_extract_enabled: bool = False
    # 单次抽取成本控制：清洗后正文最多送多少字符、最多抽多少题、模型超时秒数。
    crawl_llm_max_chars: int = 20000
    crawl_llm_max_items: int = 20
    crawl_llm_timeout: float = 90.0
    # 全局模型通道兜底（租户 BYOM 未配置时使用；密钥只走环境变量，不进 git）。
    # 留空 = 仅用租户 BYOM；都没有则明确报「未配置模型」而不是静默成功。
    crawl_llm_base_url: str = ""
    crawl_llm_api_key: str = ""
    crawl_llm_model: str = ""

    # M5-5 版权授权清单：逗号分隔的 URL 前缀或主机名。
    # 留空保持历史兼容行为，仅执行保守黑名单；配置后只允许清单内来源。
    crawl_compliance_allowlist: str = ""

    # M5-7 自主发现：搜索引擎 API（让 discovery 按 job.query 关键词真实搜索资源 URL，
    # 替代仅靠可信域名种子抓首页的弱兜底；JS 重度站点静态抓首页拿不到题，靠搜索结果页补足）。
    # 默认关：需配置 SEARCH_API_KEY 才真正启用；未启用时 discovery 退化为种子兜底（向后兼容）。
    # 密钥只走环境变量（web-backend.env），绝不进 git / 镜像。
    search_api_enabled: bool = False
    search_api_provider: str = "serper"   # 当前仅实现 serper（Google 结果聚合）
    search_api_key: str = ""
    search_api_url: str = ""              # 留空用 provider 默认端点
    search_api_max_results: int = 10

    # M5-7-8 发现策略增强：递归站内探索 + headless 渲染（让 discovery 在 JS 重度站点
    # 也能抓到真实题面，替代纯静态首页兜底）。均默认保守、向后兼容。
    # 站内链接探索深度：1=只探首页直链（默认，等同旧行为）；2+=BFS 纵深；0=不探索。
    discovery_crawl_depth: int = 1
    # 单次 discovery 递归探索的总页面预算（防失控 / 控出网成本与耗时）。
    discovery_crawl_max_pages: int = 40
    # headless 渲染（抓 JS 站点正文）：默认关，保持本地/单测行为不变；
    # 137 生产由 deploy/publish_backend.sh 的 docker run 注入 true（见下方注释）。
    discovery_headless_enabled: bool = False
    # 可选：外部 headless 渲染服务端点（POST {"url":...} -> {"html":...} 或纯文本 HTML）。
    # 推荐用于生产：把 chromium 隔离在独立容器，避免拖垮 137 的 2G web-backend。
    # 留空但开启 headless 时，discovery._make_renderer 会默认使用同网络的
    # http://headless-render:8000/render（edu-headless-render 容器）。
    discovery_render_url: str = ""
    discovery_headless_timeout: float = 20.0

    # 学生端读缓存（Redis）。留空 = 全部直查 DB（本地单测/未部署 Redis 时的降级路径）。
    # 137 上由 web-backend.env 注入 redis://:<pw>@redis:6379/0，与网关共用同一实例。
    redis_url: str = ""
    # 卷题快照发布后不变，缓存可长；判分结果会被人工改判/标答复核改写，TTL 必须短。
    exam_cache_ttl: int = 3600
    exam_result_cache_ttl: int = 300
    permission_cache_ttl: int = 60

    @property
    def kafka_enabled(self) -> bool:
        """是否启用异步管道；未配置时降级为同步直写。"""
        return bool(self.kafka_bootstrap)

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
