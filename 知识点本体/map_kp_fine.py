# -*- coding: utf-8 -*-
"""
把数学真题题库的「章节级粗标」映射为 435 点知识点 DAG 的细粒度知识点。

口径（默认值，可由教研调整）：
  1. 每道题输出 1 个 kp_primary（主知识点）+ 0~3 个 kp_secondary（关联知识点）
  2. 解答题（solve/essay）按（1）（2）… 拆小问，每个小问单独标注 kp_primary
  3. 保留原 knowledge_point 字段不动（作为粗标追溯），新增 kp_* 系列字段
  4. 匹配质量分 high/mid/low；low 一律置 need_review=True，供教研终校

用法：python map_kp_fine.py            # 处理数学 6 份干净题库
"""
import json
import os
import re
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
DAG_PATH = os.path.join(BASE, 'knowledge_dag_高中数学_人教A版_草案.json')
BANK_DIR = os.path.join(os.path.dirname(BASE), '真题素材', 'newgaokao1')

# 数学 6 份干净题库
BANKS = [
    '2021新高考1_题干答案_干净.json',
    '2022新高考1_题干答案_干净.json',
    '2023新高考1_题干答案_干净.json',
    '2024新高考1_题干答案_干净.json',
    '2025全国1_含河南_题干答案_干净.json',
    '2026全国1_含河南_题干答案_干净.json',
]

# ---------------------------------------------------------------- 章节定位规则
# (章节名, [(特征词, 权重)]) —— 专用词权重高，泛词（如"函数"）权重低，避免导数题误判
CH_RULES = [
    ('复数', [('复数', 8), ('虚部', 8), ('实部', 8), ('共轭', 6), ('复平面', 6), ('纯虚数', 6)]),
    ('圆锥曲线的方程', [('椭圆', 8), ('双曲线', 8), ('抛物线', 8), ('离心率', 7), ('准线', 7),
                        ('渐近线', 7), ('焦点', 4), ('焦距', 4), ('圆锥曲线', 6), ('长轴', 5), ('短轴', 5)]),
    ('一元函数导数及其应用', [('导数', 8), ('求导', 8), ("f'", 6), ('切线', 4), ('极值', 5),
                              ('单调递增区间', 5), ('单调递减区间', 5), ('最值', 3), ('导函数', 7), ('极', 1)]),
    ('数列', [('数列', 8), ('等差数列', 8), ('等比数列', 8), ('通项', 6), ('前n项和', 6),
              ('前 $n$ 项和', 6), ('递推', 6), ('错位相减', 7), ('裂项', 7), ('公差', 5), ('公比', 5), ('S_n', 4), ('$S_', 3)]),
    ('随机变量及其分布', [('分布列', 8), ('期望', 6), ('随机变量', 7), ('二项分布', 8), ('正态分布', 8),
                          ('条件概率', 7), ('方差', 4), ('数学期望', 7)]),
    ('成对数据的统计分析', [('独立性检验', 9), ('列联表', 9), ('卡方', 8), ('$\\chi^2$', 8), ('chi^2', 8),
                            ('回归', 7), ('线性相关', 7), ('相关系数', 7), ('散点图', 5)]),
    ('计数原理', [('排列', 7), ('组合', 7), ('二项式', 8), ('展开式', 5), ('计数原理', 8), ('$A_', 4), ('$C_', 3)]),
    ('空间向量与立体几何', [('空间向量', 9), ('法向量', 8), ('二面角', 6), ('空间直角坐标', 8), ('线面角', 6)]),
    ('立体几何初步', [('棱柱', 7), ('棱锥', 7), ('棱台', 7), ('体积', 4), ('表面积', 5), ('侧面积', 6),
                      ('外接球', 7), ('线面平行', 6), ('线面垂直', 6), ('直观图', 7), ('几何体', 3)]),
    ('直线和圆的方程', [('圆的方程', 8), ('公切线', 8), ('直线与圆', 7), ('斜率', 6), ('切线', 5),
                        ('点到直线', 7), ('圆心', 5), ('半径', 3), ('$x^2+y^2$', 4)]),
    ('三角函数', [('三角函数', 8), ('正弦', 6), ('余弦', 6), ('正切', 6), ('$\\sin', 6), ('$\\cos', 6),
                  ('$\\tan', 6), ('诱导公式', 8), ('弧度', 7), ('$\\omega', 6), ('三角恒等变换', 8),
                  ('和角', 5), ('倍角', 5), ('振幅', 5), ('周期', 3)]),
    ('平面向量及其应用', [('平面向量', 8), ('数量积', 8), ('向量', 5), ('共线向量', 7), ('模长', 4)]),
    ('指数函数与对数函数', [('对数', 7), ('指数函数', 8), ('对数函数', 8), ('$\\log', 7), ('$\\ln', 7),
                            ('$e^', 5), ('指数', 5), ('换底', 7)]),
    ('概率', [('古典概型', 8), ('概率', 5), ('互斥', 6), ('对立事件', 6), ('相互独立', 5), ('随机事件', 6)]),
    ('统计', [('中位数', 7), ('平均数', 6), ('众数', 7), ('百分位数', 8), ('频率分布', 8), ('直方图', 7),
              ('标准差', 6), ('样本', 4), ('抽样', 6), ('统计', 4), ('用样本估计', 8)]),
    ('集合与常用逻辑用语', [('集合', 8), ('交集', 8), ('并集', 8), ('补集', 8), ('子集', 7), ('充分条件', 8),
                            ('必要条件', 8), ('充要', 8), ('全称量词', 8), ('存在量词', 8), ('命题', 4)]),
    ('一元二次函数、方程和不等式', [('基本不等式', 8), ('一元二次', 7), ('不等式', 4), ('$a+b\\ge', 6), ('均值不等式', 8)]),
    ('函数的概念与性质', [('奇偶', 6), ('单调性', 4), ('对称性', 5), ('零点', 4), ('分段函数', 7), ('幂函数', 8),
                          ('值域', 5), ('定义域', 5), ('抽象函数', 7), ('函数', 1)]),
]

