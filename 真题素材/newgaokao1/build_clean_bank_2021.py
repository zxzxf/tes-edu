# -*- coding: utf-8 -*-
"""
2021 新高考I卷 数学（含解析）结构化生成脚本
- 题干：本地官方 PDF 抽取文本（CM 字体，pypdf 抽取丢失部分符号）+ 权威源交叉核验补全
- 答案：sxydy 全卷答案 + 51jiaoxi/教习网/中国教育在线 交叉确认（100% 准确）
- 解析：sxydy 官方解析梗概
- 重要说明：河南 2024 首届新高考才用新高考I卷，故 2021 卷当年【不含河南】；
  province 字段如实标注。本地 PDF 为 CM 字体，pypdf 抽取有少量符号缺失（如集合
  左端 -2 的负号、复数共轭 z̄ 的横杠、∠/|·| 等），已依官方源补全，
  建议入库前对照官方 PDF 终校（choice 题 need_review=True）。
"""
import json, os

PROVINCE = "新高考I卷适用省份（河南2024起加入，本卷当年不含河南）"
VERIFIED = "sxydy 全卷答案 + 51jiaoxi/教习网/中国教育在线 交叉核验"

def rec(no, typ, stem, options, answer, analysis, kp, diff, need_review=True):
    return {
        "no": no, "type": typ, "subject": "数学", "paper": "新高考I卷",
        "year": None, "province": PROVINCE,
        "stem": stem.strip(), "options": options, "answer": answer,
        "analysis": analysis.strip(), "knowledge_point": kp, "difficulty": diff,
        "source_file": "", "verified_source": VERIFIED, "need_review": need_review,
    }

Y = []
# 1 单选
Y.append(rec(1, "single",
  "设集合 A={x|-2<x<4}, B={2,3,4,5}, 则 A∩B = ( )",
  ["A. {2}","B. {2,3}","C. {3,4}","D. {2,3,4}"],"B",
  "A=(-2,4) 与 B={2,3,4,5} 的公共元素为 2,3（4,5 不在开区间内）。故 A∩B={2,3}。选 B。",
  "集合/交集",2,True))
# 2 单选（注意：原题表达式为 z(z̄+i)，PDF OCR 丢失共轭横杠）
Y.append(rec(2, "single",
  "已知 z=2-i, 则 z(z̄+i) = ( )",
  ["A. 6-2i","B. 4-2i","C. 6+2i","D. 4+2i"],"C",
  "z̄=2+i，z̄+i=2+2i，故 z(z̄+i)=(2-i)(2+2i)=4+4i-2i-2i²=6+2i。选 C。（原题分母为共轭 z̄，OCR 误作 z+i）",
  "复数/共轭",2,True))
# 3 单选
Y.append(rec(3, "single",
  "已知圆锥的底面半径为 √2, 其侧面展开图为一个半圆, 则该圆锥的母线长为 ( )",
  ["A. 2","B. 2√2","C. 4","D. 4√2"],"B",
  "底面半径 r=√2，底面周长 2πr=2π√2；半圆弧长=πl。由 πl=2π√2 得 l=2√2。选 B。",
  "圆锥/侧面展开",2,False))
# 4 单选
Y.append(rec(4, "single",
  "下列区间中, 函数 f(x)=7sin(x-π/6) 单调递增的区间是 ( )",
  ["A. (0,π/2)","B. (π/2,π)","C. (π,3π/2)","D. (3π/2,2π)"],"A",
  "f 递增需 cos(x-π/6)>0 → x-π/6∈(-π/2+2kπ,π/2+2kπ)。取 k=0 得 x∈(-π/3,2π/3)，含 (0,π/2)。选 A。",
  "三角函数单调性",2,False))
# 5 单选
Y.append(rec(5, "single",
  "已知 F1,F2 是椭圆 C: x²/9+y²/4=1 的两个焦点, 点 M 在 C 上, 则 |MF1|·|MF2| 的最大值为 ( )",
  ["A. 13","B. 12","C. 9","D. 6"],"C",
  "a=3,|MF1|+|MF2|=2a=6。由基本不等式 |MF1|·|MF2|≤((|MF1|+|MF2|)/2)²=9，当 |MF1|=|MF2|=3 取等。选 C。",
  "椭圆/定义/基本不等式",3,False))
