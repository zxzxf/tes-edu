<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">自动组卷</h2>
    <p class="hint">
      从题库按年级/学科/题型抽题组卷，<b>答案随卷快照</b>（判分唯一依据，0 token）。
      发布前会校验卷型与题型白名单；含解答题的混合卷要点「需人工 N 道」。
    </p>

    <div class="review-head">
      <button class="ghost" @click="toggleReview">题库审核</button>
      <template v-if="reviewOpen">
        <select v-model="reviewStatus" aria-label="审核状态" @change="loadReviewQuestions">
          <option value="draft">待审核</option>
          <option value="quarantine">已隔离</option>
          <option value="published">已通过</option>
        </select>
        <button class="ghost" :disabled="reviewBusy" @click="loadReviewQuestions">刷新</button>
      </template>
    </div>
    <section v-if="reviewOpen" class="review-queue">
      <p v-if="reviewError" class="err">{{ reviewError }}</p>
      <p v-if="!reviewBusy && !reviewRows.length" class="tes-empty">暂无题目</p>
      <div v-for="q in reviewRows" :key="q.id" class="review-row">
        <div class="review-main">
          <div class="review-meta">{{ q.type }} · {{ q.source_tag || '未知来源' }} · #{{ q.id }}</div>
          <p class="review-stem">{{ q.stem }}</p>
          <div v-if="q.options" class="review-options">
            <span v-for="option in q.options" :key="option.label">{{ option.label }}. {{ option.content }}</span>
          </div>
          <div class="review-answer">答案：{{ q.answer || '未填写' }}</div>
          <p v-if="q.analysis" class="review-analysis">解析：{{ q.analysis }}</p>
        </div>
        <div class="review-actions">
          <button v-if="q.status !== 'published'" class="tes-btn-primary approve" :disabled="reviewBusy" @click="setReview(q, 'published')">通过</button>
          <button v-if="q.status !== 'quarantine'" class="ghost" :disabled="reviewBusy" @click="setReview(q, 'quarantine')">隔离</button>
          <button v-if="q.status !== 'draft'" class="ghost" :disabled="reviewBusy" @click="setReview(q, 'draft')">退回</button>
        </div>
      </div>
    </section>

    <!-- 组卷参数 -->
    <div class="tes-card form">
      <div class="row">
        <label>
          <span>标题（可空）</span>
          <input v-model="form.title" placeholder="留空自动生成，如 高三·数学·纯选择题卷（20题）" />
        </label>
        <label>
          <span>年级</span>
          <input v-model="form.grade" />
        </label>
        <label>
          <span>学科</span>
          <input v-model="form.subject" />
        </label>
      </div>
      <div class="row">
        <label>
          <span>卷型</span>
          <select v-model="form.paper_type">
            <option value="pure_choice">纯选择题卷</option>
            <option value="pure_blank">纯填空题卷</option>
            <option value="mixed">混合卷（含解答题）</option>
          </select>
        </label>
        <label>
          <span>题量</span>
          <input v-model.number="form.count" type="number" min="1" max="100" />
        </label>
        <label class="grow">
          <span>知识点（可空，逗号分隔）</span>
          <input v-model="kps" placeholder="如 导数,数列" />
        </label>
      </div>
      <div class="foot">
        <button class="tes-btn-primary" :disabled="busy" @click="compose">
          {{ busy ? '生成中…' : '生成试卷' }}
        </button>
        <button class="ghost" @click="loadPapers">刷新试卷列表</button>
        <span v-if="msg" class="msg">{{ msg }}</span>
      </div>
      <p v-if="error" class="err">{{ error }}</p>
    </div>

    <!-- 试卷列表 -->
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
            {{ p.question_count }} 题 · 自动判 {{ p.auto_gradeable_count }}
            <span v-if="p.manual_required_count"> · 需人工 {{ p.manual_required_count }}</span>
          </span>
        </div>
      </div>
    </div>

    <!-- 当前试卷 -->
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

      <!-- 发布 / 下发 -->
      <div class="actions">
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

      <!-- 校验问题 -->
      <div v-if="violations.length" class="viol">
        <p class="viol-t">发布被拦截（{{ violations.length }} 项）：</p>
        <ul>
          <li v-for="(v, i) in violations" :key="i">
            <b>{{ v.seq ? '第 ' + v.seq + ' 题' : '整卷' }}</b> · {{ v.message }}
          </li>
        </ul>
      </div>

      <!-- 题目 -->
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

    <p v-else-if="!papers.length" class="tes-empty">
      填写参数后点「生成试卷」。题源不足时后端会明确告知可组题数，不会静默凑卷。
    </p>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import {
  assignPaper,
  composePaper,
  confirmAnswerChecks,
  getAnswerChecks,
  getPaper,
  listPapers,
  listReviewQuestions,
  publishPaper,
  reviewQuestion,
  runAnswerChecks
} from '../api/admin'

const form = ref({
  title: '',
  grade: '高三',
  subject: '数学',
  paper_type: 'pure_choice',
  count: 10
})
const kps = ref('')
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
const reviewOpen = ref(false)
const reviewStatus = ref('draft')
const reviewRows = ref([])
const reviewBusy = ref(false)
const reviewError = ref('')

async function loadReviewQuestions() {
  reviewBusy.value = true
  reviewError.value = ''
  try {
    reviewRows.value = await listReviewQuestions(reviewStatus.value)
  } catch (e) {
    reviewError.value = errText(e)
  } finally {
    reviewBusy.value = false
  }
}

