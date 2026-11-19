<template>
  <div class="cw-page">
    <h2 class="cw-title">渠道管理</h2>
    <p class="cw-hint">
      推送中心两种渠道（计划 v1.1 定稿）：<code>wechat_oa</code> 微信公众号（PushPlus 免费渠道，强触达）、
      <code>app_offline</code> App 离线收件箱。凭证<b>加密入库、永不回显</b>；
      渠道凭证到期只降级该渠道，不影响其他渠道。
    </p>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="msg" class="msg">{{ msg }}</p>

    <section class="tes-card">
      <div class="cw-head"><b>渠道配置</b><span class="cw-grow"></span>
        <button class="ghost" :disabled="busy" @click="load">刷新</button>
      </div>
      <div class="cw-table-wrap" v-if="rows.length">
        <table class="tes-table">
          <thead>
            <tr><th>类型</th><th>名称</th><th>启用</th><th>频控(次/分)</th><th>凭证</th><th>到期</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="c in rows" :key="c.channel_id">
              <td><code>{{ c.channel_type }}</code></td>
              <td>{{ c.name }}</td>
              <td><span class="tes-pill" :class="c.enabled ? 'success' : 'plain'">{{ c.enabled ? '启用' : '停用' }}</span></td>
              <td>{{ c.rate_limit_per_min }}</td>
              <td>
                <span class="tes-pill" :class="c.has_credentials ? 'success' : 'danger'">
                  {{ c.has_credentials ? '已配置' : '未配置' }}
                </span>
              </td>
              <td>
                <span v-if="c.cred_expired" class="tes-pill danger">已过期</span>
                <span v-else-if="c.cred_expiring" class="tes-pill info">临期(&lt;7天)</span>
                <span v-else>{{ c.cred_valid_until ? fmt(c.cred_valid_until) : '长期' }}</span>
              </td>
              <td class="cw-ops">
                <button class="ghost" @click="openEdit(c)">配置</button>
                <button v-if="c.channel_type === 'wechat_oa'" class="tes-btn-primary sm" :disabled="busy || !c.enabled || !c.has_credentials" @click="openTest(c)">测试发送</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="tes-empty">
        尚未配置渠道。
        <button class="ghost" @click="openEdit({ channel_type: 'wechat_oa', name: '微信公众号(PushPlus)', enabled: true, rate_limit_per_min: 30 })">+ 新建 wechat_oa</button>
      </p>
    </section>

    <!-- 配置弹窗：凭证只在提交时写入，编辑不回填（接口不回显） -->
    <div v-if="form" class="pv-mask" @click.self="form = null">
      <div class="pv-box">
        <div class="pv-head"><b>{{ form.channel_id ? '编辑' : '新建' }}渠道 {{ form.channel_type }}</b>
          <span class="cw-grow"></span><button class="ghost" @click="form = null">关闭</button>
        </div>
        <div class="rl-body">
          <label class="cw-col"><span>名称</span><input v-model.trim="form.name" maxlength="128" /></label>
          <label class="cw-col"><span>启用</span>
            <select v-model="form.enabled"><option :value="true">启用</option><option :value="false">停用</option></select>
          </label>
          <label class="cw-col"><span>频控（次/分）</span><input v-model.number="form.rate_limit_per_min" type="number" min="1" max="10000" /></label>
          <label class="cw-col"><span>凭证有效期（可选，YYYY-MM-DD）</span><input v-model.trim="form.cred_valid_until" placeholder="留空=长期" /></label>
          <label class="cw-col wide" v-if="form.channel_type === 'wechat_oa'">
            <span>PushPlus 用户 token{{ form.has_credentials ? '（已配置——留空则不改动）' : '' }}</span>
            <input v-model.trim="form.pushplus_token" :placeholder="form.has_credentials ? '不修改请留空' : 'pushplus.plus 获取的 32 位 token'" />
          </label>
          <p v-if="form.channel_type === 'app_offline'" class="cw-hint">app_offline 无需外部凭证，保存即更新名称/频控/启停。</p>
          <button class="tes-btn-primary" :disabled="busy" @click="save">保存</button>
        </div>
      </div>
    </div>

    <!-- 测试发送：真实走 emit→分发→PushPlus 全链路 -->
    <div v-if="testing" class="pv-mask" @click.self="testing = null">
      <div class="pv-box">
        <div class="pv-head"><b>渠道测试发送（真实消息）</b><span class="cw-grow"></span>
          <button class="ghost" @click="testing = null">关闭</button>
        </div>
        <div class="rl-body">
          <label class="cw-col wide">
            <span>接收人：留空=渠道主本人（微信推你）；填好友=该好友的 pushplus token（须已互加好友）</span>
            <input v-model.trim="testReceiver" placeholder="留空即推渠道配置者微信" />
          </label>
          <button class="tes-btn-primary" :disabled="busy" @click="sendTest">发送</button>
          <p v-if="testResult" class="msg">{{ testResult }}</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { listPushChannels, upsertPushChannel, testPushChannel } from '../../api/pushAdmin'

