# 课程教育 Agent 系统 · 完整方案书

> 本文档由三份子文档合并而成：
> 1. 课程教育Agent设计方案（总体架构 / 需求实现 / 技术栈 / Schema / 代码骨架 / 市场对比与缺失 / 图谱建图 / 路线图 / 技术栈与语言分工）
> 2. Web管理后台功能设计方案（附录A）
> 3. 应用拆分与开发分期（附录B）

---

# 课程教育 Agent 系统设计方案

> 目标：一个能"解题 + 讲解 + 触类旁通 + 知识图谱 + 拍照识别 + K12 知识库 + 安卓端"的教育智能体。
> 本文档给出总体架构、逐条需求实现、技术栈、数据/消息设计、关键代码骨架与落地路线图。

---

## 一、总体架构（分层）

```
┌─────────────────────────────────────────────────────────────┐
│  用户层：Android App（拍照/输入/图谱/刷题） ｜ 可选 Web 管理后台 │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTPS / JSON
┌───────────────────────────▼─────────────────────────────────┐
│  接入层：API Gateway（鉴权、限流、日志）                       │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  Agent 编排层（Orchestrator）                                  │
│  · 意图路由（文本题 / 图片题 / 查知识点 / 出图谱）             │
│  · 工具调用（Tool Calling）：RAG 检索、知识图谱查询、OCR/VLM   │
│  · Prompt 模板（老师角色、分步讲解、纠错、举一反三）           │
└──────┬──────────────┬───────────────┬───────────────────────┘
       │              │               │
┌──────▼─────┐ ┌──────▼──────┐ ┌──────▼──────┐
│ LLM 解答    │ │ 多模态 VLM  │ │ OCR 引擎    │
│ /讲解引擎   │ │ 看图识题    │ │ 文字提取    │
└──────┬─────┘ └──────┬──────┘ └──────┬──────┘
       │              │               │
┌──────▼──────────────▼───────────────▼───────────────────────┐
│  工具/检索层：RAG 检索器(pgvector) ｜ 知识图谱查询(图存储)     │
└───────────────────────────┬─────────────────────────────────┘
                            │ 事件/落库
┌───────────────────────────▼─────────────────────────────────┐
│  数据层：Kafka 消息总线 → PostgreSQL                          │
│  · course_kb（小学/初中/高中 知识点+例题+解析）               │
│  · embeddings（向量，pgvector）                               │
│  · kg_edges（知识图谱边）                                     │
│  · drill_cards（刻板知识点/背诵卡）                           │
└──────────────────────────────────────────────────────────────┘
```

要点：**Kafka 不负责"存储答案"，而是作为异步管道**——题目入库、解析结果落库、知识图谱构建事件、学情日志都走 Kafka，PostgreSQL 才是真正的数据源。这样解耦、可扩、可追溯。

---

## 二、逐条需求实现方案

### 1. 输入题目 → 写出答案并讲解
- **链路**：文本题目 → Orchestrator → LLM（带系统 Prompt）→ 结构化输出。
- **Prompt 设计**：设定角色为"耐心、循循善诱的中小学老师"；要求输出固定结构：`【答案】`、`【考点】`、`【分步讲解】`、`【易错点】`、`【举一反三】`。
- **增强**：先 RAG 检索知识库中该考点定义/公式，再让 LLM 结合检索内容作答，避免"幻觉"。
- **输出格式**：JSON 或 Markdown，前端可渲染公式（MathJax/KaTeX）。

### 2. 触类旁通（相似知识点题目列举）
- **两种实现，可叠加**：
  1. **检索式**：对当前题目做 embedding，在 `embeddings` 表按 `subject/grade` 过滤做向量相似度搜索，召回同类题。
  2. **生成式**：让 LLM 基于"考点 + 难度"生成 2–3 道变式题（换数字、换情境、换问法）。
- **推荐**：检索式保证"真题"，生成式保证"无限供给"，二者用标签去重。

### 3.（原编号4）知识点整理成知识图谱
- **节点类型**：`知识点`、`公式/定理`、`例题`、`题型`、`易混点`。
- **关系**：`前置→后继`、`包含`、`易混`、`属于考点`、`对应例题`。
- **构建**：
  - 自动：LLM 抽取"知识点→关系"三元组，写入 `kg_edges`；
  - 人工：教研审核补全。
- **存储选型（三选一，默认推荐 B）**：
  - **A. PostgreSQL 邻接表（起步）**：`kg_edges` 存边 + 递归 CTE 跑 1–3 跳查询。零额外组件；弱点是深层/分支遍历会退化。
  - **B. Apache AGE（同库图查询，推荐）**：PostgreSQL 的图扩展，原生 Cypher、`O(1)` 邻接遍历，内容表与图**同库同源**避免双库同步；Apache-2.0 商用友好。
  - **C. 独立 Neo4j（规模化 / 深层分析）**：原生图存储 + Cypher，适合百万级边、实时路径推荐 / 社区发现；但要运维一个 JVM 系统，且 Community 版为 **AGPL**（闭源商用需注意许可，或买 Enterprise）。
  - **判断标准**：K12 课程图谱通常 10万–200万节点/边，属中小图。若图谱只做"辅助记忆/展示/浅层展开"，A 或 B 足够；若要做"实时学情路径推荐 + 社区发现"等图算法，再上 C。
- **可视化**：前端用 D3.js / ECharts 力导向图渲染，支持点击展开、按年级筛选，便于记忆与复习。

### 4.（原编号5）拍照上传解析图片题目
- **两步法（推荐，成本低、稳）**：
  1. **OCR**：用 PaddleOCR / 腾讯云 OCR / 百度 OCR 提取题目文字（含印刷体、手写体、公式区域）。
  2. **VLM 兜底**：直接把图片送多模态大模型（Qwen-VL / GPT-4o / Gemini）理解版面与公式，输出结构化题目。
- **流程**：`图片 → OCR+VLM → 文本题目 → 复用需求1/2/3 的解答与图谱流程`。
- **要点**：对"图中有多道题"做切片识别；对公式用 LaTeX 还原。

### 5.（原编号6）K12 课程体系知识库 + Kafka + pgsql
- **知识库内容**：小学/初中/高中 各科（语文、数学、英语、物理、化学、生物、历史、地理…）的`知识点、例题、标准解析、考点标签、难度`。
- **入库流程（异步）**：
  ```
  教研/爬虫/批量导入 → Kafka topic=course_kb_ingest 
    → Consumer：清洗 → 切分 → 调 embedding 模型 → 写 PostgreSQL
  ```
