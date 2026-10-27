"""BYOM 租户级模型配置 ORM 模型（W6 T-M1）。

一张表 `tenant_model_config`：每租户一套大模型配置（chat / embedding 段），
密钥以 AES-256-GCM 密文（LargeBinary）落库，明文绝不入库、不随响应出库。

- 与 course_kb 共用同一个 Base，随 Base.metadata.create_all 一并建出；
- 运行态解析见 agent-backend/app/core/tenant_llm.py（T-M2）；
- 密钥加解密复用 app/services/vendor_crypto.py（与厂商凭证同一主密钥），
  本模块只定义结构，不持有密钥逻辑。
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.sql import func

from app.models.course_kb import Base


class TenantModelConfig(Base):
    __tablename__ = "tenant_model_config"

    tenant_id = Column(String(64), primary_key=True)
    provider = Column(String(32), nullable=False, default="custom")
    base_url = Column(Text, nullable=False)
    # AES-256-GCM 密文（nonce12 ‖ ct ‖ tag16）；明文不入库
    api_key_enc = Column(LargeBinary, nullable=False)
    model = Column(String(128), nullable=False)

    # embedding 段（缺省复用 chat 段；部分厂商 embed 与 chat 不同域）
    embed_base_url = Column(Text, nullable=True)
    embed_api_key_enc = Column(LargeBinary, nullable=True)
    embed_model = Column(String(128), nullable=True)
    embed_dim = Column(Integer, default=1024)

    # 分级（可选，继承 T-D2 的 LLM_MODEL_SIMPLE/HARD 思路）
    model_simple = Column(String(128), nullable=True)
    model_hard = Column(String(128), nullable=True)

    enabled = Column(Boolean, default=True)
    rag_top_k = Column(Integer, default=4)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