# ---------------------------------------------------------------- 粗标优先映射表
# 结构: (粗标正则, 章节, 子规则[(子正则, [节点名关键词, 按优先级])], 兜底关键词)
# 顺序：特殊 → 一般（先匹配先赢）。粗标是人工核对过的强信号，优先级高于正文匹配。
COARSE_RULES = [
    (r'解三角形|正弦定理|余弦定理', '平面向量及其应用', [
        (r'余弦定理', ['余弦定理', '正弦定理']),
        (r'正弦定理', ['正弦定理', '余弦定理']),
    ], ['正弦定理', '余弦定理', '三角形面积公式']),
    (r'独立性检验|列联表|卡方|chi', '成对数据的统计分析', [
        (r'独立性检验', ['独立性检验的步骤', '卡方统计量', '2×2 列联表']),
    ], ['2×2 列联表', '卡方统计量', '独立性检验的步骤']),
    (r'回归|相关|散点', '成对数据的统计分析', [],
     ['一元线性回归模型', '样本相关系数', '经验回归方程']),
    (r'正态', '随机变量及其分布', [], ['正态分布的性质', '正态曲线', '3σ 原则']),
    (r'分布列|期望|随机变量', '随机变量及其分布', [
        (r'正态', ['正态分布的性质']),
        (r'分布列', ['分布列', '分布列的求解', '数学期望']),
        (r'期望', ['数学期望', '分布列', '期望与方差的性质']),
    ], ['离散型随机变量', '分布列', '数学期望']),
    (r'条件概率|全概率|贝叶斯', '随机变量及其分布', [], ['条件概率', '乘法公式', '全概率公式']),
    (r'计数原理|排列|组合|二项式', '计数原理', [
        (r'二项式', ['二项式定理', '二项展开式的通项', '系数和与赋值法']),
        (r'排列|组合', ['排列组合综合', '组合数公式', '排列数公式']),
    ], ['分类加法计数原理', '分步乘法计数原理', '排列组合综合']),
    (r'导数', '一元函数导数及其应用', [
        (r'公切线|切线', ['导数的几何意义', '导数与函数图像', '常见函数导数公式']),
        (r'不等式|证明|恒成立', ['利用导数证明不等式', '恒成立问题', '极值点偏移']),
        (r'零点', ['导数与函数零点个数', '零点问题', '导数与函数图像']),
        (r'极值|单调性与极值', ['导数与函数极值', '导数与函数单调性']),
        (r'最值', ['导数与函数最值', '导数在实际问题中的最值应用']),
        (r'单调', ['导数与函数单调性', '含参函数的单调性讨论']),
        (r'对称|中心', ['导数与函数图像', '导数与函数极值']),
    ], ['导数的概念', '导数与函数单调性', '导数的几何意义']),
    (r'数列|等差|等比|递推|求和', '数列', [
        (r'等比', ['等比数列的定义', '等比数列的通项公式', '等比数列的前 n 项和公式']),
        (r'等差', ['等差数列的定义', '等差数列的通项公式', '等差数列的前 n 项和公式']),
        (r'错位|裂项|求和', ['错位相减法', '裂项相消法', '分组求和']),
        (r'递推', ['数列递推求通项', '数列的递推公式', '数列的单调性']),
    ], ['数列的概念', '数列的通项公式', '数列的前 n 项和']),
    (r'椭圆', '圆锥曲线的方程', [
        (r'离心率', ['椭圆的离心率', '椭圆的几何性质']),
        (r'定义', ['椭圆的定义', '椭圆的标准方程']),
    ], ['椭圆的标准方程', '椭圆的几何性质', '椭圆的离心率']),
    (r'双曲线', '圆锥曲线的方程', [
        (r'离心率', ['双曲线的离心率', '渐近线']),
    ], ['双曲线的定义', '双曲线的标准方程', '双曲线的离心率']),
    (r'抛物线', '圆锥曲线的方程', [
        (r'准线', ['抛物线的定义', '抛物线的几何性质']),
        (r'焦点弦', ['焦点弦', '抛物线的焦点弦性质']),
    ], ['抛物线的标准方程', '抛物线的定义', '抛物线的几何性质']),
    (r'曲线与方程|轨迹|定点定值', '圆锥曲线的方程', [],
     ['轨迹方程的求法', '定点定值问题', '圆锥曲线综合(联立韦达)']),
    (r'直线与圆|圆与圆|公切线|圆的方程|圆心|切线|弦长|距离', '直线和圆的方程', [
        (r'公切线|圆与圆', ['圆与圆的位置关系', '切线问题']),
        (r'切线', ['切线问题', '直线与圆的位置判定', '点到直线的距离公式']),
        (r'距离', ['点到直线的距离公式', '两点间距离公式']),
    ], ['圆的标准方程', '直线与圆的位置判定', '切线问题']),
    (r'立体几何[-/·]?向量|向量[-/·]?立体几何|法向量|坐标法|空间向量', '空间向量与立体几何', [
        (r'二面角', ['用向量求二面角', '平面的法向量', '坐标法解立体几何综合题']),
        (r'线面角|线线角', ['用向量求线面角', '直线的方向向量']),
        (r'距离', ['用向量求空间距离']),
        (r'垂直', ['用向量证明线面垂直']),
        (r'平行', ['用向量证明线面平行']),
    ], ['坐标法解立体几何综合题', '平面的法向量', '用向量求二面角']),
    (r'立体几何|棱柱|棱锥|棱台|球|圆锥|圆柱|圆台|二面角|线面|体积|侧面积|表面积|展开', '立体几何初步', [
        (r'向量|坐标法', ['用向量求二面角', '平面的法向量', '坐标法解立体几何综合题']),
        (r'体积', ['锥体的体积', '柱体的体积', '台体的体积', '球的体积']),
        (r'侧面积|表面积|展开', ['锥体的表面积', '台体的表面积', '柱体的表面积']),
        (r'二面角', ['二面角', '直线与平面所成的角']),
        (r'线面', ['直线与平面平行的判定', '直线与平面垂直的判定', '平面与平面垂直的判定']),
    ], ['棱柱的结构特征', '直线与平面平行的判定', '二面角']),
    (r'空间向量|法向量', '空间向量与立体几何', [
        (r'二面角', ['用向量求二面角', '平面的法向量']),
        (r'线面角', ['用向量求线面角']),
        (r'距离', ['用向量求空间距离']),
    ], ['空间向量的坐标表示', '平面的法向量', '用向量证明线面平行']),
    (r'平面向量|数量积|向量', '平面向量及其应用', [
        (r'数量积|点积', ['向量的数量积(点积)', '向量的夹角', '向量的模(长度)']),
        (r'垂直', ['向量垂直的坐标表示']),
        (r'共线|平行', ['共线(平行)向量', '向量共线的坐标表示']),
        (r'坐标|正交分解', ['平面向量的坐标表示', '平面向量的正交分解']),
    ], ['向量的概念', '向量的数量积(点积)', '平面向量基本定理']),
    (r'三角', '三角函数', [
        (r'恒等变换|两角和|倍角|二倍角|和差', ['三角恒等变换的综合应用', '两角和与差的余弦', '二倍角公式(正弦/余弦/正切)']),
        (r'单调', ['三角函数的单调区间', '正弦函数的性质']),
        (r'最值|值域', ['三角函数的最值', '余弦函数的性质']),
        (r'零点|交点|图象', ['正弦函数的图像', '三角函数的周期性', '由图像求解析式']),
        (r'图象|ω|φ|变换', ['图像变换(平移)', '参数 ω 的求法', '由图像求解析式']),
        (r'诱导公式|同角', ['诱导公式一', '同角三角函数基本关系(平方关系)']),
    ], ['三角函数的周期性', '正弦函数的性质', '三角函数的最值']),
    (r'复数', '复数', [
        (r'共轭', ['共轭复数', '复数的模']),
        (r'四则运算|运算|乘法|除法', ['复数的乘法', '复数的除法', '复数的加法']),
        (r'虚部|实部', ['复数的实部与虚部', '复数的概念与代数形式']),
        (r'复平面|几何意义', ['复平面与复数几何意义', '复数的模']),
        (r'模', ['复数的模']),
    ], ['复数的概念与代数形式', '复数的乘法', '复数的实部与虚部']),
    (r'统计|中位数|百分位|数字特征|方差|标准差|频率分布|抽样', '统计', [
        (r'中位数|众数|平均数|数字特征', ['众数/中位数/平均数', '百分位数']),
        (r'百分位', ['百分位数']),
        (r'方差|标准差', ['方差与标准差']),
        (r'频率分布|直方图', ['频率分布直方图', '频率分布表']),
        (r'抽样', ['简单随机抽样', '分层随机抽样']),
    ], ['用样本估计总体', '众数/中位数/平均数', '方差与标准差']),
    (r'概率|古典概型|互斥|独立', '概率', [
        (r'古典概型', ['古典概型', '样本点与样本空间']),
        (r'独立', ['相互独立事件', '独立事件的概率乘法', '独立性的判定']),
        (r'互斥|对立', ['互斥事件', '对立事件']),
    ], ['古典概型', '随机事件', '相互独立事件']),
    (r'指数|对数|指对数', '指数函数与对数函数', [
        (r'换底', ['换底公式']),
        (r'对数函数|对数', ['对数的运算性质', '对数函数的图像与性质', '对数式的化简与求值']),
        (r'指数函数|指数', ['指数函数的性质', '指数函数的图像', '指数幂的运算性质']),
    ], ['对数的运算性质', '对数函数的图像与性质', '指数函数的性质']),
    (r'集合|交集|并集|补集|逻辑|量词|充要', '集合与常用逻辑用语', [
        (r'交集', ['交集', '集合运算的性质']),
        (r'并集', ['并集']),
        (r'补集', ['补集', '全集']),
        (r'子集', ['子集', '真子集']),
        (r'充要|充分|必要', ['充要条件', '充分必要条件的判定']),
        (r'量词', ['全称量词', '存在量词']),
    ], ['集合的概念', '交集', '并集']),
    (r'基本不等式|不等式', '一元二次函数、方程和不等式', [
        (r'基本不等式|均值', ['基本不等式 √(ab)≤(a+b)/2', '利用基本不等式求最值']),
        (r'一元二次', ['一元二次不等式的解法', '三个二次的关系']),
    ], ['不等式的基本性质', '基本不等式 √(ab)≤(a+b)/2']),
    (r'函数|奇偶|单调|抽象|新定义|零点|对称', '函数的概念与性质', [
        (r'抽象|新定义', ['函数的概念', '函数解析式的求法', '函数的图像']),
        (r'奇偶', ['奇偶性的定义', '奇偶性的判定', '单调性与奇偶性的综合']),
        (r'对称', ['函数图像的对称性', '奇偶性的定义']),
        (r'分段', ['分段函数', '分段函数模型']),
        (r'零点', ['函数的零点', '零点存在定理']),
        (r'单调', ['单调性的定义', '单调性的判定与证明']),
        (r'幂函数', ['幂函数的概念', '五个幂函数的图像与性质']),
    ], ['函数的概念', '函数的定义域', '函数的值域']),
]


