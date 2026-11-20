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

    # ---- M1-7 提交异步判分（2026-11-18 降级版接线）----
    # False（默认）：提交请求内同步判完再返回整卷结果（现状行为，纯函数 0 token）。
    # True：提交只落原始作答（submission=grading / item=pending）即返回，
    #       判分管线移交 FastAPI BackgroundTasks（独立会话），客户端轮询
    #       /result 直至翻为 graded/manual_pending。判分循环体两条路径**完全相同**，
    #       将来判分链路引入 LLM（交叉验证/归因）时只需打开此开关，无需改代码。
    # 卡面验收「提交 100ms 内返回」在 True 下成立；False 下 MVP 题量实测 <1s。
    exam_async_grading: bool = False

    # ---- M1-6 答题附件（解答题图片上传）----
    # 私有目录：只经 /api/student/exam-attachments 路由取流，不做静态挂载。
    exam_attachment_dir: str = "data/exam_attachments"

    # ---- ④·数字人讲题短视频（#28）管线 ----
    # 总开关：true 时启用 LiteVideoGenerator（ffmpeg 字幕幻灯片，无 GPU 也可合成）。
    # 需部署镜像内含 ffmpeg + 中文字体（Dockerfile 已 apt 安装）；
    # 关闭请设环境变量 DIGITAL_HUMAN_ENABLED=false；缺 ffmpeg/字体时生成优雅降级为 failed。
    digital_human_enabled: bool = True
    # 合成产物目录：MinIO 接入前为「本地静态回退目录」，接入后仅作兜底（见 app/services/object_storage.py）。
    explanation_video_dir: str = "data/explanation_videos"
    # ffmpeg 二进制（容器内需安装；Dockerfile 已加 apt 安装 ffmpeg）。
    ffmpeg_bin: str = "ffmpeg"
    # 阿里云百炼 CosyVoice TTS（配音；留空则只出无声字幕视频）。
    dashscope_tts_api_key: str = ""
    dashscope_tts_base_url: str = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation"
    dashscope_tts_model: str = "cosyvoice-v1"
    # 可选：异步生成 Kafka 主题；未配置 kafka 时走 BackgroundTasks 同步降级。
    kafka_topic_explanation_video: str = "explanation_video_gen"

    # ---- 通用 App 推送中心（Push Hub，docx/通用App推送中心实施计划.md）----
    # 消息分发主题；未配置 kafka 时 emit 自动降级 BackgroundTasks 直写（同讲解视频先例）。
    kafka_topic_push_dispatch: str = "push_dispatch"
    kafka_group_push_dispatch: str = "push-dispatch-consumer"
    # portal 会话令牌独立密钥+受众（push-hub-portal）——与主站 JWT 互不互认，身份域隔离底线。
    push_portal_secret: str = ""          # 生产由 web-backend.env 注入；空=portal 登录拒绝
    push_secret_key: str = ""             # Fernet key（渠道凭证加密）；空=渠道凭证存不了也解不了
    # token 档位（月）固定四选一，不支持永不过期；宽限期=续期后旧 token 并行天数。
    push_token_grace_days: int = 7
    # 消息默认 TTL（秒）：调用方不传、主题不配时兜底（7 天）。
    push_message_default_ttl: int = 604800
    # 收件箱长轮询服务端挂起上限（秒）。**必须 <25**：Cloudflare 边缘 30s 超时，
    # 判分 502 事故教训（solution.py 同款红线），客户端超时要比它更大。
    push_poll_max_wait: int = 25
    # 业务钩子（判分完成/作业下发 → 推送中心 emit）总开关；紧急停推送时关它，
    # 业务主链路完全不受影响（钩子本就是 fire-and-forget）。
    push_business_notify_enabled: bool = True

    # ---- 股票盯盘业务中心（docx/股票盯盘业务中心实施计划.md v1.4）----
    # worker 随主进程 lifespan 起（137 内存红线不建独立容器）；紧急关停环境
    # 变量置 false 即可，读接口不受影响（只是快照不再自动刷新）。
    stock_worker_enabled: bool = True
    stock_worker_interval: int = 15  # 秒；新浪批量接口一次请求，远够限频红线
    # 非交易时段是否仍拉源（false=收盘/周末空转省源配额，读路径 miss 现拉兜底）
    stock_worker_fetch_off_hours: bool = False

    # ---- ③·对象存储（MinIO，替代本地静态目录）----
    # minio_endpoint 留空 = 走本地静态回退（兼容未部署 MinIO 的环境）；
    # 配齐后 run_generation 把 mp4 上传 MinIO，并由 web-backend 内部流式代理对外提供
    # （外部 URL / 网关 / 学生端均不变，MinIO 仅内网可达）。
    minio_endpoint: str = ""            # 例：http://minio:9000
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "tes-edu-explanation"
    minio_use_ssl: bool = False

    # ---- ⑤·真数字人（腾讯云智能数智人 IVH 播报视频制作，SaaS 出卡、137 零 GPU）----
    # 实现选择：lite=ffmpeg 字幕卡片（默认、零依赖）；ivh=SaaS 数智人肖像口播。
    # IVH 未配置 / 提交失败 / 超时 / 出片失败时**自动回退 Lite**，业务不中断。
    # 注意：这不是「交互数智人」，不需要 virtualmanProjectId，也不建长连接。
    digital_human_impl: str = "lite"            # lite | ivh
    # aPaaS 凭证：只走环境变量（web-backend.env），**绝不进 git / 镜像 / 日志**。
    # 签名由 ivh_client 每次用 accesstoken 对 appkey+timestamp 动态计算，
    # 平台给的静态 sign 字段不能用（会过期），请勿配置。
    ivh_base_url: str = "https://gw.tvs.qq.com"
    ivh_appkey: str = ""
    ivh_accesstoken: str = ""
    # 照片免训练接口：一张形象照 + 文本/音频 -> 肖像口播视频。RefPhotoUrl 必须公网可访问。
    # 说明：137 的 MinIO 仅内网可达，不能当 RefPhotoUrl 用，需 COS 公有读或预签名 URL。
    ivh_ref_photo_url: str = ""
    # 驱动方式：Text=平台内置 TTS（只传文本，最省事）；
    # OriginalVoice=复用本地 _tts 产出的音频（需公网可访问音频 URL）。
    ivh_driver_type: str = "Text"              # Text | OriginalVoice
    # OriginalVoice 模式下：把 _tts 产物上传到公网后返回 URL 的回调前缀；
    # 留空则 OriginalVoice 不可用并自动改走 Text（不中断）。
    ivh_audio_public_base: str = ""
    # Text 驱动时的音色 / 语速（SpeechParam 必填项）。
    ivh_timbre_key: str = "female_1"
    ivh_speed: float = 1.0
    ivh_volume: int = 0
    # 出片约束：照片免训练接口 InputSsml 上限 300 字、不能换行；
    # 720P 每秒视频约需 20 秒渲染，故必须异步轮询，不能用短超时同步等。
    ivh_max_ssml_chars: int = 280               # 留 20 字余量给转义
    ivh_request_timeout: float = 30.0
    ivh_poll_interval: float = 10.0
    ivh_poll_timeout: float = 1800.0            # 30 分钟上限
    ivh_download_timeout: float = 120.0
    # 成品视频只保留 7 天：出片后立即下载归档到 MinIO / 本地，绝不把 MediaUrl 直接落库。
    # 是否用 ffmpeg 把成品统一转码为 H.264/yuv420p mp4（学生端 VideoView 兼容性）。
    ivh_normalize_video: bool = True

    @property
    def kafka_enabled(self) -> bool:
        """是否启用异步管道；未配置时降级为同步直写。"""
        return bool(self.kafka_bootstrap)

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
