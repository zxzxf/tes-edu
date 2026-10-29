"""M2-7 判分指标看板端到端验证（137 容器内跑，注入 X-User-* 头直连 web-backend）。

覆盖：
A. 造混合卷（选择/可判填空/符号填空/解答）→ 两学生提交 → 指标数字逐项对得上
B. 判据覆盖率分母只含 choice+blank（解答题不算进分母）
C. 人工复核率用「曾经需要人判」（复核完不会让比率变小）
D. 判分准确率：没有复核样本时 measurable=false，不伪造 100%
E. 判据来源分布（grading_source 真的落库了，且合计等于总题数）
F. 教师判定后：待人工-1、已复核+1、时效有样本；改判后 effective_flips / 冤枉数
G. 时间窗口：days=30 排除 60 天前的提交，days=0 全量
H. 按卷下钻数字与整卷口径一致
I. 跨租户看不到（拿别人的 paper_id 也读不到）

跑完清理本脚本造的全部数据（按标题前缀 E2E-指标）。
"""
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

import asyncpg

BASE = "http://127.0.0.1:8000"
DSN = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
PILOT = "school_pilot_01"
OTHER = "demo"
T = "teacher@tes.edu"
STU = "student@tes.edu"
STU2 = "student_002"
TITLE = "E2E-指标看板卷（脚本自动清理）"
CH = "e2e-metrics-%d"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  <<< {extra}"))


