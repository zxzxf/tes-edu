<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">用户管理</h2>

    <div class="tes-toolbar">
      <button class="tes-btn-primary" v-has-permission="'system:user:add'" @click="openCreate">新建用户</button>
      <input class="tes-search" v-model.trim="keyword" placeholder="搜索用户名…" @keyup.enter="load" />
      <select v-model="statusFilter" class="tes-select" @change="load">
        <option :value="null">全部状态</option>
        <option :value="1">启用</option>
        <option :value="0">禁用</option>
      </select>
      <button class="tes-btn" @click="load">刷新</button>
      <span class="tes-tag">{{ rows.length }} 个用户</span>
      <span v-if="loading" class="tes-tag">加载中…</span>
    </div>

    <p v-if="error" class="tes-empty err">{{ error }}</p>

    <div v-if="rows.length" class="tes-table-card">
      <div class="table-scroll">
      <table class="tes-table user-table">
        <thead>
          <tr>
            <th>用户名</th><th>租户</th><th>区域</th><th>角色</th>
            <th>状态</th><th>创建时间</th><th class="op">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.user_id">
            <td><b>{{ row.username }}</b><code class="mid">{{ row.user_id }}</code></td>
            <td>{{ tenantName(row.tenant_id) || '—' }}</td>
            <td>{{ regionName(row.region_id) || '—' }}</td>
            <td>
              <span v-for="rid in (row.role_ids || [])" :key="rid" class="tes-pill info sm">
                {{ roleName(rid) }}
              </span>
              <span v-if="!row.role_ids || !row.role_ids.length" class="muted">未授权</span>
            </td>
            <td>
              <span class="tes-pill" :class="row.status === 1 ? '' : 'warn'">
                {{ row.status === 1 ? '启用' : '禁用' }}
              </span>
            </td>
            <td>{{ fmtTime(row.created_at) }}</td>
            <td class="op">
              <button class="tes-btn sm" v-has-permission="'system:user:edit'" @click="openEdit(row)">编辑</button>
              <button class="tes-btn sm" v-has-permission="'system:user:resetpwd'" @click="openReset(row)">重置密码</button>
              <button class="tes-btn sm danger" v-has-permission="'system:user:delete'" @click="remove(row)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      </div>
    </div>
    <p v-else-if="!loading" class="tes-empty">暂无用户，点击「新建用户」创建。</p>

    <!-- 新建 / 编辑对话框 -->
    <div v-if="dialog" class="mask" @click.self="dialog = null">
      <div class="tes-card dialog" role="dialog" aria-modal="true">
        <h3>{{ dialog.mode === 'create' ? '新建用户' : `编辑用户：${form.username}` }}</h3>
        <div class="form-grid">
          <label>用户名
            <input v-model="form.username" :disabled="dialog.mode === 'edit'" placeholder="登录账号，如 teacher@tes.edu" />
          </label>
          <label v-if="dialog.mode === 'create'">初始密码
            <input v-model="form.password" type="text" placeholder="至少 6 位；创建后可在列表重置" />
          </label>
          <label>状态
            <select v-model.number="form.status">
              <option :value="1">启用</option>
              <option :value="0">禁用</option>
            </select>
          </label>
          <label>租户（学校）
            <select v-model="form.tenant_id">
              <option :value="null">（平台级，不绑定学校）</option>
              <option v-for="t in tenants" :key="t.tenant_id" :value="t.tenant_id">{{ t.name }}</option>
            </select>
          </label>
          <label>区域
            <select v-model="form.region_id">
              <option :value="null">（不绑定区域）</option>
              <option v-for="r in regions" :key="r.region_id" :value="r.region_id">{{ r.region_name }}</option>
            </select>
          </label>
        </div>

        <div class="form-block">
          <div class="block-title">分配角色</div>
          <div class="role-checks">
            <label v-for="r in roles" :key="r.role_id" class="role-chk">
              <input type="checkbox" :value="r.role_id" v-model="form.role_ids" />
              <b>{{ r.role_name }}</b><code>{{ r.role_code }}</code>
            </label>
            <p v-if="!roles.length" class="muted">暂无可选角色</p>
          </div>
        </div>

        <p v-if="formError" class="tes-empty err">{{ formError }}</p>
        <div class="dlg-actions">
          <button class="tes-btn" @click="dialog = null">取消</button>
          <button class="tes-btn-primary" :disabled="saving" @click="save">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 重置密码对话框 -->
    <div v-if="resetDlg" class="mask" @click.self="resetDlg = null">
      <div class="tes-card dialog" role="dialog" aria-modal="true">
        <h3>重置密码：{{ resetDlg.username }}</h3>
        <p class="hint">可指定新密码（留空将随机生成 12 位）。重置后旧密码立即失效。</p>
        <div class="form-grid">
          <label>新密码
            <input v-model="resetPwd" type="text" placeholder="留空则随机生成" />
          </label>
        </div>
        <p v-if="resetError" class="tes-empty err">{{ resetError }}</p>
        <p v-if="resetResult" class="reset-result">
          ✅ 重置成功，新密码（请妥善告知用户，仅显示一次）：
          <code>{{ resetResult }}</code>
        </p>
        <div class="dlg-actions">
          <button class="tes-btn" @click="resetDlg = null">关闭</button>
          <button class="tes-btn-primary" :disabled="resetSaving" @click="doReset">
            {{ resetSaving ? '重置中…' : '确认重置' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import {
  createUser, deleteUser, listRegions, listRoles, listTenants, listUsers,
  resetUserPassword, updateUser,
} from '../api/rbac'
import { errMsg } from '../utils/errmsg'

const rows = ref([])
const loading = ref(false)
const error = ref('')
const keyword = ref('')
const statusFilter = ref(null)

const roles = ref([])
const tenants = ref([])
const regions = ref([])

const dialog = ref(null)      // { mode: 'create' | 'edit' }
const saving = ref(false)
const formError = ref('')
const form = ref(blankForm())

const resetDlg = ref(null)    // { user_id, username }
const resetPwd = ref('')
const resetSaving = ref(false)
const resetError = ref('')
const resetResult = ref('')

function blankForm() {
  return {
    username: '', password: '', status: 1,
    tenant_id: null, region_id: null, role_ids: [],
  }
}

const roleMap = ref({})
const tenantMap = ref({})
const regionMap = ref({})
function roleName(id) { return roleMap.value[id]?.role_name || id }
function tenantName(id) { return id ? (tenantMap.value[id]?.name || id) : '' }
function regionName(id) { return id ? (regionMap.value[id]?.region_name || id) : '' }

function fmtTime(v) {
  if (!v) return '—'
  // 只留 MM-DD HH:mm：完整时间戳会把该列撑宽，7 列表格更容易把操作列挤出可视区
  return String(v).slice(5, 16).replace('T', ' ')
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = {}
    if (keyword.value) params.keyword = keyword.value
    if (statusFilter.value !== null) params.status = statusFilter.value
    const [userRows, roleRows, tenantRows, regionRows] = await Promise.all([
      listUsers(params),
      listRoles(),
      listTenants(),
      listRegions(),
    ])
    rows.value = userRows
    roles.value = roleRows
    tenants.value = tenantRows
    regions.value = regionRows
    roleMap.value = Object.fromEntries(roleRows.map((r) => [r.role_id, r]))
    tenantMap.value = Object.fromEntries(tenantRows.map((t) => [t.tenant_id, t]))
    regionMap.value = Object.fromEntries(regionRows.map((r) => [r.region_id, r]))
  } catch (e) {
    error.value = errMsg(e, '用户加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.value = blankForm()
  formError.value = ''
  dialog.value = { mode: 'create' }
}

function openEdit(row) {
  form.value = {
    username: row.username,
    password: '',
    status: row.status ?? 1,
    tenant_id: row.tenant_id || null,
    region_id: row.region_id || null,
    role_ids: [...(row.role_ids || [])],
  }
  formError.value = ''
  dialog.value = { mode: 'edit', user_id: row.user_id }
}

function validate() {
  if (dialog.value.mode === 'create') {
    if (!form.value.username.trim()) return '用户名不能为空'
    if (!form.value.password || form.value.password.length < 6) return '初始密码至少 6 位'
  }
  return ''
}

async function save() {
  formError.value = validate()
  if (formError.value) return
  saving.value = true
  try {
    if (dialog.value.mode === 'create') {
      await createUser({
        username: form.value.username.trim(),
        password: form.value.password,
        status: form.value.status,
        tenant_id: form.value.tenant_id || null,
        region_id: form.value.region_id || null,
        role_ids: form.value.role_ids,
      })
    } else {
      await updateUser(dialog.value.user_id, {
        username: form.value.username.trim(),
        status: form.value.status,
        tenant_id: form.value.tenant_id || null,
        region_id: form.value.region_id || null,
        role_ids: form.value.role_ids,
      })
    }
    dialog.value = null
    await load()
  } catch (e) {
    formError.value = errMsg(e, '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  if (!window.confirm(`确定删除用户「${row.username}」？该操作不可恢复（角色关联一并清除）。`)) return
  try {
    await deleteUser(row.user_id)
    await load()
  } catch (e) {
    error.value = errMsg(e, '删除失败')
  }
}

function openReset(row) {
  resetDlg.value = { user_id: row.user_id, username: row.username }
  resetPwd.value = ''
  resetError.value = ''
  resetResult.value = ''
}

async function doReset() {
  resetSaving.value = true
  resetError.value = ''
  resetResult.value = ''
  try {
    const res = await resetUserPassword(resetDlg.value.user_id, resetPwd.value.trim() || undefined)
    resetResult.value = res.password
    await load()
  } catch (e) {
    resetError.value = errMsg(e, '重置失败')
  } finally {
    resetSaving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.tes-search {
  width: 200px; padding: 7px 10px; border: 1px solid var(--line);
  border-radius: var(--r-sm); font-size: 13px;
}
.tes-select {
  padding: 7px 10px; border: 1px solid var(--line);
  border-radius: var(--r-sm); font-size: 13px; background: #fff;
}
.mid { margin-left: 8px; font-size: 11px; color: var(--muted); }
.muted { color: var(--muted); }
.tes-pill.sm { font-size: 11px; padding: 2px 7px; margin-right: 4px; }
td.op, th.op { white-space: nowrap; text-align: right; }

/* ⚠️ .tes-table-card 是 overflow:hidden（全局样式），列一多就会把最右的操作列
   整列裁掉——用户看到的就是「编辑/删除按钮不见了」（BYOM 275c272 同款坑，
   本页 7 列同样中招）。两层兜底：内层 .table-scroll 提供横向滚动 + 表格
   min-width 让浏览器出滚动条而不是压缩/裁剪；配合上面把时间戳缩到 MM-DD HH:mm。 */
.table-scroll { overflow-x: auto; }
.user-table { min-width: 900px; }
.tes-btn.sm { padding: 5px 12px; font-size: 12px; border-radius: var(--r-sm); }
.tes-btn.sm.danger { color: var(--danger); border-color: #ffd6de; }
.tes-btn.sm.danger:hover { background: #fff0f3; color: var(--danger); }
.tes-empty.err { background: #ffe9ee; border-color: #ffd6de; color: var(--danger); text-align: left; }

/* ---------- 对话框 ---------- */
.mask {
  position: fixed; inset: 0; z-index: 50;
  background: rgba(31, 27, 46, .45);
  display: grid; place-items: center; padding: 24px;
}
.dialog { width: 720px; max-width: 100%; max-height: 88vh; overflow: auto; }
.dialog h3 { margin-bottom: 16px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; }
.form-grid label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; font-weight: 600; color: var(--ink-2); }
.form-block { margin-top: 16px; border-top: 1px dashed var(--line); padding-top: 14px; }
.block-title { font-size: 13px; font-weight: 700; color: var(--ink-2); margin-bottom: 10px; }
.role-checks { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px 12px; }
.role-chk { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 500; }
.role-chk input { width: auto; }
.role-chk code { font-size: 11px; color: var(--muted); }
.hint { color: var(--muted); font-size: 13px; margin: 0 0 14px; }
.reset-result { background: #e8f7ee; border: 1px solid #bfe8cf; color: #1f7a45; border-radius: var(--r-sm); padding: 10px 12px; font-size: 13px; }
.reset-result code { font-weight: 700; }
.dlg-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
</style>