- **表设计（见第四节）**：`knowledge_points`、`questions`、`embeddings`、`kg_edges`、`drill_cards`、`student_logs`。
- **Kafka 的价值**：削峰（批量导入不卡接口）、解耦（入库/建图/算学情各消费一次）、可重放（出错重消费）。

### 6.（原编号7）刻板知识点从知识库获取
- **定义"刻板知识点"** = 固定公式、定理、定义、必背古诗文/单词等，打 `tag=drill` 或单独表 `drill_cards`。
- **接口**：`GET /drill?subject=数学&grade=初三&type=公式` 返回背诵/默写卡。
- **同步策略**：教研维护 → 写 Kafka `topic=drill_sync` → 落 `drill_cards` 并刷 Redis 缓存，App 离线也能背。

### 7.（原编号8）安卓应用调用 Agent
- **技术**：Kotlin + Jetpack Compose（或 Flutter 跨端）。
- **能力页**：① 文本/语音输入 ② 拍照识题 ③ 讲解展示（含公式渲染）④ 触类旁通列表 ⑤ 知识图谱可视化 ⑥ 背诵卡/错题本。
- **通信**：Retrofit 调 Agent REST API（见第五节）；图片走 `multipart` 上传。
- **离线**：背诵卡、错题本本地 Room 数据库缓存。

### 8.（原编号9）与市场同类产品对比 & 缺失内容
见第六节（6.1 竞品速览、6.2 能力矩阵、6.3 差异化定位、6.4 缺失项优先级清单、6.5 与路线图对应）。

---

## 三、推荐技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| Agent 框架 | LangChain / LlamaIndex（Python）或自研轻量编排 | 工具调用、RAG 现成 |
| 大模型 | 通义千问 / DeepSeek / 智谱 GLM（中文强，成本低）；VLM 用 Qwen-VL / GPT-4o | 按成本与合规选 |
| OCR | PaddleOCR（本地免费）/ 腾讯云 OCR（稳定） | 公式区域需后处理 |
| 向量检索 | PostgreSQL + **pgvector**（要求 PG 13+，PG 11 装不了，见 7.9；**已就绪的 PG18 AGE 容器可直接挂 pgvector**，向量与图谱同库同源） | 与业务库同源，运维省 |
| 图存储 | PostgreSQL 邻接表 → Apache AGE（同库 Cypher，推荐；**AGE 1.5.0 支持 PG 11，或起 `apache/age:latest` 容器即 PG18+AGE 1.8.0，见七·附 / 7.9**）→ Neo4j（规模化） | 同库免同步；Neo4j 商用注意 AGPL |
| 消息队列 | **Kafka** | 按你的要求 |
| 关系库 | **PostgreSQL 15+** | 支持 pgvector、JSONB |
| 后端 | Python FastAPI（推理服务）/ 或 Java Spring（现有 aas-api 风格） | 视团队而定 |
| 安卓 | Kotlin + Compose + Retrofit + Room | 现代栈 |
| 前端图谱 | ECharts / D3.js | 力导向图 |

---

## 四、数据库 Schema 示例（PostgreSQL + pgvector）

```sql
-- 知识点
CREATE TABLE knowledge_points (
  id            BIGSERIAL PRIMARY KEY,
  subject       TEXT NOT NULL,          -- 数学/语文...
  grade         TEXT NOT NULL,          -- 小学/初中/高中 + 年级
  title         TEXT NOT NULL,
  content       TEXT,                   -- 定义/讲解
  tags          TEXT[],                 -- {公式, 必背, 易错}
  parent_id     BIGINT REFERENCES knowledge_points(id),
  created_at    TIMESTAMPTZ DEFAULT now()
);

-- 例题/题目
CREATE TABLE questions (
  id            BIGSERIAL PRIMARY KEY,
  kp_id         BIGINT REFERENCES knowledge_points(id),
  grade         TEXT,
  subject       TEXT,
  stem          TEXT NOT NULL,          -- 题干
  answer        TEXT,
  analysis      TEXT,                   -- 解析
  difficulty    SMALLINT,               -- 1-5
  source        TEXT
);

-- 向量（需先 CREATE EXTENSION vector;）
-- 注意：pgvector 要求 PostgreSQL 13+，当前 PG 11.2 无法安装，见 7.9
CREATE TABLE embeddings (
  id            BIGSERIAL PRIMARY KEY,
  qid           BIGINT REFERENCES questions(id),
  kp_id         BIGINT REFERENCES knowledge_points(id),
  model         TEXT,
  vec           VECTOR(1536)            -- 维度随模型
);
CREATE INDEX ON embeddings USING ivfflat (vec vector_cosine_ops);

-- 知识图谱边（采用 Apache AGE 时，拓扑改由 AGE 图/Cypher 承载，见第七节；
-- 此关系表作为「关系型镜像 / 报表 / 降级」保留，构建时与 AGE 图双写）
CREATE TABLE kg_edges (
  id            BIGSERIAL PRIMARY KEY,
  from_id       BIGINT,
  to_id         BIGINT,
  rel           TEXT,                   -- PREREQUISITE/CONTAINS/CONFUSES/...
  weight        REAL DEFAULT 1.0,
  created_at    TIMESTAMPTZ DEFAULT now()
);

-- 刻板知识点/背诵卡
CREATE TABLE drill_cards (
  id            BIGSERIAL PRIMARY KEY,
  kp_id         BIGINT REFERENCES knowledge_points(id),
  card_type     TEXT,                   -- 公式/定义/默写
  front         TEXT,
  back          TEXT
);

-- 学情日志（Kafka 消费写入）
CREATE TABLE student_logs (
  id            BIGSERIAL PRIMARY KEY,
  uid           TEXT,
  qid           BIGINT,
  action        TEXT,                   -- ask / wrong / drill
  ts            TIMESTAMPTZ DEFAULT now()
);
```

### Kafka Topic 设计
- `course_kb_ingest`：知识/题目批量入库事件
- `question_parsed`：图片解析完成事件（触发落库+建图）
- `kg_build`：三元组抽取完成事件
- `student_event`：学生答题/错题事件（供学情分析）
- `drill_sync`：背诵卡更新事件

---

## 五、关键代码骨架

### 1) Agent 后端（FastAPI + 工具路由，伪代码）
```python
from fastapi import FastAPI, UploadFile
from pydantic import BaseModel

app = FastAPI()

SYS_PROMPT = (
  "你是耐心的中小学老师。对题目先给答案，再分步讲解，"
  "指出考点与易错点，并出一道举一反三题。用 Markdown 输出。"
)

@app.post("/solve")
def solve(q: dict):
    # 1) RAG：按考点召回知识库定义
    ctx = rag_search(q["subject"], q["text"])
    # 2) 调 LLM
    reply = llm.chat(system=SYS_PROMPT, user=q["text"], context=ctx)
    # 3) 异步：相似题入库/学情日志 -> Kafka
    kafka_send("student_event", {"uid": q.get("uid"), "text": q["text"]})
    return {"answer": reply, "similar": similar_questions(q)}

@app.post("/solve_image")
async def solve_image(file: UploadFile):
    img = await file.read()
    text = ocr_and_vlm(img)          # OCR + 多模态理解
    return solve({"text": text})
```

