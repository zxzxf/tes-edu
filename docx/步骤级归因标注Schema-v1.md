# 步骤级归因标注 Schema v1（设计稿）

> **需求来源（用户 2026-10-23 定稿口径）**：
> 1. **选择/填空题**：每个步骤带**详解**、带**知识点串联**（解题推导过程 + 知识点如何衔接），并说明**如何让学生和老师理解知识点**，达到**触类旁通**；
> 2. **解答题**：每一步要有**错误原因**及**正确解答步骤**；整题列出**用到哪些知识点**、这些知识点**如何理解**、**加上记忆**（记忆抓手），达到**触类旁通**。
>
> 本设计稿把上述口径落成可标注、可入库、可评测的字段规范。taxonomy 错因七分类沿用前稿（concept/formula/case/calc/read/logic/strategy）。

---

## 一、总体结构：两层三块

```
题目级（course_kb.steps 等，每题标一次）
 ├── steps[]            解题推导：每步 desc + detail(详解) + kp_used + 串联链
 ├── 归因映射            选择: distractor_map ｜ 填空: wrong_value_map ｜ 解答: steps[].typical_errors(reason+correction)
 └── transfer           触类旁通：变式题 + 类题联想 + 通法命名 + 题型识别特征

知识点级（kb_knowledge_points，每个知识点标一次，全部题目复用）
 └── understand(如何理解) + memory(记忆抓手) + misconceptions(常见误区) + prerequisites(前置串联)
```

**关键设计决策：「知识点如何理解 + 记忆抓手」不进题目表，进知识点表。**
理由：同一个知识点（如"离心率定义"）会被几十道题用到。按题标注会重复劳动且口径漂移；按知识点标注一次、题目里只引用 `kp_id`，则：
- 300 个知识点 ≈ 25–40 小时一次性投入，之后 114 题和未来的 3000 题全部免费复用；
- 学生端"这个知识点没懂"可以直接跳到统一的讲解卡，老师端教研口径全库一致；
- `kb_knowledge_points` 表已存在（title/content/tags），只需加列，不是新表。

**第二个决策：steps 的知识点串联 = 知识图谱上的一条路径。**
每步记录 `kp_used` + `derives_from`（这步由哪个前置知识点推出来）+ `leads_to`（推向哪）。这样：
- 解题过程就是 DAG 上的一次行走——"图谱驱动"从口号变成每题的真实数据；
- 学生错在第 N 步 = 路径在第 N 步断裂 → P3 自适应直接知道该补哪条边，不用重推。

---

## 二、字段字典

### 2.1 题目级 · steps[]（全题型通用）

| 字段 | 类型 | 说明 |
|---|---|---|
| `no` | int | 步骤序号 |
| `desc` | str | 这一步做了什么（一句话，机器可判分的最小单元） |
| `detail` | str | **详解**：为什么这么做、依据是什么、易被跳过的理由（需求①） |
| `kp_used` | str→kp_id | 本步使用的知识点（引用知识点表） |
| `derives_from` | kp_id? | **串联**：由哪个前置知识点推出 |
| `leads_to` | kp_id? | **串联**：推向哪个后续知识点 |
| `student_view` | str? | 学生视角的直观理解（"把这步想成……"）（需求①"让学生理解"） |
| `teacher_view` | str? | 教学提示：学生常卡在哪、怎么引导（需求①"让老师理解"） |
| `typical_errors[]` | list | **仅解答题**：见 2.3 |

### 2.2 客观题归因映射（确定性查表，不走 LLM）

**选择题 `distractor_map`**（key=错误选项字母）：

| 字段 | 说明 |
|---|---|
| `wrong_step` | 该错选对应断裂的步骤号 |
| `error_type` | taxonomy 七类之一 |
| `reason` | **为什么学生会这么错**（认知根源，需求②同款要求前移到客观题） |
| `correction` | 正确做法一句话 |

