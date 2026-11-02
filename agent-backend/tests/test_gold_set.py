"""M2-8 Gold set 回归门禁（MVP 先用线上坑题 + 选择卷常见坑，后续扩到 50+ 题）。

这些题是此前线上反复踩坑的样本：歧义 a=1、等比 ±2、切线 a=4、期望 61/25、
平均数选 C；以及「纯选择卷 MVP」里最高频的判分形态——字母/小写/带点号、
选项内容替代字母、多选集合相等、漏选 partial、错选 False。

整张门禁以选择题为主（choice-first），因为第一铲 MVP = 高三·数学·纯选择题卷，
判分确定性必须优先锁死选择题形态；填空保留核心坑题（分数≡小数、带变量名）作保底。

全部走 ``equiv`` 纯函数（0 token），适合高频 CI 回归。
"""
import pytest

from app.core.equiv import blank_equivalent, choice_equivalent

# (题名, 判定种类, ref, student, options, expected, multi)
# kind: "blank" -> blank_equivalent ; "choice" -> choice_equivalent(multi=..., options=...)
OPTS = {"A": "8", "B": "9", "C": "12", "D": "18"}
CASES = [
    # ===== 填空保底：核心坑题（含分数≡小数、带变量名、多解）=====
    # 1. 歧义 a=1（主解 answer=1，学生写 a=1 必须对，不能被判错）
    ("歧义_a=1_prefix", "blank", "1", "a=1", None, True, False),
    ("歧义_a=1_num", "blank", "1", "1", None, True, False),
    ("歧义_a=1_wrong", "blank", "1", "2", None, False, False),

    # 2. 等比 ±2（标答 2 或 -2，学生写任一个都对；写 3 错）
    ("等比_±2_v1", "blank", "2 或 -2", "2", None, True, False),
    ("等比_±2_v2", "blank", "2 或 -2", "-2", None, True, False),
    ("等比_±2_pm", "blank", "2 或 -2", "±2", None, True, False),
    ("等比_±2_wrong", "blank", "2 或 -2", "3", None, False, False),

    # 3. 切线 a=4（标答 4，a=4 / 4 都对；3 错）
    ("切线_a=4_prefix", "blank", "4", "a=4", None, True, False),
    ("切线_a=4_num", "blank", "4", "4", None, True, False),
    ("切线_a=4_wrong", "blank", "4", "3", None, False, False),

    # 4. 期望 61/25（分数 ≡ 小数 2.44，答 3 错且点评不得写 3.04）
    ("期望_61/25_eq", "blank", "61/25", "61/25", None, True, False),
    ("期望_61/25_dec", "blank", "61/25", "2.44", None, True, False),
    ("期望_61/25_ex", "blank", "61/25", "E(X)=61/25", None, True, False),
    ("期望_61/25_wrong", "blank", "61/25", "3", None, False, False),

    # ===== 选择题（MVP 主力形态，choice-first）=====
    # 5. 平均数选 C（字母 / 小写 / 带点号 / 写数值都对；错字母错数值都不对）
    ("平均_C", "choice", "C", "C", OPTS, True, False),
    ("平均_c", "choice", "C", "c", OPTS, True, False),
    ("平均_Cdot_space", "choice", "C", "C. 12", OPTS, True, False),
    ("平均_Cdot_none", "choice", "C", "C.12", OPTS, True, False),
    ("平均_12", "choice", "C", "12", OPTS, True, False),
    ("平均_12_dec", "choice", "C", "12.0", OPTS, True, False),  # 小数内容等价
    ("平均_A", "choice", "C", "A", OPTS, False, False),
    ("平均_9", "choice", "C", "9", OPTS, False, False),
    ("平均_9_dec", "choice", "C", "9.0", OPTS, False, False),

    # 6. 选项内容替代字母（不同题：选 B 内容恰为 9）
    ("内容_B_eq_9", "choice", "B", "9", OPTS, True, False),
    ("内容_D_eq_18", "choice", "D", "18", OPTS, True, False),
    ("内容_A_eq_8", "choice", "A", "8", OPTS, True, False),

    # 7. 多选：集合相等（顺序无关）
    ("多选_集合_AB", "choice", "A,B", "B,A", OPTS, True, True),
    ("多选_集合_ABC", "choice", "A,B,C", "C,B,A", OPTS, True, True),

    # 8. 多选：漏选但所选皆正确 -> partial（不计错）
    ("多选_partial_A", "choice", "A,B", "A", OPTS, "partial", True),
    ("多选_partial_B", "choice", "A,B", "B", OPTS, "partial", True),
    ("多选_partial_AC", "choice", "A,B,C", "A,C", OPTS, "partial", True),

    # 9. 多选：错选 -> False（非 partial）
    ("多选_wrong_AC", "choice", "A,B", "A,C", OPTS, False, True),
    ("多选_wrong_AD", "choice", "A,B", "A,D", OPTS, False, True),

    # 10. 连写多选（红线：曾只取首字母致多选全错）—— 卡面 M2-2 要求"单测覆盖字母/值/连写三种"
    # 正确集合必须与 ref 完全一致：ref 正确答案为 A,C,D 时学生写 ACD 才判对；写 AC（漏 B）才是 partial。
    ("连写_集合_ACD", "choice", "A,C,D", "ACD", OPTS, True, True),
    ("连写_集合_acd_lower", "choice", "A,C,D", "acd", OPTS, True, True),
    ("连写_集合_ABC", "choice", "A,B,C", "ABC", OPTS, True, True),
    ("连写_partial_AC", "choice", "A,B,C", "AC", OPTS, "partial", True),
    ("连写_partial_AB", "choice", "A,B,C", "AB", OPTS, "partial", True),
    ("连写_wrong_AC_vs_AB", "choice", "A,B", "AC", OPTS, False, True),
    ("连写_wrong_AD_vs_AB", "choice", "A,B", "AD", OPTS, False, True),
    ("连写_single_should_not_match", "choice", "C", "AC", OPTS, False, True),
]


@pytest.mark.parametrize("name,kind,ref,stu,opts,exp,multi", CASES)
def test_gold_set(name, kind, ref, stu, opts, exp, multi):
    if kind == "choice":
        got = choice_equivalent(stu, ref, multi=multi, options=opts)
    else:
        got = blank_equivalent(stu, ref)
    assert got == exp, f"{name}: expected {exp!r}, got {got!r}"