### 2) Kafka 入库消费者（Python）
```python
from kafka import KafkaConsumer
import pg  # 你的数据库客户端

consumer = KafkaConsumer("course_kb_ingest", bootstrap_servers="kafka:9092")
for msg in consumer:
    item = json.loads(msg.value)
    kp_id = insert_knowledge_point(item)        # 写 knowledge_points
    vec = embed(item["stem"])                   # 调 embedding 模型
    insert_embedding(kp_id, vec)                # 写 embeddings
    kafka_send("kg_build", {"kp_id": kp_id})    # 触发建图
```

### 3) Android 调用（Kotlin + Retrofit）
```kotlin
interface AgentApi {
    @POST("solve")
    suspend fun solve(@Body req: SolveReq): SolveResp

    @Multipart
    @POST("solve_image")
    suspend fun solveImage(@Part file: MultipartBody.Part): SolveResp
}

// 拍照后
val part = MultipartBody.Part.createFormData("file", f.name, f.asRequestBody())
val resp = api.solveImage(part)   // 拿到答案+讲解，Compose 渲染
```

---

## 六、市场对比与"缺失内容"分析

> 说明：本 Agent 当前版本只覆盖"单题解答 + 讲解 + 触类旁通 + 知识图谱 + 拍照 + 知识库"主线能力；下面把市面主流产品拆开对比，并列出**真正缺失、需要补的能力**，按优先级排好"怎么补、工作量多大"。

### 6.1 竞品速览

- **作业帮 / 小猿搜题**：拍照搜题龙头，题库海量、OCR 强、有付费答疑。短板：讲解偏模板化、互动弱、依赖人工。
- **学而思网校 / 猿辅导 / 高途**：体系化直播课 + 督学服务，重运营。短板：贵、依赖真人师资、边际成本高。
- **豆包 / 通义千问 / ChatGPT**：通用大模型，问答灵活、成本低。短板：无 K12 题库沉淀、易幻觉、无学情闭环、不适合直接应试。
- **可汗学院 / 网易有道**：路径化、结构化学习。短板：本地化/应试适配弱、中文 K12 覆盖不足。
- **Question.AI / Gauth**：海外拍照解题 AI，增长快。短板：中文与国内教材适配弱。

### 6.2 能力对比矩阵

| 能力维度 | 市场成熟产品 | 本 Agent 现状 / 缺口 |
|---|---|---|
| 拍照搜题 | 成熟、题库海量 | 需自建/接 OCR+题库，起步弱 |
| 讲解质量 | 模板化讲解 | LLM 可更个性化，但需 RAG 防幻觉 |
| 个性化路径 | 有学情画像 | **缺失**：自适应学习路径 |
| 互动答疑 | 部分一轮答疑 | **缺失**：苏格拉底式追问、实时语音 |
| 视频 / 直播 | 强 | **缺失**：讲解视频、AI 老师形象 |
| 家校 / 教师端 | 有 | **缺失**：教师批改、家校互通 |
| 评测闭环 | 有错题本 | 需补：自动组卷、薄弱点诊断 |
| 手写 / 公式识别 | 较强 | 需补：手写识别、公式 LaTeX |
| 多模态拍照 | 成熟 | 需补：版面切题、多题识别 |
| 合规备案 | 已备案 | **必须补**：教育 App 备案、未成年人保护、内容审核 |
| 私有化部署 | 少 | **我方优势**：可学校/机构本地部署，数据不出域 |

### 6.3 我们的差异化定位（能赢的点）

1. **"灵活讲解" × "应试准确"结合**：用 LLM 做个性化分步讲解，用自建 K12 知识库（pgvector + 图谱）保证考点准确，不裸奔幻觉。
2. **知识图谱驱动记忆与复习**：把零散知识点连成图，自动生成复习路径，这是通用大模型没有的。
3. **轻量、可私有化**：面向学校/培训机构，数据不出域，合规友好，避开与巨头在正版题库上的硬碰。

### 6.4 缺失项清单（按优先级，含"怎么补 + 工作量"）

| # | 缺失项 | 优先级 | 怎么补 | 工作量 |
|---|---|---|---|---|
| 1 | 自适应学习路径 + 学情诊断 | 高 | 采集 `student_logs` 建学生画像，按错点/薄弱知识点推送下一题与复习计划 | 中 |
| 2 | 错题本 + 薄弱点看板 | 高 | App 用 Room 存错题（本地），后端聚合薄弱知识点出看板 | 低–中 |
| 3 | 自动组卷 / 周测 | 中 | 按"知识点 + 难度"从 `questions` 抽题，LLM 排版生成试卷/答案 | 中 |
| 4 | 苏格拉底式互动讲解 | 中 | 多轮追问 Prompt（不直接给答案，逐步引导）+ 语音 TTS/ASR | 中 |
| 5 | 手写与公式识别 | 中 | 接入手写 OCR（如 PaddleOCR 手写模型）+ 公式 LaTeX 识别 | 中 |
| 6 | 视频 / AI 老师形象 | 低 | 数字人 / 讲解视频生成（可选，成本高） | 高 |
| 7 | 家校 / 教师端 | 低→已设计 | 教师批改入口、家长周报推送；基础运营能力见「七·附 Web 管理后台」 | 高（运营后台 MVP 低–中，家校互通仍高） |
| 8 | 合规与内容安全 | 必须 | 教育 App 备案 + 内容审核 + 未成年人保护（硬门槛，P0 就要立项） | 中 |

### 6.5 与路线图对应

- 把 **#2 错题本 + 薄弱点看板** 与 **「七·附 Web 管理后台」MVP（知识点/题库/背诵卡管理 + RBAC）** 提前到 **P2**（与知识图谱同期做，性价比最高）；
- **#1 自适应路径、#3 组卷、#4 互动讲解、#5 手写公式** 放进 **P3**；
- **#8 合规** 作为横切项，从 **P0** 起即并行推进，避免上线即违规。

---

## 七、知识图谱：Apache AGE 建图与 Cypher 查询示例

