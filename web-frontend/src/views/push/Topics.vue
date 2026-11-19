<template>
  <div class="cw-page">
    <h2 class="cw-title">推送主题</h2>
    <p class="cw-hint">
      主题（plan v1.2）是渠道路由 / 默认 TTL / 日频控 / 强制触达策略的挂载点。
      第三方主题由其 portal 自助注册，平台自有主题在此维护；本页跨应用总览 + 启停。
    </p>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="msg" class="msg">{{ msg }}</p>

    <section class="tes-card">
      <div class="cw-head">
        <b>应用</b>
        <div class="tp-tabs">
          <button
            v-for="a in apps" :key="a.app_id"
            class="tp-tab" :class="{ on: app_id === a.app_id }"
            @click="select(a.app_id)"
          >{{ a.name }}<span v-if="a.owner_account_id" class="tp-tp">第三方</span></button>
        </div>
        <span class="cw-grow"></span>
        <button class="tes-btn-primary sm" @click="openCreate">+ 新建主题</button>
      </div>

      <div class="cw-table-wrap" v-if="topics.length">
        <table class="tes-table">
          <thead>
            <tr><th>code</th><th>名称</th><th>默认渠道</th><th>默认TTL</th><th>日频控</th><th>强制触达</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="t in topics" :key="t.topic_id">
              <td><code>{{ t.topic_code }}</code></td>
              <td>{{ t.name }}</td>
              <td><code>{{ (t.default_channels || []).join(' + ') }}</code></td>
              <td>{{ t.default_ttl ? `${t.default_ttl}s` : '全局默认' }}</td>
              <td>{{ t.rate_limit_per_day }}</td>
              <td>{{ (t.force_channels || []).join(' + ') || '—' }}</td>
              <td><span class="tes-pill" :class="t.enabled ? 'success' : 'danger'">{{ t.enabled ? '启用' : '停用' }}</span></td>
              <td class="cw-ops">
                <button class="ghost" :disabled="busy" @click="toggle(t)">{{ t.enabled ? '停用' : '启用' }}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="tes-empty">{{ app_id ? '该应用暂无主题。' : '暂无应用，请先在「接入应用」创建。' }}</p>
    </section>

    <div v-if="form" class="pv-mask" @click.self="form = null">
      <div class="pv-box">
        <div class="pv-head"><b>新建主题（应用 #{{ app_id }}）</b><span class="cw-grow"></span>
          <button class="ghost" @click="form = null">关闭</button>
        </div>
        <div class="rl-body">
          <label class="cw-col"><span>topic_code（小写字母数字下划线）</span><input v-model.trim="form.topic_code" placeholder="如 homework" /></label>
          <label class="cw-col"><span>名称</span><input v-model.trim="form.name" maxlength="128" /></label>
          <label class="cw-col"><span>默认渠道（路由优先级：消息显式 &gt; 主题 &gt; 应用）</span>
            <span class="ck-row">
              <label><input type="checkbox" value="app_offline" v-model="form.channels" />app_offline</label>
              <label><input type="checkbox" value="wechat_oa" v-model="form.channels" />wechat_oa</label>
            </span>
          </label>
          <label class="cw-col"><span>默认 TTL 秒（空=全局默认 7 天）</span><input v-model.number="form.default_ttl" type="number" placeholder="如 86400" /></label>
          <label class="cw-col"><span>日频控（次/天）</span><input v-model.number="form.rate_limit_per_day" type="number" min="0" /></label>
          <label class="cw-col"><span>强制触达渠道（忽略收件人静音）</span>
            <span class="ck-row">
              <label><input type="checkbox" value="wechat_oa" v-model="form.force" />wechat_oa</label>
            </span>
          </label>
          <button class="tes-btn-primary" :disabled="busy || !form.topic_code || !form.name || !form.channels.length" @click="create">创建</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { listAllApps, listAppTopics, createPlatformTopic, togglePushTopic } from '../../api/pushAdmin'

const apps = ref([])
const app_id = ref(null)
const topics = ref([])
const busy = ref(false)
const error = ref('')
const msg = ref('')
const form = ref(null)

function brief(e) { return (e?.response?.data?.detail) || e?.message || '操作失败' }

async function select(id) {
  app_id.value = id
  topics.value = []
  try {
    topics.value = await listAppTopics(id)
  } catch (e) {
    error.value = brief(e)
  }
}

function openCreate() {
  if (!app_id.value) { error.value = '先选择一个应用'; return }
  form.value = { topic_code: '', name: '', channels: ['app_offline'], default_ttl: null, rate_limit_per_day: 10000, force: [] }
}

async function create() {
  busy.value = true
  try {
    await createPlatformTopic(app_id.value, {
      topic_code: form.value.topic_code,
      name: form.value.name,
      default_channels: form.value.channels,
      default_ttl: form.value.default_ttl || null,
      rate_limit_per_day: form.value.rate_limit_per_day,
      force_channels: form.value.force,
    })
    msg.value = `主题 ${form.value.topic_code} 已创建`
    form.value = null
    await select(app_id.value)
  } catch (e) {
    error.value = brief(e)
  } finally {
    busy.value = false
  }
}

async function toggle(t) {
  busy.value = true
  try {
    await togglePushTopic(t.topic_id, !t.enabled)
    await select(app_id.value)
  } catch (e) {
    error.value = brief(e)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try {
    apps.value = await listAllApps()
    if (apps.value.length) await select(apps.value[0].app_id)
  } catch (e) {
    error.value = brief(e)
  }
})
</script>

<style scoped>
.cw-col { display: flex; flex-direction: column; gap: 4px; }
.rl-body { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-end; padding: 16px; }
.ck-row { display: flex; gap: 14px; }
.tp-tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-left: 10px; }
.tp-tab { border: 1.5px solid var(--line-strong); background: #fff; border-radius: 999px; padding: 4px 14px; font-size: 13px; cursor: pointer; }
.tp-tab.on { border-color: var(--brand-2); color: var(--brand-2); background: var(--brand-grad-soft); }
.tp-tp { margin-left: 6px; font-size: 11px; color: var(--muted); }
.pv-mask { position: fixed; inset: 0; z-index: 50; background: rgba(15, 23, 42, .55); display: flex; align-items: center; justify-content: center; padding: 16px; }
.pv-box { background: #fff; border-radius: 12px; width: min(640px, 94vw); max-height: 88vh; overflow: auto; box-shadow: 0 12px 40px rgba(0, 0, 0, .25); }
.pv-head { display: flex; align-items: center; gap: 8px; padding: 12px 16px; border-bottom: 1px solid #eef2f7; }
.cw-table-wrap { overflow-x: auto; }
.cw-ops { white-space: nowrap; }
.sm { padding: 4px 12px; font-size: 13px; }
</style>
