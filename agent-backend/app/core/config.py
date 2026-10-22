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

# ---- 视觉模型（拍照识题；VLM_MODEL 留空则降级，不阻塞主链路） ----
VLM_BASE_URL = _get("VLM_BASE_URL") or LLM_BASE_URL
VLM_MODEL = _get("VLM_MODEL")

# ---- 数据层（106 的 pgvector/pg16） ----
DB_DSN = _get("DB_DSN")  # postgresql://user:pass@host:5432/dbname

# ---- 检索 ----
TOP_K = int(_get("TOP_K", "4"))
SEED_PATH = _get("SEED_PATH", "data/seed_questions.json")


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


def mode() -> str:
    """运行模式：real（全真实）/ dev（含降级），便于 /healthz 自查。"""
    if db_ready() and llm_ready() and embed_ready():
        return "real"
    return "dev"
