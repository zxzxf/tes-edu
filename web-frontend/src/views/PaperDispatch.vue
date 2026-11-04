<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">试卷下发</h2>
    <p class="hint">
      管理本租户已生成的试卷：<b>答案校验 → 发布 → 下发给学生</b>。
      只有已发布的卷能下发，学生端立刻可见；生成新卷请到左侧「自动组卷」。
    </p>

    <div class="tes-card form toolbar">
      <div class="foot">
        <button class="ghost" :disabled="busy" @click="loadPapers">刷新试卷列表</button>
        <button class="ghost" @click="goCompose">去自动组卷</button>
        <span v-if="msg" class="msg">{{ msg }}</span>
      </div>
      <p v-if="error" class="err">{{ error }}</p>
    </div>

    <!-- 试卷列表置于页首：不再需要往下滚三四屏找下发 -->
    <div v-if="papers.length" class="papers">
      <div
        v-for="p in papers"
        :key="p.id"
        class="tes-card paper"
        :class="{ active: paper && paper.id === p.id }"
        @click="selectPaper(p.id)"
      >
        <div class="p-head">
          <b>{{ p.title }}</b>
          <span class="tes-pill" :class="p.published ? 'success' : 'warn'">
            {{ p.published ? '已发布' : '未发布' }}
          </span>
          <span class="grow"></span>
          <span class="meta">
            #{{ p.id }} · {{ p.question_count }} 题 · 自动判 {{ p.auto_gradeable_count }}
            <span v-if="p.manual_required_count"> · 需人工 {{ p.manual_required_count }}</span>
          </span>
        </div>
      </div>
    </div>
    <p v-else class="tes-empty">本租户还没有试卷，先到「自动组卷」生成一份。</p>

    <!-- 当前试卷：发布 / 下发 / 答案校验 / 题目 -->
    <div v-if="paper" class="tes-card detail">
      <div class="d-head">
        <b>{{ paper.title }}</b>
        <span class="tes-pill">{{ paperTypeLabel(paper.paper_type) }}</span>
        <span class="grow"></span>
        <span class="meta">题量 {{ paper.question_count }}</span>
      </div>

      <div class="stat">
        <span class="s ok">可自动判分 {{ paper.auto_gradeable_count }}</span>
        <span v-if="paper.manual_required_count" class="s warn">
          需人工评定 {{ paper.manual_required_count }}
        </span>
      </div>

      <p v-if="msg" class="msg">{{ msg }}</p>
      <p v-if="error" class="err">{{ error }}</p>

      <div class="actions">
        <button class="ghost" :disabled="busy" @click="copyCurrent">复制为新卷</button>
        <button v-if="!paper.published" class="tes-btn-primary" :disabled="busy" @click="doPublish">
          校验并发布
        </button>
        <template v-else>
          <span class="ok-tip">已发布</span>
          <label class="inline">
            <span>下发到</span>
            <select v-model="assign.target_type">
              <option value="grade">年级段</option>
              <option value="student">指定学生</option>
            </select>
          </label>
          <label v-if="assign.target_type === 'grade'" class="inline">
            <input v-model="assign.target_grade" placeholder="高三" />
          </label>
          <label v-else class="inline">
            <input v-model="assign.target_student" placeholder="学生ID，如 student@tes.edu" />
          </label>
          <label class="inline">
            <span>截止</span>
            <input v-model="assign.due_at" type="date" />
          </label>
          <button class="tes-btn-primary" :disabled="busy" @click="doAssign">下发</button>
        </template>
      </div>

      <!-- 答案校验（M2-9 / G1） -->
      <div class="ac-box">
        <div class="ac-head">
          <b>答案校验</b>
          <span class="ac-tip">答案是判分唯一依据：没通过校验的答案不许发布</span>
          <span class="grow"></span>
          <template v-if="checkSummary">
            <span class="tes-pill success">通过 {{ checkSummary.passed }}/{{ checkSummary.total }}</span>
            <span v-if="checkSummary.conflict" class="tes-pill danger">冲突 {{ checkSummary.conflict }}</span>
            <span v-if="checkSummary.unverified" class="tes-pill warn">
              待确认 {{ checkSummary.unverified }}
            </span>
          </template>
          <button class="ghost sm" :disabled="busy" @click="refreshChecks(true)">重新校验</button>
          <button
            v-if="!paper.published && checkSummary && !checkSummary.ok"
            class="ghost sm"
            :disabled="busy"
            @click="confirmChecks([])"
          >
            全部确认
          </button>
        </div>

        <div v-if="checks.length" class="ac-list">
          <div v-for="c in checks" :key="c.seq" class="ac-row" :class="acClass(c)">
            <span class="ac-seq">{{ c.seq }}</span>
            <span class="tes-pill" :class="acPill(c)">{{ c.status_label }}</span>
            <div class="ac-body">
              <p class="ac-detail">{{ c.detail }}</p>
              <p v-if="c.secondary_answer" class="ac-src">
                第二来源（{{ c.secondary_source }}）：{{ c.secondary_answer }}　·　
                卷面答案：{{ c.answer }}
              </p>
              <p v-if="c.llm_answer" class="ac-llm">
                模型建议（仅供参考，不作依据）：{{ c.llm_answer }}
                <span v-if="c.llm_agree === false" class="ac-llm-bad">与卷面不一致</span>
              </p>
              <p v-if="c.confirmed_by" class="ac-ok">
                已由 {{ c.confirmed_by }} 确认
                <template v-if="c.confirmed_comment">：{{ c.confirmed_comment }}</template>
              </p>
            </div>
            <div v-if="!paper.published && !acPassing(c)" class="ac-act">
              <input v-model="fixValues[c.seq]" :placeholder="'修正答案（当前 ' + (c.answer || '—') + '）'" />
              <button class="tes-btn-primary sm" :disabled="busy" @click="confirmChecks([c.seq])">
                确认
              </button>
            </div>
          </div>
        </div>
        <p v-else class="ac-empty">暂无校验记录，点「重新校验」。</p>
      </div>

      <!-- 发布拦截明细 -->
      <div v-if="violations.length" class="viol">
        <p class="viol-t">发布被拦截（{{ violations.length }} 项）：</p>
        <ul>
          <li v-for="(v, i) in violations" :key="i">
            <b>{{ v.seq ? '第 ' + v.seq + ' 题' : '整卷' }}</b> · {{ v.message }}
          </li>
        </ul>
      </div>

      <!-- M3-1/M3-4 本卷知识点图谱与整卷解析（随卷从自动组卷页一起拆出） -->
      <div class="actions">
        <button class="ghost sm" :disabled="busy" @click="loadPaperGraph">看本卷知识点图谱</button>
        <button class="ghost sm" :disabled="busy" @click="loadPaperSolutions">
          {{ solutions.length ? '刷新整卷解析' : '生成整卷解析（M3-1）' }}
        </button>
      </div>
      <div v-if="paperGraph" class="meta">
        本卷知识点 {{ paperGraph.paper_kp_count }} 个 · 节点 {{ (paperGraph.nodes || []).length }} ·
        边 {{ (paperGraph.edges || []).length }}
        <span v-if="paperGraph.unresolved_kps && paperGraph.unresolved_kps.length">
          · 未识别：{{ paperGraph.unresolved_kps.join('、') }}
        </span>
        <ul class="kg-list">
          <li v-for="n in (paperGraph.nodes || []).slice(0, 20)" :key="n.code || n.id">
            {{ n.title || n.name || n.code }}
            <span v-if="n.is_core" class="tes-pill success">本卷核心</span>
            <span v-if="n.is_prereq" class="tes-pill info">前驱</span>
            <span v-if="n.is_successor" class="tes-pill warn">后继</span>
          </li>
        </ul>
      </div>
      <ul v-if="solutions.length" class="kg-list">
        <li v-for="s in solutions" :key="s.question_id">
          第 {{ s.question_id }} 题：
          <span v-if="s.solution_steps">{{ s.solution_steps.answer || '已生成解析' }}</span>
          <span v-else class="err">{{ s.reason || '未生成' }}</span>
        </li>
      </ul>

      <!-- 题目与卷面答案快照 -->
      <ol class="qs">
        <li v-for="q in paper.questions" :key="q.seq">
          <div class="q-head">
            <span class="q-no">{{ q.seq }}</span>
            <span class="tes-pill">{{ qtypeLabel(q.qtype) }}</span>
            <span v-if="q.multi" class="tes-pill">多选</span>
            <span class="grow"></span>
            <span class="q-ans">答案：{{ q.answer || '—' }}</span>
          </div>
          <p class="stem">{{ q.stem }}</p>
          <ul v-if="q.options && q.options.length" class="opts">
            <li v-for="o in q.options" :key="o.label"><b>{{ o.label }}.</b> {{ o.content }}</li>
          </ul>
          <p v-if="q.answer_values && q.answer_values.length" class="vals">
            可接受值：<span v-for="(v, i) in q.answer_values" :key="i" class="chip">{{ v }}</span>
          </p>
        </li>
      </ol>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  assignPaper,
  confirmAnswerChecks,
  copyPaper,
  getAnswerChecks,
  getPaper,
  getPaperGraph,
  getPaperSolutions,
  listPapers,
  publishPaper,
  runAnswerChecks
} from '../api/admin'