> 采用 Apache AGE（PostgreSQL 图扩展，Apache-2.0 许可）后，知识图谱拓扑用 Cypher 管理，取代纯 `kg_edges` 关系表。前端仍由 ECharts 力导向图消费 API，无感于存储变化。
>
> **重要前提：原生 PostgreSQL 不支持 Cypher。** Cypher 只能由 Apache AGE 扩展提供（或改用 Neo4j / Memgraph）。动手前务必先确认 AGE 能装上（见 7.8）；装不了就用 7.8 的递归 CTE 回退方案，K12 浅层查询同样够用。

### 7.1 安装与建图
```sql
CREATE EXTENSION age;
SET search_path = ag_catalog, "$user", public;
SELECT create_graph('edu_graph');   -- 创建名为 edu_graph 的图
```

### 7.2 节点标签（= 知识点 / 公式 / 例题 / 易混点）
- `:KnowledgePoint`（kpid, title, subject, grade, tags）
- `:Formula`（fid, name, latex）
- `:Example`（eid, stem）
- `:Confusable`（cid, title）  -- 易混点

### 7.3 关系（边）标签
- `:PREREQUISITE` 前置→后继
- `:CONTAINS` 包含
- `:CONFUSES` 易混
- `:BELONGS_TO` 属于考点
- `:HAS_EXAMPLE` 对应例题

### 7.4 建节点 + 连关系（示例）
```cypher
-- 建一个知识点节点，并挂一条公式
SELECT * FROM cypher('edu_graph', $$
  CREATE (kp:KnowledgePoint {kpid:1001, title:'一元二次方程', subject:'数学', grade:'初三', tags:['必考']})
  CREATE (f:Formula {fid:2001, name:'求根公式', latex:'x=\\frac{-b\\pm\\sqrt{b^2-4ac}}{2a}'})
  CREATE (kp)-[:HAS_FORMULA]->(f)
$$) AS (v agtype);

-- 连「前置」关系：先学「整式乘法」再学「一元二次方程」
SELECT * FROM cypher('edu_graph', $$
  MATCH (a:KnowledgePoint {kpid:999}), (b:KnowledgePoint {kpid:1001})
  CREATE (a)-[:PREREQUISITE]->(b)
$$) AS (e agtype);

-- 连「易混」关系
SELECT * FROM cypher('edu_graph', $$
  MATCH (a:KnowledgePoint {kpid:1001}), (c:Confusable {cid:3001})
  CREATE (a)-[:CONFUSES]->(c)
$$) AS (e agtype);
```

### 7.5 查询：某知识点的 N 跳邻居（前端展开 / 复习路径用）
```cypher
-- 取 kpid=1001 在 1~3 跳内关联的所有知识点，及路径上的关系类型
SELECT * FROM cypher('edu_graph', $$
  MATCH path=(k:KnowledgePoint {kpid:1001})-[*1..3]-(n:KnowledgePoint)
  RETURN n.kpid AS kpid, n.title AS title,
         [r IN relationships(path) | type(r)] AS rels
$$) AS (kpid agtype, title agtype, rels agtype);
```

### 7.6 常用图查询（直接服务业务）
```cypher
-- 前置链：从某知识点回溯完整先修路径（学习顺序）
MATCH path=(:KnowledgePoint {kpid:1001})<-[:PREREQUISITE*]-(pre)
RETURN [n IN nodes(path) | n.title] AS chain;

-- 易混簇：找出与某点易混的所有点（考前易错提醒）
MATCH (k:KnowledgePoint {kpid:1001})-[:CONFUSES]-(c)
RETURN c.title;

-- 推荐下一站：找出「尚未掌握」的后续知识点（结合 student_logs 标记）
MATCH (k:KnowledgePoint {kpid:1001})-[:PREREQUISITE]->(next)
WHERE NOT EXISTS { MATCH (next)<-[:MASTERED]-(:Student {uid:'u123'}) }
RETURN next.title;
```

### 7.7 与原 PG 表的分工
- **内容权威源**：`knowledge_points`（讲解正文、审核状态、创建时间）仍留 PostgreSQL，便于检索 / 报表 / 权限。
- **拓扑**：由 AGE 图承载，构建时经 Kafka `kg_build` 事件**双写**（PG 关系镜像 `kg_edges` + AGE 图），互为备份与降级。
- **前端**：API 调 Cypher 取子图 → 转 ECharts `series:[{type:'graph', layout:'force', ...}]` 渲染力导向图。

---

### 7.8 前提校验与回退方案（装不上 AGE 怎么办）

**先确认 AGE 是否可用**（需 superuser）：
```sql
SELECT version();                                              -- 看 PG 大版本
SELECT name, default_version FROM pg_available_extensions
 WHERE name IN ('age','vector');                               -- age 有结果才能装

-- 装扩展（AGE 版本必须与 PG 大版本匹配，如 AGE 1.5.x 对应 PG 16；**实际已用 AGE 1.8.0 / PG 18 官方容器落地，见 7.9 与七·附**）
CREATE EXTENSION IF NOT EXISTS age;
```

**常见装不上的原因**
- **云托管 PostgreSQL**（阿里云 RDS、AWS RDS 等）有扩展白名单，AGE 通常不在其中 → 只能回退。
- **版本不匹配**：AGE 与 PostgreSQL 大版本强绑定（1.4→PG15、1.5→PG16 等），需按版本选。
- **无 superuser / 无法编译安装**：AGE 常需编译或专用 Docker 镜像，托管环境受限。

**回退方案：纯 SQL 递归 CTE 实现 N 跳（无需 AGE）**
```sql
-- 从 kpid=1001 出发，取 3 跳内可达的知识点（沿 kg_edges 双向遍历，防环）
WITH RECURSIVE walk AS (
  SELECT 1001::bigint AS kpid, 0 AS depth, ARRAY[1001::bigint] AS path
  UNION ALL
  SELECT CASE WHEN e.from_id = w.kpid THEN e.to_id ELSE e.from_id END,
         w.depth + 1,
         w.path || (CASE WHEN e.from_id = w.kpid THEN e.to_id ELSE e.from_id END)
  FROM kg_edges e
  JOIN walk w ON e.from_id = w.kpid OR e.to_id = w.kpid
  WHERE w.depth < 3
    AND NOT (CASE WHEN e.from_id = w.kpid THEN e.to_id ELSE e.from_id END = ANY(w.path))
)
SELECT DISTINCT kpid, MIN(depth) AS depth FROM walk GROUP BY kpid ORDER BY depth;

-- 前置链：只沿 PREREQUISITE 单向回溯（学习顺序）
WITH RECURSIVE pre AS (
  SELECT 1001::bigint AS kpid, 0 AS depth
  UNION ALL
  SELECT e.from_id, p.depth + 1
  FROM kg_edges e JOIN pre p ON e.to_id = p.kpid
  WHERE e.rel = 'PREREQUISITE' AND p.depth < 10
)
SELECT kpid, depth FROM pre ORDER BY depth;
```

