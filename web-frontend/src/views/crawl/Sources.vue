<template>
  <div class="cw-page">
    <h2 class="cw-title">课程源管理</h2>
    <p class="cw-hint">
      登记教材、题库包、网页或视频素材。<code>file</code>/<code>bank_pack</code> 读取服务器
      <code>COURSE_SOURCE_ROOT</code> 下的文件；<code>url</code>/<code>web</code> 走 SSRF 安全抓取公网页面。
      视频类型使用字幕或转写文本（SRT/VTT/TXT）。
    </p>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="msg" class="msg">{{ msg }}</p>

    <!-- 统计概览 -->
    <div class="cw-chips">
      <span class="cw-chip">总计 <b>{{ stats.total }}</b></span>
      <span class="cw-chip ok">已启用 <b>{{ stats.active }}</b></span>
      <span class="cw-chip warn">已暂停 <b>{{ stats.paused }}</b></span>
      <span class="cw-chip info">自动发现待审 <b>{{ stats.pending }}</b></span>
    </div>

    <section class="tes-card">
      <div class="cw-head">
        <b>新增课程源（M5-1）</b><span class="cw-grow"></span>
        <label class="cw-filter">
          <span>筛选</span>
          <select v-model="reviewFilter">
            <option value="">全部</option>
            <option value="pending_review">待审</option>
            <option value="approved">已通过</option>
            <option value="rejected">已驳回</option>
          </select>
        </label>
        <button class="ghost" :disabled="busy" @click="load">刷新</button>
      </div>
      <div class="cw-row">
        <label><span>名称</span><input v-model.trim="form.name" placeholder="人教A版必修一" /></label>
        <label>
          <span>类型</span>
          <select v-model="form.source_type">
            <option value="file">文件（本地结构化 JSON）</option>
            <option value="url">URL（公网网页）</option>
            <option value="web">网页（公网 URL）</option>
            <option value="bank">题库（JSON 数组）</option>
            <option value="bank_pack">题库包（JSON）</option>
            <option value="video">视频字幕（SRT/VTT/TXT）</option>
          </select>
        </label>
        <label class="cw-grow"><span>地址 / 路径</span><input v-model.trim="form.uri" placeholder="https://… 或 bank_数学.json" /></label>
        <button class="tes-btn-primary" :disabled="busy" @click="add">新增</button>
      </div>
      <label class="cw-check">
        <input type="checkbox" v-model="form.auto_discovered" :disabled="busy" />
        <span>标记为自动发现源（待审核）——创建后进入 M5-7 审核流程，列表显示「自动发现」徽标</span>
      </label>

      <table v-if="filteredRows.length" class="tes-table">
        <thead>
          <tr>
            <th>名称</th><th>类型</th><th>地址</th><th>状态</th>
            <th>审核</th><th>创建时间</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in filteredRows" :key="s.id">
            <td>
              {{ s.name }}
              <span v-if="s.auto_discovered" class="tes-pill info" title="由定时自主发现任务创建">自动发现</span>
            </td>
            <td>{{ s.source_type }}</td>
            <td class="cw-mono">{{ s.uri }}</td>
            <td><span class="tes-pill" :class="s.status === 'active' ? 'success' : 'warn'">{{ s.status === 'active' ? '已启用' : '已暂停' }}</span></td>
            <td>
              <span v-if="s.review_status === 'pending_review'" class="tes-pill warn">待审</span>
              <span v-else-if="s.review_status === 'approved'" class="tes-pill success">已通过</span>
              <span v-else-if="s.review_status === 'rejected'" class="tes-pill danger">已驳回</span>
              <span v-else class="cw-muted">—</span>
            </td>
            <td class="cw-muted">{{ fmt(s.created_at) }}</td>
            <td class="cw-ops">
              <template v-if="s.review_status === 'pending_review'">
                <button class="tes-btn-primary" :disabled="busy" @click="review(s, 'approve')">通过</button>
                <button class="ghost cw-ghost-danger" :disabled="busy" @click="review(s, 'reject')">驳回</button>
              </template>
              <template v-else>
                <button class="ghost" :disabled="busy" @click="toggle(s)">{{ s.status === 'active' ? '暂停' : '启用' }}</button>
                <button v-if="canManage" class="ghost cw-ghost-danger" :disabled="busy" @click="remove(s)">删除</button>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="tes-empty">还没有课程源。</p>
    </section>

    <div class="cw-links">
      <router-link class="cw-link" to="/course-intake/jobs">去建采集任务 →</router-link>
      <router-link class="cw-link" to="/course-intake/workbench">返回工作台</router-link>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { useCrawlRequest } from '../../composables/useCrawlRequest'