const rows = ref([])
const busy = ref(false)
const error = ref('')
const msg = ref('')
const form = ref(null)
const testing = ref(null)
const testReceiver = ref('')
const testResult = ref('')

function fmt(iso) { return iso ? String(iso).slice(0, 10) : '长期' }
function brief(e) { return (e?.response?.data?.detail) || e?.message || '操作失败' }

async function load() {
  error.value = ''
  busy.value = true
  try {
    rows.value = await listPushChannels()
  } catch (e) {
    error.value = brief(e)
  } finally {
    busy.value = false
  }
}

function openEdit(c) {
  form.value = {
    channel_id: c.channel_id || null,
    channel_type: c.channel_type,
    name: c.name,
    enabled: c.enabled,
    rate_limit_per_min: c.rate_limit_per_min || 30,
    cred_valid_until: '',
    pushplus_token: '',
    has_credentials: !!c.has_credentials,
  }
}

async function save() {
  if (form.value.channel_type === 'wechat_oa' && !form.value.has_credentials && !form.value.pushplus_token) {
    error.value = 'wechat_oa 渠道必须配置 PushPlus token'
    return
  }
  busy.value = true
  try {
    const body = {
      channel_type: form.value.channel_type,
      name: form.value.name,
      enabled: form.value.enabled,
      rate_limit_per_min: form.value.rate_limit_per_min,
      cred_valid_until: form.value.cred_valid_until || null,
    }
    if (form.value.pushplus_token) body.credentials = { pushplus_token: form.value.pushplus_token }
    await upsertPushChannel(form.value.channel_type, body)
    msg.value = `渠道 ${form.value.channel_type} 已保存（凭证密文存储，不回显）`
    form.value = null
    await load()
  } catch (e) {
    error.value = brief(e)
  } finally {
    busy.value = false
  }
}

function openTest(c) {
  testing.value = c
  testReceiver.value = ''
  testResult.value = ''
}

async function sendTest() {
  busy.value = true
  try {
    // 后端 receiver 必填：留空时前端填占位（渠道 token 自身=推自己），
    // 与 channels.py 语义一致：receiver==渠道token 走一对一，不等走好友 to。
    const r = await testPushChannel(testing.value.channel_type, testReceiver.value || 'SELF')
    testResult.value = `已提交分发 msg_id=${r.msg_id}，微信稍候到达（异步投递，留痕见消息查询页）`
  } catch (e) {
    testResult.value = `发送失败：${brief(e)}`
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.cw-col { display: flex; flex-direction: column; gap: 4px; }
.cw-col.wide { flex: 1 1 100%; }
.rl-body { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-end; padding: 16px; }
.pv-mask { position: fixed; inset: 0; z-index: 50; background: rgba(15, 23, 42, .55); display: flex; align-items: center; justify-content: center; padding: 16px; }
.pv-box { background: #fff; border-radius: 12px; width: min(620px, 94vw); box-shadow: 0 12px 40px rgba(0, 0, 0, .25); }
.pv-head { display: flex; align-items: center; gap: 8px; padding: 12px 16px; border-bottom: 1px solid #eef2f7; }
.cw-table-wrap { overflow-x: auto; }
.cw-ops { white-space: nowrap; }
.cw-ops button { margin: 2px 4px 2px 0; }
.sm { padding: 3px 10px; font-size: 13px; }
</style>
