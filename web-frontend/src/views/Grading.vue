<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">教师批改</h2>
    <p class="hint">
      机器判不了的题（填空含字母/符号表达式、解答题）在<b>学生提交时就自动开单</b>，
      这里逐题判定。判定结果立即回写学生端成绩，改判全程留痕。
    </p>

    <!-- M2-10 判分告警：同一标答累计 K 次改判 → 冻结该题自动判分 -->
    <div v-if="freezeCount" class="tes-card alerts">
      <div class="a-head">
        <b>判分告警</b>
        <span class="a-sub">
          {{ freezeCount }} 道题的标答因反复改判被冻结，该题已停止自动判分（新提交转人工）
        </span>
        <span class="grow"></span>
        <button class="ghost small" @click="loadFreezes()">刷新</button>
      </div>
      <div v-for="f in freezes" :key="f.freeze_id" class="a-row">
        <div class="a-line">
          <span class="seq">
            {{ f.paper_title || '试卷 ' + f.paper_id }} · 第 {{ f.seq }} 题
          </span>
          <span class="tes-pill warn">改判 {{ f.flips }} 次</span>
          <span class="tes-pill" :class="f.affected_count ? 'danger' : 'success'">
            {{ f.affected_count ? '已误判 ' + f.affected_count + ' 份' : '尚未误判' }}
          </span>
          <span v-if="f.release_count" class="tes-pill warn">复发 {{ f.release_count }} 次</span>
          <span class="grow"></span>
          <button v-if="!f.acked_at" class="ghost small" @click="ackFreeze(f)">已知悉</button>
          <button class="ghost small" @click="askRelease(f)">解冻…</button>
        </div>
        <p class="a-msg">{{ f.message }}</p>
        <p class="a-ans">
          标答：<b>{{ f.answer || '—' }}</b>
          <span v-if="f.actors && f.actors.length"> · 改判人 {{ f.actors.join('、') }}</span>
        </p>
        <div v-if="releasing === f.freeze_id" class="a-release">
          <input
            v-model="releaseComment"
            placeholder="解冻理由（必填，例如：标答已修正为 sqrt(6)/3）"
            @keyup.enter="doRelease(f)"
          />
          <button class="tes-btn-primary small" :disabled="releaseComment.trim().length < 2" @click="doRelease(f)">
            确认解冻
          </button>
          <button class="ghost small" @click="releasing = 0">取消</button>
        </div>
      </div>
    </div>

    <!-- 状态 tab + 筛选 -->
    <div class="tes-toolbar">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="tab"
        :class="{ on: status === t.key }"
        @click="setStatus(t.key)"
      >
        {{ t.label }}<b>{{ t.key ? counts[t.key] || 0 : totalAll }}</b>
      </button>
      <input v-model="studentId" placeholder="学生ID（可选）" @keyup.enter="load()" />
      <button class="tes-btn-primary" @click="load()">刷新</button>
    </div>

    <p v-if="error" class="err">{{ error }}</p>

    <div class="grid">
      <!-- 待办列表 -->
      <div class="col-list">
        <div
          v-for="t in list"
          :key="t.ticket_id"
          class="tes-card row"
          :class="{ active: detail && detail.ticket_id === t.ticket_id }"
          @click="open(t.ticket_id)"
        >
          <div class="row-top">
            <span class="seq">第 {{ t.seq }} 题</span>
            <span class="tes-pill">{{ qtypeLabel(t.qtype) }}</span>
            <span class="tes-pill" :class="statusClass(t.status)">{{ statusLabel(t.status) }}</span>
          </div>
          <p class="row-main">{{ t.student_answer || '（未作答）' }}</p>
          <p class="row-meta">
            {{ t.student_id }}
            <span v-if="t.paper_title">· {{ t.paper_title }}</span>
          </p>
          <p v-if="t.reason" class="row-reason">{{ t.reason }}</p>
          <p v-if="t.verdict" class="row-verdict">
            已判：<b>{{ verdictLabel(t.verdict) }}</b>
            <span v-if="t.reviewed_by">· {{ t.reviewed_by }}</span>
          </p>
        </div>
        <p v-if="!list.length && !loading" class="tes-empty">
          没有待办。学生交卷后，机器判不了的题会自动出现在这里。
        </p>
        <div v-if="total > list.length" class="more">
          共 {{ total }} 条，当前显示 {{ list.length }} 条
          <button class="tes-btn-primary small" @click="loadMore">加载更多</button>
        </div>
      </div>

      <!-- 详情 + 判定 -->
      <div class="col-detail">
        <div v-if="!detail" class="tes-card empty-detail">
          <p class="tes-empty">从左侧选择一道题开始批改。</p>
        </div>

        <div v-else class="tes-card detail">
          <div class="d-head">
            <span class="seq">第 {{ detail.seq }} 题</span>
            <span class="tes-pill">{{ qtypeLabel(detail.qtype) }}</span>
            <span class="tes-pill" :class="statusClass(detail.status)">
              {{ statusLabel(detail.status) }}
            </span>
            <span class="grow"></span>
            <span class="sid">{{ detail.student_id }}</span>
          </div>

          <p class="stem">{{ detail.stem }}</p>

          <!-- 冻结告警：判这一题时就要知道「标答本身可能有误」 -->
          <p v-if="detail.freeze && detail.freeze.status === 'frozen'" class="warn box-danger">
            <b>该题标答已冻结：</b>{{ detail.freeze.message }}。
            这道题现在<b>不会自动判分</b>，本卷所有学生的这题都会转人工。
          </p>
          <p v-else-if="detail.freeze && detail.freeze.flips" class="warn">
            {{ detail.freeze.message }}
          </p>
          <ul v-if="detail.options && detail.options.length" class="opts">
            <li v-for="o in detail.options" :key="o.label">
              <b>{{ o.label }}.</b> {{ o.content }}
            </li>
          </ul>

          <div class="kv">
            <div class="k">标准答案</div>
            <div class="v ans">{{ detail.correct_answer || '—' }}</div>
          </div>
          <div v-if="detail.answer_values && detail.answer_values.length" class="kv">
            <div class="k">可接受答案值</div>
            <div class="v">
              <span v-for="(v, i) in detail.answer_values" :key="i" class="chip">{{ v }}</span>
            </div>
          </div>
          <div class="kv">
            <div class="k">学生作答</div>
            <div class="v stu">{{ detail.student_answer || '（未作答）' }}</div>
          </div>
          <div v-if="detail.reason" class="kv">
            <div class="k">转人工原因</div>
            <div class="v reason">{{ detail.reason }}</div>
          </div>
          <details v-if="detail.analysis" class="analysis">
            <summary>查看解析</summary>
            <p>{{ detail.analysis }}</p>
          </details>

          <!-- 判定区 -->
          <div class="judge">
            <div class="judge-btns">
              <button class="v-btn ok" :class="{ on: verdict === 'correct' }" @click="verdict = 'correct'">
                算对
              </button>
              <button
                class="v-btn part"
                :class="{ on: verdict === 'partial' }"
                @click="verdict = 'partial'"
              >
                部分分
              </button>
              <button class="v-btn no" :class="{ on: verdict === 'wrong' }" @click="verdict = 'wrong'">
                算错
              </button>
            </div>
            <textarea v-model="comment" placeholder="评语（会显示给学生，说明为什么这么判）"></textarea>
            <label class="accept">
              <input type="checkbox" v-model="acceptAnswer" />
              这种写法也算对（记入该题可接受答案值，后续同题自动判对）
            </label>
            <p v-if="acceptAnswer && detail.answer_values_effective === false" class="warn">
              该题有多个空，可接受答案值不参与自动判分（避免空位错位误判），仅作记录。
            </p>
            <p v-if="detail.status === 'reviewed'" class="warn">
              这道题已判定过，再次提交会作为<b>改判</b>并留痕。
            </p>
            <div class="judge-foot">
              <button class="tes-btn-primary" :disabled="!verdict || saving" @click="submitVerdict">
                {{ saving ? '提交中…' : detail.status === 'reviewed' ? '提交改判' : '提交判定' }}
              </button>
              <button v-if="detail.status === 'pending'" class="ghost" @click="claim">认领</button>
              <span v-if="tip" class="tip">{{ tip }}</span>
            </div>
          </div>

          <!-- 留痕 -->
          <div v-if="detail.revisions && detail.revisions.length" class="revs">
            <h4>批改留痕</h4>
            <ul>
              <li v-for="(r, i) in detail.revisions" :key="i">
                <b>{{ actionLabel(r.action) }}</b>
                <span v-if="r.actor_id">· {{ r.actor_id }}</span>
                <span v-if="r.after_correct !== null && r.after_correct !== undefined">
                  · {{ r.before_correct ? '对' : '错' }} → {{ r.after_correct ? '对' : '错' }}
                </span>
                <span v-if="r.comment" class="rc">· {{ r.comment }}</span>
                <em>{{ shortTime(r.created_at) }}</em>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import {
  ackGradingFreeze,
  claimGradingTicket,
  getGradingTicket,
  judgeGradingTicket,
  listGradingFreezes,
  listGradingTickets,
  releaseGradingFreeze
} from '../api/admin'

