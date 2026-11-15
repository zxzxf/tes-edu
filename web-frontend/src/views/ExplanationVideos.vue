<template>
  <div class="cw-page">
    <h2 class="cw-title">讲解视频生成任务</h2>
    <p class="cw-hint">
      数字人讲题短视频（#28）生成任务管理。状态流转
      <code>pending → processing → done / failed</code>；<code>done</code> 可直接在本页播放器里看片，
      <code>failed</code> 可点「重跑」强制重新生成。{{ configNote }}
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

    <!-- 成片预览：进页面就能看到视频，不必先猜题干、也不必逐行点「播放」 -->
    <section class="tes-card" v-if="inlineRows.length">
      <div class="cw-head">
        <b>成片预览</b><span class="cw-grow"></span>
        <label class="cw-filter">选择成片
          <select v-model="selectedInlineId" @change="onInlineChange">
            <option v-for="r in inlineRows" :key="r.id" :value="r.id">
              {{ clip(r.question_text) }}
            </option>
          </select>
        </label>
      </div>
      <p v-if="inlineLoading" class="pv-loading">视频加载中…</p>
      <p v-else-if="inlineErr" class="err">{{ inlineErr }}</p>
      <video
        v-else-if="inlineUrl"
        class="iv-video"
        :src="inlineUrl"
        controls
        autoplay
        muted
        loop
        playsinline
      ></video>
      <div class="pv-meta">
        <span class="cw-clip q" :title="inlineRow?.question_text">{{ inlineRow?.question_text || '—' }}</span>
        <span v-if="inlineRow?.duration_sec != null" class="pv-dur">
          时长 {{ inlineRow.duration_sec.toFixed(1) }}s
        </span>
      </div>
      <p class="cw-hint">
        播放器默认静音自动循环，点音量键可开声音；列表里的「播放」可在大窗中查看。
      </p>
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
                <button
                  v-if="r.status === 'done' && r.video_url"
                  class="ghost"
                  :disabled="previewLoading && previewRow && previewRow.id === r.id"
                  @click="playPreview(r)"
                >播放</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="tes-empty">暂无生成任务。</p>
    </section>

    <!-- 播放预览弹窗 -->
    <div v-if="previewRow" class="pv-mask" @click.self="closePreview">
      <div class="pv-box">
        <div class="pv-head">
          <b>播放预览</b>
          <span class="cw-grow"></span>
          <button class="ghost" @click="closePreview">关闭</button>
        </div>
        <p v-if="previewLoading" class="pv-loading">视频加载中…</p>
        <p v-else-if="previewErr" class="err">{{ previewErr }}</p>
        <video
          v-else-if="previewUrl"
          class="pv-video"
          :src="previewUrl"
          controls
          autoplay
          playsinline
        ></video>
        <div v-if="previewRow" class="pv-meta">
          <span class="cw-clip q" :title="previewRow.question_text">{{ previewRow.question_text || '—' }}</span>
          <span v-if="previewRow.duration_sec != null" class="pv-dur">时长 {{ previewRow.duration_sec.toFixed(1) }}s</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import {
  generateExplanationVideo, getExplanationConfig, listExplanationVideos,
  regenExplanationVideo, streamExplanationVideo,
} from '../api/admin'

const busy = ref(false)
const error = ref('')
const msg = ref('')
const rows = ref([])
const total = ref(0)
const statusFilter = ref('')
const configNote = ref('')

// 成片预览：进页面即加载最新一条 done 视频（blob → objectURL），
// 之前这里只有文字表格，教师看不到任何画面，也容易误判「视频没生成」。
const inlineRows = computed(() => rows.value.filter(r => r.status === 'done' && r.video_url))
const selectedInlineId = ref('')
const inlineRow = ref(null)
const inlineUrl = ref('')
const inlineLoading = ref(false)
const inlineErr = ref('')

