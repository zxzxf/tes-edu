# -*- coding: utf-8 -*-
"""把细粒度标注后的数学真题，导出为 agent-backend/scripts/import_bank.py 可直接灌库的 JSON。

规避 import_bank 的三个静默坑：
  1. _norm_type 只认中文题型 —— 题库里 type=single/multi/fill/solve，
     若原样传入，single/multi 会被统统判成"解答"，直接污染 RAG 检索的题型过滤。
     -> 这里预先转成 单选/多选/填空/解答。
  2. _split_kp 的分隔符集合是 "；;、/|，，"，而知识点名里本身含 "/"（如"众数/中位数/平均数"、
     "sin/cos/tan 的定义域"），原样传入会被切碎。
     -> knowledge_points 存 DAG 的 id（只含字母数字和 -，绝不会被切），另出 id→name 映射表。
  3. course_kb.source_province 是 String(32)，题库 province 是长说明文本，会截断报错。
     -> 压缩为 河南 / 新高考I卷通用。

用法：
    python export_for_import.py                      # 导出数学（默认）
    python export_for_import.py --subject 物理        # 物理暂缓，预留开关
输出：
    真题素材/bank_数学_2021-2026_细标.json
    真题素材/kp_id_to_name.csv
"""
import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_steps import build_steps, steps_stat  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
DAG = os.path.join(os.path.dirname(BASE), '知识点本体', 'knowledge_dag_高中数学_人教A版_草案.json')

MATH_BANKS = [
    'newgaokao1/2021新高考1_题干答案_干净.json',
    'newgaokao1/2022新高考1_题干答案_干净.json',
    'newgaokao1/2023新高考1_题干答案_干净.json',
    'newgaokao1/2024新高考1_题干答案_干净.json',
    'newgaokao1/2025全国1_含河南_题干答案_干净.json',
    'newgaokao1/2026全国1_含河南_题干答案_干净.json',
]
PHYSICS_BANKS = [
    'gaokaophysics/2021全国乙卷物理_含河南_题干答案_干净.json',
    'gaokaophysics/2022全国乙卷物理_含河南_题干答案_干净.json',
    'gaokaophysics/2023全国乙卷物理_含河南_题干答案_干净.json',
    'gaokaophysics/2024新高考物理_含河南_题干答案_干净.json',
    'gaokaophysics/2025河南物理_含河南_题干答案_干净.json',
    'gaokaophysics/2026河南物理_含河南_题干答案_干净.json',
]

# 题库 type -> import_bank._norm_type 能识别的中文题型
TYPE_CN = {
    'single': '单选', 'multi': '多选', 'choice': '单选',
    'fill': '填空', 'blank': '填空',
    'solve': '解答', 'essay': '解答', 'experiment': '解答',
    '解答': '解答', '单选': '单选', '多选': '多选', '填空': '填空',
}


def load_dag_names():
    d = json.load(open(DAG, encoding='utf-8'))
    return {n['id']: n for n in d['nodes']}


def short_province(rec):
    """压缩来源省份到 String(32) 以内。"""
    p = str(rec.get('province', ''))
    year = rec.get('year', 0)
    if '河南' in p and year >= 2025:
        return '河南'
    if '河南' in p:
        # 长说明文本（如"新高考I卷适用省份（河南2025年起加入…）"）
        return '新高考I卷通用'
    return p[:28] or '新高考I卷通用'


def build(banks, subject):
    dag = load_dag_names() if subject == '数学' else {}
    out = []
    for fn in banks:
        path = os.path.join(BASE, fn)
        if not os.path.exists(path):
            print(f'[skip] 缺少 {fn}')
            continue
        recs = json.load(open(path, encoding='utf-8'))
        for r in recs:
            kps = []
            if r.get('kp_primary'):
                kps.append(r['kp_primary']['id'])
            for s in (r.get('kp_secondary') or []):
                if s['id'] not in kps:
                    kps.append(s['id'])
            out.append({
                '学科': subject,
                '年级': '高三',
                '章节': r.get('kp_chapter') or r.get('knowledge_point', ''),
                '题型': TYPE_CN.get(str(r.get('type', '')).strip(), '解答'),
                '题干': r.get('stem', '').strip(),
                '选项': r.get('options') if isinstance(r.get('options'), list) else None,
                '答案': str(r.get('answer', '')).strip(),
                '解析': str(r.get('analysis', '')).strip(),
                # 数据库 difficulty 列为 text，必须转字符串否则 asyncpg 报 expected str, got int
                '难度': str(r.get('difficulty') or 3),
                '卷别': r.get('paper') or '',
                '来源省份': short_province(r),
                '知识点': kps,
                # 全题型步骤（步骤级归因基础）：选项/空/小问/解析编号/兜底 single
                '步骤': build_steps(r, dag, subject),
                # 以下为追溯字段，import_bank 不映射，仅存档
                '年份': r.get('year'),
                '题号': r.get('no'),
                '置信': r.get('kp_confidence'),
            })
    return out, dag


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--subject', default='数学', choices=['数学', '物理'])
    args = ap.parse_args()

    banks = MATH_BANKS if args.subject == '数学' else PHYSICS_BANKS
    rows, dag = build(banks, args.subject)

    out_json = os.path.join(BASE, f'bank_{args.subject}_2021-2026_细标.json')
    json.dump(rows, open(out_json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    # id -> name 映射（灌库后 knowledge_points 里是 id，用它解读）
    if dag:
        csv_path = os.path.join(BASE, 'kp_id_to_name.csv')
        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(['id', 'name', 'chapter', 'section'])
            for n in dag.values():
                w.writerow([n['id'], n['name'], n['chapter'], n['section']])

    # 自检
    from collections import Counter
    tc = Counter(r['题型'] for r in rows)
    print(f'导出 {len(rows)} 题 -> {out_json}')
    print('题型分布:', dict(tc))
    print('无知识点题数:', sum(1 for r in rows if not r['知识点']))
    print('知识点 id 总数:', sum(len(r['知识点']) for r in rows))
    bad = [r['知识点'] for r in rows for k in r['知识点']
           if any(ch in k for ch in '；;、/|，,')]
    print('含分隔符(会被切碎)的知识点:', len(bad), bad[:3] if bad else '')
    if dag:
        miss = [k for r in rows for k in r['知识点'] if k not in dag]
        print('不在 DAG 中的 id:', len(miss), miss[:3] if miss else '')

    # 步骤自检（全题型覆盖 + 兜底）
    all_steps = [s for r in rows for s in r['步骤']]
    no_step = [r for r in rows if not r['步骤']]
    print('步骤总数:', len(all_steps), '| 按 kind:', steps_stat(all_steps))
    print('无步骤题数(必须为0):', len(no_step))
    manual = sum(1 for s in all_steps if s['kp_source'] == 'manual')
    print('manual 细标步骤数:', manual)
    for t in ('单选', '多选', '填空', '解答'):
        ts = [r['步骤'] for r in rows if r['题型'] == t]
        if ts:
            lens = [len(x) for x in ts]
            print(f'  {t}: {len(ts)} 题, 每题步数 min={min(lens)} max={max(lens)}')


if __name__ == '__main__':
    main()
