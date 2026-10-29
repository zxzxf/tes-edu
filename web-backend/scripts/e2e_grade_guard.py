"""M2-10 / G3 判分护栏端到端验证（137 容器内跑，注入 X-User-* 头直连 web-backend）。

覆盖：
A. 首次判定**不计**改判（否则填空题三个学生就自动冻结）
B. 有效改判逐步累计：1/3、2/3 不冻结
C. 第 3 次改判 → 冻结 + 告警（影响面）
D. 冻结后：新提交该题一律转人工，原因写「标答在复核」
E. 冻结的题**禁止发布**（新卷 409 QUESTION_FROZEN；dry-run 也拦）
F. 跨租户不可见（404 / 空列表）
G. 已读（ack）不解冻
H. 解冻：恢复自动判分、计数清零、可再发布
I. 解冻后复发 → refreeze 告警（说明这题反复出问题）
J. 选择题冻结后不再自动判（判分侧拦截；选择题目前无改判入口，直接落冻结记录验证）

跑完清理本脚本造的全部数据（按 paper_id 前缀 + content_hash 前缀）。
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
T2 = "teacher2@tes.edu"
STU = "student@tes.edu"
STU2 = "student_002"
STU3 = "student_003"
TITLE = "E2E-判分护栏验证卷（脚本自动清理）"
TITLE2 = "E2E-护栏发布拦截卷（脚本自动清理）"
TITLE3 = "E2E-护栏选择题卷（脚本自动清理）"
CH_BLANK = "e2e-guard-blank-1"
CH_CHOICE = "e2e-guard-choice-1"
ANS_BLANK = "3x+4y-5=0"        # 含字母的方程 → 一定转人工（数值型标答会被直接算出来）
STU_ANS = "3x+4y=5"           # 与标答不等价，必转人工

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


async def new_paper(conn, title, questions, published=False):
    pid = await conn.fetchval(
        """INSERT INTO exam_papers (tenant_id,title,paper_type,grade,subject,difficulty_avg,
                                   knowledge_points,created_by,published,published_at,created_at)
           VALUES ($1,$2,$3,'高三','数学',2,'[]'::json,'e2e',$4,
                   CASE WHEN $4 THEN now() ELSE NULL END, now()) RETURNING id""",
        PILOT, title, "pure_blank" if len(questions) == 1 and questions[0][1] == "blank" else "mixed",
        published,
    )
    for seq, qt, stem, ans, ch in questions:
        await conn.execute(
            """INSERT INTO exam_paper_questions
                 (paper_id,seq,qtype,multi,content_hash,stem,options,answer,analysis,
                  difficulty,knowledge_points,answer_values,created_at)
               VALUES ($1,$2,$3,false,$4,$5,NULL,$6,'解析',2,'[]'::json,NULL,now())""",
            pid, seq, qt, ch, stem, ans,
        )
    return pid


async def assign(conn, pid, grade="高三"):
    return await conn.fetchval(
        """INSERT INTO exam_assignments (paper_id,tenant_id,target_type,target_grade,
                                         assigned_by,assigned_at,due_at)
           VALUES ($1,$2,'grade',$3,'e2e',now(),now()+interval '7 day') RETURNING id""",
        pid, PILOT, grade,
    )


async def cleanup(conn):
    pids = await conn.fetch(
        "SELECT id FROM exam_papers WHERE title LIKE 'E2E-判分护栏%' OR title LIKE 'E2E-护栏%'"
    )
    for row in pids:
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
    await conn.execute(
        "DELETE FROM exam_grading_alerts WHERE content_hash LIKE 'e2e-guard-%'"
    )
    await conn.execute(
        "DELETE FROM exam_answer_freeze WHERE content_hash LIKE 'e2e-guard-%'"
    )


def fingerprint(ans):
    sys.path.insert(0, "/app")
    from app.core.grade_guard import answer_fingerprint

    return answer_fingerprint(ans)


async def main():
    conn = await asyncpg.connect(DSN)
    await cleanup(conn)          # 先清一遍，避免上次中断留下脏数据
    try:
        # ---------------- 造卷 ----------------
        pid = await new_paper(
            conn, TITLE,
            [(1, "blank", "把直线方程化为 3x+4y=5 的形式：______", ANS_BLANK, CH_BLANK)],
            published=True,
        )
        aid = await assign(conn, pid)
        print(f"== 造卷 paper={pid} assignment={aid}\n")

        # ---------------- A. 首次判定不计改判 ----------------
        answers = [{"seq": 1, "student_answer": STU_ANS}]   # 方程型标答 → 必转人工
        st, r1 = call("POST", f"/api/student/exams/{aid}/submit", STU, "student", PILOT,
                      {"client_id": "g-1", "answers": answers})
        check("A1 学生提交成功", st == 200, f"{st} {r1}")
        check("A2 符号标答转人工", r1["items"][0]["status"] == "manual_pending", r1["items"][0])
        tids = await conn.fetch(
            "SELECT id,seq FROM exam_grading_tickets WHERE paper_id=$1 ORDER BY seq", pid)
        check("A3 提交即开单", len(tids) == 1, len(tids))
        t1 = tids[0]["id"]

        st, d = call("POST", f"/api/admin/grading/tickets/{t1}/judge", T, "teacher", PILOT,
                     {"verdict": "correct", "comment": "数值近似，算对"})
        check("A4 首次判定成功", st == 200 and d["status"] == "reviewed", st)
        check("A5 首次判定不计改判（无护栏记录或 flips=0）",
              (d.get("freeze") or {}).get("flips", 0) == 0, d.get("freeze"))
        n = await conn.fetchval("SELECT count(*) FROM exam_answer_freeze WHERE content_hash=$1", CH_BLANK)
        check("A6 首次判定不落护栏记录", n == 0, n)

        # ---------------- B/C. 三连改判 → 冻结 ----------------
        st, d = call("POST", f"/api/admin/grading/tickets/{t1}/judge", T, "teacher", PILOT,
                     {"verdict": "wrong", "comment": "改判1"})
        fz = d.get("freeze") or {}
        check("B1 第 1 次改判 flips=1 仍未冻结",
              fz.get("flips") == 1 and fz.get("status") == "watching", fz)
        st, d = call("POST", f"/api/admin/grading/tickets/{t1}/judge", T2, "teacher", PILOT,
                     {"verdict": "correct", "comment": "改判2"})
        fz = d.get("freeze") or {}
        check("B2 第 2 次改判 flips=2 仍未冻结",
              fz.get("flips") == 2 and fz.get("status") == "watching", fz)
        st, d = call("POST", f"/api/admin/grading/tickets/{t1}/judge", T, "teacher", PILOT,
                     {"verdict": "wrong", "comment": "改判3：标答疑似有误"})
        fz = d.get("freeze") or {}
        check("C1 第 3 次改判触发冻结", fz.get("status") == "frozen", fz)
        check("C2 详情告警文案说明标答可能有误", "标答很可能有误" in (fz.get("message") or ""), fz.get("message"))
        check("C3 记录改判人（去重）", set(fz.get("actors") or []) == {T, T2}, fz.get("actors"))

        al = await conn.fetch(
            "SELECT kind,flips,affected_count,message FROM exam_grading_alerts WHERE content_hash=$1", CH_BLANK)
        check("C4 落告警一条 kind=freeze", len(al) == 1 and al[0]["kind"] == "freeze",
              [dict(a) for a in al])
        check("C5 告警带影响面字段", al and al[0]["affected_count"] is not None, al)

        # ---------------- D. 冻结后新提交转人工 ----------------
        st, r2 = call("POST", f"/api/student/exams/{aid}/submit", STU2, "student", PILOT,
                      {"client_id": "g-2", "answers": [{"seq": 1, "student_answer": STU_ANS}]})
        item = r2["items"][0]
        check("D1 冻结题新提交转人工", item["status"] == "manual_pending", item)
        check("D2 原因是「标答在复核」而非「判不了」",
              "复核" in (item.get("reason") or ""), item.get("reason"))
        n = await conn.fetchval(
            "SELECT count(*) FROM exam_grading_tickets WHERE paper_id=$1 AND student_id=$2", pid, STU2)
        check("D3 冻结题仍然开单（老师照常判）", n == 1, n)

        # ---------------- E. 冻结题禁止发布 ----------------
        pid2 = await new_paper(
            conn, TITLE2,
            [(1, "blank", "把直线方程化为 3x+4y=5 的形式：______", ANS_BLANK, CH_BLANK)],
        )
        st, v = call("GET", f"/api/admin/exam/papers/{pid2}/validation", T, "teacher", PILOT)
        codes = [x["code"] for x in (v.get("violations") or [])] if isinstance(v, dict) else []
        check("E1 dry-run 拦冻结题（ok=false + QUESTION_FROZEN）",
              st == 200 and v["ok"] is False and "QUESTION_FROZEN" in codes, (st, codes))
        st, p = call("POST", f"/api/admin/exam/papers/{pid2}/publish", T, "teacher", PILOT)
        code = (p.get("detail") or {}).get("code") if isinstance(p, dict) else None
        check("E2 发布冻结题 409 QUESTION_FROZEN", st == 409 and code == "QUESTION_FROZEN", (st, p))

        # ---------------- F. 跨租户 ----------------
        fid = await conn.fetchval(
            "SELECT id FROM exam_answer_freeze WHERE content_hash=$1", CH_BLANK)
        st, lst = call("GET", "/api/admin/grading/freezes", "teacher@demo.com", "teacher", OTHER)
        check("F1 跨租户看不到冻结列表", st == 200 and lst["total"] == 0, lst.get("total"))
        st, _ = call("POST", f"/api/admin/grading/freezes/{fid}/release", "teacher@demo.com",
                     "teacher", OTHER, {"comment": "cross tenant"})
        check("F2 跨租户解冻 404（拿真实 id 也进不去）", st == 404, st)
        st, al2 = call("GET", "/api/admin/grading/alerts", "teacher@demo.com", "teacher", OTHER)
        check("F3 跨租户读告警流水为空", st == 200 and al2["total"] == 0, al2.get("total"))

        # ---------------- G. 列表 + 已读 ----------------
        st, lst = call("GET", "/api/admin/grading/freezes", T, "teacher", PILOT)
        check("G1 冻结列表可见", st == 200 and lst["total"] == 1, lst.get("total"))
        f = lst["items"][0]
        check("G2 列表带改判次数与影响面",
              f["flips"] == 3 and "affected_count" in f, f)
        check("G3 列表带标答快照", f["answer"] == ANS_BLANK, f["answer"])
        st, ack = call("POST", f"/api/admin/grading/freezes/{f['freeze_id']}/ack", T, "teacher", PILOT)
        check("G4 已读成功且仍冻结",
              st == 200 and ack["acked_by"] == T and ack["status"] == "frozen", (st, ack.get("status")))
        st, lst = call("GET", "/api/admin/grading/freezes", T, "teacher", PILOT)
        check("G5 已读不解除冻结", lst["total"] == 1, lst["total"])

        # ---------------- H. 解冻 ----------------
        st, rel = call("POST", f"/api/admin/grading/freezes/{f['freeze_id']}/release", T, "teacher", PILOT,
                       {"comment": "标答确认无误，恢复机判"})
        check("H1 解冻成功", st == 200 and rel["status"] == "released", (st, rel.get("status")))
        check("H2 解冻计数清零", rel["flips"] == 0, rel["flips"])
        check("H3 记录解冻次数", rel["release_count"] == 1, rel["release_count"])
        st, p = call("POST", f"/api/admin/exam/papers/{pid2}/publish", T, "teacher", PILOT)
        check("H4 解冻后可发布", st == 200 and p.get("published") is True, (st, p))
        st, r3 = call("POST", f"/api/student/exams/{aid}/submit", STU3, "student", PILOT,
                      {"client_id": "g-3", "answers": [{"seq": 1, "student_answer": STU_ANS}]})
        check("H5 解冻后仍按原判据（符号标答照旧转人工，不是全放行）",
              r3["items"][0]["status"] == "manual_pending"
              and "复核" not in (r3["items"][0].get("reason") or ""), r3["items"][0])

        # ---------------- I. 复发 ----------------
        t3 = await conn.fetchval(
            "SELECT id FROM exam_grading_tickets WHERE paper_id=$1 AND student_id=$2", pid, STU3)
        for i, v in enumerate(["correct", "wrong", "correct", "wrong"]):
            st, d = call("POST", f"/api/admin/grading/tickets/{t3}/judge", T, "teacher", PILOT,
                         {"verdict": v, "comment": f"复发改判{i}"})
        fz = d.get("freeze") or {}
        check("I1 复发后再次冻结", fz.get("status") == "frozen", fz)
        check("I2 计数从 1 重算后到 3", fz.get("flips") == 3, fz.get("flips"))
        kinds = [r["kind"] for r in await conn.fetch(
            "SELECT kind FROM exam_grading_alerts WHERE content_hash=$1 ORDER BY id", CH_BLANK)]
        check("I3 告警流水 freeze → release → refreeze",
              kinds == ["freeze", "release", "refreeze"], kinds)
        check("I4 refreeze 文案标出复发", "解冻后再次" in (fz.get("message") or ""), fz.get("message"))

        # ---------------- J. 选择题冻结后不再自动判 ----------------
        pid3 = await new_paper(
            conn, TITLE3,
            [(1, "choice", "函数 f(x)=x^2 在 x=2 处的导数是", "C", CH_CHOICE)],
            published=True,
        )
        await conn.execute(
            """UPDATE exam_paper_questions SET options='{"A":"2","B":"3","C":"4","D":"5"}'::json
                WHERE paper_id=$1""", pid3)
        aid3 = await assign(conn, pid3)
        st, before_ok = call("POST", f"/api/student/exams/{aid3}/submit", STU, "student", PILOT,
                             {"client_id": "g-c1", "answers": [{"seq": 1, "student_answer": "C"}]})
        check("J1 未冻结时选择题自动判对",
              before_ok["items"][0]["status"] == "auto" and before_ok["items"][0]["correct"] is True,
              before_ok["items"][0])
        # 选择题目前没有改判入口（只有在冻结后才会转人工），故直接落一条冻结记录验证判分侧拦截
        await conn.execute(
            """INSERT INTO exam_answer_freeze
                 (tenant_id,content_hash,answer_fingerprint,answer,paper_id,paper_question_id,
                  seq,qtype,flips,threshold,status,actors,affected_count,release_count,
                  frozen_at,created_at,updated_at)
               VALUES ($1,$2,$3,'C',$4,
                       (SELECT id FROM exam_paper_questions WHERE paper_id=$4),
                       1,'choice',3,3,'frozen','[]'::json,0,0,now(),now(),now())""",
            PILOT, CH_CHOICE, fingerprint("C"), pid3,
        )
        st, after = call("POST", f"/api/student/exams/{aid3}/submit", STU2, "student", PILOT,
                         {"client_id": "g-c2", "answers": [{"seq": 1, "student_answer": "C"}]})
        check("J2 冻结后同一选择题不再自动判（答对也转人工）",
              after["items"][0]["status"] == "manual_pending", after["items"][0])
        check("J3 原因是标答复核", "复核" in (after["items"][0].get("reason") or ""),
              after["items"][0].get("reason"))
    finally:
        await cleanup(conn)
        left = await conn.fetchval(
            "SELECT count(*) FROM exam_answer_freeze WHERE content_hash LIKE 'e2e-guard-%'")
        left2 = await conn.fetchval(
            "SELECT count(*) FROM exam_papers WHERE title LIKE 'E2E-护栏%' OR title LIKE 'E2E-判分护栏%'")
        print(f"\n清理后残留：freeze={left} paper={left2}")
        await conn.close()

    print(f"\n===== {len(PASS)} PASS / {len(FAIL)} FAIL =====")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        sys.exit(1)


asyncio.run(main())