**填空题 `wrong_value_map[]`**：`value`（归一化后的典型错值）+ 同上四字段。
未命中映射的填空错值 → LLM 兜底归因，标 `attribution_source=llm`。

### 2.3 解答题 steps[].typical_errors[]（需求②核心）

| 字段 | 说明 |
|---|---|
| `error_type` | taxonomy 七类 |
| `reason` | **这一步为什么会错**（如："分段函数求值域时，学生惯性只对 x<0 段讨论，因为题干先给了 x<0 的解析式"） |
| `correction` | **正确的这一步该怎么写**（完整正确步骤文本，可直接展示给学生） |
| `evidence_hint` | 判分线索：学生作答里出现什么特征说明踩了这个错（给 judge v2 用） |

### 2.4 题目级 · transfer（触类旁通，需求①②共同落点）

| 字段 | 说明 |
|---|---|
| `method_name` | 通法命名（如"数据代表值三步法：排序→定位→取值"） |
| `applicable_when` | **题型识别特征**：看到什么条件应该想到这个通法（触类旁通的"类"在哪） |
| `variants[]` | 变式题 ≥2：改数值 / 改条件 / 逆向问（含答案与所属 kp） |
| `analogies[]` | 类题联想：同 kp 不同情境的题 id（灌库后由检索补充，标注时先留空位） |

> 与现有系统的接口：agent-backend 已有 `/similar`（运行时 LLM 生成变式）。已标注题**优先返回人工审定的 variants**，未标注题才落 LLM 现场生成——质量有保底，成本不增加。

### 2.5 知识点级（kb_knowledge_points 加列，标一次全库复用）

| 字段 | 说明 |
|---|---|
| `understand` | **如何理解**：一句话直觉 + 形式化定义 + 它在章节里的位置（需求①②） |
| `memory` | **记忆抓手**：口诀 / 图像联想 / 词源 / 对比记忆（需求②"加上记忆"） |
| `misconceptions[]` | 常见误区（与题目级 typical_errors 联动：题目里的错因可回链到知识点误区） |
| `prerequisites[]` | 前置知识点（DAG 边，P0 本体已有雏形） |

---

## 三、标注示例（2026 新高考 I 卷真题，三题型各一）

### 3.1 选择题 · 第 1 题（中位数）

题干：样本数据 6, 8, 4, 5, 12 的中位数为（ ）A.5 B.6 C.8 D.9　答案 B

```jsonc
{
  "steps": [
    { "no": 1, "desc": "将数据从小到大排序：4,5,6,8,12",
      "detail": "中位数的定义依赖'排序后的中间位置'，不排序则'中间'无意义。这是本题唯一但致命的动作。",
      "kp_used": "中位数定义", "derives_from": "大小排序", "leads_to": "奇偶个数处理",
      "student_view": "中位数就是'排队站中间的那个人'——不排队就找不到中间。",
      "teacher_view": "学生最常见的跳步：数据只有5个时'一眼看出中间'，养成不排序习惯后遇到 20+ 数据必错。要求书写时永远先写排序行。" },
    { "no": 2, "desc": "n=5 为奇数，取第 (5+1)/2=3 个数，即 6",
      "detail": "奇数个取正中间；偶数个取中间两数的平均。本题 n=5 走奇数分支。",
      "kp_used": "中位数奇偶处理", "derives_from": "中位数定义", "leads_to": null }
  ],
  "distractor_map": {
    "A": { "wrong_step": 2, "error_type": "concept",
           "reason": "把中位数与均值/众数概念混淆，或误取第2个位置",
           "correction": "中位数看位置不看数值大小，奇数个取正中间" },
    "C": { "wrong_step": 1, "error_type": "read",
           "reason": "未排序，直接取原始序列中间位置的数（4,5,[6],8,12 恰好排序后也在中间，但学生取的是原序 6,8,[4],5,12 思路混乱时易得 8）",
           "correction": "任何中位数题第一步先排序" },
    "D": { "wrong_step": 2, "error_type": "concept",
           "reason": "误算均值 (6+8+4+5+12)/5=7 后就近取 9，或与众数混淆",
           "correction": "均值≈7 不是 9；三个'代表值'定义要对比记忆" }
  },
  "kp_required": ["中位数定义", "中位数奇偶处理"],
  "transfer": {
    "method_name": "数据代表值三步法：排序 → 定位 → 取值",
    "applicable_when": "看到'中位数/百分位数/四分位数'字样，第一动作永远是排序",
    "variants": [
      { "stem": "样本 6,8,4,5,12,7 的中位数", "note": "偶数个，逼出'中间两数平均'分支", "answer": "6.5" },
      { "stem": "已知排序后数据 x₁≤…≤x₇ 的中位数为 8，求 x₄", "note": "逆向问", "answer": "8" }
    ]
  }
}
```