def call(method, path, uid, role, tenant, body=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("X-User-Id", uid)
    req.add_header("X-User-Role", role)
    req.add_header("X-Tenant-Id", tenant)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else None
        except Exception:
            return e.code, raw.decode("utf-8", "ignore")


async def new_paper(conn):
    pid = await conn.fetchval(
        """INSERT INTO exam_papers (tenant_id,title,paper_type,grade,subject,difficulty_avg,
                                   knowledge_points,created_by,published,published_at,created_at)
           VALUES ($1,$2,'mixed','高三','数学',2,'[]'::json,'e2e',true,now(),now())
           RETURNING id""",
        PILOT, TITLE,
    )
    rows = [
        ("choice", "函数 f(x)=x^2 在 x=2 处的导数是", "C", '{"A":"2","B":"3","C":"4","D":"5"}'),
        ("blank", "计算 1+1 = ______", "2", None),
        ("blank", "把直线方程化为 3x+4y=5 的形式：______", "3x+4y-5=0", None),
        ("subjective", "证明：对任意实数 x，x^2+1>0", "配方即可", None),
    ]
    for i, (qt, stem, ans, opts) in enumerate(rows, start=1):
        await conn.execute(
            """INSERT INTO exam_paper_questions
                 (paper_id,seq,qtype,multi,content_hash,stem,options,answer,analysis,
                  difficulty,knowledge_points,answer_values,created_at)
               VALUES ($1,$2,$3,false,$4,$5,$6::json,$7,'解析',2,'[]'::json,NULL,now())""",
            pid, i, qt, CH % i, stem, opts, ans,
        )
    return pid


async def assign(conn, pid):
    return await conn.fetchval(
        """INSERT INTO exam_assignments (paper_id,tenant_id,target_type,target_grade,
                                         assigned_by,assigned_at,due_at)
           VALUES ($1,$2,'grade','高三','e2e',now(),now()+interval '7 day') RETURNING id""",
        pid, PILOT,
    )


async def cleanup(conn):
    for row in await conn.fetch("SELECT id FROM exam_papers WHERE title LIKE 'E2E-指标%'"):
        pid = row["id"]
        await conn.execute(
            """DELETE FROM exam_grading_revisions WHERE ticket_id IN
                 (SELECT id FROM exam_grading_tickets WHERE paper_id=$1)""", pid)
        await conn.execute("DELETE FROM exam_grading_tickets WHERE paper_id=$1", pid)
        await conn.execute(
            """DELETE FROM exam_submission_items WHERE submission_id IN
                 (SELECT id FROM exam_submissions WHERE paper_id=$1)""", pid)
        await conn.execute("DELETE FROM exam_submissions WHERE paper_id=$1", pid)
        await conn.execute("DELETE FROM exam_assignments WHERE paper_id=$1", pid)
        await conn.execute("DELETE FROM exam_paper_questions WHERE paper_id=$1", pid)
        await conn.execute("DELETE FROM exam_papers WHERE id=$1", pid)


def build_answers():
    return [
        {"seq": 1, "student_answer": "C"},             # 选择：机判对
        {"seq": 2, "student_answer": "2"},             # 填空：与标答一致（机判对）
        {"seq": 3, "student_answer": "3x+4y=5"},       # 填空：符号 → 转人工
        {"seq": 4, "student_answer": "配方得 x^2+1>0"},  # 解答 → 转人工
    ]


async def metrics(paper_id=None, days=30, uid=T, tenant=PILOT):
    p = f"/api/admin/grading/metrics?days={days}"
    if paper_id:
        p += f"&paper_id={paper_id}"
    st, body = call("GET", p, uid, "teacher", tenant)
    assert st == 200, (st, body)
    return body


async def main():
    conn = await asyncpg.connect(DSN)
    await cleanup(conn)
    try:
        pid = await new_paper(conn)
        aid = await assign(conn, pid)
        print(f"== 造卷 paper={pid} assignment={aid}\n")

        # ---------------- A. 两学生提交 ----------------
        for uid, cid in ((STU, "m-1"), (STU2, "m-2")):
            st, r = call("POST", f"/api/student/exams/{aid}/submit", uid, "student", PILOT,
                         {"client_id": cid, "answers": build_answers()})
            check(f"A 提交成功 {uid}", st == 200, f"{st} {r}")
        st, r = call("POST", f"/api/student/exams/{aid}/submit", STU, "student", PILOT,
                     {"client_id": "m-1", "answers": build_answers()})
        check("A 重复提交幂等（不出双份 item）", st == 200, st)

        m = await metrics(pid, 30)
        it = m["items"]
        check("A1 总题数=8（2 学生 × 4 题）", it["total"] == 8, it["total"])
        check("A2 可机判分母=6（不含 2 道解答题）", it["gradable_total"] == 6, it["gradable_total"])
        check("A3 已机判=4", it["auto_gradable"] == 4, it["auto_gradable"])
        check("A4 人工题=4（2 符号填空 + 2 解答）",
              it["by_status"]["manual_pending"] == 4, it["by_status"])

        # ---------------- B/C/D. 三个核心指标 ----------------
        cov = m["metrics"]["coverage"]
        check("B1 判据覆盖率 = 4/6 = 66.7%",
              cov["value"] == 0.666667 and cov["denominator"] == 6, cov)
        check("B2 覆盖率不达标（below）", cov["status"] == "below", cov["status"])
        check("B3 冻结导致的停判为 0（本次没有冻结）", cov["frozen_manual"] == 0, cov["frozen_manual"])

        mr = m["metrics"]["manual_rate"]
        check("C1 人工复核率 = 4/8 = 50%",
              mr["value"] == 0.5 and mr["denominator"] == 8, mr)
        check("C2 人工率方向按「越低越好」判（50% → below）",
              mr["status"] == "below", mr["status"])

        acc = m["metrics"]["accuracy"]
        check("D1 无复核样本时不可测（measurable=false）", acc["measurable"] is False, acc)
        check("D2 不可测时不伪造百分比（value=null）", acc["value"] is None, acc["value"])
        check("D3 说明里写清了为什么不可测", "复核" in acc["note"], acc["note"])

        # ---------------- E. 判据来源 ----------------
        src = {s["source"]: s["count"] for s in m["sources"]}
        check("E1 选择题来源=2", src.get("choice_map") == 2, src)
        check("E2 填空·与标答一致=2", src.get("blank_same") == 2, src)
        check("E3 转人工·符号表达式=2", src.get("manual_symbol") == 2, src)
        check("E4 转人工·解答题=2", src.get("manual_subjective") == 2, src)
        check("E5 来源合计=总题数", sum(src.values()) == it["total"], (sum(src.values()), it["total"]))
        check("E6 没有未记录来源（grading_source 全部落库）",
              "unknown" not in src, src)
        check("E7 转人工来源带 manual 标记",
              all(s["manual"] for s in m["sources"] if s["source"].startswith("manual_")), m["sources"])

        # ---------------- H. 按卷下钻 ----------------
        check("H1 下钻只有本卷一行", len([p for p in m["by_paper"] if p["paper_id"] == pid]) == 1,
              m["by_paper"])
        row = [p for p in m["by_paper"] if p["paper_id"] == pid][0]
        check("H2 下钻覆盖率与整卷一致", row["coverage"] == cov["value"], (row["coverage"], cov["value"]))
        check("H3 下钻带卷名", row["title"] == TITLE, row["title"])

        # ---------------- F. 判定 → 指标跟着动 ----------------
        tid = await conn.fetchval(
            "SELECT id FROM exam_grading_tickets WHERE paper_id=$1 AND qtype='blank' LIMIT 1", pid)
        st, d = call("POST", f"/api/admin/grading/tickets/{tid}/judge", T, "teacher", PILOT,
                     {"verdict": "correct", "comment": "其实等价，算对"})
        check("F1 教师判定成功", st == 200 and d["status"] == "reviewed", st)

        m2 = await metrics(pid, 30)
        check("F2 待人工少一题", m2["items"]["by_status"]["manual_pending"] == 3,
              m2["items"]["by_status"])
        check("F3 已复核多一题", m2["items"]["by_status"]["manual_reviewed"] == 1,
              m2["items"]["by_status"])
        check("F4 人工复核率**不降**（曾经需要人判的口径）",
              m2["metrics"]["manual_rate"]["value"] == 0.5, m2["metrics"]["manual_rate"])
        check("F5 覆盖率不变（复核不是机器判的）",
              m2["metrics"]["coverage"]["value"] == 0.666667, m2["metrics"]["coverage"])
        check("F6 判定时效有 1 个样本", m2["tickets"]["turnaround_minutes"]["n"] >= 1,
              m2["tickets"]["turnaround_minutes"])
        check("F7 首次判定不算改判", m2["revisions"]["effective_flips"] == 0, m2["revisions"])

        # 改判两次：对→错，再错→对。两个方向都要能分别数出来
        call("POST", f"/api/admin/grading/tickets/{tid}/judge", T, "teacher", PILOT,
             {"verdict": "wrong", "comment": "再看还是错的"})
        m3 = await metrics(pid, 30)
        check("F8 改判计入 effective_flips", m3["revisions"]["effective_flips"] == 1, m3["revisions"])
        check("F9 对→错记在 right_to_wrong（不是冤枉）",
              m3["revisions"]["right_to_wrong"] == 1 and m3["revisions"]["wrong_to_right"] == 0,
              m3["revisions"])

        call("POST", f"/api/admin/grading/tickets/{tid}/judge", T, "teacher", PILOT,
             {"verdict": "correct", "comment": "还是学生写得对"})
        m4 = await metrics(pid, 30)
        check("F10 错→对计入冤枉数", m4["revisions"]["wrong_to_right"] == 1, m4["revisions"])
        check("F11 两次改判都计数", m4["revisions"]["effective_flips"] == 2, m4["revisions"])
        check("F12 冤枉率 = 1/2", m4["revisions"]["wrong_to_right_rate"] == 0.5, m4["revisions"])

        # ---------------- G. 时间窗口 ----------------
        await conn.execute(
            """UPDATE exam_submissions SET submitted_at = now() - interval '60 day'
                WHERE paper_id=$1 AND student_id=$2""", pid, STU2)
        win = await metrics(pid, 30)
        check("G1 30 天窗口排除 60 天前的提交（8 → 4）", win["items"]["total"] == 4, win["items"]["total"])
        check("G2 窗口口径写在响应里", win["window_days"] == 30, win["window_days"])
        allt = await metrics(pid, 0)
        check("G3 days=0 是全量口径（仍是 8）", allt["items"]["total"] == 8, allt["items"]["total"])
        check("G4 全量口径 window_days=null", allt["window_days"] is None, allt["window_days"])

        # ---------------- I. 跨租户 ----------------
        st, other = call("GET", f"/api/admin/grading/metrics?days=30&paper_id={pid}",
                         "teacher@demo.com", "teacher", OTHER)
        check("I1 跨租户读取成功但不含他校数据",
              st == 200 and other["items"]["total"] == 0, (st, other.get("items")))
        check("I2 跨租户来源分布为空", other["sources"] == [], other["sources"])
        check("I3 跨租户下钻为空", other["by_paper"] == [], other["by_paper"])

        # ---------------- 参数校验 ----------------
        st, _ = call("GET", "/api/admin/grading/metrics?days=-1", T, "teacher", PILOT)
        check("J1 负数天数被拒（422）", st == 422, st)
    finally:
        await cleanup(conn)
        left = await conn.fetchval("SELECT count(*) FROM exam_papers WHERE title LIKE 'E2E-指标%'")
        print(f"\n清理后残留 paper={left}")
        await conn.close()

    print(f"\n===== {len(PASS)} PASS / {len(FAIL)} FAIL =====")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        sys.exit(1)


asyncio.run(main())
