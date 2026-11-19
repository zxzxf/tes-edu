<template>
  <div class="cw-page">
    <h2 class="cw-title">第三方账号</h2>
    <p class="cw-hint">
      推送中心自助注册账号审批。新注册恒为 <code>pending</code>——审批通过前其一切写操作被拒；
      <code>suspended</code> 停用的账号，名下应用与 token 在<b>校验时即时失效</b>（无需逐个吊销）。
    </p>
    <p v-if="error" class="err">{{ error }}</p>
    <p v-if="msg" class="msg">{{ msg }}</p>

    <section class="tes-card">
      <div class="cw-head">
        <b>账号列表</b><span class="cw-grow"></span>
        <label class="cw-filter">状态
          <select v-model="statusFilter" @change="load">
            <option value="">全部</option>
            <option value="pending">pending</option>
            <option value="active">active</option>
            <option value="suspended">suspended</option>
          </select>
        </label>
        <button class="ghost" :disabled="busy" @click="load">刷新</button>
      </div>

      <div class="cw-table-wrap" v-if="rows.length">
        <table class="tes-table">
          <thead>
            <tr><th>ID</th><th>邮箱</th><th>机构</th><th>状态</th><th>注册时间</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="a in rows" :key="a.account_id">
              <td>{{ a.account_id }}</td>
              <td>{{ a.email }}</td>
              <td>{{ a.org_name }}</td>
              <td><span class="tes-pill" :class="pill(a.status)">{{ a.status }}</span></td>
              <td>{{ fmt(a.created_at) }}</td>
              <td class="cw-ops">
                <button v-if="a.status === 'pending'" class="tes-btn-primary sm" :disabled="busy" @click="act(a, 'approve')">通过</button>
                <button v-if="a.status === 'pending'" class="ghost" :disabled="busy" @click="reject(a)">驳回</button>
                <button v-if="a.status === 'active'" class="ghost" :disabled="busy" @click="act(a, 'suspend')">停用</button>
                <button v-if="a.status === 'suspended'" class="ghost" :disabled="busy" @click="act(a, 'resume')">恢复</button>
                <button class="ghost" :disabled="busy" @click="openRoles(a)">授权角色</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="tes-empty">暂无账号。</p>
    </section>

    <!-- 角色授予弹窗：仅 admin 能扩权（portal 自助不扩权，计划 v1.4） -->
    <div v-if="roleTarget" class="pv-mask" @click.self="roleTarget = null">
      <div class="pv-box">
        <div class="pv-head"><b>授予角色 — {{ roleTarget.email }}</b><span class="cw-grow"></span>
          <button class="ghost" @click="roleTarget = null">关闭</button>
        </div>
        <div class="rl-body">
          <label class="cw-col"><span>角色</span>
            <select v-model="roleForm.role">
              <option value="developer">developer（应用/token/主题/send）</option>
              <option value="operator">operator（主题+查消息）</option>
              <option value="viewer">viewer（只读统计）</option>
            </select>
          </label>
          <label class="cw-col"><span>限定应用（可选，留空=该账号全部应用）</span>
            <input v-model.number="roleForm.scope_app_id" type="number" placeholder="app_id" />
          </label>
          <button class="tes-btn-primary" :disabled="busy" @click="grant">授予</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import {
  listPushAccounts, approvePushAccount, grantPushRole,
} from '../../api/pushAdmin'

const rows = ref([])
const statusFilter = ref('pending')
const busy = ref(false)
const error = ref('')
const msg = ref('')

const roleTarget = ref(null)
const roleForm = reactive({ role: 'developer', scope_app_id: null })

function pill(s) {
  if (s === 'active') return 'success'
  if (s === 'pending') return 'info'
  if (s === 'suspended') return 'danger'
  return 'plain'
}
function fmt(iso) {
  return iso ? String(iso).replace('T', ' ').slice(0, 16) : '—'
}
function brief(e) {
  return (e?.response?.data?.detail) || e?.message || '操作失败'
}

async function load() {
  error.value = ''
  busy.value = true
  try {
    rows.value = await listPushAccounts(statusFilter.value || undefined)
  } catch (e) {
    error.value = brief(e)
  } finally {
    busy.value = false
  }
}

async function act(a, action) {
  msg.value = ''
  busy.value = true
  try {
    await approvePushAccount(a.account_id, action)
    msg.value = `账号 ${a.email} → ${action === 'approve' ? 'active' : action === 'suspend' ? 'suspended（名下 token 即时失效）' : 'active'}`
    await load()
  } catch (e) {
    error.value = brief(e)
  } finally {
    busy.value = false
  }
}

async function reject(a) {
  const reason = window.prompt(`驳回 ${a.email}（可补充材料后重新申请），填写原因：`, '资料不完整')
  if (reason === null) return
  busy.value = true
  try {
    await approvePushAccount(a.account_id, 'reject', reason)
    msg.value = `已驳回 ${a.email}`
    await load()
  } catch (e) {
    error.value = brief(e)
  } finally {
    busy.value = false
  }
}

function openRoles(a) {
  roleTarget.value = a
  roleForm.role = 'developer'
  roleForm.scope_app_id = null
}

async function grant() {
  busy.value = true
  try {
    const r = await grantPushRole(roleTarget.value.account_id, roleForm.role, roleForm.scope_app_id || null)
    msg.value = r.granted ? '角色已授予' : '角色已存在（未重复授予）'
    roleTarget.value = null
  } catch (e) {
    error.value = brief(e)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.cw-col { display: flex; flex-direction: column; gap: 4px; }
.rl-body { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-end; padding: 16px; }
.pv-mask { position: fixed; inset: 0; z-index: 50; background: rgba(15, 23, 42, .55); display: flex; align-items: center; justify-content: center; padding: 16px; }
.pv-box { background: #fff; border-radius: 12px; width: min(560px, 94vw); box-shadow: 0 12px 40px rgba(0, 0, 0, .25); }
.pv-head { display: flex; align-items: center; gap: 8px; padding: 12px 16px; border-bottom: 1px solid #eef2f7; }
.cw-table-wrap { overflow-x: auto; }
.cw-ops { white-space: nowrap; }
.cw-ops button { margin: 2px 4px 2px 0; }
.sm { padding: 3px 10px; font-size: 13px; }
</style>
