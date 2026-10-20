"""模块1：教研内容管理。

教研员/管理员批量导入、维护知识点、例题、解析。真实数据经 Kafka(ingest) -> PostgreSQL。
"""
from typing import List

from fastapi import APIRouter, Depends

from app.core.rbac import Role
from app.core.security import require_role
from app.models.schemas import KnowledgePoint

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

# 占位内存存储（P2 接入 PostgreSQL course_kb）；此处预置示例知识点用于演示
_STORE: List[KnowledgePoint] = [
    KnowledgePoint(id=1, subject="数学", grade="初三", title="二次函数的图像与性质", content="y=ax²+bx+c 的开口方向、顶点与对称轴。", tags=["函数", "图像"]),
    KnowledgePoint(id=2, subject="数学", grade="初三", title="勾股定理", content="直角三角形两直角边平方和等于斜边平方：a²+b²=c²。", tags=["几何", "三角形"]),
    KnowledgePoint(id=3, subject="数学", grade="初二", title="相似三角形判定", content="AA / SAS / SSS 三种相似判定方法。", tags=["几何", "相似"]),
    KnowledgePoint(id=4, subject="英语", grade="初二", title="被动语态", content="am/is/are + done 结构及不同时态的被动形式。", tags=["语法", "时态"]),
    KnowledgePoint(id=5, subject="英语", grade="初三", title="定语从句", content="关系代词 who/which/that 引导的定语从句。", tags=["语法", "从句"]),
    KnowledgePoint(id=6, subject="物理", grade="初三", title="欧姆定律", content="I=U/R，电流与电压成正比、与电阻成反比。", tags=["电学"]),
]


@router.get("", response_model=List[KnowledgePoint])
def list_knowledge(user=Depends(require_role(Role.ADMIN, Role.RESEARCHER))):
    return _STORE


@router.post("", response_model=KnowledgePoint)
def create_knowledge(kp: KnowledgePoint, user=Depends(require_role(Role.ADMIN, Role.RESEARCHER))):
    kp.id = len(_STORE) + 1
    _STORE.append(kp)
    return kp
