"""学生端能力扩展服务层（W3：T-F / T-S / T-A）。

核心：resolve_features() 按范围优先级解析学生对各功能开关的"生效集"，
供学生端渲染入口/按钮（#2 总闸）；build_seed_features() 提供 6 枚默认开关种子。
"""

from typing import Dict, List, Optional

from sqlalchemy import select

from app.models import student_features as m


# 初始功能清单（feature key）：6 枚默认开（学生端能力扩展计划书 §2.2）
SEED_FEATURES: List[dict] = [
    {"feature_key": "project", "feature_name": "我的项目", "platform": "web", "default_on": True,
     "description": "学生网页端自建学习项目（含任务清单与进度追踪）"},
    {"feature_key": "anti_fatigue", "feature_name": "防疲劳提醒", "platform": "both", "default_on": True,
     "description": "连续学习达阈值弹出休息/护眼提醒"},
    {"feature_key": "wrong_book", "feature_name": "错题本", "platform": "both", "default_on": True,
     "description": "作答错题库与归因报告"},
    {"feature_key": "group_paper", "feature_name": "自动组卷", "platform": "both", "default_on": True,
     "description": "按知识点/难度自动生成练习卷"},
    {"feature_key": "knowledge_graph", "feature_name": "知识图谱", "platform": "both", "default_on": True,
     "description": "知识点 DAG 与掌握度可视化"},
    {"feature_key": "ai_tutor", "feature_name": "AI 辅导对话", "platform": "both", "default_on": True,
     "description": "引导式 AI 辅导与解题讲解"},
]


def build_seed_features() -> List[dict]:
    """返回 6 枚默认功能开关定义（纯函数，便于单测断言）。"""
    return [dict(f) for f in SEED_FEATURES]


def _platform_match(switch_platform: str, requested: Optional[str]) -> bool:
    """开关 platform 是否适用于请求平台。"""
    if requested is None:
        return True
    if switch_platform == "both":
        return True
    return switch_platform == requested


async def resolve_features(
    db,
    *,
    tenant_id: Optional[str] = None,
    region_id: Optional[str] = None,
    class_id: Optional[str] = None,
    platform: Optional[str] = None,
) -> Dict[str, bool]:
    """解析某学生（按 tenant/region/class）对各功能开关的生效集。

    优先级（越具体越优先）：class > tenant > region > global > default_on。
    仅返回适用于请求 platform 的开关（platform 不匹配的开关不出现）。
    """
    switches = (
        await db.execute(select(m.FeatureSwitch))
    ).scalars().all()
    overrides = (
        await db.execute(select(m.FeatureSwitchOverride))
    ).scalars().all()

    # 按 (feature_key, scope_type) 建索引，便于按优先级查
    ov_index: Dict[tuple, bool] = {}
    for ov in overrides:
        ov_index[(ov.feature_key, ov.scope_type, ov.scope_id or "")] = ov.enabled

    # 范围层级优先级：class > tenant > region > global
    scope_candidates = []
    if class_id:
        scope_candidates.append(("class", class_id))
    if tenant_id:
        scope_candidates.append(("tenant", tenant_id))
    if region_id:
        scope_candidates.append(("region", region_id))
    scope_candidates.append(("global", ""))

    result: Dict[str, bool] = {}
    for sw in switches:
        if not _platform_match(sw.platform, platform):
            continue
        enabled: Optional[bool] = None
        for scope_type, scope_id in scope_candidates:
            key = (sw.feature_key, scope_type, scope_id)
            if key in ov_index:
                enabled = ov_index[key]
                break
        if enabled is None:
            enabled = bool(sw.default_on)
        result[sw.feature_key] = enabled
    return result


async def get_features_for_student(
    db,
    *,
    tenant_id: Optional[str] = None,
    region_id: Optional[str] = None,
    class_id: Optional[str] = None,
    platform: Optional[str] = None,
) -> Dict[str, bool]:
    """学生端便捷入口：从当前用户 scope 解析生效集。"""
    return await resolve_features(
        db,
        tenant_id=tenant_id,
        region_id=region_id,
        class_id=class_id,
        platform=platform,
    )