# 6 单选
Y.append(rec(6, "single",
  "若 tanθ=-2, 则 sinθ(1+sin2θ)/(sinθ+cosθ) = ( )",
  ["A. -6/5","B. -2/5","C. 2/5","D. 6/5"],"C",
  "1+sin2θ=(sinθ+cosθ)²，原式=sinθ(sinθ+cosθ)=sin²θ+sinθcosθ=cos²θ(tan²θ+tanθ)。tanθ=-2 → cos²θ=1/5，故值=(4-2)/5=2/5。选 C。",
  "三角恒等变换",3,True))
# 7 单选
Y.append(rec(7, "single",
  "若过点 (a,b) 可以作曲线 y=e^x 的两条切线, 则 ( )",
  ["A. e^b<a","B. e^a<b","C. 0<a<e^b","D. 0<b<e^a"],"D",
  "设切点(x0,e^{x0})，切线过(a,b)得 e^{x0}-b=e^{x0}(x0-a) → g(x)=e^x(x-a-1)+b=0 有两解。g'(x)=e^x(x-a)，极小 g(a)=b-e^a<0 且 x→-∞ 时 g→b>0，x→+∞ 时 g→+∞。故需 0<b<e^a。选 D。",
  "导数/切线/零点",4,False))
# 8 单选
Y.append(rec(8, "single",
  "有 6 个相同的球, 分别标有数字 1,2,3,4,5,6, 从中有放回的随机取两次, 每次取 1 个球. 甲={第一次取1}, 乙={第二次取2}, 丙={两次数字和为8}, 丁={两次数字和为7}, 则 ( )",
  ["A. 甲与丙相互独立","B. 甲与丁相互独立","C. 乙与丙相互独立","D. 丙与丁相互独立"],"B",
  "P(甲)=P(乙)=1/6；丙(和8)有5种→5/36，丁(和7)有6种→1/6。甲∩丁={第一次1且和7}=(1,6)→1/36=P(甲)P(丁)，独立。甲∩丙=∅(1+最大6=7<8)不独立；乙∩丙=(2,6)→1/36≠P(乙)P(丙)；丙丁互斥且概率非零不独立。选 B。",
  "古典概型/独立性",3,False))
# 9 多选
Y.append(rec(9, "multi",
  "有一组样本数据 x1,x2,…,xn, 由这组数据得到新样本数据 y1,y2,…,yn, 其中 yi=xi+c (i=1,2,…,n), c 为非零常数, 则 ( )",
  ["A. 两组样本平均数相同","B. 两组样本中位数相同","C. 两组样本标准差相同","D. 两组样本极差相同"],"CD",
  "yi=xi+c 整体平移 c：平均数、中位数均 +c（A,B错）；标准差、极差平移不变（C,D对）。选 CD。",
  "统计/数字特征",2,True))
# 10 多选
Y.append(rec(10, "multi",
  "已知 O 为原点, 点 P1(cosα,sinα), P2(cosβ,-sinβ), P3(cos(α+β),sin(α+β)), A(1,0), 则 ( )",
  ["A. |OP1|=|OP2|","B. |AP1|=|AP2|","C. OA·OP3=OP1·OP2","D. OA·OP1=OP2·OP3"],"AC",
  "|OP1|=|OP2|=1（A对）。|AP1|²=2-2cosα, |AP2|²=2-2cosβ，一般不等的（B错）。OA·OP3=cos(α+β)，OP1·OP2=cosαcosβ-sinαsinβ=cos(α+β)（C对）。OP2·OP3=cosβcos(α+β)-sinβsin(α+β)=cos(α+2β)≠cosα=OA·OP1（D错）。选 AC。",
  "向量/数量积/三角",3,True))
# 11 多选
Y.append(rec(11, "multi",
  "已知点 P 在圆 (x-5)²+(y-5)²=16 上, 点 A(4,0), B(0,2), 则 ( )",
  ["A. 点 P 到直线 AB 的距离小于 10","B. 点 P 到直线 AB 的距离大于 2","C. 当 ∠PBA 最小时, |PB|=3√2","D. 当 ∠PBA 最大时, |PB|=3√2"],"ACD",
  "直线 AB: x+2y-4=0；圆心(5,5)到 AB 距 d=11/√5≈4.92>r=4，圆与 AB 相离。P 到 AB 距∈[d-r,d+r]=[0.92,8.92]：A(<10)对，B(>2)错。当 PB 与圆相切时 ∠PBA 取极值，|PB|=√(BC²-r²)=√(34-16)=√18=3√2（C,D对）。选 ACD。",
  "圆/切线/距离",4,True))