**决策**：装得上 AGE → 用 Cypher（表达力强、深层遍历快）；装不上 → 递归 CTE 足够覆盖 K12 的 1–3 跳查询，等真到百万级边再迁 Neo4j。

---

### 7.9 版本兼容矩阵与当前环境结论（实测 PG 11.2）

当前环境：`PostgreSQL 11.2 (Debian 11.2-1.pgdg90+1)` → 即 **PG 11 + Debian 9(stretch)**。

| 能力 | 最低 PG 版本 | PG 11.2 能否用 | 说明 |
|---|---|---|---|
| **Cypher（Apache AGE）** | PG 11 | ✅ **可以** | AGE **1.5.0** 有专门的 PostgreSQL 11 发布包（需源码编译） |
| **pgvector 向量检索** | PG **13+** | ❌ **不行** | 官方要求 13+；0.8.0 起已停止支持 PG 12 |
| 递归 CTE 图遍历（7.8） | PG 8.4+ | ✅ 可以 | 无扩展依赖，PG 11 完全支持 |
| Kafka / 业务表 / JSONB | 无关 | ✅ 可以 | 不受影响 |
| **Cypher（Apache AGE，实测容器）** | PG 11 | ✅ **已落地** | 已在 `124.221.253.137` 起 `apache/age:latest` 容器（**PG 18.6 + AGE 1.8.0**，端口 5444），Cypher 实测可用；详见七·附 4·附 |

**结论与建议**

1. **知识图谱（Cypher）能落地**：PG 11.2 装 **AGE 1.5.0** 即可用 Cypher（需源码编译，见下）；**更省事的已验证路径**是直接在 `124.221.253.137` 起官方 `apache/age:latest` 容器（PG18.6 + AGE 1.8.0，端口 5444，镜像已存阿里云 `mis_ser/apache-age`），免去编译与 Debian 9 EOL 之坑，详见七·附 4·附。源码编译路径：
   ```bash
   sudo apt-get install build-essential libreadline-dev zlib1g-dev flex bison postgresql-server-dev-11
   cd /tmp/apache-age-1.5.0
   make PG_CONFIG=/usr/lib/postgresql/11/bin/pg_config
   sudo make PG_CONFIG=/usr/lib/postgresql/11/bin/pg_config install
   # 之后在库中：CREATE EXTENSION age; LOAD 'age';
   #            SET search_path = ag_catalog, "$user", public;
   ```
2. **向量检索**：原结论"PG 11 装不了、须另起 PG16"仍成立；但**已就绪的 PG18 AGE 容器（端口 5444）本身即 PG 18，可直接 `CREATE EXTENSION vector;` 挂载 pgvector**，向量检索与知识图谱现已**同库同源**，无需再单独起 PG16。仅在需要独立向量库或更大规模时，才外挂：
   - 单独起一个 **PostgreSQL 16** 实例（或 `pgvector/pgvector:pg16` 容器）专做向量库，与老库并存，业务库不动；
   - 或外挂 **Qdrant / Milvus / Chroma** 等向量数据库；
   - 或先用「关键词/标签检索 + 递归 CTE」过渡（无语义召回，触类旁通效果打折）。
3. **强烈建议整体升级到 PostgreSQL 16**：
   - **PG 11 已于 2023-11 EOL**，无安全补丁；**Debian 9 同样 EOL**；
   - 第九节已把「教育 App 合规 / 未成年人保护」列为**硬门槛**，跑在 EOL 版本上有合规风险；
   - 升级后 pgvector 与 AGE 1.6 可一步到位，不必外挂向量库。
4. **升级路径**：`pg_upgrade`（11→16，原地升级，停机时间短）或 `pg_dump` 逻辑导出 → 新实例 restore（最稳，跨版本/跨机皆可）。

---

### 7.10 实测补充：`pg_available_extensions` 查不到 age / vector 怎么办

实测执行 `SELECT name, default_version FROM pg_available_extensions WHERE name IN ('age','vector');` **返回空**，含义如下：

- 该视图列出的是「**当前服务器上已具备安装条件**」的扩展。返回空 = 服务器**还没有**这两个扩展的文件，**不等于版本不支持**。
- **`vector`（pgvector）**：PG 11 上**永远装不上**（现版本要求 PG 13+；极早期 0.1.x 曾支持 PG11，但已停维、无 HNSW，不建议生产）。此项为空是**永久性的**。
- **`age`（Apache AGE）**：**从不随 PostgreSQL 自带**，必须下载源码编译安装，装好后才会出现在该视图。此项为空是**正常且可解决的**。

**安装后如何验证**
```sql
SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name IN ('age','vector');
SELECT extname, extversion FROM pg_extension ORDER BY extname;   -- 已启用的扩展
SELECT name FROM pg_available_extensions ORDER BY name LIMIT 50; -- 看看目前到底有哪些
```

**在 PG 11 + Debian 9 上硬装 AGE 的现实阻碍**
- Debian 9(stretch) **已 EOL**，官方 apt 源已归档，`apt-get install postgresql-server-dev-11` 很可能失败 → 需把 sources.list 指向 `archive.debian.org`。
- 装完还需在 `postgresql.conf` 设 `shared_preload_libraries = 'age'` 并**重启实例**。

**因此更推荐（方案 A）：不要折腾 EOL 老系统，新起 PG 16 承载向量+图谱**
```bash
# 起一个带 pgvector 的 PG 16（5433 端口，避免与老库 5432 冲突）
docker run --name pg16 --restart unless-stopped \
  -e POSTGRES_PASSWORD=你的密码 -p 5433:5432 \
  -v pgdata16:/var/lib/postgresql/data -d pgvector/pgvector:pg16

# 老 PG 11 继续跑业务数据；向量检索 + 知识图谱全部走新库
# 需要 Cypher 时再在新库编译 AGE 1.6(PG16)：
#   docker exec -it pg16 bash
#   apt-get update && apt-get install -y build-essential flex bison postgresql-server-dev-16
#   下载 apache-age-1.6.0 源码 → make && make install
```
- **收益**：避开 Debian 9 归档源与 PG 11 EOL 的双重坑；pgvector 与 AGE 一次到位；老库零改动、风险最低。
- **代价**：多维护一个实例（可用 Docker 或本机新装 PG 16）。

---

## 七·附、Web 管理后台（运营 / 教研 / 教师端）

> 原方案将"家校 / 教师端"列为 6.4 缺失项 #7（低优先级）。现将管理后台作为**正式子系统**设计：它不直接服务学生，而是给**教研、运营、教师、管理员**用的内部工作台，与安卓端共享同一套 Agent 后端 REST API。

