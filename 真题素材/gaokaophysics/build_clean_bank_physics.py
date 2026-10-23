# -*- coding: utf-8 -*-
"""
物理（理综/新高考）真题 解析版 PDF -> 干净题库 JSON + 人眼校对 MD
复用 math 卷的字段规范：no/type/subject/paper/year/province/stem/options/
answer/analysis/knowledge_point/difficulty/source_file/verified_source/need_review
差异：物理题依赖图示 -> 追加 figure_note（标注"见源 PDF 第X页"）+ page 字段
处理流程：
  1. pypdf 抽全文（保留 PAGE 页标记）
  2. 总题数 N = 【详解】+【解析】 出现次数
  3. 从"选择题"章节标题之后开始按顺序定位题号 1..N（跳过卷首答题注意事项）
  4. 每题以解析标签为界拆：前=题干+选项，后=解析
  5. 选择题答案取解析内"故选 X"（一题多个则合并）
  6. 非选择题答案取解析末尾结论行，need_review=True
"""
import re, json, os, glob

BASE = os.path.dirname(os.path.abspath(__file__))
ANALYSIS_PAT = re.compile(r'【详解】|【解析】')

# 非选择题官方答案（源自 21cnjy / 教习网 / 官方答案版，外网核验）
NONCHOICE_ANS = {
    '2021': {
        9: '(1) 水平分量 1.0 m/s，竖直分量 2.0 m/s；(2) g = 9.7 m/s²',
        10: '(1) 15.0 Ω；(5) E = 1.54 V，r = 1.1 Ω；(6) 误差 5.19%',
        11: '(1) W = 4.5 J；(2) F = 9 N',
        12: '(1) F安 = 0.18 N；（2）（3）见解析（官方答案以公式给出）',
        15: 'v = 0.5 m/s，T = 0.4 s（沿 x 正方向）；沿 x 负方向传播时 T = 1.2 s',
    },
    '2022': {
        9: '① 相邻 1 s 内位移之差接近 Δx = 80 m；② 547；③ 79',
        11: '(2) 0.016 J',
        13: 'ABD',
        14: '见解析（选修3-3(2)：求弹簧劲度系数，及活塞Ⅱ到连接处时气体的压强与温度）',
        15: '① 4；② 加强；③ 向下',
        16: '1.5（棱镜折射率）',
    },
    '2023': {
        9: '① CD（或 DC）；② 相同位置；③ 大小和方向',
        10: '① (U₂−U₁)/R₀；② U₁R₀/(U₂−U₁)；③ 0.150；④ 5.0',
        11: '(1) q，A、B、C 均为正电荷；(2) (3−√3)/3·q',
        12: '(1) 小球速度 √(2gl)/2，圆盘速度 √(2gl)/2；(2) l；(3) 4',
        13: 'ACD',
        14: 'p_A = 74.36 cmHg，p_B = 54.36 cmHg',
        15: 'ABE',
        16: '(3−√3)/3·l',
    },
    '2024': {
        9: '(1) >；(2) ① m_a·x_P = m_a·x_M + m_b·x_N；② 小球离开斜槽末端后做平抛运动，竖直高度相同故下落时间相同',
        10: '(1) ① CAB；② 负极、正极；③ ×100；④ 1.6；(2) ① R₁；② a；(3) U R₀/(U₁−U)；(4) 1.57',
        11: '(1) 1200 N，900 N；(2) −4200 J',
        12: '(1) 4 m/s，1/3 s；(2) 5/9 m',
    },
    '2025': {
        11: '(1) 非线性；(2) a；(3) 130.0；(4) 210.0',
        12: '(1) ④①⑥⑤；(2) 1.79；(3) 通过、19.0；(4) 3.1',
        13: '(1) 5×10⁻³ m；(2) 0.11 m',
        14: '(1) 24.5 J；(2) 5 s',
        15: '(1) mv₀/(2qh)；(2) mv₀²/(2qh)；（3）见解析',
    },
    '2026': {
        11: '(1) 5.10 mm；(2) 0.50 m/s；(3) a = (v₂²−v₁²)/(2s)，a = gh/L',
        12: '(1) 见解析（实物连线）；(2) AD；(3) 10.0 Ω，对应 R₂；(4) E ≈ 3.55 V',
        13: '(1) 1.5 L；(2) 1:9',
        14: '(1) 见解析（证明射程与 q、m 无关）；(2) 比荷之比 9:4',
        15: '(1) 0.05 m；(2) 2 m/s，0.1 s；(3) √88 m/s',
    },
}
FIG_PAT = re.compile(r'如图|图（|图\(|下图|上图|图像|图象|示意图|照片|图中|图线')
SEC_PAT = re.compile(r'^\s*[一二三四五]、\s*.{0,20}(选择题|非选择题)')