const route = useRoute()
const router = useRouter()

const papers = ref([])
const paper = ref(null)
const violations = ref([])
const checks = ref([])
const checkSummary = ref(null)
const fixValues = ref({})
const busy = ref(false)
const msg = ref('')
const error = ref('')
const assign = ref({ target_type: 'grade', target_grade: '高三', target_student: '', due_at: '' })
const paperGraph = ref(null)
const solutions = ref([])

function errText(e) {
  const d = e && e.response && e.response.data && e.response.data.detail
  if (typeof d === 'string') return d
  if (d && d.message) return d.message
  return (e && e.message) || '请求失败'
}

async function goCompose() {
  router.push('/exam')
}

async function loadPapers() {
  error.value = ''
  try {
    papers.value = await listPapers()
  } catch (e) {
    error.value = errText(e)
  }
}

async function selectPaper(id) {
  error.value = ''
  msg.value = ''
  violations.value = []
  checks.value = []
  checkSummary.value = null
  paperGraph.value = null
  solutions.value = []
  try {
    paper.value = await getPaper(id)
    await refreshChecks()
  } catch (e) {
    error.value = errText(e)
  }
}

const PASSING = ['verified', 'mechanical_pass', 'teacher_confirmed']
const acPassing = (c) => PASSING.includes(c.status)
const acPill = (c) =>
  c.status === 'conflict' ? 'danger' : acPassing(c) ? 'success' : c.suspect ? 'warn' : 'info'
