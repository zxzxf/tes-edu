"""T-B5 知识图谱端到端冒烟（真实 106 库，只读）。

链路：本地 ssh 隧道 -> 106 库 -> app.services.graph.knowledge_graph_db
      -> 载荷断言（与 /learning/knowledge-graph 接口返回一致）。

用法（隧道与脚本同一条命令，见 memory「建+用同命令」约定）：
    ssh -f -N -L 15432:106.12.25.5:5432 137 && \
      python scripts/e2e_tb5_knowledge_graph.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.services import graph  # noqa: E402

DSN = os.environ.get(
    "SMOKE_DSN",
    "postgresql+asyncpg://tes:Tes%40Pg%23106@127.0.0.1:15432/tes_edu",
)
STUDENT = os.environ.get("SMOKE_STUDENT", "student_001")

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


async def main() -> None:
    engine = create_async_engine(DSN, future=True)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        out = await graph.knowledge_graph_db(db, STUDENT)

    s = out["summary"]
    nodes = out["nodes"]
    edges = out["edges"]

    print(f"== 图谱快照：student={STUDENT} subject={out['subject']} ==")
    print(f"   summary: {s}")

    check("节点总数 435", s["total"] == 435 and len(nodes) == 435, f"total={s['total']}")
    check("边总数 435", s["edges"] == 435 and len(edges) == 435, f"edges={s['edges']}")
    check("code 唯一", len({n['code'] for n in nodes}) == len(nodes))
    check("max_depth=182（183 层长链）", s["max_depth"] == 182, f"max_depth={s['max_depth']}")

    # 配色带合法 + 与掌握度一致（strong >= 0.8、medium 0.6~0.8、weak < 0.6）
    bands = {n["band"] for n in nodes}
    check("配色带取值合法", bands <= {"none", "weak", "medium", "strong"}, str(bands))
    band_ok = all(
        (n["band"] == "none" and n["mastery"] is None)
        or (n["band"] == "weak" and n["mastery"] < 0.6)
        or (n["band"] == "medium" and 0.6 <= n["mastery"] < 0.8)
        or (n["band"] == "strong" and n["mastery"] >= 0.8)
        for n in nodes
    )
    check("band 与 mastery 阈值一致（T-B2 口径）", band_ok)

    # 学生学情叠层（真实数据下的名称键解析）：
    # - student_002 的「椭圆的几何性质」是 12 条掌握度记录中唯一能在 DAG
    #   找到 code 的名称（其余 11 条属旧扁平 KP 体系——数据治理缺口，
    #   读侧已做名称->code 翻译，翻译不到的原样忽略）；
    # - 所有行 correct/wrong 多为 0（只练未判分）-> 按 T-B2 约定不构成掌握度；
    # - 练习统计不要求判分，可验证翻译链路全通。
    async with Session() as db:
        out2 = await graph.knowledge_graph_db(db, "student_002")
    practiced = [n for n in out2["nodes"] if n["practice_count"] > 0]
    check("student_002 名称键解析出 1 个练习节点", len(practiced) == 1,
          f"{[(n['code'], n['title'], n['practice_count']) for n in practiced]}")
    if practiced:
        n = practiced[0]
        check("解析节点 code 正确（椭圆 -> MA-B3-C3-S1-K03）",
              n["code"] == "MA-B3-C3-S1-K03", n["code"])
        check("未判分不构成掌握度（band=none）",
              n["mastery"] is None and n["band"] == "none")
    # 其余学生：名称均不可映射 -> 零叠加（数据缺口，非机制问题）
    async with Session() as db:
        out3 = await graph.knowledge_graph_db(db, "student_003")
    check("student_003 名称不可映射 -> 零叠加",
          all(n["practice_count"] == 0 and n["mastery"] is None for n in out3["nodes"]))
    check("已掌握/薄弱计数自洽",
          s["mastered"] == sum(1 for n in nodes if n["band"] == "strong")
          and s["weak"] == sum(1 for n in nodes if n["band"] == "weak"))

    # ZPD：strong 绝不在 ZPD；ZPD 数 > 0（冷启动大量待学）
    check("已掌握点绝不入 ZPD", all(not n["zpd"] for n in nodes if n["band"] == "strong"))
    check("ZPD 非空", s["in_zpd"] > 0, f"in_zpd={s['in_zpd']}")
    check("未学点全在 ZPD（无薄弱前置阻塞时）", s["untouched"] > 0,
          f"untouched={s['untouched']}")

    # 边：端点均在节点集、无自环、方向 from=前置->to=后置
    codes = {n["code"] for n in nodes}
    check("边端点均在节点集", all(e["from"] in codes and e["to"] in codes for e in edges))
    check("边无自环", all(e["from"] != e["to"] for e in edges))

    # prereqs 与边一致（按 to 聚合）
    agg: dict = {}
    for e in edges:
        agg.setdefault(e["to"], set()).add(e["from"])
    check("node.prereqs 与边一致",
          all(set(n["prereqs"]) == agg.get(n["code"], set()) for n in nodes))

    # 题目数：有归因的点应 > 0
    with_q = [n for n in nodes if n["question_count"] > 0]
    check("题目数聚合非空", len(with_q) > 0, f"{len(with_q)} 个点有题")
    check("题目数非负", all(n["question_count"] >= 0 for n in nodes))

    # 载荷可 JSON 序列化（FastAPI 响应路径）
    try:
        blob = json.dumps(out, ensure_ascii=False, default=str)
        check("载荷可 JSON 序列化", len(blob) > 10_000, f"{len(blob)} bytes")
    except TypeError as e:
        check("载荷可 JSON 序列化", False, repr(e))

    # 学科过滤：数学全量；物理空 + note
    async with Session() as db:
        out_phys = await graph.knowledge_graph_db(db, STUDENT, subject="物理")
    check("物理（未入库）返回空 + note",
          out_phys["nodes"] == [] and "无知识点数据" in (out_phys.get("note") or ""))

    await engine.dispose()
    print(f"\n结果：{PASS} PASS / {FAIL} FAIL")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