# 章节级知识点粗标（关键词规则）
# 注意：按"专业性从强到弱"排列，力学关键词过于通用，放最后兜底
KP_RULES = [
    ('近代物理', ['原子', '核', '衰变', '半衰', '光电', '量子', '光子', '能级', '逸出功',
                 '放射性', '核素', '黑洞', '诺贝尔', '铍', '质谱仪']),
    ('光学', ['折射', '反射', '干涉', '衍射', '棱镜', '透镜', '全反射', '折射率', '玻璃砖', '光路']),
    ('热学', ['温度', '气体', '热力', '内能', '压强', '分子', '汽缸', '活塞', '理想气体',
             '比热', '玻意耳', '查理', '盖-吕萨克']),
    ('机械振动与波', ['振动', '简谐', '单摆', '周期', '频率', '波长', '波', '声波', '骨笛',
                   '振幅', '波源', '横波']),
    ('电磁学', ['电场', '磁场', '电势', '电荷', '电路', '电流', '电压', '电动势', '安培',
               '洛伦兹', '感应', '电阻', '电容', '变压器', '匝数', '等势面', '金属板',
               '导体', '逸出', '极板', '电子', '离子']),
    ('物理实验', ['实验', '测量', '频闪', '电阻箱', '电压表', '电流表', '游标', '螺旋测微',
                 '打点计时器', '电路图', '光电门', '气垫导轨', '传感器']),
    ('力学', ['牛顿', '动量', '动能', '功', '功率', '平抛', '圆周', '弹簧', '摩擦',
             '斜', '匀速', '加速', '自由落体', '碰撞', '守恒', '拉力', '滑块', '木板', '小车']),
]


def guess_kp(text):
    for name, kws in KP_RULES:
        for kw in kws:
            if kw in text:
                return name
    return '综合'


def page_map(lines):
    """每行 -> 所属页码"""
    out, cur = [], 1
    for ln in lines:
        m = re.match(r'=====\s*PAGE\s*(\d+)\s*=====', ln)
        if m:
            cur = int(m.group(1))
        out.append(cur)
    return out


def find_questions(raw, n_q):
    """从选择题章节之后，按顺序定位 1..n_q 题号"""
    lines = raw.split('\n')
    start_line = 0
    for i, ln in enumerate(lines):
        if SEC_PAT.search(ln):
            start_line = i
            break
    idx, st = [], start_line
    for k in range(1, n_q + 1):
        # 题号分隔符后不能紧跟数字，避免把 "6.63×10⁻³⁴" 之类常量误判为第6题
        pat = re.compile(r'^\s*' + str(k) + r'\s*(?:[．、]|\.(?!\d))')
        found = None
        for i in range(st, len(lines)):
            if pat.match(lines[i]):
                found = i
                break
        if found is None:
            found = st
        idx.append(found)
        st = found + 1
    return lines, idx


def extract_options(stem):
    opts = {}
    for m in re.finditer(r'[（(]([A-D])[）)]\s*([^\n（(]*)', stem):
        opts.setdefault(m.group(1), m.group(2).strip())
    return opts


def extract_result(analysis):
    """非选择题：抽取解析中的结论句（含'解得/可得/代入数据'），作为答案"""
    if not analysis:
        return ''
    sents = re.split(r'[。；\n]', analysis)
    hits = [s.strip() for s in sents
            if re.search(r'解得|可得|代入数据|因此|故有|联立.*?得', s) and len(s.strip()) > 3]
    if not hits:
        tail = [l for l in analysis.split('\n') if l.strip()]
        return tail[-1][:120] if tail else ''
    return '；'.join(hits[:4])[:200]


def clean(s):
    s = re.sub(r'=====\s*PAGE\s*\d+\s*=====', '', s)
    s = re.sub(r'第\s*\d+\s*页\s*共\s*\d+\s*页', '', s)
    s = re.sub(r'[ \t]+', ' ', s)
    s = re.sub(r'\n\s*\n+', '\n', s)
    return s.strip()