### 3.2 填空题 · 第 12 题（双曲线离心率）

题干：双曲线 5x² − 6y² = 1 的离心率为＿＿。　答案 √66/6

```jsonc
{
  "steps": [
    { "no": 1, "desc": "化标准方程：x²/(1/5) − y²/(1/6) = 1",
      "detail": "离心率公式 e=c/a 依赖 a²、b²，非标准形式必须先化为 x²/a²−y²/b²=1。",
      "kp_used": "双曲线标准方程", "leads_to": "a,b,c关系",
      "student_view": "右边必须是 1，x²、y² 系数必须挪到分母——'化标准'就是整理成课本第一行的样子。" },
    { "no": 2, "desc": "a²=1/5, b²=1/6, c²=a²+b²=11/30",
      "detail": "双曲线 c²=a²+b²；椭圆是 c²=a²−b²。两曲线公式仅一符号之差，是最高频混淆点。",
      "kp_used": "双曲线abc关系", "derives_from": "双曲线标准方程",
      "teacher_view": "板书时永远并排写椭圆/双曲线两式，让学生看符号差异而不是背两条。" },
    { "no": 3, "desc": "e=c/a=√(c²/a²)=√(11/30÷1/5)=√(11/6)=√66/6",
      "detail": "e=√(c²/a²) 可跳过开方 a 的分数运算，直接比值开方，最后分母有理化。",
      "kp_used": "离心率定义" }
  ],
  "wrong_value_map": [
    { "value": "√6/6", "wrong_step": 2, "error_type": "formula",
      "reason": "误用椭圆关系 c²=a²−b²：c²=1/5−1/6=1/30，e=√(1/30·5)=√6/6",
      "correction": "双曲线 c²=a²+b²（c 最大，斜边记忆）" },
    { "value": "√11/6", "wrong_step": 3, "error_type": "calc",
      "reason": "e=c/a 算成 c²/a² 未开方",
      "correction": "比值后还差一步开方" }
  ],
  "kp_required": ["双曲线标准方程", "双曲线abc关系", "离心率定义"],
  "transfer": {
    "method_name": "离心率两步法：化标准 → 比值开方",
    "applicable_when": "方程系数不是标准形式（系数≠1 或右边≠1）时，第一步永远是化标准",
    "variants": [
      { "stem": "双曲线 x²/4 − y²/5 = 1 的离心率", "note": "已是标准形，跳过步骤1", "answer": "3/2" },
      { "stem": "椭圆 5x²+6y²=1 的离心率", "note": "同系数换曲线，逼出 c²=a²−b² 分支", "answer": "√30/30·…" }
    ]
  }
}
```

### 3.3 解答题 · 第 19 题（压轴，D(x₀) 集合定义）

