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
              <option v-for="t in tenantOptions" :key="t.tenant_id" :value="t.tenant_id">{{ t.name }}</option>
            </select>
          </label>
          <label>区域
            <select v-model="form.region_id">
              <option :value="null">（不绑定区域）</option>
              <option v-for="r in regionOptions" :key="r.region_id" :value="r.region_id">{{ r.region_name }}</option>
            </select>
          </label>
        </div>

        <div class="form-block">
          <div class="block-title">分配角色</div>
          <div class="role-checks">
            <label
              v-for="r in roleOptions" :key="r.role_id" class="role-chk"
              :class="{ locked: r.locked }"
              :title="r.locked ? '平台级角色，仅回显不可修改' : ''"
            >
              <input type="checkbox" :value="r.role_id" v-model="form.role_ids" :disabled="r.locked" />
              <b>{{ r.role_name }}</b><code>{{ r.role_code }}</code>
              <span v-if="r.locked" class="lock-tip">平台级</span>
            </label>
            <p v-if="!roleOptions.length" class="muted">暂无可选角色</p>
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
import { computed, onMounted, ref } from 'vue'
import {
  createUser, deleteUser, listRegions, listRoles, listTenants, listUsers,
  resetUserPassword, updateUser,
} from '../api/rbac'
import { dictMap } from '../api/dict'
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
const roleDictMap = ref({})    // 数据字典：角色名映射（任意角色可读，优先使用）
const regionDictMap = ref({})  // 数据字典：区域名映射（dict_code = region_id）
const tenantMap = ref({})
const regionMap = ref({})
function roleName(id) {
  // 优先用数据字典翻译（school 也能读到 /dict/map），失败回退到角色接口映射 / 原始编码
  return roleDictMap.value[id] || roleMap.value[id]?.role_name || id
}
function tenantName(id) { return id ? (tenantMap.value[id]?.name || id) : '' }
function regionName(id) {
  // 区域名同样优先从数据字典读取：school 无权读 /region，regionMap 往往为空，
  // 直接显示 rgn_east 这类原始编码；字典 /dict/map 对所有已登录角色开放。
  return id ? (regionDictMap.value[id] || regionMap.value[id]?.region_name || id) : ''
}

// ---- 编辑 / 新建对话框的下拉选项 ----
// school 对 /role、/region 是 403，roles/regions 均为空；而 /dict/map 对所有
// 已登录角色开放，故用字典兜底生成选项，保证次级角色打开对话框也能看到数据。
// admin 仍优先用接口返回的完整数据（含 role_code 等附加字段）。
// 平台级角色：school 是租户级管理员，不应通过编辑用户给自己或他人授予平台级
// 权限（否则可提权）。这些角色仍出现在选项里以正确回显已有授权，但 checkbox
// 禁用；form.role_ids 从用户行初始化，保存时原角色不会被误删。
const PLATFORM_ROLES = new Set(['role_admin', 'role_region_admin'])
const roleOptions = computed(() => {
  if (roles.value.length) {
    return roles.value.map((r) => ({
      role_id: r.role_id, role_name: r.role_name || r.role_id,
      role_code: r.role_code || r.role_id, locked: false,
    }))
  }
  return Object.entries(roleDictMap.value).map(([id, name]) => ({
    role_id: id, role_name: name || id, role_code: id,
    locked: PLATFORM_ROLES.has(id),
  }))
})

const regionOptions = computed(() => {
  if (regions.value.length) {
    return regions.value.map((r) => ({
      region_id: r.region_id, region_name: r.region_name || r.region_id,
    }))
  }
  return Object.entries(regionDictMap.value).map(([id, name]) => ({
    region_id: id, region_name: name || id,
  }))
})

// /tenant 对 school 有意做租户隔离（只返回本校），但 /user 会返回其它租户的用户；
// 编辑这类用户时 select 因无匹配 option 会显示空白。补一个「仅用于回显当前值」
// 的 option，不扩大可切换的租户范围、不破坏后端隔离边界。
const tenantOptions = computed(() => {
  const list = tenants.value.map((t) => ({
    tenant_id: t.tenant_id, name: t.name || t.tenant_id,
  }))
  const cur = form.value.tenant_id
  if (cur && !list.some((t) => t.tenant_id === cur)) {
    list.unshift({ tenant_id: cur, name: tenantMap.value[cur]?.name || cur })
  }
  return list
})

function fmtTime(v) {
  if (!v) return '—'
  // 只留 MM-DD HH:mm：完整时间戳会把该列撑宽，7 列表格更容易把操作列挤出可视区
  return String(v).slice(5, 16).replace('T', ' ')
}

async function load() {
  loading.value = true
  error.value = ''
  const params = {}
  if (keyword.value) params.keyword = keyword.value
  if (statusFilter.value !== null) params.status = statusFilter.value
  try {
    // 用户列表是页面核心数据，优先独立拉取：学校管理员等对 /user 有权，
    // 必须保证即便其它接口失败也能正常展示。
    rows.value = await listUsers(params)
  } catch (e) {
    error.value = errMsg(e, '用户加载失败')
    rows.value = []
    loading.value = false
    return
  }
  // 下拉辅助数据（角色/租户/区域）与用户列表解耦：部分角色（如学校管理员）
  // 无权读取 /role、/region。用 allSettled 让有权限的接口各自成功——若用
  // Promise.all，任一 403 会整批失败，连 /tenant 的映射也拿不到，租户列会
  // 一起退化成 demo / school_pilot_01 这类原始编码。
  try {
    const [roleRes, tenantRes, regionRes] = await Promise.allSettled([
      listRoles(), listTenants(), listRegions(),
    ])
    if (roleRes.status === 'fulfilled') {
      roles.value = roleRes.value
      roleMap.value = Object.fromEntries(roleRes.value.map((r) => [r.role_id, r]))
    }
    if (tenantRes.status === 'fulfilled') {
      tenants.value = tenantRes.value
      tenantMap.value = Object.fromEntries(tenantRes.value.map((t) => [t.tenant_id, t]))
    }
    if (regionRes.status === 'fulfilled') {
      regions.value = regionRes.value
      regionMap.value = Object.fromEntries(regionRes.value.map((r) => [r.region_id, r]))
    }
  } catch (e) {
    console.warn('[SystemUser] 辅助下拉数据加载失败（不影响用户列表展示）', e)
  }
  // 角色名 / 区域名统一从数据字典读取（/dict/map 对所有已登录角色开放），
  // 独立于上面的下拉接口；两个字典互不影响，任一失败只让对应列回退。
  try {
    const [roleDictRes, regionDictRes] = await Promise.allSettled([
      dictMap('role'), dictMap('region'),
    ])
    if (roleDictRes.status === 'fulfilled') roleDictMap.value = roleDictRes.value
    else console.warn('[SystemUser] 角色字典加载失败，角色名回退', roleDictRes.reason)
    if (regionDictRes.status === 'fulfilled') regionDictMap.value = regionDictRes.value
    else console.warn('[SystemUser] 区域字典加载失败，区域名回退', regionDictRes.reason)
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
.role-chk.locked { opacity: .6; cursor: not-allowed; }
.lock-tip { font-size: 11px; color: var(--muted); }
.hint { color: var(--muted); font-size: 13px; margin: 0 0 14px; }
.reset-result { background: #e8f7ee; border: 1px solid #bfe8cf; color: #1f7a45; border-radius: var(--r-sm); padding: 10px 12px; font-size: 13px; }
.reset-result code { font-weight: 700; }
.dlg-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
</style>