const tabs = [
  { key: '', label: '全部' },
  { key: 'pending', label: '待认领' },
  { key: 'claimed', label: '已认领' },
  { key: 'reviewed', label: '已判定' }
]

const status = ref('')
const counts = ref({ pending: 0, claimed: 0, reviewed: 0 })
const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const detail = ref(null)
const studentId = ref('')
const verdict = ref('')
const comment = ref('')
const acceptAnswer = ref(false)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const tip = ref('')

// M2-10 判分护栏
const freezes = ref([])
const freezeCount = ref(0)
const releasing = ref(0)
const releaseComment = ref('')

const totalAll = computed(
  () => (counts.value.pending || 0) + (counts.value.claimed || 0) + (counts.value.reviewed || 0)
)

function errText(e) {
  const d = e && e.response && e.response.data && e.response.data.detail
  if (typeof d === 'string') return d
  if (d && d.message) return d.message
  return (e && e.message) || '请求失败'
}

async function load(append = false) {
  loading.value = true
  error.value = ''
  try {
    const res = await listGradingTickets({
      status: status.value || undefined,
      student_id: studentId.value || undefined,
      page: page.value,
      page_size: pageSize
    })
    list.value = append ? list.value.concat(res.items) : res.items
    counts.value = res.counts || counts.value
    total.value = res.total
  } catch (e) {
    error.value = errText(e)
  } finally {
    loading.value = false
  }
}

