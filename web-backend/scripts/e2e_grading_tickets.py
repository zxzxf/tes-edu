"""M2-4/M2-5/M2-6 端到端验证（137 容器内跑，注入 X-User-* 头直连 web-backend）。

覆盖：
A. 混合卷提交 → 转人工题自动开单（幂等、原因落库）
B. 教师待办列表 / 详情（答案值、题干、留痕）
C. 认领 → 判定 → **汇总全量重算** → 学生端结果回写
D. 改判留痕（before/after）
E. accept_answer 沉淀：同题第二个学生自动判对，不再重复开单
F. 跨租户 404
G. 重复提交幂等

结束后清理本脚本造的全部数据（按 paper_id）。
"""
import asyncio
import json
import os
import urllib.error
import urllib.request

import asyncpg

BASE = "http://127.0.0.1:8000"
DSN = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
PILOT = "school_pilot_01"
STU = "student@tes.edu"
STU2 = "student_002"
TITLE = "E2E-人工工单验证卷（脚本自动清理）"

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


async def seed(conn):
    pid = await conn.fetchval(
        """INSERT INTO exam_papers (tenant_id,title,paper_type,grade,subject,difficulty_avg,
                                   knowledge_points,created_by,published,published_at,created_at)
           VALUES ($1,$2,'mixed','高三','数学',2,'[]'::json,'e2e',true,now(),now()) RETURNING id""",
        PILOT,
        TITLE,
    )
    qs = [
        # seq, qtype, multi, stem, options, answer, analysis
        (1, "choice", False, "函数 f(x)=x^2 在 x=2 处的导数是", '{"A":"2","B":"3","C":"4","D":"5"}', "C", "f'(x)=2x，故 f'(2)=4"),
        (2, "blank", False, "计算：1/4 + 1/5 = ______", None, "9/20", "通分得 9/20"),
        (3, "blank", False, "写出抛物线 y=x^2+1 的对称轴方程______", None, "x=0", "对称轴为 y 轴，即 x=0"),
        (4, "subjective", False, "证明：对任意实数 x，x^2+x+1>0", None,
         "配方得 (x+1/2)^2+3/4>0，故恒成立", "配方法"),
    ]
    for seq, qt, multi, stem, opts, ans, ana in qs:
        await conn.execute(
            """INSERT INTO exam_paper_questions
                 (paper_id,seq,qtype,multi,stem,options,answer,analysis,difficulty,
                  knowledge_points,answer_values,created_at)
               VALUES ($1,$2,$3,$4,$5,$6::json,$7,$8,2,'[]'::json,NULL,now())""",
            pid, seq, qt, multi, stem, opts, ans, ana,
        )
    aid = await conn.fetchval(
        """INSERT INTO exam_assignments (paper_id,tenant_id,target_type,target_grade,
                                         assigned_by,assigned_at,due_at)
           VALUES ($1,$2,'grade','高三','e2e',now(),now()+interval '7 day') RETURNING id""",
        pid, PILOT,
    )
    return pid, aid


async def cleanup(conn, pid):
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


