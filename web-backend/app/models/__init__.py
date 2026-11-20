"""模型包：集中 import，确保 Base.metadata 能收集到所有表（供 create_all 建表）。"""
from app.models import course_kb  # noqa: F401
from app.models import rbac_models  # noqa: F401
from app.models import student_features  # noqa: F401
from app.models import ops  # noqa: F401
from app.models import byom  # noqa: F401  (W6 BYOM: 租户级模型配置)
from app.models import byom_preset  # noqa: F401  (W6 BYOM T-M3: 预设供应商清单)
from app.models import explanation_video  # noqa: F401  (④·数字人讲题短视频 #28)
from app.models import push_hub  # noqa: F401  (推送中心 Push Hub P1)
from app.models import stock_models  # noqa: F401  (股票盯盘业务中心 v1.4)