def coarse_hit(coarse):
    """按粗标返回 (章节, 关键词列表, 是否命中子规则)。"""
    for pat, chapter, subs, default in COARSE_RULES:
        if re.search(pat, coarse):
            for sub_pat, kws in subs:
                if re.search(sub_pat, coarse):
                    return chapter, kws, True
            return chapter, default, False
    return None, None, False


def nodes_by_kws(chapter, kws, by_chapter, topk=4):
    """在指定章节内按关键词顺序取节点；返回 [(node, 命中kw)]。"""
    cands = by_chapter.get(chapter, [])
    out = []
    used = set()
    for kw in kws:
        for n in cands:
            if n['id'] in used:
                continue
            if kw in n['name'] or n['name'] in kw:
                out.append(n)
                used.add(n['id'])
                break
        if len(out) >= topk:
            break
    return out


# ---------------------------------------------------------------- 工具


def load_dag():
    d = json.load(open(DAG_PATH, encoding='utf-8'))
    nodes = d['nodes']
    by_chapter = defaultdict(list)
    for n in nodes:
        by_chapter[n['chapter']].append(n)
    return d, nodes, by_chapter


def normalize(t: str) -> str:
    """几何符号规范化，便于特征词命中（题干里垂直/平行多用符号表示）。"""
    return (t.replace('⊥', '垂直').replace('∥', '平行')
             .replace('$\\perp$', '垂直').replace('$\\parallel$', '平行'))


