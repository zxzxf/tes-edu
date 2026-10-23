# -*- coding: utf-8 -*-
"""
物理（理综）真题解析版 PDF -> 结构化题目切分
思路：
  1. pypdf 抽出全文本并保留 ===== PAGE n ===== 页标记
  2. 用「顺序题号匹配」切分题目（expected 递增），避免公式里的数字串被误判为小题号
  3. 每题以【详解】为界：前面=题干+选项，后面=解析
  4. 选择题答案从解析里正则取「故选 X / 故选 BC」
  5. 题干含「如图/图（a）/图像/示意」等关键词 -> 标注图在源 PDF 的页码
输出中间 JSON 供人工/脚本进一步清洗
"""
import re, json, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))

# 题目类型判定：按题号区间（各年份配置在 YEAR_CFG）
FIG_PAT = re.compile(r'如图|图（|图\(|下图|上图|图像|图象|示意图|照片|照片中|图中')


def split_questions(raw: str):
    """返回 [{no, page, body}]
    稳键做法：总题数 N = 【详解】出现次数；再按 1..N 顺序在文本中定位题号，
    保证不漏题（公式里的数字串不会干扰，因为要求行首且按序定位）。
    """
    n_q = raw.count('【详解】')
    # 按页标记 -> 每行页码
    page_of_line = []
    lines = raw.split('\n')
    cur_page = 1
    for ln in lines:
        m = re.match(r'=====\s*PAGE\s*(\d+)\s*=====', ln)
        if m:
            cur_page = int(m.group(1))
        page_of_line.append(cur_page)

    q_line_idx = []  # 每题起始行号（下标），按题号 1..N 顺序
    start_line = 0
    for k in range(1, n_q + 1):
        found = None
        for i in range(start_line, len(lines)):
            m = re.match(r'^\s*' + str(k) + r'\s*[．.、]', lines[i])
            if m:
                found = i
                break
        if found is None:
            # 找不到该题号（可能编号方式不同），占位延续上一题位置
            found = start_line
        q_line_idx.append(found)
        start_line = found + 1

    out = []
    for idx, li in enumerate(q_line_idx):
        end = q_line_idx[idx + 1] if idx + 1 < len(q_line_idx) else len(lines)
        body = '\n'.join(lines[li:end])
        out.append({'no': idx + 1, 'page': page_of_line[li], 'body': body})
    return out


def split_stem_analysis(body: str):
    """以【详解】为界拆题干/解析"""
    m = re.search(r'【详解】', body)
    if not m:
        return body.strip(), ''
    stem = body[:m.start()].strip()
    analysis = body[m.end():].strip()
    # 去掉解析里残留的下一题页眉噪声
    analysis = re.sub(r'=====\s*PAGE\s*\d+\s*=====', '', analysis).strip()
    return stem, analysis


def extract_options(stem: str):
    """抽取（A）（B）（C）（D）或 A. B. C. D. 选项"""
    opts = {}
    # 形如（A）...（B）...（C）...（D）...
    pat_full = re.compile(r'[（(]([A-D])[）)]\s*([^\n]*)')
    for m in pat_full.finditer(stem):
        opts.setdefault(m.group(1), m.group(2).strip())
    if len(opts) >= 4:
        return opts
    return opts if len(opts) >= 2 else {}


def extract_choice_answer(analysis: str):
    """从解析取 '故选 X' / '故选 BC'"""
    m = re.search(r'故选\s*([A-D]{1,4})', analysis)
    if m:
        return m.group(1)
    # 兜底：'故X正确' 组合判定
    rights = set(re.findall(r'([A-D])\s*(?:正确|对)', analysis))
    wrongs = set(re.findall(r'([A-D])\s*(?:错误|错)', analysis))
    if rights:
        cand = ''.join(sorted(rights - wrongs))
        if cand:
            return cand
    return None


def main():
    years = sys.argv[1:] or ['2021', '2022', '2023', '2024', '2025', '2026']
    result = {}
    for y in years:
        f = os.path.join(BASE, f'_raw_{y}.txt')
        if not os.path.exists(f):
            print('missing', f)
            continue
        raw = open(f, encoding='utf-8').read()
        qs = split_questions(raw)
        recs = []
        for q in qs:
            stem, analysis = split_stem_analysis(q['body'])
            opts = extract_options(stem)
            ans = extract_choice_answer(analysis)
            has_fig = bool(FIG_PAT.search(stem))
            # 去掉题干里的选项行，得到纯题干
            stem_pure = stem
            if opts:
                for k, v in opts.items():
                    stem_pure = stem_pure.replace(f'（{k}）{v}', '')
                stem_pure = re.sub(r'\n\s*\n', '\n', stem_pure).strip()
            recs.append({
                'no': q['no'],
                'page': q['page'],
                'stem': re.sub(r'\s+', ' ', stem_pure).strip(),
                'options': opts,
                'answer': ans,
                'analysis': re.sub(r'\s+', ' ', analysis).strip(),
                'has_figure': has_fig,
            })
        result[y] = recs
        print(f'--- {y}: {len(recs)} 题, 选择题答案: ' +
              ','.join(str(r['answer']) for r in recs if r['answer']))

    json.dump(result, open(os.path.join(BASE, '_parsed.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    print('\nwritten _parsed.json')


if __name__ == '__main__':
    main()