### 1) 定位与边界
- **不重复造前端讲解 UI**：管理后台不实现解题/讲解，只做"内容生产 + 配置 + 监控 + 审核"。
- **共享后端**：复用第五节 FastAPI 的 `/solve`、`/solve_image`、Kafka topic，后台通过同域 REST + 管理专用接口（前缀 `/admin/*`）与 Agent 服务通信。
- **鉴权隔离**：学生端（安卓）走 API Gateway 用户态 JWT；后台走独立管理员账号 + RBAC（角色：教研 / 教师 / 运营 / 超级管理员），与企业 SSO（可选）对接。

### 2) 核心能力模块
| 模块 | 功能 | 数据来源 |
|---|---|---|
| 知识点管理 | 知识点 / 公式 / 易混点 CRUD、审核状态流转、打标签(`必考/易错/drill`) | `knowledge_points` / AGE `edu_graph` |
| 题库管理 | 题目录入、解析编辑、难度标注、批量导入（触发 `course_kb_ingest`） | `questions` / `embeddings` |
| 知识图谱可视化 | 力导向图展示、点击展开 N 跳、按年级/学科筛选、人工补边（双写 `kg_edges` + AGE） | AGE Cypher `edu_graph` |
| 背诵卡管理 | 刻板知识点卡（公式/定义/默写）增删改、刷 Redis 缓存 | `drill_cards` |
| 学情看板 | 薄弱知识点聚合、错题 TopN、班级/学生维度报表 | `student_logs` + 聚合视图 |
| 内容审核 | 题目 / 解析 / LLM 输出的合规标注、下架、人工复核队列 | 6.4 #8 合规项 |
| 运营配置 | 模型开关、RAG 召回参数、限流配额、公告 | 配置表 / 环境变量 |
| 系统监控 | Kafka 堆积、PG/Age 健康、接口延迟、Token 消耗 | Prometheus/Grafana（可选） |

### 3) 推荐技术栈
- **框架**：Vue 3 + Element Plus（或 React + Ant Design Pro），管理后台生态成熟、表格/表单组件全。
- **构建**：Vite；与安卓端共用设计 token。
- **通信**：Axios 封装，统一错误处理 + 401 跳登录；大文件（批量导入）走分片上传。
- **图表**：ECharts（与前端图谱共用，力导向图直接复用安卓端渲染逻辑）。
- **部署**：静态产物 `nginx` 托管；反向代理到 API Gateway，与安卓端同域或子域（`admin.xxx.com`）。

### 4) 与知识图谱的关系（关键）
后台的"图谱可视化 + 人工补边"是 AGE 图的**生产侧入口**之一：教研在后台画的点/边，经 `kg_build` 事件双写 `kg_edges`(关系镜像) 与 AGE `edu_graph`(图)，学生端查询只读 AGE，保证单一拓扑来源、可降级。

### 4·附、与当前实际部署环境的对接
> 本节把上文抽象的"AGE 图"落到本方案已验证的真实环境，供后台联调与运维直接参照。

- **已就绪的 AGE 实例**（2026-10-20 在 `124.221.253.137` 实测跑通）：
  - 镜像：`registry.cn-shanghai.aliyuncs.com/mis_ser/apache-age:latest`（PostgreSQL **18.6** + Apache AGE **1.8.0**，487MB，已 push 至阿里云 ACR `mis_ser/apache-age`）。
  - 部署方式：Docker Compose 服务 `age`，端口映射 **`5444:5432`**（避开同机 postgis 占用的 5432），数据卷 `/data/age`，`shared_preload_libraries=age` 已写死。
  - 接入信息：host `124.221.253.137:5444`，库 `gis`，用户 `postgres`，密码取 `/opt/.env` 的 `POSTGRES_PASSWORD`；**每次新连接须先** `LOAD 'age'; SET search_path = ag_catalog, "$user", public;` 才能调 `cypher()`。
  - 建图命令（已验证）：`SELECT create_graph('edu_graph');` 后，Cypher 读写经 `cypher('edu_graph', $$ ... $$)` 执行。
- **后台图谱模块连线方式**：
  - 后端（FastAPI）持常驻连接或连接池，在 `psycopg` 连接初始化钩子里执行 `LOAD 'age'` + `SET search_path`，免去每次手工设置；
  - 后台前端经 `/admin/graph/*` 调后端 → 后端发 Cypher 取子图 → 转 ECharts `series:[{type:'graph',layout:'force'}]` 渲染；人工补边写回走 `kg_build` 双写（见 §4）。
- **环境注意**：
  - 该 AGE 容器为 PG18，可直接 `CREATE EXTENSION vector;` 挂 pgvector（老 postgis 的 PG11 装不了，此处无此限制）——向量检索与图谱现已**同库同源**，比原 §7.9 假设的"另起 PG16"更简单。
  - 单容器无副本，生产需补：定期 `pg_dump` `/data/age`、或挂 ACR 镜像做重建基线、反向代理加 TLS 与访问白名单。
  - 当前 `age` 容器 `restart: unless-stopped`；若整机重启，Compose 会自拉，`/data/age` 持久化数据不丢。

### 5) 工作量与优先级建议
- MVP（P2 同期）：知识点管理 + 题库管理 + 背诵卡管理 + 登录/RBAC —— **低–中**，性价比高，与知识图谱 P2 复用数据结构。
- 增强（P3）：图谱可视化编辑、学情看板、内容审核队列 —— **中**。
- 合规联动：审核模块与 6.4 #8 合规项绑定，P0 起预留字段、P3 完善工作流。

---

## 八、落地路线图（建议分 4 期）

- **P0（2–3 周）**：FastAPI Agent + LLM 解答讲解 + PostgreSQL/pgvector 建表 + 安卓基础壳（文本输入→答案）。
- **P1（3–4 周）**：Kafka 接入知识库批量入库 + RAG + 触类旁通（检索式）+ 拍照 OCR/VLM。
- **P2（3–4 周）**：知识图谱构建+可视化 + 刻板知识点背诵卡 + 错题本 + **Web 管理后台 MVP（知识点/题库/背诵卡管理 + RBAC，见七·附）**。
- **P3（持续）**：自适应路径、组卷、互动讲解、合规备案、市场差异化补齐。

---

## 九、风险与注意
- **幻觉**：所有解答必须 RAG 兜底 + 关键答案人工/规则校验。
- **版权**：题库来源需合法授权，避免爬取侵权。
- **成本**：LLM + embedding 调用按量计费，需缓存高频题。
- **合规**：K12 教育 App 需 ICP/备案、未成年人保护、内容审核。

---

## 十、技术栈与语言分工（更新）

