<template>
  <div class="stock-page">
    <h2>涨跌预警</h2>

    <!-- 全局默认接收方（v1.4 §3.3，新建预警继承） -->
    <div class="default-bar">
      <b>全局默认接收方：</b>
      <label><input type="radio" value="self" v-model="defMode" @change="saveDefault" /> 推自己</label>
      <label><input type="radio" value="friends" v-model="defMode" @change="saveDefault" /> 推指定好友</label>
      <span v-if="defMode === 'friends'" class="muted">
        {{ defTokens.length ? defTokens.map(mask).join('、') : '未选好友——请先在下方新增弹窗或好友页选择' }}
      </span>
      <span class="muted">（单条预警可覆盖：接收方留空=跟随默认）</span>
    </div>

    <table class="alert-table">
      <thead>
        <tr>
          <th>标的</th><th>条件</th><th>推送</th><th>接收方</th><th>冷却</th>
          <th>状态</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="a in alerts" :key="a.alert_id">
          <td>{{ a.name || a.symbol }}<span class="muted">（{{ a.symbol }}）</span></td>
          <td>{{ condText(a) }}</td>
          <td>{{ a.push_enabled ? '微信' : '仅站内' }}</td>
          <td class="muted">{{ receiverText(a) }}</td>
          <td>{{ a.cooldown_minutes }} 分钟</td>
          <td>
            <span class="badge" :class="a.enabled ? 'active' : 'paused'">
              {{ a.enabled ? '监控中' : '已停用' }}
            </span>
          </td>
          <td>
            <button class="btn-link" @click="toggle(a)">{{ a.enabled ? '停用' : '启用' }}</button>
            <button class="btn-link" @click="edit(a)">编辑</button>
            <button class="btn-del" @click="remove(a)">删除</button>
          </td>
        </tr>
        <tr v-if="!alerts.length"><td colspan="7" class="empty">暂无预警规则</td></tr>
      </tbody>
    </table>
    <button class="btn-add" @click="openForm()">+ 新增预警</button>

    <!-- 新增/编辑弹窗 -->
    <div v-if="form" class="mask" @click.self="form = null">
      <div class="dialog">
        <h3>{{ form.alert_id ? '编辑预警' : '新增预警' }}</h3>
        <div class="field" v-if="!form.alert_id">
          <label>标的</label>
          <input v-model="form.sym" placeholder="6 位代码（如 600519）" />
        </div>
        <div class="field">
          <label>条件</label>
          <select v-model="form.condition">
            <option value="price_above">价格 ≥</option>
            <option value="price_below">价格 ≤</option>
            <option value="pct_up">涨幅 ≥ (%)</option>
            <option value="pct_down">跌幅 ≥ (%)</option>
          </select>
          <input v-model.number="form.threshold" type="number" step="0.01" class="th-input" />
          <span class="muted">{{ form.condition.startsWith('pct') ? '%' : '元' }}</span>
        </div>
        <div class="field">
          <label><input type="checkbox" v-model="form.push_enabled" /> 触达推送微信</label>
          <label style="margin-left:16px"><input type="checkbox" v-model="form.enabled" /> 启用</label>
        </div>
        <div class="field">
          <label>冷却（分钟，同规则窗口内只触发一次）</label>
          <input v-model.number="form.cooldown_minutes" type="number" min="0" class="th-input" />
        </div>
        <div class="field">
          <label>接收好友（不选=跟随全局默认；最多 10 人）</label>
          <div class="friend-pick">
            <label v-for="f in friends" :key="f.friend_id" class="fr">
              <input type="checkbox" :value="f.token" v-model="form.tokens" /> {{ f.alias }}
            </label>
            <span v-if="!friends.length" class="muted">暂无好友，去「推送好友」页添加</span>
          </div>
        </div>
        <p class="err" v-if="formErr">{{ formErr }}</p>
        <div class="dialog-foot">
          <button class="btn-add" @click="submit">保存</button>
          <button class="btn-link" @click="form = null">取消</button>
        </div>
      </div>
    </div>

    <!-- 触发记录 -->
    <h3 class="sec">触发记录</h3>
    <table class="alert-table">
      <thead>
        <tr><th>时间</th><th>标的</th><th>触发价</th><th>涨跌%</th><th>推送</th><th>接收方状态</th></tr>
      </thead>
      <tbody>
        <tr v-for="l in logs" :key="l.id">
          <td>{{ fmtTime(l.fired_at) }}</td>
          <td>{{ l.symbol }}</td>
          <td>{{ l.price.toFixed(2) }}</td>
          <td :class="l.pct > 0 ? 'up' : l.pct < 0 ? 'down' : ''">
            {{ l.pct > 0 ? '+' : '' }}{{ l.pct.toFixed(2) }}%
          </td>
          <td>{{ l.pushed ? '已发起' : '仅站内' }}</td>
          <td class="muted">
            <span v-for="(r, i) in l.receivers" :key="i" class="rcv">
              {{ mask(r.receiver) }}：{{ r.status }}<template v-if="r.msg_id">（{{ r.msg_id.slice(0, 8) }}…）</template>
            </span>
            <template v-if="!l.receivers.length">—</template>
          </td>
        </tr>
        <tr v-if="!logs.length"><td colspan="6" class="empty">暂无触发记录</td></tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import {
  addAlert, delAlert, getPushDefault, listAlertLogs, listAlerts,
  listFriends, patchAlert, setPushDefault,
} from '../../api/stock'

