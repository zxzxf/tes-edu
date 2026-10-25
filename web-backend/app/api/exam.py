"""模块4：自动组卷 / 周测。

按"知识点 + 难度"从 questions 抽题，LLM 排版生成试卷与答案。
"""
from fastapi import APIRouter, Depends

from app.core.rbac import Role
from app.core.security import require_role
from app.models.schemas import ExamPaper

# 示例题库（P2 接入 questions 表后由 DB 抽取替代）
_BANK: dict[str, list[str]] = {
    "二次函数": [
        "已知二次函数 y=x²-2x-3，求其顶点坐标与对称轴。",
        "抛物线 y=ax² 过点 (2,8)，求 a 的值。",
    ],
    "被动语态": [
        "将 'They built the bridge in 1999.' 改为被动语态。",
        "用被动语态翻译：会议将于下周一举行。",
    ],
    "勾股定理": [
        "Rt△ 中两直角边为 6、8，求斜边。",
        "判断边长 5、12、13 能否构成直角三角形。",
    ],
    "欧姆定律": [
        "U=12V，R=4Ω，求电流 I。",
        "某用电器电流 0.5A、电压 220V，求电阻。",
    ],
}


def _sample_question(kp: str, difficulty: int = 3) -> str:
    qs = _BANK.get(kp)
    if qs:
        return qs[0]
    return f"【{kp}】请从题库抽取一道综合题（难度 {difficulty}）。"


router = APIRouter(prefix="/exam", tags=["exam"])


@router.post("/generate", response_model=ExamPaper)
def generate(
    paper: ExamPaper,
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.REGION_ADMIN)),
):
    # 占位：真实场景按知识点+难度从 questions 抽题，LLM 排版
    paper.id = 1
    paper.questions = [
        _sample_question(kp, paper.difficulty) for kp in paper.knowledge_points
    ] or [_sample_question("综合", paper.difficulty)]
    return paper