def qtext(rec):
    """拼接题目全文（题干+选项+答案+解析），粗标重复 2 次作为强先验。"""
    opts = rec.get('options')
    if isinstance(opts, list):
        opts = ' '.join(str(x) for x in opts)
    elif opts is None:
        opts = ''
    parts = [rec.get('stem', ''), str(opts), str(rec.get('answer', '')), str(rec.get('analysis', ''))]
    base = '\n'.join(p for p in parts if p)
    coarse = str(rec.get('knowledge_point', ''))
    return normalize(base) + '\n' + coarse + '\n' + coarse   # 粗标加权


def pick_chapter(text):
    scores = {}
    for ch, kws in CH_RULES:
        s = 0.0
        for kw, w in kws:
            c = text.count(kw)
            if c:
                s += w * min(c, 3)
        if s:
            scores[ch] = s
    if not scores:
        return None, 0.0, {}
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return ranked[0][0], ranked[0][1], dict(ranked[:3])


# 正文特征词：正文出现该词 且 节点名含该词 → 双向命中（比整名匹配更鲁棒）
KEY_FEATURES = [
    '对称', '周期', '奇偶', '单调', '最值', '极值', '零点', '平移', '伸缩', '图像', '图象',
    '定义域', '值域', '解析式', '恒成立', '切线', '离心率', '渐近线', '准线', '焦点', '焦点弦',
    '弦长', '通项', '递推', '求和', '错位', '裂项', '公差', '公比', '分布列', '期望', '方差',
    '标准差', '相关系数', '回归', '列联表', '卡方', '法向量', '二面角', '垂直', '平行', '体积',
    '表面积', '侧面积', '面积', '展开', '共轭', '虚部', '实部', '交集', '并集', '补集', '子集',
    '充要', '量词', '排列', '组合', '二项式', '抽样', '中位数', '百分位', '频率', '模', '夹角',
    '数量积', '共线', '基本不等式', '判别式', '韦达', '导数', '瞬时', '平均变化率',
]