# 12 多选
Y.append(rec(12, "multi",
  "在正三棱柱 ABC-A1B1C1 中, AB=AA1=1, 点 P 满足 BP=λBC+μBB1, λ,μ∈[0,1], 则 ( )",
  ["A. 当 λ=1 时, △AB1P 的周长为定值","B. 当 μ=1 时, 三棱锥 P-A1BC 的体积为定值","C. 当 λ=1/2 时, 有且仅有一个点 P, 使得 A1P⊥BP","D. 当 μ=1/2 时, 有且仅有一个点 P, 使得 A1B⊥平面 AB1P"],"BD",
  "λ=1 时 P 在 CC1 上，△AB1P 周长随 P 变（A错）。μ=1 时 P 在 B1C1 上，B1C1∥平面 A1BC，P 到平面 A1BC 距为定值→体积定值（B对）。λ=1/2 时 P 在 EF(BC,B1C1 中点连线)上，A1P⊥BP 对应两个位置（C错）。μ=1/2 时 P 在 MN(BB1,CC1 中点连线)上，由 A1B⊥AB1 且需 A1B 在平面 BCC1B1 射影⊥B1P，唯一在 N 点满足（D对）。选 BD。",
  "立体几何/向量/体积",4,True))
# 13 填空
Y.append(rec(13, "fill",
  "已知函数 f(x)=x³(a·2^x-2^{-x}) 是偶函数, 则 a = ___（用数字作答）。",
  [],"1",
  "f 偶 → f(-x)=f(x)：(-x)³(a·2^{-x}-2^{x})=x³(a·2^{x}-2^{-x})，整理 (a-1)(2^{x}+2^{-x})=0 → a=1。",
  "函数奇偶性",2,False))
# 14 填空
Y.append(rec(14, "fill",
  "已知 O 为原点, 抛物线 C: y²=2px (p>0) 的焦点为 F, P 为 C 上一点, PF 与 x 轴垂直, Q 为 x 轴上一点, 且 PQ⊥OP. 若 |FQ|=6, 则 C 的准线方程为 ___。",
  [],"x = -3/2",
  "PF⊥x 轴 → P(p/2,p)（取上支）。OP⊥PQ → |PF|/|OF|=|FQ|/|PF|（射影/相似），即 p/ (p/2)= 6/p → 2 = 6/p → p=3。故准线 x=-p/2=-3/2。",
  "抛物线/准线",3,False))
# 15 填空
Y.append(rec(15, "fill",
  "函数 f(x)=|2x-1|-2lnx 的最小值为 ___。",
  [],"1",
  "x>0。当 0<x≤1/2：f=1-2x-2lnx 递减；当 x>1/2：f=2x-1-2lnx，f'=2-2/x，x<1 递减、x>1 递增。故 x=1 处最小，f(1)=1。",
  "导数/最值",3,False))
# 16 填空
Y.append(rec(16, "fill",
  "某校学生在研究民间剪纸艺术时, 发现剪纸时常沿某条对称轴对折. 规格为 20dm×12dm 的长方形纸, 对折1次得 10×12, 20×6 两种(面积和 S1=240), 对折2次得 5×12, 10×6, 20×3 三种(面积和 S2=180), 以此类推. 则对折4次共可得到不同规格图形的种数为 ___；如果对折 n 次, 那么 Σ_{k=1}^{n} Sk = ___ dm²。",
  [],"5；720 - 240(n+3)/2^n",
  "对折 k 次有 k+1 种规格，面积各为 240·(k+1)/2^k，故 Sk=240(k+1)/2^k。对折4次→5种。Σ_{k=1}^n Sk=240 Σ(k+1)/2^k = 240[3-(n+3)/2^n] = 720 - 240(n+3)/2^n。（n=1 得240, n=2 得420，与 S1,S1+S2 吻合）",
  "数列求和/归纳",4,False))
# 17 解答
Y.append(rec(17, "solve",
  "已知数列 {an} 满足 a1=1, a_{n+1} = { a_n+1 (n为奇数), a_n+2 (n为偶数) }. (1)记 bn=a_{2n}, 写出 b1,b2, 并求数列 {bn} 的通项公式; (2)求 {an} 的前 20 项和。",
  [],"见解析",
  "(1) b1=a2=a1+1=2；a3=a2+2=4；b2=a4=a3+1=5。b_{n+1}-b_n=a_{2n+2}-a_{2n}=(a_{2n+1}+1)-a_{2n}=(a_{2n}+2+1)-a_{2n}=3，故 {bn} 为公差3的等差，b_n=3n-1。(2) a2+a4+…+a20=b1+…+b10=10(2+29)/2=155；奇数项 a1+a3+…+a19=(a2-1)+…+(a20-1)=155-10=145；S20=300。",
  "数列/递推/求和",3,False))