const alerts = ref([])
const logs = ref([])
const friends = ref([])
const defMode = ref('self')
const defTokens = ref([])
const form = ref(null)
const formErr = ref('')

const mask = (t) => (t === 'SELF' ? '本人' : `${t.slice(0, 4)}…${t.slice(-4)}`)
const fmtTime = (s) => (s ? new Date(s).toLocaleString('zh-CN', { hour12: false }) : '')
const condText = (a) => {
  const cn = { price_above: '价格 ≥', price_below: '价格 ≤', pct_up: '涨幅 ≥', pct_down: '跌幅 ≥' }[a.condition]
  return `${cn} ${a.threshold}${a.condition.startsWith('pct') ? '%' : '元'}`
}
const receiverText = (a) => {
  if (!a.push_enabled) return '不推送'
  if (!a.receiver_tokens?.length) return `默认（${defMode.value === 'self' ? '推自己' : `好友×${defTokens.value.length}`}）`
  return a.receiver_tokens.map(mask).join('、')
}

async function load() {
  const [ar, lg, fr, def] = await Promise.all([
    listAlerts(), listAlertLogs({ size: 30 }), listFriends(), getPushDefault(),
  ])
  alerts.value = ar.items
  logs.value = lg.items
  friends.value = fr.items
  defMode.value = def.mode || 'self'
  defTokens.value = def.tokens || []
}

async function saveDefault() {
  try {
    if (defMode.value === 'friends' && !defTokens.value.length && friends.value.length) {
      defTokens.value = [friends.value[0].token] // 至少一个：先默认第一位好友，可再调整
    }
    const r = await setPushDefault({ mode: defMode.value, tokens: defTokens.value })
    defTokens.value = r.tokens || []
    await load()
  } catch (e) {
    window.alert(e.response?.data?.detail || '保存默认接收方失败')
  }
}

function openForm(a) {
  formErr.value = ''
  form.value = a
    ? {
        alert_id: a.alert_id, sym: a.symbol, condition: a.condition,
        threshold: a.threshold, push_enabled: a.push_enabled,
        tokens: [...(a.receiver_tokens || [])],
        cooldown_minutes: a.cooldown_minutes, enabled: a.enabled,
      }
    : {
        alert_id: null, sym: '', condition: 'pct_up', threshold: 5,
        push_enabled: true, tokens: [], cooldown_minutes: 30, enabled: true,
      }
}

const edit = (a) => openForm(a)

async function submit() {
  const f = form.value
  if (f.tokens.length > 10) return (formErr.value = '接收好友最多 10 个（PushPlus 实名口径）')
  const body = {
    condition: f.condition, threshold: f.threshold, push_enabled: f.push_enabled,
    receiver_tokens: f.tokens, cooldown_minutes: f.cooldown_minutes || 0, enabled: f.enabled,
  }
  try {
    if (f.alert_id) {
      await patchAlert(f.alert_id, body)
    } else {
      await addAlert({ symbol: f.sym, ...body })
    }
    form.value = null
    await load()
  } catch (e) {
    formErr.value = e.response?.data?.detail || '保存失败'
  }
}

async function toggle(a) {
  await patchAlert(a.alert_id, { enabled: !a.enabled })
  await load()
}

async function remove(a) {
  if (!window.confirm(`删除「${a.name || a.symbol}」预警？`)) return
  await delAlert(a.alert_id)
  await load()
}

onMounted(load)
</script>

<style scoped>
.stock-page { padding: 20px; }
.muted { color: #888; font-size: 12px; }
.err { color: #e53935; font-size: 13px; }
.up { color: #e53935; }
.down { color: #43a047; }
.default-bar {
  display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
  background: #fff8e1; border: 1px solid #ffe082; border-radius: 6px;
  padding: 8px 12px; margin-bottom: 14px; font-size: 13px;
}
.alert-table { width: 100%; border-collapse: collapse; margin-bottom: 14px; }
.alert-table th, .alert-table td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #eee; font-size: 13px; }
.alert-table th { background: #f5f5f5; font-weight: 600; }
.badge { padding: 2px 8px; border-radius: 10px; font-size: 12px; }
.badge.active { background: #e8f5e9; color: #2e7d32; }
.badge.paused { background: #fff3e0; color: #e65100; }
.btn-add { padding: 7px 18px; background: #1976d2; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
.btn-link { color: #1976d2; border: none; background: none; cursor: pointer; margin-right: 8px; font-size: 13px; }
.btn-del { color: #e53935; border: none; background: none; cursor: pointer; font-size: 13px; }
.empty { text-align: center; color: #999; padding: 20px; }
.sec { margin: 22px 0 10px; font-size: 15px; }
.rcv { margin-right: 10px; }
.mask { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 99; }
.dialog { background: #fff; border-radius: 10px; padding: 20px 24px; width: 460px; max-height: 84vh; overflow: auto; }
.dialog h3 { margin: 0 0 14px; font-size: 16px; }
.field { margin-bottom: 12px; font-size: 13px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.field label { min-width: 60px; }
.field input, .field select { padding: 5px 8px; border: 1px solid #d0d0d0; border-radius: 4px; font-size: 13px; }
.th-input { width: 90px; }
.friend-pick { display: flex; gap: 12px; flex-wrap: wrap; }
.fr { min-width: auto !important; }
.dialog-foot { display: flex; gap: 12px; margin-top: 14px; }
</style>