const acClass = (c) => ({ bad: c.status === 'conflict', todo: !acPassing(c) && c.status !== 'conflict' })

function applyChecks(data) {
  checks.value = (data && data.items) || []
  checkSummary.value = (data && data.summary) || null
  fixValues.value = {}
}

async function refreshChecks(refresh = false) {
  if (!paper.value) return
  try {
    const data = refresh
      ? await runAnswerChecks(paper.value.id)
      : await getAnswerChecks(paper.value.id)
    applyChecks(data)
  } catch (e) {
    error.value = errText(e)
  }
}

async function confirmChecks(seqs) {
  if (!paper.value) return
  busy.value = true
  error.value = ''
  try {
    const answers = {}
    for (const s of seqs) {
      const v = (fixValues.value[s] || '').trim()
      if (v) answers[String(s)] = v
    }
    const data = await confirmAnswerChecks(paper.value.id, {
      seqs: seqs || [],
      answers,
      comment: ''
    })
    applyChecks(data)
    if (data.summary && data.summary.ok) msg.value = '答案校验全部通过，可以发布'
    else msg.value = `已确认 ${seqs.length || '全部'} 道；仍有 ${data.summary.unverified + data.summary.conflict} 道待处理`
    if (answers && Object.keys(answers).length) paper.value = await getPaper(paper.value.id)
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = false
  }
}

