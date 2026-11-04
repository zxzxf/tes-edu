<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">自动组卷</h2>
    <p class="hint">
      从题库按年级/学科/题型抽题组卷，<b>答案随卷快照</b>（判分唯一依据，0 token）。
      生成后会自动跳到「试卷下发」完成答案校验、发布与下发。
    </p>

    <div class="tes-card dispatch-tip">
      <div>
        <b>试卷列表 / 发布 / 下发</b> 已移到左侧独立菜单「试卷下发」；本页只负责生成试卷。
      </div>
      <button class="ghost" @click="goDispatch()">去试卷下发</button>
    </div>

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

    <!-- M4-3 LLM 辅助组卷：只出草稿，必须教师审核后才可发布 -->
    <div class="tes-card form">
      <div class="row">
        <label class="grow">
          <span>AI 草稿组卷（M4-3）· 主题</span>
          <input v-model.trim="llm.topic" placeholder="如 等比数列的通项与前n项和" />
        </label>
        <label>
          <span>题量</span>
          <input v-model.number="llm.count" type="number" min="1" max="30" />
        </label>
        <label>
          <span>卷型</span>
          <select v-model="llm.paper_type">
            <option value="pure_choice">纯选择题卷</option>
            <option value="pure_blank">纯填空题卷</option>
            <option value="mixed">混合卷</option>
          </select>
        </label>
        <button class="ghost" :disabled="busy" @click="llmGenerate">
          {{ busy ? '生成中…' : '生成草稿' }}
        </button>
      </div>
      <p class="hint">
        生成的题一律 <b>draft</b>（未审核不可组卷、学生端不可见）；审核通过后才可发布。
        本租户未配置模型时接口返回 503，不会用模板题冒充生成。
      </p>
      <div v-if="llmResult" class="review-queue">
        <p>
          <b>已入库 {{ llmResult.created }} 题草稿</b>
          <span v-if="llmResult.duplicated"> · 重复 {{ llmResult.duplicated }}</span>
          <span v-if="llmResult.paper_id" class="tes-pill warn">草稿卷 #{{ llmResult.paper_id }}</span>
        </p>
        <p v-if="llmResult.rejected && llmResult.rejected.length" class="err">
          被拦下 {{ llmResult.rejected.length }} 题：{{ llmResult.rejected.map((r) => r.reason).join('；') }}
        </p>
      </div>
    </div>

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
        <button class="ghost" @click="goDispatch()">去试卷下发</button>
        <span v-if="msg" class="msg">{{ msg }}</span>
      </div>
      <p v-if="error" class="err">{{ error }}</p>
    </div>

    <!-- M4-1 手动出卷：按 kp/难度/题型/学科/年级筛选选题 + 逐题配分 -->
    <div class="tes-card form">
      <div class="row">
        <label>
          <span>手动出卷（M4-1）· 年级</span>
          <input v-model.trim="manual.grade" placeholder="高三" />
        </label>
        <label>
          <span>学科</span>
          <input v-model.trim="manual.subject" placeholder="数学" />
        </label>
        <label>
          <span>卷型</span>
          <select v-model="manual.paper_type">
            <option value="pure_choice">纯选择题卷</option>
            <option value="pure_blank">纯填空题卷</option>
            <option value="mixed">混合卷</option>
          </select>
        </label>
        <label>
          <span>知识点</span>
          <input v-model.trim="manual.kp" placeholder="如 等比数列" />
        </label>
        <label>
          <span>难度区间</span>
          <span class="range">
            <input v-model.number="manual.difficulty_min" type="number" min="1" max="5" placeholder="1" />
            <input v-model.number="manual.difficulty_max" type="number" min="1" max="5" placeholder="5" />
          </span>
        </label>
        <button class="ghost" :disabled="busy" @click="searchManual">筛选题库</button>
      </div>
      <p class="hint">
        只列出本租户 <b>已审核(published)</b>、非隔离、有答案的题；勾选后逐题配分。
        保存为草稿卷，发布前仍会过答案/分值/抽样闸门；未配分时发布会被拦（不替教师猜分）。
      </p>
      <div v-if="manualRows.length" class="qpick">
        <label v-for="q in manualRows" :key="q.id" class="qpick-row">
          <input type="checkbox" :checked="manualPicked[q.id] !== undefined" @change="togglePick(q)" />
          <span class="qpick-main">
            <span class="qpick-stem">{{ q.stem }}</span>
            <span class="meta">
              #{{ q.id }} · {{ q.type }} · {{ q.grade || '—' }} · 难度 {{ q.difficulty }} · {{ (q.knowledge_points || []).join('、') || '未标知识点' }}
            </span>
          </span>
          <input
            v-if="manualPicked[q.id] !== undefined"
            class="qpick-score"
            type="number"
            min="0"
            placeholder="分值"
            :value="manualPicked[q.id]"
            @input="manualPicked[q.id] = $event.target.value === '' ? null : Number($event.target.value)"
          />
          <span v-if="manualPicked[q.id] !== undefined" class="meta">分</span>
        </label>
      </div>
      <p v-else-if="manualSearched" class="tes-empty">没有符合条件的题。</p>
      <div class="foot">
        <span class="meta">已选 {{ pickedIds.length }} 题</span>
        <button class="ghost" :disabled="busy || !pickedIds.length" @click="manualCreate">保存草稿卷</button>
      </div>
      <p v-if="manualMsg" class="msg">{{ manualMsg }}</p>
    </div>

    <!-- M3-3/M3-4 题目知识点绑定与覆盖度（教师端；本卷图谱/解析已移到试卷下发页） -->
    <div class="tes-card form">
      <div class="row">
        <label class="grow">
          <span>题目知识点绑定（M3-3）· 题目 id（逗号分隔）</span>
          <input v-model.trim="kpBind.ids" placeholder="如 101,102" />
        </label>
        <label class="grow">
          <span>知识点（名称或 code，逗号分隔）</span>
          <input v-model.trim="kpBind.codes" placeholder="如 等比数列,MA-K1" />
        </label>
        <button class="ghost" :disabled="busy" @click="bindKps">批量绑定</button>
        <button class="ghost" :disabled="busy" @click="backfillKps">从题库标签回填</button>
        <button class="ghost" :disabled="busy" @click="loadKpCoverage">看覆盖度</button>
      </div>
      <p v-if="kpMsg" class="msg">{{ kpMsg }}</p>
      <div v-if="kpCoverage" class="meta">
        已绑定 {{ kpCoverage.bound_questions }} / {{ kpCoverage.total_questions }} 题
        （覆盖率 {{ (kpCoverage.coverage_rate * 100).toFixed(1) }}%），待标注 {{ kpCoverage.unbound_count }} 题。
        <span v-if="kpCoverage.unresolved && kpCoverage.unresolved.length" class="err">
          未识别：{{ kpCoverage.unresolved.join('、') }}
        </span>
      </div>
    </div>

  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  composePaper,
  createManualPaper,
  bindQuestionKp,
  backfillQuestionKp,
  getQuestionKpCoverage,
  listReviewQuestions,
  llmGeneratePaper,
  reviewQuestion,
  searchQuestions
} from '../api/admin'

