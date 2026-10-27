<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">学校管理</h2>

    <div class="tes-toolbar">
      <!-- T-AM9：按钮级权限（后端 POST/PUT/DELETE 经 require_role + 越权 403 兜底，这里只做显隐） -->
      <button class="tes-btn-primary" v-has-permission="'system:tenant:add'" @click="openCreate">新建学校</button>
      <input class="tes-search" v-model.trim="keyword" placeholder="搜索学校名称 / ID" />
      <button class="tes-btn" @click="load">刷新</button>
      <span class="tes-tag">{{ filtered.length }} 所学校</span>
      <span v-if="loading" class="tes-tag">加载中…</span>
    </div>

    <p v-if="error" class="tes-empty err">{{ error }}</p>

    <div v-if="filtered.length" class="tes-table-card">
      <table class="tes-table">
        <thead>
          <tr>
            <th>学校名称</th><th>学校 ID</th><th>所属区域</th><th>状态</th><th class="op">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in filtered" :key="row.tenant_id">
            <td><b>{{ row.name }}</b></td>
            <td><code class="mid">{{ row.tenant_id }}</code></td>
            <td>{{ regionName(row.region_id) || '—' }}</td>
            <td>
              <span v-if="row.status === 1" class="tes-pill ok">启用</span>
              <span v-else class="tes-pill muted">停用</span>
            </td>
            <td class="op">
              <button class="tes-btn sm" v-has-permission="'system:tenant:edit'" @click="openEdit(row)">编辑</button>
              <button class="tes-btn sm danger" v-has-permission="'system:tenant:delete'" @click="remove(row)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else-if="!loading" class="tes-empty">暂无学校，点击「新建学校」创建。</p>

    <!-- 新建 / 编辑对话框 -->
    <div v-if="dialog" class="mask" @click.self="dialog = null">
      <div class="tes-card dialog" role="dialog" aria-modal="true">
        <h3>{{ dialog.mode === 'create' ? '新建学校' : `编辑学校：${form.name}` }}</h3>
        <div class="form-grid">
          <label>学校 ID
            <input v-model="form.tenant_id" :disabled="dialog.mode === 'edit'" placeholder="如 school_pilot_01（唯一）" />
          </label>
          <label>所属区域
            <select v-model="form.region_id">
              <option :value="null">（不绑定区域）</option>
              <option v-for="r in regions" :key="r.region_id" :value="r.region_id">{{ r.region_name }}</option>
            </select>
          </label>
          <label class="wide">学校名称
            <input v-model="form.name" placeholder="展示名，如 试点一中" />
          </label>
          <label>状态
            <select v-model.number="form.status">
              <option :value="1">启用</option>
              <option :value="0">停用</option>
            </select>
          </label>
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
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  createTenant, deleteTenant, listRegions, listTenants, updateTenant,
} from '../api/rbac'
import { errMsg } from '../utils/errmsg'

const rows = ref([])
const regions = ref([])
const loading = ref(false)
const error = ref('')
const keyword = ref('')
const dialog = ref(null)  // { mode: 'create' | 'edit' }
const saving = ref(false)
const formError = ref('')
const form = ref(blankForm())

function blankForm() {
  return { tenant_id: '', name: '', region_id: null, status: 1 }
}

const regionMap = computed(() => new Map(regions.value.map((r) => [r.region_id, r])))
function regionName(regionId) {
  return regionId ? (regionMap.value.get(regionId)?.region_name || regionId) : ''
}

const filtered = computed(() => {
  const kw = keyword.value.toLowerCase()
  if (!kw) return rows.value
  return rows.value.filter(
    (r) => (r.name || '').toLowerCase().includes(kw)
      || (r.tenant_id || '').toLowerCase().includes(kw),
  )
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [tenantRows, regionRows] = await Promise.all([listTenants(), listRegions()])
    rows.value = tenantRows
    regions.value = regionRows
  } catch (e) {
    error.value = errMsg(e, '学校加载失败')
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
    tenant_id: row.tenant_id,
    name: row.name,
    region_id: row.region_id || null,
    status: row.status ?? 1,
  }
  formError.value = ''
  dialog.value = { mode: 'edit' }
}

/** 表单 → TenantIn 载荷（tenant_id 编辑时锁定，仅名称/区域/状态可改） */
function toPayload() {
  return {
    tenant_id: form.value.tenant_id.trim(),
    name: form.value.name.trim(),
    region_id: form.value.region_id || null,
    status: Number(form.value.status) || 0,
  }
}

function validate(payload) {
  if (!payload.tenant_id) return '学校 ID 不能为空'
  if (!payload.name) return '学校名称不能为空'
  return ''
}

async function save() {
  const payload = toPayload()
  formError.value = validate(payload)
  if (formError.value) return
  saving.value = true
  try {
    if (dialog.value.mode === 'create') {
      await createTenant(payload)
    } else {
      await updateTenant(payload.tenant_id, payload)
    }
    dialog.value = null
    await load()
  } catch (e) {
    // 409 已存在 / 403 越权 / 404 不存在等后端校验原样透出
    formError.value = errMsg(e, '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  // 删除守卫：后端在仍有用户绑定时返回 409，先提示再确认
  if (!window.confirm(`确定删除学校「${row.name}」？\n若该校仍有账号绑定，删除会被后端拒绝（409）。`)) return
  try {
    await deleteTenant(row.tenant_id)
    await load()
  } catch (e) {
    error.value = errMsg(e, '删除失败')
  }
}

onMounted(load)
</script>

<style scoped>
.mid { margin-left: 8px; font-size: 11px; color: var(--muted); }
.muted { color: var(--muted); }
td.op, th.op { white-space: nowrap; text-align: right; }
.tes-btn.sm { padding: 5px 12px; font-size: 12px; border-radius: var(--r-sm); }
.tes-btn.sm.danger { color: var(--danger); border-color: #ffd6de; }
.tes-btn.sm.danger:hover { background: #fff0f3; color: var(--danger); }
.tes-empty.err { background: #ffe9ee; border-color: #ffd6de; color: var(--danger); text-align: left; }
.tes-pill { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.tes-pill.ok { background: #e7f7ed; color: #1c8a4f; border: 1px solid #c5ecd3; }
.tes-pill.muted { background: #f1f1f4; color: var(--muted); border: 1px solid #e4e4ea; }
.tes-search { padding: 7px 12px; border: 1px solid var(--line); border-radius: var(--r-sm); font-size: 13px; min-width: 200px; background: var(--bg-card); }

/* ---------- 对话框 ---------- */
.mask {
  position: fixed; inset: 0; z-index: 50;
  background: rgba(31, 27, 46, .45);
  display: grid; place-items: center; padding: 24px;
}
.dialog { width: 560px; max-width: 100%; max-height: 88vh; overflow: auto; }
.dialog h3 { margin-bottom: 16px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; }
.form-grid label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; font-weight: 600; color: var(--ink-2); }
.form-grid .wide { grid-column: 1 / -1; }
.dlg-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
</style>