async function copyCurrent() {
  if (!paper.value) return
  busy.value = true
  error.value = ''
  try {
    paper.value = await copyPaper(paper.value.id)
    msg.value = '已复制为新卷（草稿、快照独立，不影响原卷判据）'
    await loadPapers()
    await refreshChecks()
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = false
  }
}

async function doPublish() {
  busy.value = true
  error.value = ''
  msg.value = ''
  violations.value = []
  try {
    paper.value = await publishPaper(paper.value.id)
    msg.value = '已发布，可下发给学生'
    await loadPapers()
  } catch (e) {
    error.value = errText(e)
    const d = e && e.response && e.response.data && e.response.data.detail
    if (d && d.violations) violations.value = d.violations
    // 被答案校验拦住时，问题明细在 checks 面板里，顺手刷新一下
    if (d && (d.code === 'ANSWER_UNVERIFIED' || d.code === 'ANSWER_CONFLICT')) await refreshChecks()
  } finally {
    busy.value = false
  }
}

async function doAssign() {
  busy.value = true
  error.value = ''
  msg.value = ''
  try {
    const body = {
      target_type: assign.value.target_type,
      target_grade: assign.value.target_type === 'grade' ? assign.value.target_grade : null,
      target_student: assign.value.target_type === 'student' ? assign.value.target_student : null
    }
    // 只传日期时补成当天 23:59，避免 ISO 解析歧义
    if (assign.value.due_at) body.due_at = `${assign.value.due_at}T23:59:00`
    const a = await assignPaper(paper.value.id, body)
    msg.value = `已下发（assignment_id=${a.assignment_id}），学生端立刻可见`
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = false
  }
}

async function loadPaperGraph() {
  if (!paper.value) return
  busy.value = true
  error.value = ''
  try {
    paperGraph.value = await getPaperGraph(paper.value.id)
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = false
  }
}

async function loadPaperSolutions() {
  if (!paper.value) return
  busy.value = true
  error.value = ''
  try {
    solutions.value = await getPaperSolutions(paper.value.id, true)
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = false
  }
}

const PAPER_TYPE = { pure_choice: '纯选择题卷', pure_blank: '纯填空题卷', mixed: '混合卷' }
const QTYPE = { choice: '选择题', blank: '填空题', subjective: '解答题' }
const paperTypeLabel = (v) => PAPER_TYPE[v] || v || ''
const qtypeLabel = (v) => QTYPE[v] || v || ''

onMounted(async () => {
  await loadPapers()
  // 从自动组卷页生成完跳过来时带 ?paper=ID；否则默认展开列表第一份（最新卷）
  const wanted = Number(route.query.paper)
  const first = Number.isFinite(wanted) && wanted > 0 && papers.value.some((p) => p.id === wanted)
    ? wanted
    : (papers.value[0] && papers.value[0].id)
  if (first) await selectPaper(first)
})
</script>

<style scoped>
.hint { font-size: 13px; color: var(--muted); margin: -6px 0 16px; }
.hint b { color: var(--brand-2); }
.form { margin-bottom: 16px; }
.toolbar { padding: 12px 16px; }
.foot { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.ghost {
  border: 1px solid var(--line-strong); background: transparent; border-radius: var(--r-sm);
  padding: 8px 14px; font-size: 13px; color: var(--ink-2); cursor: pointer;
}
.msg { font-size: 13px; color: var(--success); font-weight: 600; }
.err { font-size: 13px; color: var(--danger); font-weight: 600; margin: 10px 0 0; }
.grow { flex: 1; }
.meta { font-size: 12px; color: var(--muted); }
input, select {
  border: 1px solid var(--line-strong); border-radius: var(--r-sm);
  padding: 7px 10px; font-size: 13px; font-family: var(--font); background: var(--surface);
}
label.inline { display: flex; flex-direction: row; align-items: center; gap: 8px; font-size: 12px; color: var(--ink-2); }
label.inline span { font-weight: 600; color: var(--ink-2); }

.papers { display: flex; flex-direction: column; gap: 8px; margin-bottom: 16px; }
.paper { cursor: pointer; }
.paper.active { border: 1px solid var(--brand); }
.p-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }

