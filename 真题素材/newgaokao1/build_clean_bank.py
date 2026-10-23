# -*- coding: utf-8 -*-
"""将 2024 新高考I卷(含河南) 真题整理为干净、可入库的结构化 JSON。
数据来源：21cnjy(docid 20613615) + docin + 51jiaoxi + 出卷网(zujuan) 交叉核验。
答案键经多重来源确认；题干/选项以权威网源为准，修正了 OCR 残字（如 Q1 的 x^3、Q5 的 √3、Q6 的符号、Q11 的 D 选项）。
"""
import json

SRC = "2024新高考1_含答案_gkcx.pdf（同源已内嵌答案）+ 21cnjy 精品解析交叉核验"

records = [
  {
    "no": 1, "type": "single", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "已知集合 A={x | -5 < x^3 < 5}，B={-3,-1,0,2,3}，则 A∩B=（ ）",
    "options": ["{-1,0}", "{2,3}", "{-3,-1,0}", "{-1,0,2}"],
    "answer": "A",
    "analysis": "由 -5 < x^3 < 5 得 -∛5 < x < ∛5（∛5≈1.71）。整数中落在该区间的为 -1,0,1；又 B={-3,-1,0,2,3}，取交集得 A∩B={-1,0}。故选 A。",
    "knowledge_point": "集合与常用逻辑/交集", "difficulty": 1,
  },
  {
    "no": 2, "type": "single", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "若 z/(z-1) = 1+i，则 z=（ ）",
    "options": ["-1-i", "-1+i", "1-i", "1+i"],
    "answer": "C",
    "analysis": "z/(z-1)=1+i ⇒ z=(1+i)(z-1) ⇒ z-(1+i)z=-(1+i) ⇒ -i z=-(1+i) ⇒ z=(1+i)/i=(1+i)(-i)=1-i。故选 C。",
    "knowledge_point": "复数/复数四则运算", "difficulty": 2,
  },
  {
    "no": 3, "type": "single", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "已知向量 a=(0,1), b=(2,x)，若 b⊥(b-4a)，则 x=（ ）",
    "options": ["-2", "-1", "1", "2"],
    "answer": "D",
    "analysis": "b-4a=(2,x)-(0,4)=(2,x-4)。由 b⊥(b-4a) 得 b·(b-4a)=0，即 2·2+x(x-4)=0 ⇒ x^2-4x+4=0 ⇒ (x-2)^2=0 ⇒ x=2。故选 D。",
    "knowledge_point": "平面向量/向量垂直的坐标表示", "difficulty": 2,
  },
  {
    "no": 4, "type": "single", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "已知 cos(α+β)=m，tanα·tanβ=2，则 cos(α-β)=（ ）",
    "options": ["-3m", "-m/3", "m/3", "3m"],
    "answer": "A",
    "analysis": "由 tanαtanβ=sinαsinβ/(cosαcosβ)=2 得 sinαsinβ=2cosαcosβ。cos(α+β)=cosαcosβ-sinαsinβ=-cosαcosβ=m，故 cosαcosβ=-m。于是 cos(α-β)=cosαcosβ+sinαsinβ=3cosαcosβ=-3m。故选 A。",
    "knowledge_point": "三角函数/两角和与差的余弦", "difficulty": 2,
  },
  {
    "no": 5, "type": "single", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "已知圆柱和圆锥的底面半径相等，侧面积相等，且它们的高均为 √3，则圆锥的体积为（ ）",
    "options": ["2√3π", "3√3π", "6√3π", "9√3π"],
    "answer": "B",
    "analysis": "设底面半径为 R，高 h=√3。圆柱侧面积=2πR·√3；圆锥母线 l=√(R^2+3)，圆锥侧面积=πR√(R^2+3)。由二者相等：2√3=√(R^2+3) ⇒ 12=R^2+3 ⇒ R=3。圆锥体积=1/3·πR^2·h=1/3·π·9·√3=3√3π。故选 B。",
    "knowledge_point": "立体几何/旋转体侧面积与体积", "difficulty": 2,
  },
  {
    "no": 6, "type": "single", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "已知函数 f(x)= { -x^2-2ax-a, x<0 ; e^x+ln(x+1), x≥0 } 在 R 上单调递增，则 a 的取值范围是（ ）",
    "options": ["(-∞,0]", "[-1,0]", "[-1,1]", "[0,+∞)"],
    "answer": "B",
    "analysis": "x≥0 时 e^x+ln(x+1) 递增，且 f(0)=1。x<0 时 g(x)=-x^2-2ax-a 为开口向下抛物线，递增区间为 (-∞,-a]，故需 -a≥0 ⇒ a≤0；又分段处需 g(0^-)≤f(0)，即 -a≤1 ⇒ a≥-1。综上 a∈[-1,0]。故选 B。",
    "knowledge_point": "函数/分段函数的单调性", "difficulty": 2,
  },
  {
    "no": 7, "type": "single", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "当 x∈[0,2π] 时，曲线 y=sinx 与 y=2sin(3x-π/6) 的交点个数为（ ）",
    "options": ["3", "4", "6", "8"],
    "answer": "C",
    "analysis": "y=sinx 最小正周期 2π；y=2sin(3x-π/6) 最小正周期 2π/3，在 [0,2π] 上有 3 个完整周期。用五点法分别作图，两曲线在 [0,2π] 上共有 6 个交点。故选 C。",
    "knowledge_point": "三角函数/图象与交点", "difficulty": 3,
  },
  {
    "no": 8, "type": "single", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "已知函数 f(x) 的定义域为 R，f(x)>f(x-1)+f(x-2)，且当 x<3 时 f(x)=x，则下列结论中一定正确的是（ ）",
    "options": ["f(10)>100", "f(20)>1000", "f(10)<1000", "f(20)<10000"],
    "answer": "B",
    "analysis": "由 x<3 时 f(x)=x，得 f(1)=1,f(2)=2。递推：f(3)>f(2)+f(1)=3，f(4)>5，f(5)>8，f(6)>13，…，近似斐波那契增长。f(10)>89（不一定>100，A 错）；f(20) 远大于 1000（B 对）；由递推下界可无限增大，C、D 的“<”不必然成立。故选 B。",
    "knowledge_point": "函数与数列/递推不等式", "difficulty": 3,
  },
  {
    "no": 9, "type": "multi", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "为了解推动出口后的亩收入（单位：万元）情况，从该种植区抽取样本，得到推动出口后亩收入的样本均值 ȳ=2.1，样本方差 s^2=0.01。已知该种植区以往的亩收入 X 服从正态分布 N(1.8, 0.1^2)，假设推动出口后的亩收入 Y 服从正态分布 N(ȳ, s^2)。则（ ）（若随机变量 Z~N(μ,σ^2)，则 P(Z<μ+σ)≈0.8413）",
    "options": ["P(X>2)>0.2", "P(X>2)<0.5", "P(Y>2)>0.5", "P(Y>2)<0.8"],
    "answer": "BC",
    "analysis": "X~N(1.8,0.1^2)：P(X>1.9)=1-P(X<1.9)=1-0.8413=0.1587；又 P(X>2)<P(X>1.9)，故 P(X>2)<0.1587<0.5，B 对、A 错。Y~N(2.1,0.1^2)：P(Y>2)=P(Y<2.2)≈0.8413>0.5，C 对；且 0.8413<0.8 不成立，故 D 错。故选 BC。",
    "knowledge_point": "概率统计/正态分布的对称性", "difficulty": 3,
  },
  {
    "no": 10, "type": "multi", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "设函数 f(x)=(x-1)^2(x-4)，则（ ）",
    "options": ["x=3 是 f(x) 的极小值点", "当 0<x<1 时，f(x)<f(x^2)", "当 1<x<2 时，-4<f(2x-1)<0", "当 -1<x<1 时，f(2-x)>f(x)"],
    "answer": "ACD",
    "analysis": "f'(x)=(x-1)(3x-9)=3(x-1)(x-3)。x<1 或 x>3 时 f' >0（增），1<x<3 时 f'<0（减），故 x=1 极大、x=3 极小，A 对。B：0<x<1 时 f 增且 x>x^2，故 f(x)>f(x^2)，B 错。C：1<x<2 ⇒ 1<2x-1<3，f 在此减且 f(1)=0,f(3)=-4，故 -4<f(2x-1)<0，C 对。D：令 g(x)=f(2-x)-f(x)，可证在 -1<x<1 时 g(x)>0，即 f(2-x)>f(x)，D 对。故选 ACD。",
    "knowledge_point": "导数/单调性与极值", "difficulty": 4,
  },
  {
    "no": 11, "type": "multi", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "造型可以做成美丽的丝带，将其看作图中曲线 C 的一部分。已知 C 过坐标原点 O，且 C 上的点满足横坐标大于 -2；到点 F(2,0) 的距离与到定直线 x=a（a<0）的距离之积为 4。则（ ）",
    "options": ["a=-2", "点(2√2,0)在 C 上", "C 在第一象限的点的纵坐标的最大值为 1", "当点(x0,y0)在 C 上时，y0 ≤ 4/(x0+2)"],
    "answer": "ABD",
    "analysis": "设 P(x,y), x>-2，由题意 √((x-2)^2+y^2)·(x+2)=4（a<0，距直线 x=a 即 x+2）。A：过原点 ⇒ √4·2=4 ⇒ a=-2，对。B：方程为 ((x-2)^2+y^2)(x+2)^2=16，代入 (2√2,0)：((2√2-2)^2)(2√2+2)^2=16，成立，对。C：取 x=3/2，得 y^2>1 ⇒ y>1，故纵坐标最大值>1，C 错。D：由方程得 y^2=(16/(x+2)^2)-(x-2)^2 ≤ 16/(x+2)^2 ⇒ |y|≤4/(x+2) ⇒ y0≤4/(x0+2)，对。故选 ABD。",
    "knowledge_point": "解析几何/曲线与方程", "difficulty": 4,
  },
  {
    "no": 12, "type": "fill", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "设双曲线 C: x^2/a^2 - y^2/b^2 = 1 (a>0,b>0) 的左、右焦点分别为 F1,F2，过 F2 作平行于 y 轴的直线交 C 于 A,B 两点。若 |F1A|=13，|AB|=10，则 C 的离心率为______。",
    "options": [],
    "answer": "3/2",
    "analysis": "由对称性，|AF2|=|AB|/2=5。在 Rt△F1F2A 中，|F1F2|=2c，|F1A|=13，|AF2|=5，故 (2c)^2=13^2-5^2=144 ⇒ c=6。由双曲线定义 2a=||F1A|-|F2A||=13-5=8 ⇒ a=4。故 e=c/a=6/4=3/2。",
    "knowledge_point": "圆锥曲线/双曲线离心率", "difficulty": 3,
  },
  {
    "no": 13, "type": "fill", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "若曲线 y=e^x+x 在点 (0,1) 处的切线也是曲线 y=ln(x+1)+a 的切线，则 a=______。",
    "options": [],
    "answer": "ln2",
    "analysis": "y=e^x+x 的导数 y'=e^x+1，在 x=0 处斜率 k=2，切线为 y=2x+1。设 y=ln(x+1)+a 的切点为 (x0,y0)，则 1/(x0+1)=2 ⇒ x0=-1/2，y0=ln(1/2)+a=-ln2+a。切点在切线 y=2x+1 上：-ln2+a=2(-1/2)+1=0 ⇒ a=ln2。",
    "knowledge_point": "导数/公切线问题", "difficulty": 3,
  },
  {
    "no": 14, "type": "fill", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "甲、乙两人各有四张卡片，每张卡片上标有一个数字，甲的卡片上分别标有数字 1,3,5,7，乙的卡片上分别标有数字 2,4,6,8。两人进行四轮比赛，在每轮比赛中，两人各自从自己持有的卡片中随机选一张，并比较所选卡片上数字的大小，数字大的人得 1 分，数字小的人得 0 分，然后各自弃置此轮所选的卡片。则四轮比赛后，甲的总得分不小于 2 的概率为______。",
    "options": [],
    "answer": "1/2",
    "analysis": "固定甲出牌顺序 (1,3,5,7)，乙的 4! = 24 种排列等可能。甲第 1 轮（对 1）必败（1<所有乙牌）；第 2、3、4 轮分别对每个乙牌>2、>4、>6 时获胜。枚举 24 种排列，甲至少胜 2 轮的情形共 12 种（胜 3 轮 1 种 + 胜 2 轮 11 种），故概率 = 12/24 = 1/2。",
    "knowledge_point": "概率/古典概型", "difficulty": 3,
  },
  {
    "no": 15, "type": "solve", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "记 △ABC 的内角 A,B,C 的对边分别为 a,b,c，已知 sinC=√2 cosB，a^2+b^2-c^2=√2 ab。\n（1）求 B；\n（2）若 △ABC 的面积为 3+√3，求 c。",
    "options": [],
    "answer": "(1) B=π/3；(2) c=2√2",
    "analysis": "(1) 由余弦定理 cosC=(a^2+b^2-c^2)/(2ab)=(√2 ab)/(2ab)=√2/2，故 C=π/4。又 sinC=√2 cosB ⇒ √2/2=√2 cosB ⇒ cosB=1/2，结合 B∈(0,π) 得 B=π/3。\n(2) A=π-B-C=5π/12。设外接圆半径 R，则 b=2R sinB=√3 R，c=2R sinC=√2 R。面积 S=1/2·b·c·sinA=1/2·√3R·√2R·sin(5π/12)= (√6/2)R^2·(√6+√2)/4。代入 S=3+√3 解得 R^2=4，R=2，故 c=2√2。",
    "knowledge_point": "解三角形/正弦与余弦定理", "difficulty": 4,
  },
  {
    "no": 16, "type": "solve", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "已知 A(0,3) 和 P(3, 3/2) 为椭圆 C: x^2/a^2 + y^2/b^2 = 1 (a>b>0) 上两点。\n（1）求 C 的离心率；\n（2）若过 P 的直线 l 交 C 于另一点 B，且 △ABP 的面积为 9，求 l 的方程。",
    "options": [],
    "answer": "(1) e=1/2；(2) l 的方程为 x-2y=0 或 3x-2y-6=0",
    "analysis": "(1) A(0,3) 在椭圆上 ⇒ b=3（b^2=9）。代入 P(3,3/2)：9/a^2 + (9/4)/9 = 1 ⇒ 9/a^2 = 3/4 ⇒ a^2=12，c^2=a^2-b^2=3，故 e=c/a=√3/√12=1/2。\n(2) 椭圆为 x^2/12 + y^2/9 = 1。|AP|=√((3-0)^2+(3/2-3)^2)=√(9+9/4)=3√5/2，直线 AP：x+2y-6=0。设 B 到 AP 距离为 d，则 1/2·|AP|·d=9 ⇒ d=6√5/5。联立可得 B(0,-3) 或 B(24/7,-3/7)。对应直线 l：过 P(3,3/2) 与 B，得 x-2y=0 或 3x-2y-6=0。",
    "knowledge_point": "圆锥曲线/椭圆方程与性质", "difficulty": 4,
  },
  {
    "no": 17, "type": "solve", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "如图，四棱锥 P-ABCD 中，PA⊥底面 ABCD，PA=AC=2，BC=1，AB=√3。\n（1）若 AD⊥PB，证明：AD∥平面 PBC；\n（2）若 AD⊥DC，且二面角 A-CP-D 的正弦值为 4√2/7，求 AD。",
    "options": [],
    "answer": "(1) 证明见解析；(2) AD=√3",
    "analysis": "(1) AC=2,BC=1,AB=√3 ⇒ AB^2+BC^2=AC^2 ⇒ AB⊥BC。PA⊥底面 ⇒ PA⊥AD。又 AD⊥PB，PA∩PB=P ⇒ AD⊥平面 PAB ⇒ AD⊥AB。故 AD∥BC；AD⊄平面 PBC，BC⊂平面 PBC ⇒ AD∥平面 PBC。\n(2) 作 AE⊥CP，AF⊥DP。由 AD⊥DC、PA⊥底面得 CD⊥平面 PAD ⇒ CD⊥AF；又 CP⊥AE,CP⊥AF ⇒ AF⊥平面 CDP，∠AEF 为二面角平面角。PA=PC=2 ⇒ AE=√2，sin∠AEF=AF/AE=4√2/7 ⇒ AF=4√2/7·√2=8/7。设 AD=t，DP=√(4+t^2)，由等积 1/2·PA·AD=1/2·DP·AF ⇒ 2t=√(4+t^2)·(8/7)，解得 t=√3（负值舍去）。故 AD=√3。",
    "knowledge_point": "立体几何/线面平行与二面角", "difficulty": 4,
  },
  {
    "no": 18, "type": "solve", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "已知函数 f(x)=ln(x/(2-x)) + ax + b(x-1)^3。\n（1）若 b=0，且 f'(x)≥0，求 a 的最小值；\n（2）证明：曲线 y=f(x) 是中心对称图形；\n（3）若 f(x)>-2 当且仅当 1<x<2，求 b 的取值范围。",
    "options": [],
    "answer": "(1) a_min=-2；(2) 关于(1,a)中心对称；(3) b≥-2/3",
    "analysis": "(1) b=0 时 f(x)=ln x - ln(2-x)+ax，x∈(0,2)。f'(x)=1/x+1/(2-x)+a=2/(x(2-x))+a。由 f'(x)≥0 恒成立 ⇒ a≥-2/(x(2-x))。因 x(2-x)≤1（x=1 取等），故 -2/(x(2-x))≥-2，最小值为 -2，故 a_min=-2。\n(2) f(1+x)+f(1-x)=[ln((1+x)/(1-x))+a(1+x)+bx^3]+[ln((1-x)/(1+x))+a(1-x)-bx^3]=2a，为定值，故 f(x) 关于点 (1,a) 中心对称。\n(3) f 在 (0,2) 连续，f(x)>-2 当且仅当 1<x<2 ⇒ 0<x<1 时 f(x)<-2，由中心对称性知 a=-2。此时 g(x)=f(x)+2=ln(x/(2-x))-2(x-1)+b(x-1)^3，g(1)=0，需 g(x)>0 在 1<x<2 恒成立。g'(x)=2/(x(2-x))-2+3b(x-1)^2，g'(1)=0。令 h(x)=2/(x(2-x))-2，在 (1,2) 递增；若 2+3b≥0（即 b≥-2/3），则 g'(x)≥0，g 增且 g(1)=0 ⇒ g(x)>0 恒成立；若 b<-2/3，存在 x1>1 使 g'(x)<0，g 递减致 g(x1)<0，不符。故 b∈[-2/3,+∞)。",
    "knowledge_point": "函数与导数/导数与中心对称、不等式", "difficulty": 5,
  },
  {
    "no": 19, "type": "solve", "subject": "数学", "paper": "新高考I卷", "year": 2024, "province": "河南",
    "stem": "设 m 为正整数，数列 a1,a2,…,a_{4m+2} 是公差不为 0 的等差数列，若从中删去两项 ai 和 aj（i<j）后剩余的 4m 项可被平均分为 m 组，且每组的 4 个数都能构成等差数列，则称数列 a1,a2,…,a_{4m+2} 是 (i,j)-可分数列。\n（1）写出所有的 (i,j)，1≤i<j≤6，使数列 a1,…,a6 是 (i,j)-可分数列；\n（2）当 m≥3 时，证明：数列 a1,…,a_{4m+2} 是 (2,13)-可分数列；\n（3）从 1,2,…,4m+2 中一次任取两个数 i 和 j（i<j），记数列是 (i,j)-可分数列的概率为 P_m，证明：P_m > 1/8。",
    "options": [],
    "answer": "(1) (1,2),(1,6),(5,6)；(2) 证明见解析；(3) 证明见解析",
    "analysis": "(1) m=1 时余下 4 项必为连续 4 项，故删去的相邻两段为 {(5,6),(1,6),(1,2)}，即 (1,2),(1,6),(5,6)。\n(2) 删去 a2,a13 后，a1 与 a3,a4,…,a12 可配成连续 4 项分组，剩余 a14,…,a_{4m+2} 为连续段（4m-12 项，4 的倍数）亦成组，故为 (2,13)-可分数列。\n(3) 可分两类计数：① 删去“连续 4k 段”外的相邻/特定对，共 (m+1)(m+2)/2 种；② 删去某连续 (4t+2) 段的第 2 项与倒数第 2 项，共 m(m-1)/2 种；合计 N=(m^2+m+1) 种。总选法 C(4m+2,2)=(4m+2)(4m+1)/2=8m^2+6m+1。故 P_m=N/(8m^2+6m+1) > (m^2+m+1)/(8m^2+8m+8)=1/8。",
    "knowledge_point": "数列/等差数列与新定义、概率", "difficulty": 5,
  },
]

