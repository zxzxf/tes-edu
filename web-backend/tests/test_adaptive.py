"""T-B3 ZPD + 自适应推题引擎单测（纯函数层，不连库）。

覆盖计划书验收：
- 推题 100% 落在 ZPD：构造掌握度样例，断言已掌握点绝不入 ZPD、
  薄弱且前置就绪的点必入、前置薄弱的点被阻塞（超纲不推）；
- 难度随答对升 / 答错降（difficulty_band 的近期正确率修正）；
- 间隔重复计划：练过但正在遗忘的点按紧迫度排出，刚练熟的跳过；
- 与 T-B2 一致性：掌握度缺省按就绪处理（冷启动不全阻塞）。
"""
from datetime import datetime, timedelta, timezone

from app.services import adaptive

NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)

# DAG：A 是 B 的前置，B 是 C 的前置；(X,Y) 表示 Y 是 X 的前置
EDGES = [("B", "A"), ("C", "B"), ("D", "A"), ("E", "D")]
ALL_KPS = ["A", "B", "C", "D", "E", "F"]   # F：无前置的根节点


def _days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


# ------------------------------------------------------------------ ZPD

def test_mastered_kp_never_in_zpd():
    """已掌握（>=0.8）的点绝不推——验收核心。"""
    mmap = {"A": 0.95, "B": 0.82}
    zpd = adaptive.compute_zpd(mmap, EDGES, ALL_KPS)
    assert "A" not in zpd
    assert "B" not in zpd


def test_weak_kp_with_ready_prereq_in_zpd():
    """自身弱（0.3）但前置已掌握（0.9）-> 正是 ZPD，该推。"""
    mmap = {"A": 0.9, "B": 0.3}
    zpd = adaptive.compute_zpd(mmap, EDGES, ALL_KPS)
    assert "B" in zpd


def test_weak_prereq_blocks_descendant():
    """前置是已知薄弱点（0.4<0.8）-> 后置点被阻塞，超纲不推。"""
    mmap = {"A": 0.4, "B": 0.3}
    zpd = adaptive.compute_zpd(mmap, EDGES, ALL_KPS)
    assert "B" not in zpd      # 前置 A 薄弱
    assert "C" not in zpd      # 传递阻塞
    assert "A" in zpd          # A 自身弱但无前置 -> 该推


def test_missing_prereq_is_neutral_not_blocking():
    """T-B2 约定：前置无记录按就绪处理（避免冷启动全阻塞 / 误扣）。"""
    mmap = {"B": 0.3}           # A 无记录
    zpd = adaptive.compute_zpd(mmap, EDGES, ALL_KPS)
    assert "B" in zpd
    assert "A" in zpd           # 无记录 = 未掌握 -> 候选


def test_cold_start_roots_in_zpd():
    """冷启动（全无记录）：根节点（无前置）全部就绪，形成学习路径起点。"""
    zpd = adaptive.compute_zpd({}, EDGES, ALL_KPS)
    assert "A" in zpd and "F" in zpd      # 根
    assert "B" in zpd and "C" in zpd      # 缺省就绪 -> 全部候选
    assert zpd == set(ALL_KPS)            # 无一被阻塞


def test_zpd_100_percent_excludes_mastered_over_full_map():
    """全图断言：任意构造下 ZPD 与已掌握点不相交（100% 落在 ZPD）。"""
    mmap = {k: 0.85 + 0.01 * i for i, k in enumerate(ALL_KPS)}   # 全部已掌握
    zpd = adaptive.compute_zpd(mmap, EDGES, ALL_KPS)
    assert zpd == set()
    # 再把 B 打回薄弱、A 保持已掌握 -> 只有 B 入 ZPD
    mmap["B"] = 0.2
    zpd = adaptive.compute_zpd(mmap, EDGES, ALL_KPS)
    assert zpd == {"B"}


def test_empty_candidates():
    assert adaptive.compute_zpd({"A": 0.9}, EDGES, []) == set()


# ------------------------------------------------------------------ 难度带

