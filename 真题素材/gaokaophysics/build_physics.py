# -*- coding: utf-8 -*-
"""
物理真题 解析版 PDF -> 干净题库 JSON/MD 生成器
流程：本地 PDF 抽题干+选项+【详解】+答案(故选X) -> 含图题标注页码 -> 外网答案键核验(选择题) -> 输出
与数学卷同一套 schema：no,type,subject,paper,year,province,stem,options,answer,analysis,
                          knowledge_point,difficulty,source_file,verified_source,need_review,page
"""
import pypdf, re, json, os, sys

SRC_DIR = os.path.dirname(os.path.abspath(__file__))

# 各科章节粗标关键词（命中第一个即采用，优先级从上到下）
KP_RULES = [
    ('实验', ['实验', '频闪', '照相机', '电路图', '电阻箱', '电压表', '电流表', '游标', '螺旋测微', '光电门', '纸带']),
    ('原子物理', ['衰变', '放射性', '核素', '半衰期', '量子', '能级', '波粒', '光子', '原子核', '质子', '中子', '聚变', '裂变']),
    ('热学', ['气体', '压强', '水银', '热力学', '温度', '内能', 'p-V', '等温', '等压', '理想气体', '分子']),
    ('光学与近代物理', ['折射', '全反射', '干涉', '衍射', '光线', '波速', '波长', '振动', '简谐', '波形', '偏振', '临界角']),
    ('电磁学', ['电场', '电势', '等势面', '磁场', '洛伦兹', '安培', '电磁感应', '电动势', '电流', '电阻', '电容', '带电粒子', '匀强电场', '导体', '导体框', '磁通']),
    ('力学', ['动量', '机械能', '牛顿', '平抛', '圆周', '引力', '黑洞', '卫星', '弹簧', '摩擦力', '加速度', '动能', '势能', '冲量', '功', '斜面', '木板', '小球']),
]

FIG_KW = ['图', '如图', '下图', '示意图', '等势面', '波形', '电路', '磁场区域', '轨迹', '图像', '曲线', '纸带', '方格', 'p-V', 'v-t', 's-t']

CONFIGS = {
    2021: dict(file='2021全国乙理综(河南,山西,内蒙古,吉林,黑龙江,安徽,江西,陕西,甘肃,青海,宁夏,新疆).pdf',
               paper='2021全国乙卷物理', province='河南等(全国乙卷)', label='2021全国乙卷物理_含河南'),
    2022: dict(file='2022全国乙理综(河南,山西,内蒙古,吉林,黑龙江,安徽,江西,陕西,甘肃,青海,宁夏,新疆).pdf',
               paper='2022全国乙卷物理', province='河南等(全国乙卷)', label='2022全国乙卷物理_含河南'),
    2023: dict(file='2023全国乙理综(河南,内蒙古,江西,陕西,甘肃,青海,宁夏,新疆).pdf',
               paper='2023全国乙卷物理', province='河南等(全国乙卷)', label='2023全国乙卷物理_含河南'),
    2024: dict(file='2024新高考理综(河南,山西,云南,新疆).pdf',
               paper='2024新高考物理(新课标)', province='河南(新高考)', label='2024新高考物理_含河南'),
    2025: dict(file='2025河南.pdf',
               paper='2025河南物理', province='河南', label='2025河南物理_含河南'),
    2026: dict(file='2026河南.pdf',
               paper='2026河南物理', province='河南', label='2026河南物理_含河南'),
}

# 选择题单选/多选区间（按卷面说明；含图五选多选取自选考段，单独处理）
# single_range / multi_range 仅作类型兜底；答案含>1字母自动判多选
SINGLE_RANGE = range(1, 6)
MULTI_RANGE = range(6, 9)


def read_tokens(pdf_path):
    r = pypdf.PdfReader(pdf_path)
    tokens = []
    for pi, p in enumerate(r.pages):
        txt = p.extract_text() or ''
        for line in txt.split('\n'):
            s = line.rstrip()
            if not s.strip():
                continue
            if re.fullmatch(r'\s*\d+\s*/\s*\d+\s*', s):
                continue
            if re.fullmatch(r'第\s*\d+\s*页\s*共\s*\d+\s*页', s):
                continue
            if re.fullmatch(r'第\d+页', s):
                continue
            tokens.append((pi + 1, s))
    return tokens