function setStatus(key) {
  status.value = key
  page.value = 1
  load()
}

function loadMore() {
  page.value += 1
  load(true)
}

async function open(id) {
  error.value = ''
  tip.value = ''
  try {
    detail.value = await getGradingTicket(id)
    verdict.value = detail.value.verdict || ''
    comment.value = detail.value.comment || ''
    acceptAnswer.value = false
  } catch (e) {
    error.value = errText(e)
  }
}

async function claim() {
  try {
    detail.value = await claimGradingTicket(detail.value.ticket_id)
    tip.value = '已认领'
    load()
  } catch (e) {
    error.value = errText(e)
  }
}

async function submitVerdict() {
  if (!verdict.value) return
  saving.value = true
  error.value = ''
  tip.value = ''
  try {
    detail.value = await judgeGradingTicket(detail.value.ticket_id, {
      verdict: verdict.value,
      comment: comment.value || null,
      accept_answer: acceptAnswer.value
    })
    const frozen = detail.value.freeze && detail.value.freeze.status === 'frozen'
    tip.value = frozen
      ? '已判定。这道题的标答已触发冻结：后续同题提交一律转人工'
      : '已判定，学生端成绩已更新'
    acceptAnswer.value = false
    load()
    loadFreezes()      // 这次改判可能就是压垮骆驼的第 K 次
  } catch (e) {
    error.value = errText(e)
  } finally {
    saving.value = false
  }
}