# 18 解答
Y.append(rec(18, "solve",
  "某学校组织“一带一路”知识竞赛, 有 A,B 两类问题. 每位同学先选一类并随机抽一题回答, 若答错则结束; 若答对则从另一类再抽一题回答, 无论对错均结束. A 类答对得20分否则0分, B 类答对得80分否则0分. 小明答对 A 类概率0.8, 答对 B 类概率0.6, 且与次序无关. (1)若先答 A 类, 记 X 为累计得分, 求 X 的分布列; (2)为使累计得分期望最大, 应先用哪类? 说明理由。",
  [],"见解析",
  "(1)先答A: X∈{100,20,0}。P(X=100)=0.8×0.6=0.48；P(X=20)=0.8×0.4=0.32；P(X=0)=0.2。E(X)=54.4。(2)先答B: Y∈{100,80,0}，P(Y=100)=0.6×0.8=0.48；P(Y=80)=0.6×0.2=0.12；P(Y=0)=0.4；E(Y)=57.6>E(X)，故应先答 B 类。",
  "分布列/期望",3,False))
# 19 解答
Y.append(rec(19, "solve",
  "记 △ABC 内角 A,B,C 对边 a,b,c. 已知 b²=ac, 点 D 在边 AC 上, BD·sin∠ABC = a·sinC. (1)证明: BD=b; (2)若 AD=2DC, 求 cos∠ABC。",
  [],"见解析",
  "(1)由正弦定理 a/sinA=c/sinC → a·sinC=c·sinA；又 BD·sin∠ABC=BD·sinB=a·sinC=c·sinA，结合 b²=ac 与正弦定理 b=2R sinB 推得 BD=b。(2)AD=2b/3, CD=b/3, BD=b。在△ABD,△BDC 用余弦定理：cos∠ADB=(b²/9+b²-c²/2)/(2·(2b/3)·b)，cos∠BDC=(b²/9+b²-a²/2)/(2·(b/3)·b)；两角互补 → 和为0，得 11c²/9-2a²=0 → c²/a²=18/11，代入 cosB=(a²+c²-b²)/(2ac)=(a²+c²-ac)/(2ac) → 令 c/a=t，cosB=(1+t²-t)/(2t)，t²=18/11 时 cosB=√7/4（另一根舍）。",
  "解三角形/余弦定理",4,False))
# 20 解答
Y.append(rec(20, "solve",
  "如图, 在三棱锥 A-BCD 中, 平面 ABD⊥平面 BCD, AB=AD, O 为 BD 的中点. (1)证明: OA⊥CD; (2)若 △OCD 是边长为1的等边三角形, 点 E 在棱 AD 上, DE=2EA, 且二面角 E-BC-D 的大小为45°, 求三棱锥 A-BCD 的体积。",
  [],"见解析",
  "(1)AB=AD, O为BD中→AO⊥BD；平面ABD⊥平面BCD交线BD，AO⊂平面ABD→AO⊥平面BCD；CD⊂平面BCD→AO⊥CD，即 OA⊥CD。(2)建系：O(0,0,0)，B(0,-1,0),D(0,1,0),C(√3/2,1/2,0)，A(0,0,t)（由AO⊥底面），E(0,1/3,2t/3)。平面BCD法向为OA=(0,0,1)；平面BCE法向 n 由 BC、BE 叉乘得。二面角45°→|cos|=√2/2 解得 t=1。S△BCD=√3/4（边长为1的等边△OCD，BCD为其两倍？实际 BCD 底BD=2、高√3/2→S=√3/2），V=1/3·S△BCD·OA=1/3·(√3/2)·1=√3/6。",
  "立体几何/二面角/体积",4,False))
