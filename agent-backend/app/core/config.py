"""统一配置：全部走环境变量。

- 真实模式：配置 LLM_*（DeepSeek / 通义 / Qwen 均走 OpenAI 兼容 /v1）+ EMBED_* + DB_DSN。
- dev 模式：缺密钥时自动降级（本地确定性向量 + 基于检索上下文拼装的讲解），保证链路可跑通。

切到生产只需补环境变量，无需改代码。
"""
import os

try:  # 本地/CI 可用 .env 注入；生产用环境变量或 docker --env-file 即可
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# ---- 大模型（DeepSeek / 通义 / Qwen，OpenAI 兼容 /v1/chat/completions） ----
LLM_BASE_URL = _get("LLM_BASE_URL")      # 例：https://api.deepseek.com/v1
LLM_API_KEY = _get("LLM_API_KEY")
LLM_MODEL = _get("LLM_MODEL", "deepseek-chat")

# ---- 向量化 ----
# 注意：很多大模型端点（含本 MaaS 节点）不提供 embedding 模型。
# 因此 EMBED_MODEL 默认留空 = 走本地确定性向量回退；只有显式配置 EMBED_MODEL 才走真实 embedding。
EMBED_BASE_URL = _get("EMBED_BASE_URL") or LLM_BASE_URL
EMBED_API_KEY = _get("EMBED_API_KEY") or LLM_API_KEY
EMBED_MODEL = _get("EMBED_MODEL")
EMBED_DIM = int(_get("EMBED_DIM", "1024"))
# 单次 embedding 请求的文本数上限（text-embedding-v4 为 10，qwen3.7 为 20）；超出自动分批
EMBED_BATCH = int(_get("EMBED_BATCH", "10"))

# ---- 视觉模型（拍照识题；VLM_MODEL 留空则降级，不阻塞主链路） ----
VLM_BASE_URL = _get("VLM_BASE_URL") or LLM_BASE_URL
VLM_MODEL = _get("VLM_MODEL")

# ---- 解题输出长度限制（按档位，单位 token） ----
# quick/explain/guide/similar 四档分别给上限，避免简单题也烧大量 token。
# 默认值：quick=300, explain=800, guide=600, similar=400
LLM_MAX_TOKENS_QUICK = int(_get("LLM_MAX_TOKENS_QUICK", "300"))
LLM_MAX_TOKENS_EXPLAIN = int(_get("LLM_MAX_TOKENS_EXPLAIN", "800"))
LLM_MAX_TOKENS_GUIDE = int(_get("LLM_MAX_TOKENS_GUIDE", "600"))
LLM_MAX_TOKENS_SIMILAR = int(_get("LLM_MAX_TOKENS_SIMILAR", "400"))

# ---- 触类旁通：是否随主解一起生成（关掉可省掉一次 LLM 调用，约一半耗时） ----
SIMILAR_ON_SOLVE = _get("SIMILAR_ON_SOLVE", "1") not in ("0", "false", "False", "")

MODE_TO_MAX_TOKENS = {
    "quick": LLM_MAX_TOKENS_QUICK,
    "explain": LLM_MAX_TOKENS_EXPLAIN,
    "guide": LLM_MAX_TOKENS_GUIDE,
    "similar": LLM_MAX_TOKENS_SIMILAR,
}

# ---- 数据层（106 的 pgvector/pg16） ----
DB_DSN = _get("DB_DSN")  # postgresql://user:pass@host:5432/dbname

# ---- 检索 ----
TOP_K = int(_get("TOP_K", "4"))
SEED_PATH = _get("SEED_PATH", "data/seed_questions.json")

# ---- Kafka 学情事件上报 ----
# 137 的 kafka 已接入 edu-net，容器内用 kafka:9092 即可；留空则不上报（不影响解题）
KAFKA_BOOTSTRAP = _get("KAFKA_BOOTSTRAP")
KAFKA_TOPIC_EVENT = _get("KAFKA_TOPIC_EVENT", "student_event")