// ---------------------------------------------------------------- 判分护栏
async function loadFreezes() {
  try {
    const res = await listGradingFreezes({ status: 'frozen', page_size: 20 })
    freezes.value = res.items || []
    freezeCount.value = (res.counts && res.counts.frozen) || res.total || 0
  } catch (e) {
    // 告警区加载失败不挡住批改主流程（老师照样能判，只是看不到告警）
    freezes.value = []
    freezeCount.value = 0
  }
}

async function ackFreeze(f) {
  try {
    await ackGradingFreeze(f.freeze_id)
    tip.value = '已标记知悉；冻结仍然生效'
    loadFreezes()
  } catch (e) {
    error.value = errText(e)
  }
}

function askRelease(f) {
  releasing.value = f.freeze_id
  releaseComment.value = ''
}

async function doRelease(f) {
  const reason = releaseComment.value.trim()
  if (reason.length < 2) return
  try {
    await releaseGradingFreeze(f.freeze_id, { comment: reason })
    releasing.value = 0
    releaseComment.value = ''
    tip.value = '已解冻：这道题恢复自动判分（改判计数已清零）'
    loadFreezes()
    if (detail.value && detail.value.ticket_id) open(detail.value.ticket_id)
  } catch (e) {
    error.value = errText(e)
  }
}

const QTYPE = { choice: '选择题', blank: '填空题', subjective: '解答题' }
const STATUS = { pending: '待认领', claimed: '已认领', reviewed: '已判定' }
const VERDICT = { correct: '正确', partial: '部分分', wrong: '错误' }
const ACTION = { claim: '认领', judge: '判定', rejudge: '改判', add_value: '追加答案值' }

const qtypeLabel = (v) => QTYPE[v] || v || ''
const statusLabel = (v) => STATUS[v] || v || ''
const verdictLabel = (v) => VERDICT[v] || v || ''
const actionLabel = (v) => ACTION[v] || v || ''

function statusClass(v) {
  if (v === 'pending') return 'warn'
  if (v === 'claimed') return 'info'
  if (v === 'reviewed') return 'success'
  return ''
}

function shortTime(s) {
  if (!s) return ''
  return String(s).replace('T', ' ').slice(5, 16)
}

onMounted(() => {
  load()
  loadFreezes()
})
</script>

<style scoped>
.hint { font-size: 13px; color: var(--muted); margin: -6px 0 16px; }
.hint b { color: var(--brand-2); }
.err { color: var(--danger); font-size: 13px; font-weight: 600; }
.tab {
  border: 1px solid var(--line-strong);
  background: var(--surface);
  border-radius: 999px;
  padding: 6px 14px;
  font-size: 13px;
  color: var(--ink-2);
  cursor: pointer;
}
.tab b { margin-left: 6px; color: var(--brand-2); }
.tab.on { border-color: var(--brand); color: var(--brand-2); font-weight: 700; }
.small { padding: 4px 10px; font-size: 12px; }

.grid { display: grid; grid-template-columns: minmax(300px, 1fr) minmax(400px, 1.4fr); gap: 16px; }
@media (max-width: 1000px) { .grid { grid-template-columns: 1fr; } }

.row { margin-bottom: 10px; cursor: pointer; transition: box-shadow .2s, transform .2s; }
.row:hover { box-shadow: var(--shadow); transform: translateY(-1px); }
.row.active { border: 1px solid var(--brand); }
.row-top { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; flex-wrap: wrap; }
.seq { font-weight: 800; color: var(--brand-2); font-size: 13px; }
.row-main { margin: 0 0 4px; font-size: 14px; color: var(--ink); word-break: break-all; }
.row-meta { margin: 0; font-size: 12px; color: var(--muted); }
.row-reason { margin: 6px 0 0; font-size: 12px; color: var(--warning); }
.row-verdict { margin: 6px 0 0; font-size: 12px; color: var(--ink-2); }
.more { font-size: 12px; color: var(--muted); display: flex; gap: 10px; align-items: center; margin-top: 8px; }

