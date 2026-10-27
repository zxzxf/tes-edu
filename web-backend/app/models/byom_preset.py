"""预设供应商清单 ORM 模型（W6 T-M3）。

可热更新配置表：清单存库而非硬编码，新增/调整厂商无需发版（计划书 §4 / §9）。
与 course_kb 共用 Base；运行时由 app/services/byom_preset.py 读取并下发给前端自动填充。
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.models.course_kb import Base


class VendorPreset(Base):
    __tablename__ = "vendor_preset"

    provider = Column(String(32), primary_key=True)  # aliyun/tencent/ark/deepseek/openai/azure/zhipu/moonshot/custom
    display_name = Column(String(64), nullable=False)
    base_url = Column(Text, nullable=False)
    models = Column(Text, nullable=True)  # 逗号分隔候选模型列表
    embed_base_url = Column(Text, nullable=True)  # 缺省复用 base_url
    embed_model = Column(String(128), nullable=True)
    notes = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