def detect_section(line):
    """根据行内容判断所属板块，用于题号重置与标注。"""
    s = line
    if '选修' in s or '选考' in s:
        if '3-3' in s or '3－3' in s or '3−3' in s:
            return '选考3-3'
        if '3-4' in s or '3－4' in s or '3−4' in s:
            return '选考3-4'
        return '选考'
    if '非选择题' in s:
        return '非选择题'
    if '选择题' in s:
        if '不定项' in s or '多项' in s:
            return '多选题'
        if '单项' in s:
            return '单选题'
        return '选择题'
    # 一、/二、/三、 带章节
    m = re.match(r'^\s*[一二三四五六七八]', s)
    if m and ('选择' in s or '非选择' in s):
        return '非选择题' if '非选择' in s else '选择题'
    return None


def split_blocks(tokens):
    """检测题号起点（全角"．"编号 或 无编号"（N 分）"），按板块顺序生成
    全局唯一序号 no（1..N）；同时保留原始题号 exam_no 与板块 section。"""
    starts = []  # (idx, kind, value, section)
    cur_section = None
    for idx, (pg, line) in enumerate(tokens):
        sec = detect_section(line)
        if sec:
            cur_section = sec
            continue
        m = re.match(r'^\s*(\d{1,2})\s*[．]', line)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 40:
                starts.append((idx, 'num', n, cur_section))
                continue
        m2 = re.match(r'^\s*[（(]\s*\d+\s*分\s*[）)]', line)
        if m2:
            starts.append((idx, 'score', None, cur_section))
    # 全局顺序唯一序号；exam_no 保留原始编号（撞号时加板块后缀）
    seen = {}
    norm = []
    for i, (idx, kind, val, sec) in enumerate(starts):
        gno = i + 1
        if kind == 'num':
            exam_no = str(val)
        else:
            exam_no = f'#{gno}'
        key = (sec, exam_no)
        if key in seen:
            seen[key] += 1
            exam_no = f'{exam_no}-{seen[key]}'
        else:
            seen[key] = 1
        norm.append((idx, gno, exam_no, sec))
    blocks = []
    for bi, (idx, gno, exam_no, sec) in enumerate(norm):
        end = norm[bi + 1][0] if bi + 1 < len(norm) else len(tokens)
        blines = tokens[idx:end]
        page0 = blines[0][0]
        text = '\n'.join(l for _, l in blines)
        blocks.append({'no': gno, 'exam_no': exam_no, 'section': sec, 'page': page0, 'text': text})
    return blocks


def split_stem_analysis(text):
    """返回 (stem, analysis, answer_from_label)。
    兼容【详解】【解析】【解答】【答案】多种标记；答案优先取【答案】X。"""
    markers = ['【详解】', '【解析】', '【解答】', '【答案】']
    found = [(text.find(m), m) for m in markers if text.find(m) >= 0]
    if not found:
        return text.strip(), '', ''
    found.sort()
    first_pos, first_m = found[0]
    stem = text[:first_pos].strip()
    # 答案：优先【答案】X
    ans = ''
    ma = re.search(r'【答案】\s*([A-Za-z0-9，,、\s\-+./√²³¹⁰×÷]+)', text)
    if ma:
        ans = re.sub(r'\s+', '', ma.group(1))
    # 解析正文
    da = re.search(r'【详解】|【解析】|【解答】', text)
    if da:
        analysis = text[da.end():].strip()
    else:
        analysis = text[first_pos + len(first_m):].strip()
    return stem, analysis, ans


def extract_options(stem):
    pats = [r'[（(]([A-E])[）)]\s*', r'(?<![A-Za-z0-9])([A-E])[．.、]\s*']
    for pat in pats:
        ms = list(re.finditer(pat, stem))
        if len(ms) >= 2:
            opts = {}
            for i, m in enumerate(ms):
                start = m.end()
                end = ms[i + 1].start() if i + 1 < len(ms) else len(stem)
                opts[m.group(1)] = stem[start:end].strip()
            keys = list(opts.keys())
            if keys == ['A', 'B', 'C', 'D'] or keys == ['A', 'B', 'C', 'D', 'E']:
                return opts
    return {}


def extract_choice_answer(analysis):
    m = re.search(r'故选\s*([A-E][A-E\s]*?)[。.\n]', analysis)
    if m:
        return re.sub(r'\s+', '', m.group(1))
    return ''


def has_figure(stem):
    return any(k in stem for k in FIG_KW)


def coarse_kp(stem):
    for kp, kws in KP_RULES:
        if any(k in stem for k in kws):
            return kp
    return '未分类'