function clip(text, n = 26) {
  const s = String(text || '—')
  return s.length > n ? `${s.slice(0, n)}…` : s
}

async function loadInline(row) {
  if (inlineUrl.value) {
    URL.revokeObjectURL(inlineUrl.value)
    inlineUrl.value = ''
  }
  inlineRow.value = row || null
  inlineErr.value = ''
  if (!row) return
  inlineLoading.value = true
  try {
    const blob = await streamExplanationVideo(row.id)
    inlineUrl.value = URL.createObjectURL(blob)
  } catch (e) {
    inlineErr.value = (e && e.message) || '视频加载失败'
  } finally {
    inlineLoading.value = false
  }
}

function onInlineChange() {
  loadInline(inlineRows.value.find(r => r.id === selectedInlineId.value))
}

// 播放预览：inline <video> 弹窗
const previewRow = ref(null)
const previewUrl = ref('')
const previewLoading = ref(false)
const previewErr = ref('')

async function playPreview(r) {
  if (!r || r.status !== 'done' || !r.video_url) return
  previewRow.value = r
  previewUrl.value = ''
  previewErr.value = ''
  previewLoading.value = true
  try {
    // 走 admin 令牌取 blob（规避 <video src> 直链缺 Authorization 头被网关拦），
    // 再转 objectURL 内联播放。
    const blob = await streamExplanationVideo(r.id)
    previewUrl.value = URL.createObjectURL(blob)
  } catch (e) {
    previewErr.value = (e && e.message) || '视频加载失败'
  } finally {
    previewLoading.value = false
  }
}

function closePreview() {
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = ''
  }
  previewRow.value = null
  previewErr.value = ''
}

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
    // 配置说明单独取，失败不影响列表（后端未升级时 config 可能不存在）
    try {
      const cfg = await getExplanationConfig()
      configNote.value = cfg && cfg.note ? `当前实现：${cfg.note}` : ''
    } catch (_) {
      configNote.value = ''
    }
    const first = inlineRows.value[0] || null
    selectedInlineId.value = first ? first.id : ''
    await loadInline(first)
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
onUnmounted(() => {
  if (inlineUrl.value) URL.revokeObjectURL(inlineUrl.value)
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
})
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
:deep(.tes-table col.c-ops) { width: 150px; }
.cw-clip { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cw-clip.q { max-width: 360px; }
.cw-clip.err { max-width: 280px; color: #dc2626; }
.cw-ops { white-space: nowrap; }
.cw-ops .ghost { margin: 2px 4px 2px 0; }
:deep(.tes-pill.danger) { background: #fee2e2; color: #b91c1c; }
:deep(.tes-pill.success) { background: #dcfce7; color: #15803d; }
:deep(.tes-pill.info) { background: #e0f2fe; color: #0369a1; }
:deep(.tes-pill.plain) { background: #f1f5f9; color: #475569; }

/* 播放预览弹窗 */
.pv-mask {
  position: fixed; inset: 0; z-index: 50;
  background: rgba(15, 23, 42, 0.55);
  display: flex; align-items: center; justify-content: center;
  padding: 16px;
}
.pv-box {
  background: #fff; border-radius: 12px; width: min(760px, 94vw);
  max-height: 92vh; overflow: auto; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
}
.pv-head {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 16px; border-bottom: 1px solid #eef2f7;
}
.pv-head b { font-size: 15px; }
.pv-loading { padding: 40px 16px; text-align: center; color: #6b7280; }
.pv-video {
  display: block; width: 100%; max-height: 60vh; background: #000;
  border-radius: 0;
}
.iv-video {
  display: block; width: 100%; height: 320px; object-fit: contain;
  background: #000; border-radius: 8px; margin-top: 6px;
}
.pv-meta {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 12px 16px; border-top: 1px solid #eef2f7; font-size: 13px; color: #475569;
}
.pv-meta .q { max-width: 560px; }
.pv-dur { color: #0369a1; white-space: nowrap; }
</style>