```jsonc
{
  "steps": [
    { "no": 1, "desc": "算基准值 f(−1)=2⁻¹=0.5",
      "detail": "−1<0 走 f(x)=2ˣ 分支。一切讨论的锚点。",
      "kp_used": "分段函数求值",
      "typical_errors": [
        { "error_type": "read", "reason": "未注意 −1<0，误用 x≥0 分支的 1−x 得 f(−1)=2",
          "correction": "先判 x 在哪个区间，再代对应解析式",
          "evidence_hint": "作答开头出现 f(−1)=2" } ] },
    { "no": 2, "desc": "设 d<1（即 −1+d<0），解 2^(d−1)>2⁻¹ 得 d>0，交集 0<d<1",
      "detail": "x<0 段 f 严格增，指数比较直接去掉底数。",
      "kp_used": "指数函数单调性", "derives_from": "分段函数求值",
      "typical_errors": [
        { "error_type": "logic", "reason": "解出 d>0 后忘记与前提 d<1 取交集",
          "correction": "分段讨论的每一段，结论必须与该段前提取交集",
          "evidence_hint": "第一段结论写成 (0,+∞)" } ] },
    { "no": 3, "desc": "设 d≥1（即 −1+d≥0），解 2−d>0.5 得 d<1.5，交集 1≤d<1.5",
      "detail": "x≥0 段 f(x)=1−x 严格减，注意变号方向。",
      "kp_used": "一次函数单调性",
      "typical_errors": [
        { "error_type": "case", "reason": "整段遗漏——只讨论 d<1。根源：题干先给 x<0 解析式，学生惯性认为讨论完毕",
          "correction": "d 的范围是整个 R，分段函数有几段就要讨论几次",
          "evidence_hint": "答案只有 (0,1)，无 [1,1.5) 部分" } ] },
    { "no": 4, "desc": "并集得 D(−1)=(0,1.5)",
      "detail": "两段结果取并，注意 1 处衔接（左段开右段闭，并为开区间）。",
      "kp_used": "集合区间运算",
      "typical_errors": [
        { "error_type": "calc", "reason": "端点开闭处理错，写成 (0,1)∪[1,1.5) 不合并或 (0,1.5]",
          "correction": "1∈第二段且满足不等式？2−1=1>0.5 成立，故 1 在集合内，可并为 (0,1.5)",
          "evidence_hint": "最终答案带 ] 或未合并" } ] }
  ],
  "kp_required": ["分段函数求值", "指数函数单调性", "一次函数单调性", "集合区间运算", "分类讨论思想"],
  "transfer": {
    "method_name": "分段函数不等式：定锚点 → 按段讨论 → 段内交集 → 段间并集",
    "applicable_when": "题干给出分段解析式 + 要求解集/集合表示时",
    "variants": [ "…(改 f(x≥0)=x−1 等，逼出不同交并结构)" ]
  }
}
```

知识点级示例（`kb_knowledge_points` 一行）：

```jsonc
{ "kp_id": "kp_hyperbola_abc", "title": "双曲线 a,b,c 关系",
  "understand": "c 是焦点到原点距离，永远最大；a,b 是两轴半长。直角三角形里 c 是斜边——椭圆'切掉'一块(c²=a²−b²)，双曲线'补上'一块(c²=a²+b²)。",
  "memory": "口诀：「椭圆减、双曲加，c 最大当斜边」。图像记忆：画焦点三角形，c 永远对着直角。",
  "misconceptions": ["与椭圆公式混用（见 2026 卷 12 题 wrong_value_map）", "把 a,b 大小关系当成判断焦点位置的依据（应看哪项为正）"],
  "prerequisites": ["双曲线标准方程", "椭圆abc关系"] }
```

---

## 四、落库映射