def build_year(y, prov, paper):
    raw = open(os.path.join(BASE, f'_raw_{y}.txt'), encoding='utf-8').read()
    # 2022 存在"【解析】【详解】"双标签（同一题），先折叠，避免题数虚增
    raw = re.sub(r'【解析】\s*【详解】', '【详解】', raw)
    n_q = len(ANALYSIS_PAT.findall(raw))
    lines, q_idx = find_questions(raw, n_q)
    pmap = page_map(lines)
    # "非选择题"章节起始行，用于题型判定
    nonchoice_line = len(lines)
    for i, ln in enumerate(lines):
        if re.match(r'^\s*[一二三四五]、\s*非选择题', ln):
            nonchoice_line = i
            break
    recs = []
    for j, li in enumerate(q_idx):
        end = q_idx[j + 1] if j + 1 < len(q_idx) else len(lines)
        body = '\n'.join(lines[li:end])
        am = ANALYSIS_PAT.search(body)
        if am:
            stem = clean(body[:am.start()])
            analysis = clean(body[am.end():])
        else:
            stem, analysis = clean(body), ''
        no = j + 1
        page = pmap[li]
        opts = extract_options(stem)
        # 选考题选项含 E（如 2023 第15题答案 ABE），故允许 A-E
        ans_list = re.findall(r'故选\s*([A-E]{1,5})', analysis)
        # 去掉题干里的选项行
        stem_pure = stem
        for k, v in opts.items():
            stem_pure = stem_pure.replace(f'（{k}）{v}', '')
        stem_pure = clean(stem_pure)
        # 去掉题干开头的题号
        stem_pure = re.sub(r'^\s*\d+\s*[．.、]\s*', '', stem_pure)

        # 位于"非选择题"章节之后的题，一律按非选择处理（即使内含"故选"小题）
        in_nonchoice = (li >= nonchoice_line)
        is_exp = bool(re.search(r'实验|测量|图（a）|图 1|电路|器材|读数', stem_pure))
        curated = NONCHOICE_ANS.get(y, {}).get(no)
        if curated:
            # 非选择题：优先采用外网核验过的官方答案
            answer = curated
            qtype = 'experiment' if is_exp else 'solve'
            need_review = False
        elif in_nonchoice:
            qtype = 'experiment' if is_exp else 'solve'
            answer = ''.join(ans_list) if ans_list else extract_result(analysis)
            need_review = True
        elif ans_list:
            answer = ''.join(ans_list)
            qtype = 'single' if len(answer) == 1 else 'multi'
            need_review = False
        else:
            answer = extract_result(analysis)
            qtype = 'experiment' if is_exp else 'solve'
            need_review = True

        fig = bool(FIG_PAT.search(stem_pure))
        fig_note = f'（图略，见源 PDF 第 {page} 页）' if fig else ''
        recs.append({
            'no': no,
            'type': qtype,
            'subject': '物理',
            'paper': paper,
            'year': int(y),
            'province': prov,
            'stem': stem_pure + (('  ' + fig_note) if fig else ''),
            'options': opts,
            'answer': answer,
            'analysis': analysis,
            'knowledge_point': guess_kp(stem_pure + ' ' + analysis),
            'difficulty': 3,
            'source_file': f'{y}源PDF',
            'page': page,
            'has_figure': fig,
            'verified_source': '本地解析版PDF【详解/解析】',
            'need_review': need_review,
        })
    return recs


def write_md(y, recs, path):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f'# {y} 物理真题（干净版）\n\n')
        f.write(f'共 {len(recs)} 题。来源：本地解析版 PDF；图示标注见源 PDF 页码。\n\n')
        for r in recs:
            tn = {'single': '单选', 'multi': '多选', 'experiment': '实验', 'solve': '解答'}[r['type']]
            f.write(f"## 第{r['no']}题（{tn}｜{r['knowledge_point']}｜第{r['page']}页）\n\n")
            f.write(f"**题干**：{r['stem']}\n\n")
            if r['options']:
                f.write('**选项**：' + '  '.join(f"{k}. {v}" for k, v in sorted(r['options'].items())) + '\n\n')
            f.write(f"**答案**：{r['answer']}\n\n")
            if r['analysis']:
                f.write(f"**解析**：{r['analysis'][:1500]}\n\n")
            if r['need_review']:
                f.write('> 待复核（非选择题答案为解析末行抽取）\n\n')
            f.write('---\n\n')


def main():
    # (年份, 适用省份, 卷别, 输出文件名前缀) —— 命名与数学卷保持一致
    YEARS = [
        ('2021', '河南、山西、内蒙古、吉林、黑龙江、安徽、江西、陕西、甘肃、青海、宁夏、新疆',
         '全国乙卷·理综物理', '2021全国乙卷物理'),
        ('2022', '河南、山西、内蒙古、吉林、黑龙江、安徽、江西、陕西、甘肃、青海、宁夏、新疆',
         '全国乙卷·理综物理', '2022全国乙卷物理'),
        ('2023', '河南、内蒙古、江西、陕西、甘肃、青海、宁夏、新疆',
         '全国乙卷·理综物理', '2023全国乙卷物理'),
        ('2024', '河南、山西、云南、新疆', '新课标卷·理综物理', '2024新高考物理'),
        ('2025', '河南', '河南卷·物理', '2025河南物理'),
        ('2026', '河南', '河南卷·物理', '2026河南物理'),
    ]
    for y, prov, paper, prefix in YEARS:
        recs = build_year(y, prov, paper)
        jp = os.path.join(BASE, f'{prefix}_含河南_题干答案_干净.json')
        mp = os.path.join(BASE, f'{prefix}_含河南_题干答案_干净.md')
        json.dump(recs, open(jp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        write_md(y, recs, mp)
        ch = [r for r in recs if r['type'] in ('single', 'multi')]
        print(f"{y}: 共{len(recs)}题 | 选择{len(ch)}题 答案="
              + ','.join(str(r['answer']) for r in ch)
              + f" | 非选择{len(recs)-len(ch)}题(待复核)")


if __name__ == '__main__':
    main()
