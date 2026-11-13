<template>
  <div class="cw-page">
    <h2 class="cw-title">课程源管理</h2>
    <p class="cw-hint">
      登记教材、题库包、网页或视频素材。<code>file</code>/<code>bank_pack</code> 读取服务器
      <code>COURSE_SOURCE_ROOT</code> 下的文件；<code>url</code>/<code>web</code> 走 SSRF 安全抓取公网页面。
      视频类型使用字幕或转写文本（SRT/VTT/TXT）。
      新建后默认暂停，需手动启用。
    </p>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="msg" class="msg">{{ msg }}</p>

    <section class="tes-card">
      <div class="cw-head"><b>新增课程源（M5-1）</b><span class="cw-grow"></span>
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
      <table v-if="rows.length" class="tes-table">
        <thead><tr><th>名称</th><th>类型</th><th>地址</th><th>状态</th><th>审核</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="s in rows" :key="s.id">
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
            <td>
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
const form = reactive({ name: '', source_type: 'file', uri: '', parser: 'auto' })
const auth = useAuthStore()
const canManage = computed(() => ['admin', 'school'].includes(auth.user?.role))

async function load() {
  await guard(async () => { rows.value = await listCourseSources() })
}
async function add() {
  await guard(async () => {
    await createCourseSource({ ...form })
    form.name = ''
    form.uri = ''
    rows.value = await listCourseSources()
    msg.value = '课程源已创建（默认暂停，需手动启用）'
  })
}
async function toggle(s) {
  await guard(async () => {
    await setCourseSourceStatus(s.id, s.status === 'active' ? 'paused' : 'active')
    rows.value = await listCourseSources()
  })
}
async function remove(s) {
  await guard(async () => {
    await deleteCourseSource(s.id)
    rows.value = await listCourseSources()
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