| 表 | 改动 |
|---|---|
| `course_kb` | + `steps JSONB`（含 detail/kp_used/串联/typical_errors/distractor_map/wrong_value_map 全部装进这一个 JSONB，避免宽表）+ `transfer JSONB` + `kp_required JSONB`(kp_id 列表) |
| `kb_knowledge_points` | + `understand TEXT` + `memory TEXT` + `misconceptions JSON` + `prerequisites JSON` |
| `wrong_questions` | + `wrong_step_no INT` + `error_type VARCHAR(16)` + `evidence TEXT` + `attribution_source VARCHAR(8)`(table/llm/unknown) |
| `student_step_errors`（新） | student_id, tenant_id, question_id, step_no, error_type, attribution_source, occurred_at |
| judge v2（agent-backend） | 三路分流：选择查 distractor_map → 填空查 wrong_value_map（未中 LLM 兜底）→ 解答 LLM 对齐 steps；统一输出 `{correct, first_wrong_step, error_type, evidence}` |

`import_bank.py` 增加 steps/transfer 列的导入映射（JSON 字符串→JSONB）。

---

## 五、工作量重估（含知识点复用红利）

| 块 | 量 | 教师时间（LLM 预标+人工审核口径） |
|---|---|---|
| 知识点卡（understand/memory/misconceptions） | ~300 个 KP，**一次性** | 预标后审核 ≈ **15–25 h**（全库所有题复用） |
| 选择题 48 | steps+distractor_map+variants | 每题 15–20 min ≈ **12–16 h** |
| 多选题 18 | 同上+部分选对组合 | 每题 20 min ≈ **6 h** |
| 填空题 18 | steps+wrong_value_map(≥2 错值) | 每题 15 min ≈ **4.5 h** |
| 解答/压轴 30 | steps+每步 typical_errors+variants | 每题 30–45 min ≈ **15–22 h** |
| **合计** | 114 题 + 300 KP | **≈ 52–73 h**（试标后校准） |

压缩手段：① LLM 预标全部字段草稿（H 档模型），教师只做判定与改写——预计省 40–60%；② 干扰项 reason 可从教辅解析中**提取事实性错误定位**（不复制原文，避版权）；③ 知识点卡与 P0 的 300 点本体合并施工，一次访谈两用。

**试标校准（先行）**：每题型各 1 题（选择 Q1、填空 Q12、解答 Q19(1)）完整标注并掐表 → 得真实单题耗时 → 回校本表。三题的"标准答案版"标注即本文档 §3，教师直接在其上修订即算试标。

---

## 六、标注质量验收清单（每题过检）

- [ ] steps 覆盖完整推导，无跳步（detail 能回答"为什么这步成立"）
- [ ] 每步 kp_used 命中知识点表已有条目（新 KP 先建卡再引用）
- [ ] 选择题：每个错误选项都有 distractor_map 条目，reason 写的是**认知根源**不是"粗心"
- [ ] 填空题：wrong_value_map ≥2 条，错值可复算出来（不是想象的错法）
- [ ] 解答题：每步 typical_errors ≥1，correction 是**可直接展示给学生的完整正确步骤**
- [ ] transfer.variants ≥2 且至少 1 个改变题型分支（逼出不同讨论）
- [ ] 知识点卡：memory 是抓手不是复述定义（口诀/图像/对比至少其一）

---

## 七、与既有计划的接口

| 既有项 | 关系 |
|---|---|
| P0 知识点本体（≥300 点 DAG） | KP 卡（understand/memory）与本体**同一批施工**，本体建到哪、卡标到哪 |
| P1 灌库 | steps/transfer 随 import_bank 入库；先灌 2026 卷 19 题（文本层干净） |
| P2 六项埋点指标 | judge v2 输出 first_wrong_step 后，"提示有效推进率"可按步骤粒度计算 |
| P3 掌握度图谱 | steps 的 derives_from/leads_to 即 DAG 边；错步=路径断点，自适应补边直接可用 |
| 试点校方案 §三 | "步骤归因准确率 ≥85%"评测：客观题查表命中率 + 解答题 LLM 对齐率，两指标分开报 |
| `/similar` 触类旁通 | 已标注题优先返回人工 variants，未标注题落 LLM 现场生成 |