def node_score_text(node, text):
    """节点与正文的相关度：整名命中 > 特征词双向命中 > 节名命中。"""
    nm = node['name']
    s = 0.0
    if len(nm) >= 2 and nm in text:
        s += len(nm) * 2.0
    else:
        for frag in (nm[:4], nm[-4:]) if len(nm) >= 4 else ():
            if frag in text:
                s += len(frag) * 1.2
                break
    for k in KEY_FEATURES:
        if k in text and k in nm:
            s += len(k) * 1.5
    sec = node.get('section', '')
    if sec and sec in text:
        s += len(sec) * 1.0
    if node.get('importance') == 'core':
        s += 0.5
    return s


def pick_nodes(text, chapter, by_chapter, topk=4):
    """章内节点精选：优先命中节点名（长名优先），其次命中节名。"""
    cands = by_chapter.get(chapter, [])
    scored = []
    for n in cands:
        s = node_score_text(n, text)
        if s > 0:
            scored.append((s, n))
    scored.sort(key=lambda x: (-x[0], x[1]['id']))
    if not scored:
        # 无命中：取该章第一个 core 节点兜底
        fallback = next((n for n in cands if n.get('importance') == 'core'), cands[0] if cands else None)
        return ([fallback] if fallback else []), 'low'
    primary_score = scored[0][0]
    picked = [n for s, n in scored[:topk]]
    if primary_score >= 6:
        conf = 'high'
    elif primary_score >= 3:
        conf = 'mid'
    else:
        conf = 'low'
    return picked, conf


