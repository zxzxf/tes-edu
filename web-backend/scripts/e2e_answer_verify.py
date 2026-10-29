"""M2-9 / G1 答案双源交叉校验端到端验证（137 容器内跑，注入 X-User-* 头）。

覆盖：
A. 组卷后立刻校验：可代入题走机械档（mechanical_pass），不可验证题必须教师确认
B. 未通过校验**禁止发布**（409 ANSWER_UNVERIFIED；dry-run 也拦）
C. 教师确认（可顺手修正答案）后放行；确认留痕（谁/何时）
D. 题库被改过 → 两个来源真的不同 → conflict，且带第二来源答案
E. 同源（题库=快照）**不算**第二来源，不会伪造 verified
F. 已发布卷不许直接改答案（会让历史成绩的判据变化）
G. 跨租户拿不到校验结果（404）
H. 重复跑校验幂等（一题一行）

跑完清理本脚本造的全部数据（按标题前缀 + content_hash 前缀）。
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
OTHER = "demo"
T = "teacher@tes.edu"
T_OTHER = "teacher@demo.com"
TITLE = "E2E-答案校验卷（脚本自动清理）"
TITLE2 = "E2E-答案校验冲突卷（脚本自动清理）"
CH_SUB = "e2e-verify-sub-1"      # 可代入：x^2-2x-3=0，答案 3
CH_CONF = "e2e-verify-conf-1"    # 冲突：题库被改成 5

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


async def new_paper(conn, title, questions):
    pid = await conn.fetchval(
        """INSERT INTO exam_papers (tenant_id,title,paper_type,grade,subject,difficulty_avg,
                                   knowledge_points,created_by,published,published_at,created_at)
           VALUES ($1,$2,'pure_blank','高三','数学',2,'[]'::json,'e2e',false,NULL,now())
           RETURNING id""",
        PILOT, title,
    )
    for seq, stem, ans, ch in questions:
        await conn.execute(
            """INSERT INTO exam_paper_questions
                 (paper_id,seq,qtype,multi,content_hash,stem,options,answer,analysis,
                  difficulty,knowledge_points,answer_values,created_at)
               VALUES ($1,$2,'blank',false,$3,$4,NULL,$5,'解析',2,'[]'::json,NULL,now())""",
            pid, seq, ch, stem, ans,
        )
    return pid


async def seed_kb(conn, ch, stem, answer):
    await conn.execute(
        """INSERT INTO course_kb (subject,grade,type,stem,answer,content_hash,tenant_id,created_at)
           VALUES ('数学','高三','填空',$1,$2,$3,$4,now())""",
        stem, answer, ch, PILOT,
    )


async def cleanup(conn):
    for row in await conn.fetch(
        "SELECT id FROM exam_papers WHERE title LIKE 'E2E-答案校验%'"
    ):
        pid = row["id"]
        await conn.execute("DELETE FROM exam_answer_checks WHERE paper_id=$1", pid)
        await conn.execute("DELETE FROM exam_paper_questions WHERE paper_id=$1", pid)
        await conn.execute("DELETE FROM exam_papers WHERE id=$1", pid)
    await conn.execute("DELETE FROM course_kb WHERE content_hash LIKE 'e2e-verify-%'")


async def main():
    conn = await asyncpg.connect(DSN)
    await cleanup(conn)
    try:
        # ---------------- 造卷 ----------------
        stem_sub = "已知 x^2-2x-3=0，求 x 的正根：x=______"
        stem_conf = "求下列各式的值：______"
        pid = await new_paper(
            conn, TITLE,
            [(1, stem_sub, "3", CH_SUB), (2, stem_conf, "7", CH_CONF)],
        )
        await seed_kb(conn, CH_SUB, stem_sub, "3")     # 同源（组卷即抄自题库）
        await seed_kb(conn, CH_CONF, stem_conf, "7")
        print(f"== 造卷 paper={pid}\n")

        # ---------------- A. 组卷后校验 ----------------
        st, ac = call("GET", f"/api/admin/exam/papers/{pid}/answer-checks", T, "teacher", PILOT)
        items = {i["seq"]: i for i in (ac or {}).get("items", [])} if isinstance(ac, dict) else {}
        check("A1 校验结果按题返回", st == 200 and len(items) == 2, (st, [i for i in items]))
        check("A2 可代入题走机械档（代入检验通过）",
              items.get(1, {}).get("status") == "mechanical_pass",
              items.get(1, {}).get("status"))
        check("A3 同源不算第二来源（不能是 verified）",
              items.get(1, {}).get("secondary_source") is None
              and items.get(1, {}).get("status") != "verified",
              items.get(1))
        check("A4 不可验证题必须待确认",
              items.get(2, {}).get("status") == "unverified", items.get(2, {}).get("status"))
        check("A5 整卷未通过", ac["summary"]["ok"] is False and ac["summary"]["passed"] == 1,
              ac.get("summary"))

        # ---------------- B. 未通过禁止发布 ----------------
        st, v = call("GET", f"/api/admin/exam/papers/{pid}/validation", T, "teacher", PILOT)
        codes = [x["code"] for x in (v.get("violations") or [])]
        check("B1 dry-run 拦未校验答案（ANSWER_UNVERIFIED）",
              st == 200 and v["ok"] is False and "ANSWER_UNVERIFIED" in codes, (st, codes))
        st, p = call("POST", f"/api/admin/exam/papers/{pid}/publish", T, "teacher", PILOT)
        code = (p.get("detail") or {}).get("code") if isinstance(p, dict) else None
        vios = (p.get("detail") or {}).get("violations") or []
        check("B2 发布 409 ANSWER_UNVERIFIED", st == 409 and code == "ANSWER_UNVERIFIED", (st, code))
        check("B3 只拦没通过的那一道", [x["seq"] for x in vios] == [2], vios)

        # ---------------- C. 教师确认后放行 ----------------
        st, ac2 = call("POST", f"/api/admin/exam/papers/{pid}/answer-checks/confirm", T,
                       "teacher", PILOT, {"seqs": [2], "comment": "原卷答案已核对"})
        conf = {i["seq"]: i for i in (ac2 or {}).get("items", [])}
        check("C1 确认后整卷通过", st == 200 and ac2["summary"]["ok"] is True, ac2.get("summary"))
        check("C2 确认留痕（谁/何时/备注）",
              conf[2]["confirmed_by"] == T and bool(conf[2]["confirmed_at"])
              and conf[2]["confirmed_comment"] == "原卷答案已核对", conf.get(2))
        check("C3 确认不覆盖机械档（第1题仍是 mechanical_pass）",
              conf[1]["status"] == "mechanical_pass", conf[1]["status"])
        st, p = call("POST", f"/api/admin/exam/papers/{pid}/publish", T, "teacher", PILOT)
        check("C4 校验通过后可发布", st == 200 and p.get("published") is True, (st, p))

        # ---------------- D. 已发布卷不许改答案 ----------------
        st, r = call("POST", f"/api/admin/exam/papers/{pid}/answer-checks/confirm", T,
                     "teacher", PILOT, {"seqs": [1], "answers": {"1": "4"}})
        check("D1 已发布卷改答案被拒（409）", st == 409, (st, r))

        # ---------------- E. 题库被改 → 双源冲突 ----------------
        pid2 = await new_paper(conn, TITLE2, [(1, stem_conf, "7", "e2e-verify-conf-2")])
        await seed_kb(conn, "e2e-verify-conf-2", stem_conf, "7")
        await conn.execute(
            "UPDATE course_kb SET answer='5' WHERE content_hash='e2e-verify-conf-2'"
        )
        st, ac3 = call("POST", f"/api/admin/exam/papers/{pid2}/answer-checks/run", T,
                       "teacher", PILOT)
        it = (ac3 or {}).get("items", [{}])[0] if isinstance(ac3, dict) else {}
        check("E1 题库与卷面不一致 → conflict", it.get("status") == "conflict", it.get("status"))
        check("E2 带出第二来源答案", it.get("secondary_source") == "kb"
              and it.get("secondary_answer") == "5", it)
        st, p = call("POST", f"/api/admin/exam/papers/{pid2}/publish", T, "teacher", PILOT)
        vios = (p.get("detail") or {}).get("violations") or []
        check("E3 冲突题发布被拦（ANSWER_CONFLICT）",
              st == 409 and any(x["code"] == "ANSWER_CONFLICT" for x in vios), (st, vios))
        st, ac4 = call("POST", f"/api/admin/exam/papers/{pid2}/answer-checks/confirm", T,
                       "teacher", PILOT, {"seqs": [1], "answers": {"1": "5"}})
        it4 = (ac4 or {}).get("items", [{}])[0]
        check("E4 教师改答案后转为已确认（并回写卷面答案）",
              st == 200 and it4.get("status") == "teacher_confirmed" and it4.get("answer") == "5",
              it4)
        st, p = call("POST", f"/api/admin/exam/papers/{pid2}/publish", T, "teacher", PILOT)
        check("E5 修正后可发布", st == 200 and p.get("published") is True, (st, p))

        # ---------------- F. 跨租户 ----------------
        st, x = call("GET", f"/api/admin/exam/papers/{pid}/answer-checks", T_OTHER,
                     "teacher", OTHER)
        check("F1 跨租户读不到（404）", st == 404, st)
        st, x = call("POST", f"/api/admin/exam/papers/{pid}/answer-checks/confirm", T_OTHER,
                     "teacher", OTHER, {"seqs": [1]})
        check("F2 跨租户确认被拒（404）", st == 404, st)

        # ---------------- G. 幂等 ----------------
        n = await conn.fetchval(
            "SELECT count(*) FROM exam_answer_checks WHERE paper_id=$1", pid)
        m = await conn.fetchval(
            "SELECT count(*) FROM exam_paper_questions WHERE paper_id=$1", pid)
        check("G1 一题一行（重复跑不新增）", n == m == 2, (n, m))

    finally:
        await cleanup(conn)
        await conn.close()

    print(f"\n===== {len(PASS)} passed, {len(FAIL)} failed =====")
    if FAIL:
        print("FAILED: " + "; ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