.detail { margin-bottom: 16px; }
.d-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
.stat { display: flex; gap: 14px; margin-bottom: 12px; }
.s { font-size: 13px; font-weight: 600; }
.s.ok { color: var(--success); }
.s.warn { color: var(--warning); }
.actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.ok-tip { font-size: 13px; color: var(--success); font-weight: 700; }
.ghost.sm, .tes-btn-primary.sm { padding: 5px 10px; font-size: 12px; }

.ac-box { margin: 0 0 14px; }
.ac-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.ac-tip { font-size: 12px; color: var(--muted); }
.ac-list { display: flex; flex-direction: column; gap: 6px; }
.ac-row {
  display: flex; gap: 10px; align-items: flex-start;
  border: 1px solid var(--line-strong); border-radius: var(--r-sm);
  padding: 8px 10px; background: var(--surface);
}
.ac-row.bad { border-color: #f3b7c5; background: #fff7f9; }
.ac-row.todo { border-color: #f0d6a8; background: #fffdf5; }
.ac-seq {
  width: 22px; height: 22px; border-radius: 7px; display: grid; place-items: center; flex: none;
  background: var(--brand-grad-soft); color: var(--brand-2); font-weight: 800; font-size: 12px;
}
.ac-body { flex: 1; min-width: 0; }
.ac-detail { margin: 0; font-size: 13px; color: var(--ink); line-height: 1.6; }
.ac-src { margin: 3px 0 0; font-size: 12px; color: var(--muted); }
.ac-llm { margin: 3px 0 0; font-size: 12px; color: var(--accent); }
.ac-llm-bad { color: var(--danger); font-weight: 700; margin-left: 6px; }
.ac-ok { margin: 3px 0 0; font-size: 12px; color: var(--success); }
.ac-act { display: flex; gap: 6px; align-items: center; flex: none; }
.ac-act input {
  width: 150px; border: 1px solid var(--line-strong); border-radius: var(--r-sm);
  padding: 5px 8px; font-size: 12px; font-family: var(--font); background: var(--surface);
}
.ac-empty { font-size: 12px; color: var(--muted); margin: 0; }

.viol {
  background: #ffeef1; border-radius: var(--r-sm); padding: 10px 12px; margin-bottom: 12px;
  font-size: 13px; color: #b3243f;
}
.viol-t { margin: 0 0 6px; font-weight: 700; }
.viol ul { margin: 0; padding-left: 18px; }
.kg-list { margin: 6px 0 0; padding-left: 18px; font-size: 12px; color: var(--ink-2); }
.chip {
  display: inline-block; background: var(--brand-grad-soft); color: var(--brand-2);
  border-radius: 8px; padding: 1px 7px; margin: 0 5px 4px 0; font-size: 11px;
}

.qs { padding-left: 0; list-style: none; counter-reset: q; }
.qs > li { border-top: 1px dashed var(--line-strong); padding: 12px 0; }
.q-head { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; flex-wrap: wrap; }
.q-no {
  width: 24px; height: 24px; border-radius: 8px; display: grid; place-items: center;
  background: var(--brand-grad-soft); color: var(--brand-2); font-weight: 800; font-size: 12px;
}
.q-ans { font-size: 12px; color: var(--success); font-weight: 600; }
.stem { font-size: 14px; line-height: 1.7; color: var(--ink); margin: 0 0 8px; white-space: pre-wrap; }
.opts { list-style: none; padding: 0; margin: 0 0 8px; font-size: 13px; color: var(--ink-2); }
.opts li { margin-bottom: 3px; }
.vals { font-size: 12px; color: var(--muted); margin: 0; }
</style>
