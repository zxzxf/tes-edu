# -*- coding: utf-8 -*-
"""从 _raw_{year}.txt 补全物理选择题丢失的 options（2022-2026 源 PDF 已删，raw 文本还在）。

背景：早期解析物理 clean JSON 时只提了部分年份的 options，2022-2026 选择题
options 为空，导致 build_steps 只能走 single 兜底、无法按选项归因。
raw 文本里 A．B．C．D． 选项块完整保留，这里回填。

只回填 options 为空的 single/multi 题；其他字段不动。
写回原 clean JSON（灌库幂等由 content_hash 保证——注意：options 不进 hash，
hash = subject|stem|answer，所以回填不会造成重复行）。
"""
import glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
FULLWIDTH = str.maketrans("Ａ-Ｅ", "A-E")


def norm_text(s):
    return re.sub(r"\s+", "", str(s or "")).translate(FULLWIDTH)


def split_raw_segments(raw):
    """按行首 'N．' 切题段 -> {no: [seg, ...]}

    坑：半角点后跟数字是小数字点（如行首 '6.63×10⁻³⁴'），不能当题号，
    用 \\.(?!\\d) 排除；真题题号均为全角．
    """
    idxs = [(int(m.group(1)), m.start())
            for m in re.finditer(r"(?m)^\s*(\d{1,2})(?:．|\.(?!\d))", raw)]
    segs = {}
    for i, (no, s) in enumerate(idxs):
        e = idxs[i + 1][1] if i + 1 < len(idxs) else len(raw)
        segs.setdefault(no, []).append(raw[s:e])
    return segs


def extract_options(seg):
    """从题段提取 A-E 选项。支持一行多个选项（'A．x  B．y'）。"""
    m = re.search(r"A[．.]([\s\S]*?)(?=【详解】|【解析】|$)", seg)
    if not m:
        return None
    blk = m.group(1).translate(FULLWIDTH)
    marks = [(mm.start(), mm.group(1)) for mm in re.finditer(r"([B-E])[．.]", blk)]
    if len(marks) < 3:          # 至少 B/C/D 三个后续选项
        return None
    opts = {"A": blk[:marks[0][0]]}
    for i, (pos, letter) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(blk)
        opts[letter] = blk[pos + 2:end]
    out = {k: re.sub(r"\s+", " ", v).strip(" 。；;") for k, v in opts.items()}
    return out if len(out) >= 4 and all(v for v in out.values()) else None


def find_segment_for(segs, no, stem):
    """按题号 + 题干前 8 字匹配题段，防止误配。"""
    cands = segs.get(no) or []
    key = norm_text(stem)[:8]
    for seg in cands:
        if key and key in norm_text(seg)[:200]:
            return seg
    return cands[0] if len(cands) == 1 else None


def main():
    raws = {}
    for f in glob.glob(os.path.join(BASE, "_raw_*.txt")):
        year = int(re.search(r"_raw_(\d{4})\.txt", f).group(1))
        raws[year] = open(f, encoding="utf-8").read()
    print("可用 raw 年份:", sorted(raws))

    total_filled = total_miss = 0
    for f in sorted(glob.glob(os.path.join(BASE, "*_干净.json"))):
        recs = json.load(open(f, encoding="utf-8"))
        year = None
        for r in recs:
            y = int(r.get("year") or 0)
            if y:
                year = y
                break
        raw = raws.get(year)
        if not raw:
            print(f"[skip] {os.path.basename(f)} 无 raw_{year}")
            continue
        segs = split_raw_segments(raw)
        filled = miss = 0
        for r in recs:
            if r.get("type") not in ("single", "multi") or r.get("options"):
                continue
            seg = find_segment_for(segs, r.get("no"), r.get("stem"))
            opts = extract_options(seg) if seg else None
            if opts:
                r["options"] = opts
                filled += 1
            else:
                miss += 1
                print(f"  [miss] {os.path.basename(f)} no={r.get('no')} "
                      f"stem={str(r.get('stem'))[:40]}")
        if filled or miss:
            json.dump(recs, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        total_filled += filled
        total_miss += miss
        print(f"{os.path.basename(f)}: 回填 {filled}，失败 {miss}")
    print(f"\n合计：回填 {total_filled}，仍缺 {total_miss}")


if __name__ == "__main__":
    main()