const router = useRouter()
const goDispatch = (paperId) =>
  router.push(paperId ? { path: '/papers', query: { paper: paperId } } : '/papers')

const form = ref({
  title: '',
  grade: '高三',
  subject: '数学',
  paper_type: 'pure_choice',
  count: 10
})
const kps = ref('')
const busy = ref(false)
const msg = ref('')
const error = ref('')
const reviewOpen = ref(false)
const reviewStatus = ref('draft')
const reviewRows = ref([])
const reviewBusy = ref(false)
const reviewError = ref('')
// M4-3 LLM 草稿组卷
const llm = ref({ topic: '', count: 10, paper_type: 'pure_choice' })
const llmResult = ref(null)
// M4-1 手动出卷：筛选条件 + 已选（id -> 分值）
const manual = ref({
  grade: '高三',
  subject: '数学',
  paper_type: 'pure_choice',
  kp: '',
  difficulty_min: null,
  difficulty_max: null
})
const manualRows = ref([])
const manualPicked = ref({})
const manualSearched = ref(false)
const manualMsg = ref('')
const pickedIds = computed(() => Object.keys(manualPicked.value).map(Number))

// M3-1/M3-3/M3-4 教师端
const kpBind = ref({ ids: '', codes: '' })
const kpCoverage = ref(null)
const kpMsg = ref('')

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

function errText(e) {
  const d = e && e.response && e.response.data && e.response.data.detail
  if (typeof d === 'string') return d
  if (d && d.message) return d.message
  return (e && e.message) || '请求失败'
}

async function compose() {
  busy.value = true
  error.value = ''
  msg.value = ''
  try {
    const body = { ...form.value }
    const list = kps.value.split(',').map((s) => s.trim()).filter(Boolean)
    if (list.length) body.knowledge_points = list
    if (!body.title) delete body.title
    const created = await composePaper(body)
    msg.value = `已生成「${created.title}」，正在跳转到试卷下发`
    await goDispatch(created.id)
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = false
  }
}

async function searchManual() {
  busy.value = true
  error.value = ''
  manualMsg.value = ''
  try {
    const params = {
      grade: manual.value.grade || undefined,
      subject: manual.value.subject || undefined,
      knowledge_point: manual.value.kp || undefined,
      difficulty_min: manual.value.difficulty_min ?? undefined,
      difficulty_max: manual.value.difficulty_max ?? undefined,
      answerable: true,
      limit: 100
    }
    manualRows.value = await searchQuestions(params)
    manualSearched.value = true
    manualMsg.value = `筛出 ${manualRows.value.length} 题（只含已审核、非隔离、有答案的题）`
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = false
  }
}