def classify_type(no, opts, answer):
    if opts:
        if len(answer) > 1:
            return 'multi'
        if no in SINGLE_RANGE:
            return 'single'
        if no in MULTI_RANGE:
            return 'multi'
        # 选考段五选多
        return 'multi' if len(opts) >= 5 or len(answer) > 1 else 'single'
    # 无文字选项：可能是图形式选择题，也可能是非选择题；按题号区间兜底
    if no in SINGLE_RANGE:
        return 'single'
    if no in MULTI_RANGE:
        return 'multi'
    return 'essay'


def extract_nonchoice_answer(analysis):
    """从解析末段抽取数值结论作为非选择题答案提示（需人工复核）。"""
    if not analysis:
        return ''
    # 取结论性表达式：单字母变量 = 数值(可带单位)
    pat = re.compile(r'(?<![A-Za-z0-9])([A-Za-zα-ωΔλμνρθφΩ]{1,3})\s*=\s*([0-9][0-9.\-×/±\s]{0,14}?\s*(?:[a-zA-Z℃°%μµΩJNs]{0,4}))')
    found = []
    for m in pat.finditer(analysis):
        val = (m.group(2) or '').strip()
        if not val:
            continue
        full = f'{m.group(1)}={val}'
        if full not in found:
            found.append(full)
    return ' ; '.join(found[-3:])  # 取末尾若干结论


def difficulty_of(t):
    return {'single': 2, 'multi': 3, 'fill': 3, 'essay': 4}.get(t, 3)


def build(pdf_path, cfg):
    tokens = read_tokens(pdf_path)
    blocks = split_blocks(tokens)
    records = []
    for b in blocks:
        no = b['no']
        stem, analysis, label_ans = split_stem_analysis(b['text'])
        # 任何带编号的块都保留（无标记的非选择题答案留待外网核验注入）
        if not stem or len(stem) < 8:
            continue
        opts = extract_options(stem)
        answer = extract_choice_answer(analysis) or label_ans
        # 非选择题：解析版已在【详解】给出解答，抽取末尾数值结论作答案提示
        if not answer and analysis:
            answer = extract_nonchoice_answer(analysis)
        fig = has_figure(stem)
        if fig:
            stem = stem.rstrip() + f'\n（图略，见 source PDF 第{b["page"]}页）'
        t = classify_type(no, opts, answer)
        rec = {
            'no': no,
            'exam_no': b['exam_no'],
            'section': b['section'],
            'type': t,
            'subject': '物理',
            'paper': cfg['paper'],
            'year': cfg['year'],
            'province': cfg['province'],
            'stem': stem,
            'options': opts if opts else None,
            'answer': answer,
            'analysis': analysis,
            'knowledge_point': coarse_kp(stem),
            'difficulty': difficulty_of(t),
            'source_file': os.path.basename(pdf_path),
            'verified_source': '本地解析版PDF(含详解)+外网核验(待)',
            'need_review': True,
            'page': b['page'],
        }
        records.append(rec)
    return records


def to_md(records, cfg):
    lines = [f'# {cfg["paper"]} 干净题库（含答案+解析）', '']
    lines.append(f'- 年份：{cfg["year"]}  地区：{cfg["province"]}')
    lines.append(f'- 总题数：{len(records)}')
    lines.append('')
    for r in records:
        opt = ''
        if r['options']:
            opt = '\n'.join(f'  {k}．{v}' for k, v in r['options'].items())
        lines.append(f'## 第{r["no"]}题  [{r["type"]} / {r["knowledge_point"]} / 难度{r["difficulty"]}]')
        lines.append('**题干：**')
        lines.append(r['stem'])
        if opt:
            lines.append('**选项：**')
            lines.append(opt)
        a = r['answer'] if r['answer'] else '（见解析）'
        lines.append(f'**答案：** {a}')
        lines.append('**解析：**')
        lines.append(r['analysis'])
        lines.append('')
    return '\n'.join(lines)


def main():
    years = [int(x) for x in sys.argv[1:]] if len(sys.argv) > 1 else list(CONFIGS.keys())
    for y in years:
        cfg = dict(CONFIGS[y]); cfg['year'] = y
        pdf = os.path.join(SRC_DIR, cfg['file'])
        recs = build(pdf, cfg)
        out_json = os.path.join(SRC_DIR, cfg['label'] + '_题干答案_干净.json')
        out_md = os.path.join(SRC_DIR, cfg['label'] + '_题干答案_干净.md')
        json.dump(recs, open(out_json, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        open(out_md, 'w', encoding='utf-8').write(to_md(recs, cfg))
        print(f'[{y}] {len(recs)} 题 -> {os.path.basename(out_json)}')


if __name__ == '__main__':
    main()