> 决策背景：抽取独立的**网关层**后，各层语言可独立选择。本方案采用 **Go 网关 + Python 双后端 + Kotlin 客户端** 的组合（详见对话演进与 `应用拆分与开发分期.md` / `应用全景图.html`）。

### 1) 分层与语言决策
| 层 | 语言 / 框架 | 选型理由 |
|---|---|---|
| 网关层 | **Go**（自研轻网关 / 或 APISIX） | 边缘高并发、单二进制易部署；鉴权·限流·熔断·路由·审计 |
| Agent 后端 | **Python / FastAPI** | AI/LLM/RAG 生态最强，方案既定 |
| Web 管理后台后端 | **Python / FastAPI** | 与 Agent 同栈，复用模型/SDK/鉴权，降低语言种类 |
| Web 管理后台前端 | Vue 3 + Element Plus / React + Ant Design | 表格/表单组件成熟（见七·附） |
| Android 客户端 | Kotlin + Jetpack Compose | 原生体验，Retrofit 调 API |
| 数据层 | Kafka + PostgreSQL(pgvector / AGE) | 异步管道 + 业务/向量/图谱同源 |

### 2) 为什么这样分
- 网关与后端**语言无关**：Go 网关只做转发与边界防护，后面 Python 服务无感。
- Web 与 Agent **同栈**（均 FastAPI），服务端仅 **Go + Python 两种运行时**，比引入 Java/Go 写后台更省。
- Web 后台是 B 端，流量远小于 C 端 App，**Python 性能足够**；高并发边缘交给 Go 网关扛。

### 3) 网关层请求路由表
| 入口路径 | 转发目标 | 说明 |
|---|---|---|
| `/api/app/*`、`/solve`、`/solve_image` | Agent 后端（Python） | C 端解题 / 讲解 / 拍照 |
| `/api/admin/*` | Web 后端服务（Python） | B 端运营 / 学情 / 组卷 / 审核 |
| 文件上传（multipart） | 网关 → 对象存储 → 对应服务 | 图片先到网关再转交 |
| 静态资源 | 前端产物（nginx） | Web 后台静态托管 |

### 4) 部署与合规
- 全容器化（Docker），**网关为唯一公网入口**，后端服务不直接暴露。
- 审计日志、访问控制、内容安全校验在**网关层统一落盘**，直接承接 6.4 #8 合规缺失项。
- 私有化部署，数据不出域（契合差异化定位）。
- Go 网关编译为单二进制；Python 服务用容器 + 依赖隔离（uv/venv）运行。

### 5) 延伸：若不想引入 Go
- 可接受产品化网关 **APISIX / Kong**（配置为主，深度插件用 Lua），几乎零自研代码，合规插件齐全。
- 若团队 Java 为主、想零新语言，可改用 **Spring Cloud Gateway（Java）** 复用 aas-api 的 Spring 安全体系。


---

# 附录A：Web管理后台功能设计方案

# Web 管理后台功能设计方案（补充版）

> 目的：原《课程教育 Agent 系统设计方案》将 Web 管理后台定位为"可选"，仅覆盖教研内容维护。
> 本补充基于第六节"与市场相比缺失内容"的分析，将 Web 后台升级为
> **教研 + 教务 + 学情 + 合规** 一体化的运营后台，补齐与市场成熟产品的差距。
> 每一节标注对应的"缺失项编号"（对应原文档 6.4 缺失清单）。

---

## 一、后台定位

从"轻量教研后台"升级为**运营中枢**：
- 教研侧：知识库、图谱、背诵卡的维护与审核（原已隐含）。
- 教务侧：班级/学生、批改、组卷、家校互通（原缺失 #3、#7）。
- 学情侧：看板、薄弱点诊断、自适应路径（原缺失 #1、#2）。
- 合规侧：内容审核、未成年人保护、备案跟踪（原缺失 #8，P0）。

---

## 二、角色与权限（RBAC）

| 角色 | 权限范围 |
|---|---|
| 超级管理员 | 系统配置、用户/角色管理、合规与备案、全量数据 |
| 教研员 | 知识库导入、图谱审核补全、背诵卡维护、组卷、内容审核 |
| 教师 | 班级/学生管理、作业批改、布置练习、查看本班学情 |
| 家长 | 查看子女周报与错题（只读） |
| 学生 | 不登录 Web，仅使用 Android 端 |

> 学生画像与权限数据落在 PostgreSQL，认证走 API Gateway 统一鉴权。

---

## 三、功能模块（一一对应缺失项）

### 模块 1 · 教研内容管理（原已隐含）
- 知识库批量导入：教研/Excel/爬虫 → Kafka `course_kb_ingest` → 清洗切分 → embedding → 写 PG。
- 知识图谱审核补全工作台：LLM 抽取三元组 → 人工审核 → 写 `kg_edges` / AGE 图。
- 背诵卡 / 刻板知识点维护 → 写 Kafka `drill_sync` → 落 `drill_cards` 并刷 Redis（App 离线可背）。

### 模块 2 · 学情看板与薄弱点诊断（缺失 #1、#2）
- 学生画像：正确率、各知识点掌握度（来自 `student_logs`）。
- 薄弱点看板：按 `subject / grade` 聚合错题与低掌握度知识点，热力图展示。
- 自适应学习路径配置：设置"推荐下一题 / 复习计划"的触发规则（命中薄弱点即推送）。

### 模块 3 · 错题本管理（缺失 #2）
- 后端聚合错题（消费 `student_event` 中 `action=wrong`）。
- 错题检索、归因标签、导出，供教师讲评与组卷复用。

### 模块 4 · 自动组卷 / 周测（缺失 #3）
- 按"知识点 + 难度 + 题量 + 题型"从 `questions` 抽题。
- LLM 排版生成试卷 + 答案，支持导出 PDF / 推送班级。
- 周测计划编排与定时发布。

### 模块 5 · 教师端：批改与布置（缺失 #7）
- 作业 / 主观题人工批改入口（Agent 给参考答案，教师定最终分）。
- 班级、学生管理，练习布置与回收。
- 家校互通消息（通知、提醒）。

### 模块 6 · 家长端：周报（缺失 #7）
- 家长周报生成与推送：掌握度、错题 TOP、学习建议。
- 只读视图，关联子女账号。

### 模块 7 · 互动讲解策略配置（缺失 #4）
- 苏格拉底式追问 Prompt 模板管理（不直接给答案、逐步引导）。
- 语音 TTS / ASR 开关与音色配置（供 Android 端调用）。

### 模块 8 · 多模态 / 识别管理（缺失 #5）
- 手写 OCR、公式 LaTeX 识别模型选型与开关。
- 识别校准样例库（典型错题图、公式图，用于回归校验）。