function togglePick(q) {
  const next = { ...manualPicked.value }
  if (next[q.id] !== undefined) delete next[q.id]
  else next[q.id] = null
  manualPicked.value = next
}

async function manualCreate() {
  const ids = pickedIds.value
  if (!ids.length) {
    error.value = '请先勾选题目'
    return
  }
  busy.value = true
  error.value = ''
  msg.value = ''
  manualMsg.value = ''
  try {
    const created = await createManualPaper({
      grade: manual.value.grade,
      subject: manual.value.subject,
      paper_type: manual.value.paper_type,
      questions: ids.map((id) => ({
        question_id: id,
        score: manualPicked.value[id] === null ? undefined : Number(manualPicked.value[id])
      }))
    })
    manualMsg.value = `已保存草稿卷「${created.title}」`
    msg.value = manualMsg.value
    await goDispatch(created.id)
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = false
  }
}

function splitIds(text) {
  return String(text || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .map(Number)
    .filter((n) => Number.isFinite(n))
}

async function bindKps() {
  const ids = splitIds(kpBind.value.ids)
  const codes = String(kpBind.value.codes || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
  if (!ids.length || !codes.length) {
    error.value = '请填写题目 id 与知识点'
    return
  }
  busy.value = true
  error.value = ''
  kpMsg.value = ''
  try {
    const out = await bindQuestionKp({ question_ids: ids, kp_codes: codes })
    kpMsg.value = `已绑定 ${out.created} 行` +
      (out.skipped && out.skipped.length ? `，跳过 ${out.skipped.length} 行（已存在）` : '') +
      (out.unresolved && out.unresolved.length ? `；未识别：${out.unresolved.join('、')}` : '')
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = false
  }
}

async function backfillKps() {
  busy.value = true
  error.value = ''
  kpMsg.value = ''
  try {
    const ids = splitIds(kpBind.value.ids)
    const out = await backfillQuestionKp(ids.length ? ids : null)
    kpMsg.value = `扫描 ${out.scanned} 题，回填 ${out.created} 行` +
      (out.unresolved && out.unresolved.length ? `；未识别：${out.unresolved.join('、')}` : '')
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = false
  }
}

async function loadKpCoverage() {
  busy.value = true
  error.value = ''
  try {
    kpCoverage.value = await getQuestionKpCoverage()
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = false
  }
}

async function llmGenerate() {
  if (!llm.value.topic.trim()) {
    error.value = '请先填主题'
    return
  }
  busy.value = true
  error.value = ''
  msg.value = ''
  llmResult.value = null
  try {
    llmResult.value = await llmGeneratePaper({
      ...llm.value,
      grade: form.value.grade,
      subject: form.value.subject
    })
    msg.value = '草稿已生成：需在「题库审核」里逐题通过后才能组卷/发布'
    if (reviewOpen.value) await loadReviewQuestions()
  } catch (e) {
    const d = e && e.response && e.response.data && e.response.data.detail
    if (d && d.code === 'LLM_UNAVAILABLE') {
      error.value = `本租户模型不可用：${d.message}（可先在「模型配置」里配好再试）`
    } else if (d && d.code === 'LLM_NO_VALID_QUESTION') {
      error.value = `生成结果没有可入库的题：${d.message}`
    } else {
      error.value = errText(e)
    }
  } finally {
    busy.value = false
  }
}

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
.dispatch-tip { display: flex; gap: 12px; align-items: center; justify-content: space-between; flex-wrap: wrap; margin-bottom: 16px; font-size: 13px; }
.dispatch-tip b { color: var(--brand-2); }
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
.range { display: flex; gap: 6px; }
.range input { width: 64px; }
.qpick { display: flex; flex-direction: column; gap: 6px; max-height: 320px; overflow: auto; margin-bottom: 10px; }
.qpick-row {
  display: flex; gap: 10px; align-items: flex-start;
  border: 1px solid var(--line-strong); border-radius: var(--r-sm);
  padding: 8px 10px; background: var(--surface);
}
.qpick-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.qpick-stem { font-size: 13px; color: var(--ink); }
.qpick-score {
  width: 76px; border: 1px solid var(--line-strong); border-radius: var(--r-sm);
  padding: 5px 8px; font-size: 12px;
}
.kg-list { margin: 6px 0 0; padding-left: 18px; font-size: 12px; color: var(--ink-2); }
.chip {
  display: inline-block; background: var(--brand-grad-soft); color: var(--brand-2);
  border-radius: 8px; padding: 1px 7px; margin: 0 5px 4px 0; font-size: 11px;
}
</style>