def split_subq(stem):
    """按（1）（2）… 拆解答题小问；返回 [(idx, text)]，无小问则返回空列表。"""
    # 匹配 （1）(1) 1） 等，行首或句中
    pat = re.compile(r'[（(【]\s*([1-9１-９])\s*[）)】]')
    marks = list(pat.finditer(stem))
    # 过滤误判：如 (1+5i) 这类非小问编号（后面紧跟数字/字母而非中文或换行）
    real = []
    for m in marks:
        tail = stem[m.end():m.end() + 2]
        if re.match(r'^\d', tail):
            continue
        real.append(m)
    if len(real) < 2:
        return []
    out = []
    head = stem[:real[0].start()].strip()
    for i, m in enumerate(real):
        end = real[i + 1].start() if i + 1 < len(real) else len(stem)
        out.append((m.group(1), stem[m.start():end].strip()))
    return ([('0', head)] if head else []) + out


def fmt(node):
    return {'id': node['id'], 'name': node['name'], 'chapter': node['chapter'], 'section': node['section']}


def process_bank(fn, by_chapter):
    path = os.path.join(BANK_DIR, fn)
    recs = json.load(open(path, encoding='utf-8'))
    stat = defaultdict(int)
    for r in recs:
        text = qtext(r)
        coarse = str(r.get('knowledge_point', ''))
        ch_c, kws, strong = coarse_hit(coarse)
        if ch_c:
            # 粗标路径：章节由人工粗标确定，节点按关键词精选，再用正文证据重排
            picked = nodes_by_kws(ch_c, kws, by_chapter, topk=4)
            if not picked:
                picked, _ = pick_nodes(text, ch_c, by_chapter)
                conf = 'mid'
            else:
                conf = 'high' if strong else 'mid'
                # 粗标命中子规则时以关键词顺序为主；仅命中兜底关键词时让正文证据主导
                kw_w = 2.0 if strong else 0.5
                scored = [((len(picked) - i) * kw_w + node_score_text(n, text), n)
                          for i, n in enumerate(picked)]
                ids = {n['id'] for _, n in scored}
                # 用正文匹配补充关联知识点（同章）
                extra, _ = pick_nodes(text, ch_c, by_chapter, topk=6)
                for n in extra:
                    if n['id'] not in ids:
                        scored.append((node_score_text(n, text), n))
                        ids.add(n['id'])
                scored.sort(key=lambda x: -x[0])
                picked = [n for _, n in scored[:4]]
            ch, top3 = ch_c, {ch_c: 'coarse'}
        else:
            ch, chscore, top3 = pick_chapter(text)
            if ch is None:
                ch = '函数的概念与性质'      # 兜底章节
                conf = 'low'
                picked, _ = pick_nodes(text, ch, by_chapter)
            else:
                picked, conf = pick_nodes(text, ch, by_chapter)
        if not picked:
            r['kp_chapter'] = ch
            r['kp_primary'] = None
            r['kp_secondary'] = []
            r['kp_confidence'] = 'low'
            r['need_review'] = True
            stat['low'] += 1
            continue
        r['kp_chapter'] = ch
        r['kp_primary'] = fmt(picked[0])
        r['kp_secondary'] = [fmt(x) for x in picked[1:]]
        r['kp_confidence'] = conf
        r['kp_chapter_top3'] = top3
        # 解答题拆小问
        subs = []
        if r.get('type') in ('solve', 'essay', '解答'):
            raw_subs = [s for s in split_subq(r.get('stem', '')) if s[0] != '0']
            for si, (idx, txt) in enumerate(raw_subs):
                # 小问正文（强信号）；题级粗标仅作弱先验，不能压过小问自身证据
                text_only = normalize(txt)
                stext = text_only + '\n' + str(r.get('knowledge_point', ''))
                # 1a) 小问正文自身有信号 → 直接用（避免被粗标带偏）
                own_pick, _ = pick_nodes(text_only, ch, by_chapter, topk=1)
                s_own = node_score_text(own_pick[0], text_only) if own_pick else 0
                if s_own >= 3:
                    in_pick, s_in, use_ch = own_pick, s_own, ch
                else:
                    # 1b) 否则在题级章节内用（正文+粗标）匹配
                    in_pick, _ = pick_nodes(stext, ch, by_chapter, topk=1)
                    s_in = node_score_text(in_pick[0], stext) if in_pick else 0
                    use_ch = ch
                # 2) 仅当小问自身章节信号显著更强时才跨章
                sch, _, _ = pick_chapter(stext)
                if sch and sch != ch and s_own < 3:
                    alt_pick, _ = pick_nodes(stext, sch, by_chapter, topk=1)
                    s_alt = node_score_text(alt_pick[0], stext) if alt_pick else 0
                    if s_alt >= 6 and s_alt > s_in * 1.5:
                        use_ch = sch
                        in_pick = alt_pick
                        s_in = s_alt
                # 3) 正文无信号时，按粗标关键词顺序分配（高考解答题多由浅入深）
                if s_in < 3 and kws:
                    kw = kws[min(si, len(kws) - 1)]
                    seq = nodes_by_kws(ch, [kw], by_chapter, topk=1)
                    if seq:
                        in_pick = seq
                        sconf = 'mid'
                    else:
                        sconf = 'low'
                else:
                    sconf = 'high' if s_in >= 6 else ('mid' if s_in >= 3 else 'low')
                subs.append({
                    'sub_no': idx,
                    'text': txt[:400],
                    'kp_primary': fmt(in_pick[0]) if in_pick else None,
                    'confidence': sconf,
                })
        if subs:
            r['sub_questions'] = subs
        if conf == 'low':
            r['need_review'] = True
        stat[conf] += 1
    json.dump(recs, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    return recs, stat


def main():
    dag, nodes, by_chapter = load_dag()
    all_stat = defaultdict(int)
    total = 0
    report = []
    for fn in BANKS:
        recs, stat = process_bank(fn, by_chapter)
        total += len(recs)
        for k, v in stat.items():
            all_stat[k] += v
        nsub = sum(1 for r in recs if r.get('sub_questions'))
        report.append((fn, len(recs), dict(stat), nsub))
        print(f'{fn}: {len(recs)}题  {dict(stat)}  拆小问{nsub}题')
    print()
    print(f'合计 {total} 题  high={all_stat["high"]} mid={all_stat["mid"]} low={all_stat["low"]}')
    # 校对表
    md = ['# 数学题库细粒度知识点映射校对表', '',
          f'- 依据：`knowledge_dag_高中数学_人教A版_草案.json`（{len(nodes)} 点 / {len(dag["chapters"])} 章）',
          f'- 覆盖：数学 {len(BANKS)} 份干净题库，共 {total} 题',
          f'- 置信度：high={all_stat["high"]} / mid={all_stat["mid"]} / low={all_stat["low"]}（low 已置 need_review=true）',
          '']
    for fn, n, st, nsub in report:
        md.append(f'## {fn}（{n} 题，拆小问 {nsub} 题）')
        md.append('')
        md.append('| 题号 | 粗标 | 主知识点 | 关联知识点 | 置信 |')
        md.append('|---|---|---|---|---|')
        recs = json.load(open(os.path.join(BANK_DIR, fn), encoding='utf-8'))
        for r in recs:
            p = r.get('kp_primary') or {}
            sec = '、'.join(x['name'] for x in (r.get('kp_secondary') or [])) or '-'
            md.append(f"| {r['no']} | {r.get('knowledge_point','')} | {p.get('name','-')} | {sec} | {r.get('kp_confidence','-')} |")
        md.append('')
    out = os.path.join(BASE, '数学题库_细粒度知识点映射校对表.md')
    open(out, 'w', encoding='utf-8').write('\n'.join(md))
    print('校对表 ->', out)


if __name__ == '__main__':
    main()
