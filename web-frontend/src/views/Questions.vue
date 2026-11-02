<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">题库录题</h2>

    <div class="tes-toolbar">
      <span class="tes-tag">
        录题表单：题干 · 题型 · 选项 · 答案 · 知识点
      </span>
      <button class="tes-btn" @click="loadRecent">刷新最近录入</button>
    </div>

    <!-- 单题录入 -->
    <div class="tes-card entry-form">
      <h3 class="sub-title">单题录入</h3>

      <div class="row">
        <label>学科
          <input v-model.trim="form.subject" placeholder="数学" />
        </label>
        <label>年级
          <input v-model.trim="form.grade" placeholder="高三" />
        </label>
        <label>题型
          <select v-model="form.qtype">
            <option value="choice">选择</option>
            <option value="blank">填空</option>
            <option value="subjective">解答</option>
          </select>
        </label>
        <label v-if="form.qtype === 'choice'">选择类型
          <select v-model="form.choice_type">
            <option value="">未区分</option>
            <option value="single">单选</option>
            <option value="multi">多选</option>
          </select>
        </label>
        <label>难度(1~5)
          <input type="number" min="1" max="5" v-model.number="form.difficulty" />
        </label>
      </div>

      <label class="block">题干 <span class="hint">支持 LaTeX，如 \dfrac{\sqrt{2}}{2}</span>
        <textarea v-model="form.stem" rows="3" placeholder="已知 f(x)=x^2-2x，求 f(3)"></textarea>
      </label>

      <label v-if="form.qtype === 'choice'" class="block">
        选项 <span class="hint">每行一个，A. / B. 前缀可留可不留</span>
        <textarea v-model="form.optionsText" rows="4" placeholder="A. 1&#10;B. 2&#10;C. 3&#10;D. 4"></textarea>
      </label>

      <div class="row">
        <label>答案
          <input v-model.trim="form.answer" :placeholder="answerPlaceholder" />
        </label>
        <label>标答预展开值 <span class="hint">可选，逗号分隔；留空按答案自动展开</span>
          <input v-model.trim="form.answerValuesText" placeholder="如 2,-2" />
        </label>
      </div>

      <label class="block">解析
        <textarea v-model="form.analysis" rows="2" placeholder="代入得 f(3)=3"></textarea>
      </label>

      <label class="block">知识点 <span class="hint">支持 、；/|, 分隔</span>
        <input v-model.trim="form.kpText" placeholder="函数；导数" />
      </label>

      <div class="actions">
        <button class="tes-btn-primary" :disabled="busy" @click="submitOne">
          {{ busy ? '提交中…' : '录入题目' }}
        </button>
        <button class="tes-btn" :disabled="busy" @click="resetForm">清空</button>
        <span class="hint">录入后状态为「待审核(draft)」，需审核通过才能进卷</span>
      </div>

      <p v-if="error" class="tes-empty err">{{ error }}</p>

      <div v-if="result" class="result">
        <p>
          <b>入库 {{ result.created }} 题</b>，重复 {{ result.duplicated }} 题
          <span class="tes-pill" :class="result.embedding.ok ? 'on' : 'off'">
            向量：{{ result.embedding.ok ? '已写入 ' + result.embedding.written + ' 条' : '未生成' }}
          </span>
        </p>
        <p v-if="!result.embedding.ok" class="warn">
          向量未生成：{{ result.embedding.reason }}。题目已入库且为 draft，
          待 embedding 恢复后由回填脚本补向量再发布——不影响录入。
        </p>
        <p v-else class="hint">模型：{{ result.embedding.model }}（来源 {{ result.embedding.source }}）</p>
      </div>
    </div>

    <!-- 批量粘贴导入 -->
    <div class="tes-card entry-form">
      <h3 class="sub-title">批量粘贴录入（JSON 数组）</h3>
      <textarea
        v-model="batchText"
        rows="5"
        placeholder='[{"subject":"数学","grade":"高三","qtype":"choice","choice_type":"single","stem":"...","options":["A. 1","B. 2"],"answer":"A","knowledge_points":["函数"]}]'
      ></textarea>
      <div class="actions">
        <button class="tes-btn-primary" :disabled="busy" @click="submitBatch">批量录入</button>
        <span class="hint">逐题幂等：已存在的题按「重复」计数，不覆盖原题</span>
      </div>
    </div>

    <!-- 最近录入 -->
    <h3 class="sub-title">最近录入</h3>
    <div v-if="recent.length" class="tes-table-card">
      <table class="tes-table">
        <thead>
          <tr><th>题型</th><th>学科/年级</th><th>题干</th><th>答案</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="q in recent" :key="q.id">
            <td><span class="tes-pill">{{ q.type }}</span></td>
            <td>{{ q.subject }} / {{ q.grade || '-' }}</td>
            <td class="stem">{{ q.stem }}</td>
            <td>{{ q.answer || '-' }}</td>
            <td>
              <span class="tes-pill" :class="q.status === 'published' ? 'on' : 'off'">
                {{ q.status === 'published' ? '已发布' : '待审核' }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="tes-empty">暂无题目（或尚未加载）。</p>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { createQuestion, createQuestionsBatch, listQuestions } from '../api/admin'

const form = reactive({
  subject: '数学',
  grade: '高三',
  qtype: 'choice',
  choice_type: 'single',
  difficulty: 3,
  stem: '',
  optionsText: '',
  answer: '',
  answerValuesText: '',
  analysis: '',
  kpText: '',
})

const busy = ref(false)
const error = ref('')
const result = ref(null)
const recent = ref([])
const batchText = ref('')

const answerPlaceholder = computed(() => {
  if (form.qtype === 'choice') return '字母，多选可连写 ACD'
  if (form.qtype === 'blank') return '多解用「或」，如 2 或 -2'
  return '解答题可写「见解析」'
})

function splitKp(text) {
  // 与后端 _KP_SEPARATORS 同口径：、，,；;|/ 都算分隔
  return (text || '')
    .split(/[、，,；;|/]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

function splitOptions(text) {
  return (text || '')
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
}

function splitValues(text) {
  return (text || '')
    .split(/[,，、;；]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

function toPayload() {
  const payload = {
    subject: form.subject || '未知',
    grade: form.grade || null,
    qtype: form.qtype,
    stem: form.stem,
    answer: form.answer || null,
    analysis: form.analysis || null,
    difficulty: form.difficulty || 3,
    knowledge_points: splitKp(form.kpText),
  }
  if (form.qtype === 'choice') {
    payload.choice_type = form.choice_type || null
    payload.options = splitOptions(form.optionsText)
  }
  const values = splitValues(form.answerValuesText)
  if (values.length) payload.answer_values = values
  return payload
}

async function submitOne() {
  if (!form.stem.trim()) {
    error.value = '题干不能为空'
    return
  }
  busy.value = true
  error.value = ''
  result.value = null
  try {
    result.value = await createQuestion(toPayload())
    await loadRecent()
  } catch (e) {
    error.value = e?.response?.data?.detail || e?.message || '录入失败'
  } finally {
    busy.value = false
  }
}

async function submitBatch() {
  let list
  try {
    list = JSON.parse(batchText.value)
    if (!Array.isArray(list) || !list.length) throw new Error('需要一个非空 JSON 数组')
  } catch (e) {
    error.value = `批量内容解析失败：${e.message}`
    return
  }
  busy.value = true
  error.value = ''
  result.value = null
  try {
    result.value = await createQuestionsBatch(list)
    await loadRecent()
  } catch (e) {
    error.value = e?.response?.data?.detail || e?.message || '批量录入失败'
  } finally {
    busy.value = false
  }
}

function resetForm() {
  form.stem = ''
  form.optionsText = ''
  form.answer = ''
  form.answerValuesText = ''
  form.analysis = ''
  form.kpText = ''
  result.value = null
  error.value = ''
}

async function loadRecent() {
  try {
    recent.value = (await listQuestions(20)) || []
  } catch (e) {
    // 列表失败不打断录题：只在控制台留痕
    console.warn('加载最近录入失败', e?.message)
  }
}

onMounted(loadRecent)
</script>

<style scoped>
/* 已接入全局设计系统；此处只补本页表单布局 */
.entry-form { margin-bottom: 18px; }
.sub-title { margin: 4px 0 12px; font-size: 15px; }
.row { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 12px; }
.row > label { display: flex; flex-direction: column; gap: 4px; min-width: 140px; }
.block { display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; }
.block textarea, .block input { width: 100%; }
.entry-form textarea { width: 100%; box-sizing: border-box; }
.actions { display: flex; align-items: center; gap: 12px; margin-top: 8px; }
.hint { font-size: 12px; color: #8a7f74; }
.warn { color: #b5541f; font-size: 13px; }
.err { color: #c0392b; }
.result { margin-top: 12px; padding-top: 12px; border-top: 1px dashed #e6ddd2; }
.stem { max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