.detail { position: sticky; top: 16px; }
.d-head { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
.grow { flex: 1; }
.sid { font-size: 12px; color: var(--muted); }
.stem { font-size: 15px; line-height: 1.7; color: var(--ink); margin: 0 0 12px; white-space: pre-wrap; }
.opts { list-style: none; padding: 0; margin: 0 0 12px; font-size: 14px; color: var(--ink-2); }
.opts li { margin-bottom: 4px; }

.kv { display: flex; gap: 10px; font-size: 13px; margin-bottom: 8px; }
.kv .k { flex-shrink: 0; width: 88px; color: var(--muted); }
.kv .v { color: var(--ink); word-break: break-all; }
.kv .ans { color: var(--success); font-weight: 700; }
.kv .stu { color: var(--accent); font-weight: 600; }
.kv .reason { color: var(--warning); }
.chip {
  display: inline-block; background: var(--brand-grad-soft); color: var(--brand-2);
  border-radius: 8px; padding: 2px 8px; margin: 0 6px 6px 0; font-size: 12px;
}
.analysis { font-size: 13px; color: var(--ink-2); margin-bottom: 12px; }
.analysis summary { cursor: pointer; color: var(--brand-2); font-weight: 600; }

.judge { border-top: 1px dashed var(--line-strong); padding-top: 14px; }
.judge-btns { display: flex; gap: 8px; margin-bottom: 10px; }
.v-btn {
  flex: 1; border: 1px solid var(--line-strong); background: var(--surface);
  border-radius: var(--r-sm); padding: 9px 0; font-size: 14px; font-weight: 600;
  color: var(--ink-2); cursor: pointer;
}
.v-btn.ok.on { border-color: var(--success); color: var(--success); background: #eafaf3; }
.v-btn.part.on { border-color: var(--warning); color: #a86a00; background: #fff6e5; }
.v-btn.no.on { border-color: var(--danger); color: var(--danger); background: #ffeef1; }
textarea {
  width: 100%; min-height: 64px; border: 1px solid var(--line-strong);
  border-radius: var(--r-sm); padding: 10px; font-size: 13px; font-family: var(--font);
  resize: vertical; margin-bottom: 10px;
}
.accept { display: flex; gap: 8px; align-items: center; font-size: 13px; color: var(--ink-2); margin-bottom: 8px; }
.warn { font-size: 12px; color: var(--warning); margin: 0 0 8px; }
.judge-foot { display: flex; gap: 10px; align-items: center; }
.ghost {
  border: 1px solid var(--line-strong); background: transparent; border-radius: var(--r-sm);
  padding: 8px 14px; font-size: 13px; color: var(--ink-2); cursor: pointer;
}
.tip { font-size: 13px; color: var(--success); font-weight: 600; }

.revs { margin-top: 16px; border-top: 1px dashed var(--line-strong); padding-top: 12px; }
.revs h4 { margin: 0 0 8px; font-size: 13px; color: var(--ink-2); }
.revs ul { list-style: none; padding: 0; margin: 0; font-size: 12px; color: var(--ink-2); }
.revs li { margin-bottom: 6px; }
.revs em { color: var(--muted); font-style: normal; margin-left: 6px; }
.revs .rc { color: var(--muted); }
.empty-detail { min-height: 160px; display: grid; place-items: center; }

/* M2-10 判分告警区 */
.alerts { margin-bottom: 14px; border-left: 3px solid var(--danger); }
.a-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 4px; }
.a-head b { color: var(--danger); font-size: 14px; }
.a-sub { font-size: 12px; color: var(--muted); }
.a-row { border-top: 1px dashed var(--line-strong); padding-top: 10px; margin-top: 10px; }
.a-line { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.a-msg { margin: 6px 0 2px; font-size: 13px; color: var(--ink); }
.a-ans { margin: 0; font-size: 12px; color: var(--muted); word-break: break-all; }
.a-ans b { color: var(--ink-2); }
.a-release { display: flex; gap: 8px; align-items: center; margin-top: 8px; }
.a-release input {
  flex: 1; border: 1px solid var(--line-strong); border-radius: var(--r-sm);
  padding: 7px 10px; font-size: 13px; font-family: var(--font);
}
.box-danger {
  background: #ffeef1; border: 1px solid var(--danger); border-radius: var(--r-sm);
  padding: 8px 10px; color: var(--danger);
}
</style>