import {
  createCourseSource, deleteCourseSource, listCourseSources, setCourseSourceStatus,
  reviewCourseSource,
} from '../../api/admin'

const { busy, error, msg, guard } = useCrawlRequest()
const rows = ref([])
const form = reactive({ name: '', source_type: 'file', uri: '', parser: 'auto', auto_discovered: false })
const reviewFilter = ref('')
const auth = useAuthStore()
// 课程源页本身要求 teacher/admin/school 可见，删除同样对三者开放（租户隔离保证只删本租户数据）
const canManage = computed(() => ['admin', 'school', 'teacher'].includes(auth.user?.role))

const stats = computed(() => {
  const all = rows.value
  return {
    total: all.length,
    active: all.filter((s) => s.status === 'active').length,
    paused: all.filter((s) => s.status === 'paused').length,
    pending: all.filter((s) => s.review_status === 'pending_review').length,
  }
})
const filteredRows = computed(() =>
  reviewFilter.value ? rows.value.filter((s) => s.review_status === reviewFilter.value) : rows.value
)

function fmt(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d)) return iso
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function load() {
  await guard(async () => { rows.value = await listCourseSources() })
}
async function add() {
  await guard(async () => {
    await createCourseSource({
      name: form.name, source_type: form.source_type, uri: form.uri,
      parser: form.parser, auto_discovered: form.auto_discovered,
    })
    form.name = ''
    form.uri = ''
    form.auto_discovered = false
    rows.value = await listCourseSources()
    msg.value = form.auto_discovered
      ? '已创建为自动发现源（待审核），可在审核列「通过 / 驳回」'
      : '课程源已创建（默认暂停，需手动启用）'
  })
}
async function toggle(s) {
  await guard(async () => {
    await setCourseSourceStatus(s.id, s.status === 'active' ? 'paused' : 'active')
    rows.value = await listCourseSources()
  })
}
async function remove(s) {
  if (!window.confirm(`确认删除课程源「${s.name}」？关联的采集任务与运行日志也会一并移除。`)) return
  await guard(async () => {
    await deleteCourseSource(s.id)
    rows.value = await listCourseSources()
    msg.value = '课程源已删除'
  })
}
async function review(s, action) {
  await guard(async () => {
    await reviewCourseSource(s.id, action)
    msg.value = action === 'approve' ? '待审源已通过，草稿采集任务已启用' : '待审源已驳回并清理'
    rows.value = await listCourseSources()
  })
}
onMounted(load)
</script>

<style scoped>
.cw-chips {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin: 4px 0 16px;
}
.cw-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 13px;
  background: #f2f4f7;
  color: #41526b;
  border: 1px solid #e3e8ef;
}
.cw-chip b { font-size: 14px; color: #1f2a3d; }
.cw-chip.ok { background: #e8f6ee; border-color: #bfe6cf; color: #1f7a47; }
.cw-chip.ok b { color: #1f7a47; }
.cw-chip.warn { background: #fff4e5; border-color: #ffe0b3; color: #a35b00; }
.cw-chip.warn b { color: #a35b00; }
.cw-chip.info { background: #e8f1fb; border-color: #c5dcf6; color: #1f5fae; }
.cw-chip.info b { color: #1f5fae; }
.cw-filter { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: #5a6b85; }
.cw-filter select { padding: 4px 8px; border-radius: 6px; border: 1px solid #d6dde8; }
.cw-check {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0 2px;
  font-size: 13px;
  color: #41526b;
}
.cw-check input { width: 16px; height: 16px; }
.cw-ops { display: flex; gap: 6px; flex-wrap: wrap; }
</style>
