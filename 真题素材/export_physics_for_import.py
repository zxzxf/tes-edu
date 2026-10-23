"""物理真题（2021-2024）→ import_bank.py 可用的中文列名 JSON。

与数学 export_for_import.py 的区别（物理的实际情况）：
  1) 物理没有细粒度知识点 DAG（用户已决定物理降优先级），knowledge_point 是
     章节域粗标（力学/电磁学/热学/光学/近代物理/机械振动与波/物理实验/综合），
     中文无 `/` 分隔符，不会被 _split_kp 切碎，可原样存。
  2) 物理 JSON 自带 year 字段 -> 导出保留，供灌库后回填 course_kb.year。
  3) 题型多了 experiment（实验题）。
  4) province 是长串省份列表，必须压缩（course_kb.source_province 为 String(32)）。

已知坑（与数学一致，必须规避）：
  - _norm_type 只认中文题型，single/multi 原样传会被判成"解答" -> 预转中文。
  - difficulty 必须是字符串：course_kb_embeddings.difficulty 是 text 列。

产出：真题素材/bank_物理_2021-2024.json
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_steps import build_steps, steps_stat  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
PHYS_DIR = os.path.join(BASE, 'gaokaophysics')

# (文件名, 年份) —— 2021-2024 已入库；2025/2026 河南卷本次补灌。
# 灌库幂等（content_hash 去重），重跑整份文件只会新增缺的那两年。
PHYS_BANKS = [
    ('2021全国乙卷物理_含河南_题干答案_干净.json', 2021),
    ('2022全国乙卷物理_含河南_题干答案_干净.json', 2022),
    ('2023全国乙卷物理_含河南_题干答案_干净.json', 2023),
    ('2024新高考物理_含河南_题干答案_干净.json', 2024),
    ('2025河南物理_含河南_题干答案_干净.json', 2025),
    ('2026河南物理_含河南_题干答案_干净.json', 2026),
]

TYPE_CN = {
    'single': '单选',
    'multi': '多选',
    'fill': '填空',
    'experiment': '实验',
    'solve': '解答',
    'essay': '解答',
}

# source_province 为 String(32)，必须压缩
PROV_SHORT = {
    '全国乙卷·理综物理': '全国乙卷通用',
    '新课标卷·理综物理': '新课标卷通用',
    '河南卷·物理': '河南',
}


def to_options(opts):
    """物理的 options 是 dict{A:..,B:..}，数学是 list；统一成 dict 供 jsonb 存。"""
    if not opts:
        return None
    if isinstance(opts, dict):
        return {str(k): str(v) for k, v in opts.items()}
    return opts


def main():
    out = []
    stat = {}
    for fname, year in PHYS_BANKS:
        path = os.path.join(PHYS_DIR, fname)
        recs = json.load(open(path, encoding='utf-8'))
        for r in recs:
            t = TYPE_CN.get(str(r.get('type', '')).strip(), '解答')
            stat[t] = stat.get(t, 0) + 1
            kp = str(r.get('knowledge_point') or '').strip()
            out.append({
                '学科': '物理',
                '年级': '高三',
                '章节': kp,
                '题型': t,
                '题干': str(r.get('stem', '')).strip(),
                '选项': to_options(r.get('options')),
                '答案': str(r.get('answer', '')).strip(),
                '解析': str(r.get('analysis', '')).strip(),
                # 数据库 difficulty 列为 text，必须转字符串
                '难度': str(r.get('difficulty') or 3),
                '卷别': str(r.get('paper') or ''),
                '来源省份': PROV_SHORT.get(str(r.get('paper') or ''), '物理通用'),
                '知识点': [kp] if kp else [],
                # 全题型步骤（步骤级归因基础）：选项/空/解析编号小问/兜底 single
                '步骤': build_steps(r, None, '物理'),
                # 以下为追溯字段，import_bank 不映射，仅用于回填 year / 抽样核对
                '年份': year,
                '题号': r.get('no'),
                '待复核': r.get('need_review'),
            })

    path = os.path.join(BASE, 'bank_物理_2021-2026.json')
    json.dump(out, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('题目总数:', len(out))
    print('题型分布:', stat)
    print('年份分布:', {str(y): sum(1 for r in out if r['年份'] == y) for _, y in PHYS_BANKS})
    bad = [r['知识点'] for r in out if not r['知识点']]
    print('无知识点题数:', len(bad))
    sep = [k for r in out for k in r['知识点'] if '/' in k or '、' in k or '，' in k]
    print('含分隔符(会被切碎)的知识点:', sep if sep else 0)
    # 步骤自检（全题型覆盖 + 兜底）
    all_steps = [s for r in out for s in r['步骤']]
    no_step = [r for r in out if not r['步骤']]
    print('步骤总数:', len(all_steps), '| 按 kind:', steps_stat(all_steps))
    print('无步骤题数(必须为0):', len(no_step))
    for t in ('单选', '多选', '实验', '解答'):
        ts = [r['步骤'] for r in out if r['题型'] == t]
        if ts:
            lens = [len(x) for x in ts]
            print(f'  {t}: {len(ts)} 题, 每题步数 min={min(lens)} max={max(lens)}')
    print('输出:', path)


if __name__ == '__main__':
    main()