async function toggleReview() {
  reviewOpen.value = !reviewOpen.value
  if (reviewOpen.value) await loadReviewQuestions()
}

async function setReview(question, status) {
  reviewBusy.value = true
  reviewError.value = ''
  try {
    await reviewQuestion(question.id, status)
    reviewRows.value = await listReviewQuestions(reviewStatus.value)
  } catch (e) {
    reviewError.value = errText(e)
  } finally {
    reviewBusy.value = false
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

function errText(e) {
  const d = e && e.response && e.response.data && e.response.data.detail
  if (typeof d === 'string') return d
  if (d && d.message) return d.message
  return (e && e.message) || '请求失败'
}

async function loadPapers() {
  error.value = ''
  try {
    papers.value = await listPapers()
  } catch (e) {
    error.value = errText(e)
  }
}

async function compose() {
  busy.value = true
  error.value = ''
  msg.value = ''
  violations.value = []
  try {
    const body = { ...form.value }
    const list = kps.value.split(',').map((s) => s.trim()).filter(Boolean)
    if (list.length) body.knowledge_points = list
    if (!body.title) delete body.title
    paper.value = await composePaper(body)
    msg.value = `已生成「${paper.value.title}」`
    await loadPapers()
    await refreshChecks()
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = false
  }
}

async function selectPaper(id) {
  error.value = ''
  msg.value = ''
  violations.value = []
  checks.value = []
  checkSummary.value = null
  try {
    paper.value = await getPaper(id)
    await refreshChecks()
  } catch (e) {
    error.value = errText(e)
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

const PAPER_TYPE = { pure_choice: '纯选择题卷', pure_blank: '纯填空题卷', mixed: '混合卷' }
const QTYPE = { choice: '选择题', blank: '填空题', subjective: '解答题' }
const paperTypeLabel = (v) => PAPER_TYPE[v] || v || ''
const qtypeLabel = (v) => QTYPE[v] || v || ''

onMounted(() => loadPapers())
</script>

<style scoped>
.review-head { display: flex; align-items: center; gap: 8px; margin: 0 0 12px; }
.review-queue { border-top: 1px solid var(--line-strong); border-bottom: 1px solid var(--line-strong); margin-bottom: 18px; }
.review-row { display: flex; justify-content: space-between; gap: 16px; padding: 12px 0; border-bottom: 1px solid var(--line-strong); }
.review-row:last-child { border-bottom: 0; }
.review-main { min-width: 0; flex: 1; }
.review-meta, .review-answer, .review-analysis { color: var(--muted); font-size: 12px; }
.review-stem { margin: 5px 0; font-size: 13px; white-space: pre-wrap; overflow-wrap: anywhere; }
.review-options { display: flex; gap: 8px; flex-wrap: wrap; font-size: 12px; }
.review-analysis { margin: 4px 0 0; white-space: pre-wrap; }
.review-actions { display: flex; align-items: flex-start; gap: 6px; flex-wrap: wrap; }
@media (max-width: 640px) { .review-row { flex-direction: column; } }
.hint { font-size: 13px; color: var(--muted); margin: -6px 0 16px; }
.hint b { color: var(--brand-2); }
.form { margin-bottom: 16px; }
.row { display: flex; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
.row label, label.inline { display: flex; flex-direction: column; gap: 5px; font-size: 12px; color: var(--ink-2); }
label.inline { flex-direction: row; align-items: center; gap: 8px; }
.row label span, label.inline span { font-weight: 600; color: var(--ink-2); }
.grow { flex: 1; }
input, select {
  border: 1px solid var(--line-strong); border-radius: var(--r-sm);
  padding: 7px 10px; font-size: 13px; font-family: var(--font); background: var(--surface);
}
.foot { display: flex; gap: 10px; align-items: center; }
.ghost {
  border: 1px solid var(--line-strong); background: transparent; border-radius: var(--r-sm);
  padding: 8px 14px; font-size: 13px; color: var(--ink-2); cursor: pointer;
}
.msg { font-size: 13px; color: var(--success); font-weight: 600; }
.err { font-size: 13px; color: var(--danger); font-weight: 600; margin: 10px 0 0; }

.papers { display: flex; flex-direction: column; gap: 8px; margin-bottom: 16px; }
.paper { cursor: pointer; }
.paper.active { border: 1px solid var(--brand); }
.p-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.meta { font-size: 12px; color: var(--muted); }

.detail { margin-bottom: 16px; }
.d-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
.stat { display: flex; gap: 14px; margin-bottom: 12px; }
.s { font-size: 13px; font-weight: 600; }
.s.ok { color: var(--success); }
.s.warn { color: var(--warning); }
.actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.ok-tip { font-size: 13px; color: var(--success); font-weight: 700; }
/* 答案校验（M2-9）：结论必须一眼可辨 —— 冲突红、待确认黄、通过绿 */
.ac-box { margin: 0 0 14px; }
.ac-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.ac-tip { font-size: 12px; color: var(--muted); }
.ghost.sm, .tes-btn-primary.sm { padding: 5px 10px; font-size: 12px; }
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
.chip {
  display: inline-block; background: var(--brand-grad-soft); color: var(--brand-2);
  border-radius: 8px; padding: 1px 7px; margin: 0 5px 4px 0; font-size: 11px;
}
</style>
