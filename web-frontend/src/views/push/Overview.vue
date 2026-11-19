<template>
  <div class="cw-page">
    <h2 class="cw-title">推送总览</h2>
    <p class="cw-hint">
      通用 App 推送中心运行概览。隐私口径（计划 v1.4）：本页只见<b>数量与状态分布</b>，
      不展示任何第三方消息正文。
    </p>
    <p v-if="error" class="err">{{ error }}</p>

    <section class="tes-card">
      <div class="cw-head">
        <b>统计窗口</b><span class="cw-grow"></span>
        <label class="cw-filter">近
          <select v-model.number="days" @change="load">
            <option :value="1">1 天</option>
            <option :value="7">7 天</option>
            <option :value="30">30 天</option>
            <option :value="90">90 天</option>
          </select>
        </label>
        <button class="ghost" @click="load">刷新</button>
      </div>
      <div class="po-kpis" v-if="stats">
        <div class="po-kpi"><b>{{ totalMsg }}</b><span>消息总量</span></div>
        <div class="po-kpi ok"><b>{{ sentPct }}</b><span>送达率(sent/全部)</span></div>
        <div class="po-kpi" :class="{ danger: (stats.accounts.pending || 0) > 0 }">
          <b>{{ stats.accounts.pending || 0 }}</b><span>待审批账号</span>
        </div>
        <div class="po-kpi" :class="{ warn: stats.tokens_expiring_7d > 0 }">
          <b>{{ stats.tokens_expiring_7d }}</b><span>7 天内到期 token</span>
        </div>
      </div>
    </section>

    <div class="po-cols">
      <section class="tes-card">
        <div class="cw-head"><b>消息状态分布</b></div>
        <table class="tes-table" v-if="stats && Object.keys(stats.message_status).length">
          <thead><tr><th>状态</th><th>数量</th></tr></thead>
          <tbody>
            <tr v-for="(n, k) in stats.message_status" :key="k">
              <td><span class="tes-pill" :class="msgPill(k)">{{ k }}</span></td>
              <td>{{ n }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="tes-empty">窗口内暂无消息。</p>
      </section>

      <section class="tes-card">
        <div class="cw-head"><b>渠道投递结果</b></div>
        <table class="tes-table" v-if="stats && Object.keys(stats.delivery_results).length">
          <thead><tr><th>渠道</th><th>结果码</th><th>次数</th></tr></thead>
          <tbody>
            <template v-for="(results, chType) in stats.delivery_results" :key="chType">
              <tr v-for="(n, code) in results" :key="chType + code">
                <td>{{ chType }}</td>
                <td><span class="tes-pill" :class="code === 'ok' ? 'success' : 'plain'">{{ code }}</span></td>
                <td>{{ n }}</td>
              </tr>
            </template>
          </tbody>
        </table>
        <p v-else class="tes-empty">窗口内暂无投递记录。</p>
      </section>

      <section class="tes-card">
        <div class="cw-head"><b>第三方账号状态</b></div>
        <table class="tes-table" v-if="stats && Object.keys(stats.accounts).length">
          <thead><tr><th>状态</th><th>数量</th></tr></thead>
          <tbody>
            <tr v-for="(n, k) in stats.accounts" :key="k">
              <td><span class="tes-pill" :class="accPill(k)">{{ k }}</span></td>
              <td>{{ n }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="tes-empty">暂无第三方注册账号。</p>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { getPushStats } from '../../api/pushAdmin'

const days = ref(7)
const stats = ref(null)
const error = ref('')

const totalMsg = computed(() =>
  Object.values(stats.value?.message_status || {}).reduce((a, b) => a + b, 0))
const sentPct = computed(() => {
  const t = totalMsg.value
  if (!t) return '—'
  const sent = stats.value?.message_status?.sent || 0
  return `${Math.round((sent / t) * 100)}%`
})

function msgPill(k) {
  if (k === 'sent') return 'success'
  if (k === 'dead' || k === 'expired') return 'danger'
  if (k === 'partial') return 'info'
  return 'plain'
}
function accPill(k) {
  if (k === 'active') return 'success'
  if (k === 'pending') return 'info'
  if (k === 'suspended') return 'danger'
  return 'plain'
}

async function load() {
  error.value = ''
  try {
    stats.value = await getPushStats(days.value)
  } catch (e) {
    error.value = (e?.response?.data?.detail) || e?.message || '加载失败'
  }
}
onMounted(load)
</script>

<style scoped>
.po-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-top: 8px; }
.po-kpi { border: 1px solid var(--line); border-radius: var(--r-md); padding: 14px 16px; background: var(--bg-2); }
.po-kpi b { display: block; font-size: 26px; color: var(--ink); }
.po-kpi span { font-size: 13px; color: var(--muted); }
.po-kpi.ok b { color: var(--success); }
.po-kpi.danger b { color: var(--danger); }
.po-kpi.warn b { color: var(--warning); }
.po-cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 14px; margin-top: 14px; }
</style>