# ---- 缓存（T-D1）：137 edu-redis，agent 容器同网 ----
# 优先读 REDIS_URL；未设则按 host/port/db/password 拼；agent 与 redis 同处 edu-net，
# 主机名即 compose 服务名 `redis`，密码与网关共用 REDIS_PASSWORD。
REDIS_HOST = _get("REDIS_HOST", "redis")
REDIS_PORT = int(_get("REDIS_PORT", "6379"))
REDIS_DB = int(_get("REDIS_DB", "0"))
REDIS_PASSWORD = _get("REDIS_PASSWORD")
REDIS_URL = _get("REDIS_URL") or (
    f"redis://{(':') if REDIS_PASSWORD else ''}"
    f"{REDIS_PASSWORD + '@' if REDIS_PASSWORD else ''}"
    f"{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
)

# ---- T-D2 模型路由分级 ----
# 简单题/直给走小模型，难题走大模型。未配（或与 LLM_MODEL 相同）时回落单一
# LLM_MODEL，行为与接入路由前一致——缺配绝不启动失败。
LLM_MODEL_SIMPLE = _get("LLM_MODEL_SIMPLE") or LLM_MODEL
LLM_MODEL_HARD = _get("LLM_MODEL_HARD") or LLM_MODEL

# 缓存总开关（T-D1）。出线上事故（如缓存串味、Redis 抖动）时可一键关掉，
# 关掉后 cached_solve 直接回源 LLM，行为与接入缓存前完全一致。
CACHE_ENABLED = _get("CACHE_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")

# ---- T-D3 用量记录与预算告警 ----
# 用量总开关：关掉后不写 llm_usage、不查当日累计（行为等同接入前）。
USAGE_ENABLED = _get("USAGE_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
# 单价（元 / 1k tokens）：未登记模型用这组默认值。
LLM_COST_PER_1K_TOKENS_IN = float(_get("LLM_COST_PER_1K_TOKENS_IN", "0.002"))
LLM_COST_PER_1K_TOKENS_OUT = float(_get("LLM_COST_PER_1K_TOKENS_OUT", "0.006"))
# 按模型覆盖单价，JSON：{"qwen3.8-flash": {"in": 0.002, "out": 0.006}}
LLM_MODEL_PRICES_JSON = _get("LLM_MODEL_PRICES")
# 日预算（元）。<=0 表示不设预算（state 恒 ok）。
LLM_DAILY_BUDGET = float(_get("LLM_DAILY_BUDGET", "50"))
# 预算告警线（占预算比例）：越过即 alert（记审计日志）
LLM_BUDGET_WARN_RATIO = float(_get("LLM_BUDGET_WARN_RATIO", "0.8"))
# 预算超线后的动作：alert=仅告警；force_simple=强制降级为小模型并发审计日志；none=不处理
LLM_BUDGET_ACTION = _get("LLM_BUDGET_ACTION", "alert").strip().lower()


def cache_enabled() -> bool:
    """是否启用请求级缓存（T-D1）。单测/降级场景可整体关闭。"""
    return bool(CACHE_ENABLED)


def usage_enabled() -> bool:
    """是否启用用量记录（T-D3）。单测默认关闭，避免连 106 生产库。"""
    return bool(USAGE_ENABLED)


def routing_enabled() -> bool:
    """是否启用模型分级（T-D2）。

    仅当 ``LLM_MODEL_SIMPLE`` 与 ``LLM_MODEL_HARD`` 配成**不同**模型时才生效；
    任一未配时二者都回落 ``LLM_MODEL``，路由自然退化为"始终单一模型"。
    """
    return bool(LLM_MODEL_SIMPLE and LLM_MODEL_HARD and LLM_MODEL_SIMPLE != LLM_MODEL_HARD)


def llm_ready() -> bool:
    """是否具备真实大模型调用条件。"""
    return bool(LLM_BASE_URL and LLM_API_KEY and LLM_MODEL)


def embed_ready() -> bool:
    """是否具备真实向量化条件。"""
    return bool(EMBED_BASE_URL and EMBED_API_KEY and EMBED_MODEL)


def db_ready() -> bool:
    """是否连接真实 pgvector。"""
    return bool(DB_DSN)


def vlm_ready() -> bool:
    """是否启用视觉模型（拍照识题）。"""
    return bool(VLM_BASE_URL and LLM_API_KEY and VLM_MODEL)


def kafka_ready() -> bool:
    """是否启用学情事件上报。"""
    return bool(KAFKA_BOOTSTRAP and KAFKA_TOPIC_EVENT)


def mode() -> str:
    """运行模式：real（全真实）/ dev（含降级），便于 /healthz 自查。"""
    if db_ready() and llm_ready() and embed_ready():
        return "real"
    return "dev"