### 模块 9 · 合规与内容安全（缺失 #8，P0 横切）
- 内容审核队列：题目 / 解析 / 图谱人工复核，留痕可追溯。
- 未成年人保护设置（使用时长、防沉迷、隐私脱敏）。
- 教育 App 备案状态跟踪、敏感词与合规规则配置。

### 模块 10 · 视频 / AI 老师（缺失 #6，低优先级，预留）
- 数字人 / 讲解视频生成任务管理（预留入口，成本高、后续迭代）。

---

## 四、与整体架构的数据流

```
Web 管理后台
   │
   ├─ 写内容 ──────────► Kafka: course_kb_ingest / drill_sync / kg_build
   │                         │
   │                         ▼
   │                      Consumer → PostgreSQL（知识库 / 图谱 / 背诵卡）
   │
   ├─ 读学情 ──────────► 直读 PostgreSQL: student_logs / questions / student_profiles
   │
   ├─ 组卷 / 讲解 ──────► 调 Agent 后端 LLM 服务（与 Android 共用同一 Orchestrator）
   │
   └─ 合规审核 ────────► audit_queue（审核留痕）→ PostgreSQL
```

要点：Web 后台**复用** Android 端的同一套 Agent 编排层与 LLM / 检索服务，仅新增"运营/教务"专属页面与数据表，不重复建设推理能力。

---

## 五、建议补充的数据表（PostgreSQL）

| 表 | 作用 |
|---|---|
| `admin_users` / `roles` | 后台账号与 RBAC |
| `classes` / `class_students` | 班级与学生归属 |
| `wrong_questions` | 错题聚合（消费 `student_event` 生成） |
| `exam_papers` / `exam_items` | 组卷结果与题目 |
| `audit_queue` | 合规内容审核队列 |
| `parent_reports` | 家长周报记录 |
| `learning_path_rules` | 自适应路径推荐规则 |

---

## 六、优先级与路线图对应

| 阶段 | 纳入的 Web 后台模块 |
|---|---|
| **P0** | 模块 9 合规（RBAC、内容审核、未成年人保护）、`admin_users` |
| **P2** | 模块 2 学情看板 + 模块 3 错题本（与 Android 知识图谱同期，性价比最高） |
| **P3** | 模块 4 组卷、模块 5 教师端、模块 7 互动配置、模块 8 多模态管理 |
| 后续 | 模块 6 家长周报、模块 10 视频 / AI 老师 |

> 与原文 6.5 建议一致：错题本/看板提前到 P2，自适应路径/组卷/互动/手写放进 P3，合规从 P0 并行。

---

## 七、小结

补齐后，Web 管理后台从"可选教研工具"变为覆盖 **教研维护、学情诊断、教务批改、家校互通、合规审核** 的完整运营平台，
直接回应了原文档第六节列出的 8 项市场缺失能力，且全部复用既有 Agent / Kafka / PostgreSQL 架构，无重复建设。
私有化部署（数据不出域）仍是我方相对巨头的核心差异化优势。


---

# 附录B：应用拆分与开发分期

# 应用拆分与开发分期

> 基于《课程教育 Agent 系统设计方案》的应用产品拆解。
> 结论：这套系统可拆出 **2 个端到端产品应用 + 1 个共享后端服务**，再加 **1 个可选跨端**，最多约 **4 个可开发的"应用"**。
> 它们复用同一套 Agent 后端 + Kafka + PostgreSQL，是一套内核、多端复用，而非从零并行重建。

---

## 一、可开发应用清单

### 1. 学生端 App（C 端，核心产品）
- **平台/技术**：Android（Kotlin + Jetpack Compose），或 Flutter 跨端
- **面向用户**：中小学生
- **功能（六大能力页）**：文本/语音输入、拍照识题、讲解展示（公式渲染）、触类旁通、知识图谱可视化、背诵卡/错题本
- **离线**：Room 本地缓存背诵卡与错题本
- **配套图**：`安卓功能架构图.html`

### 2. Web 管理后台（B 端，运营中枢）
- **平台/技术**：Web（前后端不限，复用后端 API）
- **面向用户**：教研员、教师、家长、管理员（RBAC 四角色）
- **功能**：教研内容管理、学情看板、错题本、自动组卷、教师批改、家长周报、互动配置、多模态管理、合规审核
- **配套文档**：`Web管理后台功能设计方案.md` + `Web管理后台功能结构图.html`

### 3. Agent 后端服务（AI 核心，被两端共用）
- **平台/技术**：FastAPI（Python）/ 或 Spring（沿用 aas-api 风格）
- **定位**：非终端用户"应用"，但是**必须独立开发部署的服务**，提供 `/solve`、`/solve_image` 等 API，内含 Orchestrator、RAG、LLM、OCR、图谱查询
- **价值**：学生 App 与 Web 后台都调它，是系统的"大脑"

### 4.（可选）微信小程序 / Flutter 跨端
- **定位**：替代或补充 Android，低成本覆盖 iOS 与微信生态
- **取舍**：文档已提 Flutter 跨端路线；小程序适合轻量搜题引流，但备案/合规要求更高

---

## 二、依赖关系（决定开发顺序）

```
        ┌─────────────┐      ┌──────────────┐
        │ 学生端 App   │      │ Web 管理后台  │
        └──────┬──────┘      └──────┬───────┘
               │   HTTPS / JSON     │
               └─────────┬──────────┘
                         ▼
                 ┌──────────────────┐
                 │ Agent 后端服务    │  ← 先建，两端都依赖
                 │ (Orchestrator)   │
                 └────────┬─────────┘
                          ▼
              Kafka + PostgreSQL(图谱/向量/业务)
```

**关键点**：第 3 项（后端服务）是地基，第 1、2 项都依赖它；第 4 项是可选项，可在 App 验证后再决定。

---

## 三、开发分期（呼应原文档 P0–P3）

| 阶段 | 开发的应用 / 模块 |
|---|---|
| **P0** | Agent 后端（解题+讲解）+ 学生端 App 基础壳 + 合规(RBAC) |
| **P1** | 后端补全 RAG / 触类旁通 / 拍照；App 补全拍照与图谱页 |
| **P2** | Web 后台：学情看板 + 错题本（性价比最高） |
| **P3** | Web 后台：组卷 / 教师端 / 互动 / 多模态；可选小程序 |

---

## 四、一句话总结

> 可开发的应用 = **学生 App + Web 管理后台（含教师/家长角色）+ Agent 后端服务**，可选再加 **小程序**，共 3–4 个。
> 后端服务是共享内核，两个端产品在其上生长，建议**先后端、后双端、再补可选跨端**。