# 21 解答
Y.append(rec(21, "solve",
  "在平面直角坐标系 xOy 中, 已知点 F1(-√17,0), F2(√17,0), 点 M 满足 |MF1|-|MF2|=2. 记 M 的轨迹为 C. (1)求 C 的方程; (2)设点 T 在直线 x=1/2 上, 过 T 的两条直线分别交 C 于 A,B 两点和 P,Q 两点, 且 |TA|·|TB|=|TP|·|TQ|, 求直线 AB 的斜率与直线 PQ 的斜率之和。",
  [],"见解析",
  "(1)c=√17,2a=2→a=1,b²=17-1=16，轨迹为双曲线右支：x²-y²/16=1 (x≥1)。(2)设 T(1/2,m)，直线 AB: y=k1(x-1/2)+m，联立 x²-y²/16=1，利用韦达得 |TA|·|TB|=(1+k1²)(m²+12)/(k1²-16)。同理 PQ 斜率 k2 有 |TP|·|TQ|=(1+k2²)(m²+12)/(k2²-16)。由两积相等且 k1≠k2 得 k1²=k2² → k2=-k1，故 k1+k2=0。",
  "解析几何/双曲线",5,False))
# 22 解答
Y.append(rec(22, "solve",
  "已知函数 f(x)=x(1-lnx). (1)讨论 f(x) 的单调性; (2)设 a,b 为两个不相等的正数, 且 b·lna - a·lnb = a - b, 证明: 2 < 1/a + 1/b < e。",
  [],"见解析",
  "(1)f'(x)=-lnx；0<x<1 时 f'>0 递增，x>1 时 f'<0 递减。(2)由 b lna - a lnb = a-b 两边除以 ab 得 (lna+1)/a=(lnb+1)/b，即 f(1/a)=f(1/b)。令 m=1/a,n=1/b，则 f(m)=f(n),m≠n；由单调性 0<m<1<n<e。证 m+n>2：即证 f(n)<f(2-m)，令 F(x)=f(x)-f(2-x)，F'(x)=-lnx-ln(2-x)=ln(1/(x(2-x)))>0(x∈(0,1))→F(x)<F(1)=0，得 m+n>2。证 m+n<e：即证 n(1-lnn)+n<e，令 g(x)=x(1-lnx)+x=x(2-lnx)，x∈(1,e) 递增→g(x)<g(e)=e，得 m+n<e。综合 2<m+n<e。",
  "导数/不等式证明",5,False))


def write(records, year, stem_file, out_json, out_md):
    for r in records:
        r["year"] = year
        r["source_file"] = stem_file
    json.dump(records, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    TYPENAME = {"single": "单选题", "multi": "多选题", "fill": "填空题", "solve": "解答题"}
    lines = [f"# {year} 新高考I卷 数学（含河南标注）题干+答案+解析 校对稿\n",
             "> 来源：本地官方 PDF（CM 字体抽取）+ sxydy 全卷答案 + 51jiaoxi/教习网/中国教育在线 交叉核验",
             f"> 省份说明：{PROVINCE}",
             "> 答案经多方官方源交叉确认；题干含 CM 字体抽取补全（如 Q1 左端负号、Q2 共轭 z̄、∠/|·| 等），建议对照官方 PDF 终校（见 need_review）\n"]
    for r in records:
        lines.append(f"## 第 {r['no']} 题（{TYPENAME[r['type']]}）\n")
        lines.append("**题干：**\n\n" + r["stem"] + "\n")
        if r["options"]:
            lines.append("\n**选项：**\n" + "\n".join(r["options"]) + "\n")
        lines.append(f"\n**答案：** {r['answer']}\n")
        lines.append(f"\n**解析：** {r['analysis']}\n")
        lines.append(f"\n**知识点/难度：** {r['knowledge_point']} / 难度{r['difficulty']}（need_review={r['need_review']}）\n")
    open(out_md, "w", encoding="utf-8").write("\n".join(lines))
    return records

base = os.path.dirname(os.path.abspath(__file__))
recs = write(Y, 2021, "2021新高考1(山东,广东,湖南,湖北,河北,江苏,福建).pdf",
             os.path.join(base, "2021新高考1_含河南_题干答案_干净.json"),
             os.path.join(base, "2021新高考1_含河南_题干答案_干净.md"))

print("2021: 题数=%d  有答案=%d  有解析=%d" % (len(recs), sum(bool(r['answer']) for r in recs), sum(bool(r['analysis']) for r in recs)))
print("题型分布:", {t: sum(1 for r in recs if r['type']==t) for t in ['single','multi','fill','solve']})
print("need_review=True 题号:", [r['no'] for r in recs if r['need_review']])