def test_depth_map_learning_path_order():
    """depth：根=0，沿最长前置链递增；无前置的点为 0。"""
    dm = adaptive.depth_map(EDGES, ALL_KPS)
    assert dm["A"] == 0 and dm["F"] == 0      # 根
    assert dm["B"] == 1 and dm["D"] == 1
    assert dm["C"] == 2                        # C<-B<-A
    assert dm["E"] == 2                        # E<-D<-A


def test_depth_map_survives_cycle():
    """坏数据（环）不死循环：stack 保护下终止并给出有限深度。"""
    dm = adaptive.depth_map([("X", "Y"), ("Y", "X")], ["X", "Y"])
    assert dm == {"X": 2, "Y": 1}              # 环上第二跳即被 stack 拦下


def test_difficulty_band_by_mastery():
    assert adaptive.difficulty_band(None) == "easy"     # 未学
    assert adaptive.difficulty_band(0.3) == "easy"
    assert adaptive.difficulty_band(0.5) == "medium"
    assert adaptive.difficulty_band(0.85) == "hard"


def test_difficulty_band_rises_with_recent_correct():
    """答对升档：同样掌握度，近期全对 -> 难度带上抬。"""
    assert adaptive.difficulty_band(0.5, recent_rate=1.0) == "hard"    # 0.75
    assert adaptive.difficulty_band(0.5, recent_rate=0.0) == "easy"    # 0.25


def test_difficulty_band_recent_rate_clamped():
    assert adaptive.difficulty_band(0.5, recent_rate=5.0) == "hard"
    assert adaptive.difficulty_band(0.5, recent_rate=-1.0) == "easy"


def test_band_to_difficulty_range():
    assert adaptive.band_to_difficulty("easy") == (1, 2)
    assert adaptive.band_to_difficulty("medium") == (3, 3)
    assert adaptive.band_to_difficulty("hard") == (4, 5)
    assert adaptive.band_to_difficulty("unknown") == (3, 3)   # 兜底中档


# ------------------------------------------------------------------ 间隔重复

def test_review_due_when_forgotten():
    """60 天没练、曾掌握 0.9 -> 衰减 0.225 跌破阈值 -> 立即复习。"""
    out = adaptive.plan_review(
        [{"kp": "A", "correct_count": 9, "wrong_count": 1, "last_practiced_at": _days_ago(60)}],
        now=NOW,
    )
    assert len(out) == 1
    assert out[0]["urgency"] == "due"
    assert out[0]["days_until_review"] == 0.0
    assert out[0]["decayed"] < adaptive.WEAK_THRESHOLD


def test_review_upcoming_with_countdown():
    """1 天前练的 0.9 -> 还在阈值上，给出倒计时天数。"""
    out = adaptive.plan_review(
        [{"kp": "A", "correct_count": 9, "wrong_count": 1, "last_practiced_at": _days_ago(1)}],
        now=NOW,
    )
    assert len(out) == 1
    assert out[0]["urgency"] == "upcoming"
    assert 0 < out[0]["days_until_review"] < 30


def test_review_skips_never_mastered_and_fresh_and_unjudged():
    """从未真掌握（归 ZPD 推题）/ 还很熟 / 只练没判分 -> 都不排入。"""
    out = adaptive.plan_review(
        [
            {"kp": "weak", "correct_count": 1, "wrong_count": 9, "last_practiced_at": _days_ago(90)},
            {"kp": "fresh", "correct_count": 10, "wrong_count": 0, "last_practiced_at": _days_ago(1)},
            {"kp": "unjudged", "correct_count": 0, "wrong_count": 0, "last_practiced_at": _days_ago(90)},
        ],
        now=NOW,
    )
    assert out == []


def test_review_sorted_by_urgency():
    """最紧迫（最旧的）排最前。"""
    out = adaptive.plan_review(
        [
            {"kp": "soon", "correct_count": 9, "wrong_count": 1, "last_practiced_at": _days_ago(20)},
            {"kp": "late", "correct_count": 9, "wrong_count": 1, "last_practiced_at": _days_ago(80)},
        ],
        now=NOW,
    )
    assert [r["kp"] for r in out] == ["late", "soon"]
    assert out[0]["days_until_review"] <= out[1]["days_until_review"]


def test_review_empty_input():
    assert adaptive.plan_review([], now=NOW) == []
    assert adaptive.plan_review(None, now=NOW) == []
