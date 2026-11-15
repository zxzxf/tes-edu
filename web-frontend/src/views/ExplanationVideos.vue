<template>
  <div class="cw-page">
    <h2 class="cw-title">讲解视频生成任务</h2>
    <p class="cw-hint">
      数字人讲题短视频（#28）生成任务管理。触发后由 LiteVideoGenerator（ffmpeg 字幕幻灯片）异步合成，
      状态流转 <code>pending → processing → done / failed</code>。<code>failed</code> 可点「重跑」强制重新生成。
    </p>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="msg" class="msg">{{ msg }}</p>

    <!-- 新建生成任务 -->
    <section class="tes-card">
      <div class="cw-head"><b>新建生成任务</b><span class="cw-grow"></span>
        <button class="ghost" :disabled="busy" @click="loadAll">刷新</button>
      </div>
      <div class="cw-row">
        <label class="cw-col">
          <span>题干（必填）</span>
          <textarea v-model.trim="form.question" rows="2" placeholder="例如：求函数 f(x)=x² 的导数，并说明每一步依据。" />
        </label>
        <label><span>知识点</span><input v-model.trim="form.knowledge_point" placeholder="可选，如：导数/幂函数" /></label>
        <label class="cw-col">
          <span>讲题台词稿（可选，留空退化为题干）</span>
          <textarea v-model.trim="form.script" rows="2" placeholder="自定义台词；不填则用题干本身生成字幕" />
        </label>
        <button class="tes-btn-primary" :disabled="busy || !form.question" @click="create">触发生成</button>
      </div>
    </section>

    <!-- 列表 + 过滤 -->
    <section class="tes-card">
      <div class="cw-head">
        <b>任务列表</b><span class="cw-grow"></span>
        <label class="cw-filter">状态
          <select v-model="statusFilter" @change="loadAll">
            <option value="">全部</option>
            <option value="pending">pending</option>
            <option value="processing">processing</option>
            <option value="done">done</option>
            <option value="failed">failed</option>
          </select>
        </label>
        <span class="cw-total">共 {{ total }} 条</span>
      </div>

      <div class="cw-table-wrap" v-if="rows.length">
        <table class="tes-table">
          <colgroup>
            <col class="c-q" /><col class="c-kp" /><col class="c-st" />
            <col class="c-dur" /><col class="c-err" /><col class="c-time" /><col class="c-ops" />
          </colgroup>
          <thead>
            <tr>
              <th>题干</th><th>知识点</th><th>状态</th><th>时长(s)</th><th>失败原因</th><th>创建时间</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.id">
              <td><span class="cw-clip q" :title="r.question_text">{{ r.question_text || '—' }}</span></td>
              <td>{{ r.knowledge_point || '—' }}</td>
              <td>
                <span class="tes-pill" :class="pillClass(r.status)">{{ r.status }}</span>
              </td>
              <td>{{ r.duration_sec != null ? r.duration_sec.toFixed(1) : '—' }}</td>
              <td class="cw-err">
                <span class="cw-clip err" :title="r.error || ''">{{ r.error || '—' }}</span>
              </td>
              <td>{{ fmtTime(r.created_at) }}</td>
              <td class="cw-ops">
                <button class="ghost" :disabled="busy" @click="rerun(r)">重跑</button>
                <a v-if="r.status === 'done' && r.video_url" class="ghost" :href="r.video_url" target="_blank" rel="noopener">预览</a>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="tes-empty">暂无生成任务。</p>
    </section>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import {
  generateExplanationVideo, listExplanationVideos, regenExplanationVideo,
} from '../api/admin'

const busy = ref(false)
const error = ref('')
const msg = ref('')
const rows = ref([])
const total = ref(0)
const statusFilter = ref('')

const form = reactive({ question: '', knowledge_point: '', script: '' })

function pillClass(status) {
  if (status === 'done') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'processing') return 'info'
  return 'plain'
}

function fmtTime(iso) {
  if (!iso) return '—'
  // 后端返回 ISO 字符串（含 +00:00），截到秒显示
  return String(iso).replace('T', ' ').replace(/\.\d+|\+00:00|Z$/g, '').slice(0, 19)
}

async function loadAll() {
  error.value = ''
  busy.value = true
  try {
    const params = { limit: 50, offset: 0 }
    if (statusFilter.value) params.status = statusFilter.value
    const data = await listExplanationVideos(params)
    rows.value = data.items || []
    total.value = data.total || 0
  } catch (e) {
    error.value = (e && e.message) || '加载失败'
  } finally {
    busy.value = false
  }
}

async function create() {
  if (!form.question) return
  error.value = ''
  msg.value = ''
  busy.value = true
  try {
    await generateExplanationVideo({
      question: form.question,
      knowledge_point: form.knowledge_point || null,
      script: form.script || null,
    })
    form.question = ''
    form.knowledge_point = ''
    form.script = ''
    msg.value = '已触发生成（pending → processing，稍后刷新查看结果）'
    await loadAll()
  } catch (e) {
    error.value = (e && e.message) || '触发失败'
  } finally {
    busy.value = false
  }
}

async function rerun(r) {
  error.value = ''
  busy.value = true
  try {
    await regenExplanationVideo({ question: r.question_text })
    msg.value = `任务 #${r.id} 已强制重跑`
    await loadAll()
  } catch (e) {
    error.value = (e && e.message) || '重跑失败'
  } finally {
    busy.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.cw-row { display: flex; flex-wrap: wrap; gap: 10px 16px; align-items: flex-end; margin-top: 6px; }
.cw-col { display: flex; flex-direction: column; }
.cw-col textarea { width: 360px; max-width: 90vw; }
.cw-filter { display: inline-flex; align-items: center; gap: 6px; }
.cw-total { color: #6b7280; font-size: 13px; }
.cw-table-wrap { overflow-x: auto; margin-top: 4px; }
:deep(.tes-table) { table-layout: auto; min-width: 1100px; }
:deep(.tes-table thead th) { white-space: nowrap; vertical-align: middle; padding: 10px 8px; }
:deep(.tes-table tbody td) { white-space: nowrap; vertical-align: middle; line-height: 1.6; padding: 10px 8px; }
:deep(.tes-table col.c-q) { width: 360px; }
:deep(.tes-table col.c-kp) { width: 140px; }
:deep(.tes-table col.c-st) { width: 110px; }
:deep(.tes-table col.c-dur) { width: 80px; }
:deep(.tes-table col.c-err) { width: 280px; }
:deep(.tes-table col.c-time) { width: 160px; }
:deep(.tes-table col.c-ops) { width: 140px; }
.cw-clip { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cw-clip.q { max-width: 360px; }
.cw-clip.err { max-width: 280px; color: #dc2626; }
.cw-ops { white-space: nowrap; }
.cw-ops .ghost { margin: 2px 4px 2px 0; }
:deep(.tes-pill.danger) { background: #fee2e2; color: #b91c1c; }
:deep(.tes-pill.success) { background: #dcfce7; color: #15803d; }
:deep(.tes-pill.info) { background: #e0f2fe; color: #0369a1; }
:deep(.tes-pill.plain) { background: #f1f5f9; color: #475569; }
</style>