async def main():
    conn = await asyncpg.connect(DSN)
    pid = aid = None
    try:
        pid, aid = await seed(conn)
        print(f"== 造卷 paper={pid} assignment={aid}\n")

        # ---------------- A. 学生提交 ----------------
        answers = [
            {"seq": 1, "student_answer": "C"},
            {"seq": 2, "student_answer": "0.45"},
            {"seq": 3, "student_answer": "y轴"},
            {"seq": 4, "student_answer": "配方得 (x+1/2)^2+3/4>0"},
        ]
        st, res = call("POST", f"/api/student/exams/{aid}/submit", STU, "student", PILOT,
                       {"client_id": "e2e-1", "answers": answers})
        check("A1 提交成功", st == 200, f"{st} {res}")
        check("A2 单选+数值填空自动判对（correct_count=2）",
              res["correct_count"] == 2, res["correct_count"])
        check("A3 整卷待人工（manual_pending_count=2）",
              res["manual_pending_count"] == 2, res["manual_pending_count"])
        check("A4 整卷状态 manual_pending", res["status"] == "manual_pending", res["status"])
        by_seq = {i["seq"]: i for i in res["items"]}
        check("A5 符号填空题转人工（含原因）",
              by_seq[3]["status"] == "manual_pending" and by_seq[3]["reason"], by_seq[3])
        check("A6 解答题转人工", by_seq[4]["status"] == "manual_pending")
        sub_id = res["submission_id"]

        rows = await conn.fetch(
            "SELECT seq,status,reason,tenant_id FROM exam_grading_tickets WHERE paper_id=$1 ORDER BY seq", pid)
        check("A7 提交即自动开单 2 张", len(rows) == 2, len(rows))
        check("A8 工单租户正确", all(r["tenant_id"] == PILOT for r in rows), [r["tenant_id"] for r in rows])
        check("A9 工单带转人工原因", all(r["reason"] for r in rows), [r["reason"] for r in rows])

        # 重复提交幂等
        st2, res2 = call("POST", f"/api/student/exams/{aid}/submit", STU, "student", PILOT,
                         {"client_id": "e2e-2", "answers": answers})
        check("A10 重复提交返回同一提交（幂等）",
              st2 == 200 and res2["submission_id"] == sub_id, st2)
        n = await conn.fetchval("SELECT count(*) FROM exam_grading_tickets WHERE paper_id=$1", pid)
        check("A11 重复提交不重复开单", n == 2, n)

        # ---------------- B. 教师待办 ----------------
        st, lst = call("GET", f"/api/admin/grading/tickets?assignment_id={aid}", "teacher@tes.edu", "teacher", PILOT)
        check("B1 待办列表可读", st == 200 and lst["total"] == 2, f"{st} {lst}")
        check("B2 状态计数 pending=2", lst["counts"]["pending"] == 2, lst["counts"])
        t3 = [t for t in lst["items"] if t["seq"] == 3][0]
        t4 = [t for t in lst["items"] if t["seq"] == 4][0]
        check("B3 列表带试卷标题", t3["paper_title"] == TITLE, t3["paper_title"])

        st, d3 = call("GET", f"/api/admin/grading/tickets/{t3['ticket_id']}", "teacher@tes.edu", "teacher", PILOT)
        check("B4 详情含题干/标答", st == 200 and d3["stem"] and d3["correct_answer"] == "x=0", st)
        check("B5 详情含预展开答案值", "x=0" in d3["answer_values"], d3["answer_values"])
        check("B6 单空题答案值生效标记", d3["answer_values_effective"] is True, d3["answer_values_effective"])
        check("B7 详情含转人工原因", bool(d3["reason"]), d3["reason"])

        # ---------------- F. 跨租户 ----------------
        st, _ = call("GET", f"/api/admin/grading/tickets/{t3['ticket_id']}", "teacher@demo.com", "teacher", "demo")
        check("F1 跨租户取工单 404", st == 404, st)
        st, l2 = call("GET", "/api/admin/grading/tickets", "teacher@demo.com", "teacher", "demo")
        check("F2 跨租户待办列表为空", st == 200 and l2["total"] == 0, l2.get("total"))

        # ---------------- C. 认领 + 判定 ----------------
        st, c3 = call("POST", f"/api/admin/grading/tickets/{t3['ticket_id']}/claim", "teacher@tes.edu", "teacher", PILOT, {})
        check("C1 认领成功", st == 200 and c3["status"] == "claimed", f"{st} {c3.get('status')}")
        check("C2 认领人记录", c3["assignee"] == "teacher@tes.edu", c3["assignee"])

        st, j3 = call("POST", f"/api/admin/grading/tickets/{t3['ticket_id']}/judge",
                      "teacher@tes.edu", "teacher", PILOT,
                      {"verdict": "correct", "comment": "写「y轴」等价于 x=0，算对", "accept_answer": True})
        check("C3 判定成功", st == 200 and j3["status"] == "reviewed", f"{st} {j3}")
        check("C4 判定结果落工单", j3["verdict"] == "correct", j3["verdict"])
        check("C5 accept_answer 追加答案值", "y轴" in j3["answer_values"], j3["answer_values"])
        check("C6 留痕含 judge + add_value",
              {"judge", "add_value"} <= {r["action"] for r in j3["revisions"]},
              [r["action"] for r in j3["revisions"]])

        row = await conn.fetchrow(
            """SELECT s.correct_count,s.status,i.correct,i.status AS istatus
                 FROM exam_submissions s
                 JOIN exam_submission_items i ON i.submission_id=s.id
                WHERE s.id=$1 AND i.seq=3""", sub_id)
        check("C7 回写 item.correct=True", row["correct"] is True, row["correct"])
        check("C8 item 状态 manual_reviewed", row["istatus"] == "manual_reviewed", row["istatus"])
        check("C9 汇总重算 correct_count=3", row["correct_count"] == 3, row["correct_count"])
        check("C10 仍有待人工题时整卷保持 manual_pending",
              row["status"] == "manual_pending", row["status"])

        # 判定解答题（算错）
        st, j4 = call("POST", f"/api/admin/grading/tickets/{t4['ticket_id']}/judge",
                      "teacher@tes.edu", "teacher", PILOT,
                      {"verdict": "wrong", "comment": "证明不完整，缺恒成立结论"})
        check("C11 解答题判定为错", st == 200 and j4["verdict"] == "wrong", st)
        row = await conn.fetchrow("SELECT correct_count,status FROM exam_submissions WHERE id=$1", sub_id)
        check("C12 全部判定后整卷 graded", row["status"] == "graded", row["status"])
        check("C13 汇总 correct_count=3（解答题不给分）", row["correct_count"] == 3, row["correct_count"])

        # ---------------- D. 改判 ----------------
        st, j4b = call("POST", f"/api/admin/grading/tickets/{t4['ticket_id']}/judge",
                       "teacher@tes.edu", "teacher", PILOT,
                       {"verdict": "correct", "comment": "复核后认定完整，改判正确"})
        check("D1 改判成功", st == 200 and j4b["verdict"] == "correct", st)
        rev = [r for r in j4b["revisions"] if r["action"] == "rejudge"]
        check("D2 改判留痕 rejudge", len(rev) == 1, [r["action"] for r in j4b["revisions"]])
        check("D3 留痕记录 before/after",
              rev and rev[0]["before_correct"] is False and rev[0]["after_correct"] is True,
              rev[0] if rev else None)
        row = await conn.fetchrow("SELECT correct_count FROM exam_submissions WHERE id=$1", sub_id)
        check("D4 改判后汇总=4", row["correct_count"] == 4, row["correct_count"])

        # ---------------- 学生端回看 ----------------
        st, r = call("GET", f"/api/student/exams/{aid}/result", STU, "student", PILOT)
        check("E1 学生端结果可读", st == 200, st)
        check("E2 成绩回写 correct_count=4", r["correct_count"] == 4, r["correct_count"])
        check("E3 整卷 graded", r["status"] == "graded", r["status"])
        check("E4 无待人工题", r["manual_pending_count"] == 0, r["manual_pending_count"])
        check("E5 已复核题数=2", r["manual_reviewed_count"] == 2, r["manual_reviewed_count"])
        by_seq = {i["seq"]: i for i in r["items"]}
        check("E6 老师评语可见", by_seq[3]["teacher_comment"] == "写「y轴」等价于 x=0，算对",
              by_seq[3]["teacher_comment"])
        check("E7 复核题状态 manual_reviewed", by_seq[3]["status"] == "manual_reviewed", by_seq[3]["status"])

        # ---------------- E. accept_answer 沉淀生效 ----------------
        st, res2 = call("POST", f"/api/student/exams/{aid}/submit", STU2, "student", PILOT,
                        {"client_id": "e2e-2nd", "answers": answers})
        check("E8 第二个学生提交成功", st == 200, f"{st} {res2}")
        b2 = {i["seq"]: i for i in res2["items"]}
        check("E9 同题同写法自动判对（不再转人工）",
              b2[3]["status"] == "auto" and b2[3]["correct"] is True, b2[3])
        check("E10 第二学生只需人工 1 题（解答题）",
              res2["manual_pending_count"] == 1, res2["manual_pending_count"])
        n = await conn.fetchval(
            "SELECT count(*) FROM exam_grading_tickets WHERE paper_id=$1 AND student_id=$2", pid, STU2)
        check("E11 第二学生只开 1 张工单", n == 1, n)

        # 学生端列表能看到该卷
        st, l = call("GET", "/api/student/exams", STU, "student", PILOT)
        check("E12 学生端作业列表含本卷", any(x["assignment_id"] == aid for x in l), st)

        # 教师端卷详情带答案值（组卷校对用）
        st, p = call("GET", f"/api/admin/exam/papers/{pid}", "teacher@tes.edu", "teacher", PILOT)
        q3 = [q for q in p["questions"] if q["seq"] == 3][0]
        check("E13 教师卷详情含答案值（含教师追加项）",
              "y轴" in q3["answer_values"], q3["answer_values"])

    finally:
        if pid:
            await cleanup(conn, pid)
            left = await conn.fetchval("SELECT count(*) FROM exam_papers WHERE id=$1", pid)
            print(f"\n== 清理完成（paper 残留 {left}）")
        await conn.close()

    print(f"\n===== PASS {len(PASS)} / FAIL {len(FAIL)} =====")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print(" -", f)


asyncio.run(main())