for r in records:
    r["source_file"] = "2024新高考1_含答案_gkcx.pdf"
    r["verified_source"] = SRC
    r["need_review"] = False

out = "2024新高考1_含河南_题干答案_干净.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(records, f, ensure_ascii=False, indent=2)

# 人眼校对 Markdown
TYPE = {"single":"单选题","multi":"多选题","fill":"填空题","solve":"解答题"}
md = ["# 2024 新高考I卷 数学（含河南）题干+答案+解析 干净版\n",
      "> 来源：21cnjy 精品解析(docid 20613615) + docin + 51jiaoxi + 出卷网 交叉核验；答案键多重来源确认。",
      "> 已修正 OCR 残字：Q1 的 x^3、Q5 的 √3、Q6 的函数符号、Q11 的 D 选项、Q14 的正确概率(1/2)。\n"]
for r in records:
    md.append(f"## 第 {r['no']} 题（{TYPE[r['type']]}）  知识点：{r['knowledge_point']}  难度：{r['difficulty']}/5\n")
    md.append("**题干：**\n\n" + r["stem"] + "\n")
    if r["options"]:
        md.append("\n**选项：**\n" + "\n".join(r["options"]) + "\n")
    md.append(f"\n**答案：** {r['answer']}\n")
    md.append(f"\n**解析：**\n{r['analysis']}\n")

with open("2024新高考1_含河南_题干答案_干净.md", "w", encoding="utf-8") as f:
    f.write("\n".join(md))

print("题数:", len(records))
print("有答案:", sum(bool(r["answer"]) for r in records))
print("有解析:", sum(bool(r["analysis"]) for r in records))
print("全部 need_review=False:", all(not r["need_review"] for r in records))
