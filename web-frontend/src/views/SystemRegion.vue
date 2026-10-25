<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">区域管理</h2>

    <div class="tes-toolbar">
      <!-- T-AM5：按钮级权限（后端 require_perm 同步拦截，这里只做显隐） -->
      <button class="tes-btn-primary" v-has-permission="'system:region:add'" @click="openCreate">新建区域</button>
      <button class="tes-btn" @click="load">刷新</button>
      <span class="tes-tag">{{ rows.length }} 个区域</span>
      <span v-if="loading" class="tes-tag">加载中…</span>
    </div>

    <p v-if="error" class="tes-empty err">{{ error }}</p>

    <div v-if="rows.length" class="tes-table-card">
      <table class="tes-table">
        <thead>
          <tr>
            <th>区域名称</th><th>编码</th><th>上级区域</th><th>排序</th>
            <th>绑定学校</th><th>绑定用户</th><th class="op">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.region_id">
            <td>
              <b>{{ row.region_name }}</b>
              <code class="mid">{{ row.region_id }}</code>
            </td>
            <td><code>{{ row.region_code }}</code></td>
            <td>{{ nameOf(row.parent_id) || '—' }}</td>
            <td>{{ row.sort }}</td>
            <!-- 绑定计数同时是删除守卫依据：非零时删除会被后端 400 拒绝 -->
            <td>
              <span v-if="row.tenant_count" class="tes-pill warn">{{ row.tenant_count }}</span>
              <span v-else class="muted">0</span>
            </td>
            <td>
              <span v-if="row.user_count" class="tes-pill warn">{{ row.user_count }}</span>
              <span v-else class="muted">0</span>
            </td>
            <td class="op">
              <button class="tes-btn sm" v-has-permission="'system:region:edit'" @click="openEdit(row)">编辑</button>
              <button class="tes-btn sm danger" v-has-permission="'system:region:delete'" @click="remove(row)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else-if="!loading" class="tes-empty">暂无区域，点击「新建区域」创建首个区域。</p>

    <!-- 新建 / 编辑对话框 -->
    <div v-if="dialog" class="mask" @click.self="dialog = null">
      <div class="tes-card dialog" role="dialog" aria-modal="true">
        <h3>{{ dialog.mode === 'create' ? '新建区域' : `编辑区域：${form.region_name}` }}</h3>
        <div class="form-grid">
          <label>区域 ID
            <input v-model="form.region_id" :disabled="dialog.mode === 'edit'" placeholder="如 rgn_east" />
          </label>
          <label>上级区域
            <select v-model="form.parent_id">
              <option :value="null">（根级）</option>
              <option v-for="p in parentOptions" :key="p.region_id" :value="p.region_id">
                {{ p.region_name }}
              </option>
            </select>
          </label>
          <label>区域编码
            <input v-model="form.region_code" placeholder="如 east（唯一）" />
          </label>
          <label>排序
            <input v-model.number="form.sort" type="number" />
          </label>
          <label class="wide">区域名称
            <input v-model="form.region_name" placeholder="展示名，如 华东" />
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
import { createRegion, deleteRegion, listRegions, updateRegion } from '../api/rbac'
import { errMsg } from '../utils/errmsg'

const rows = ref([])
const loading = ref(false)
const error = ref('')
const dialog = ref(null)  // { mode: 'create' | 'edit' }
const saving = ref(false)
const formError = ref('')
const form = ref(blankForm())

function blankForm() {
  return {
    region_id: '', region_code: '', region_name: '', parent_id: null, sort: 0,
  }
}

const byId = computed(() => new Map(rows.value.map((r) => [r.region_id, r])))
function nameOf(regionId) {
  return byId.value.get(regionId)?.region_name || ''
}

/** 某区域的后代集合（BFS；编辑时父级下拉排除自身与子孙防环） */
function descendantIds(regionId) {
  const out = new Set()
  const queue = [regionId]
  while (queue.length) {
    const cur = queue.shift()
    for (const r of rows.value) {
      if (r.parent_id === cur && !out.has(r.region_id)) {
        out.add(r.region_id)
        queue.push(r.region_id)
      }
    }
  }
  return out
}

// 父级候选：编辑时排除自身与子孙防环（后端同样会 400 挡自父级）
const parentOptions = computed(() => {
  const self = dialog.value?.mode === 'edit' && form.value.region_id
    ? form.value.region_id
    : null
  const exclude = new Set(self ? [self, ...descendantIds(self)] : [])
  return rows.value.filter((r) => !exclude.has(r.region_id))
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    rows.value = await listRegions()
  } catch (e) {
    error.value = errMsg(e, '区域加载失败')
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
    region_id: row.region_id,
    region_code: row.region_code,
    region_name: row.region_name,
    parent_id: row.parent_id || null,
    sort: row.sort ?? 0,
  }
  formError.value = ''
  dialog.value = { mode: 'edit' }
}

/** 表单 → RegionIn 载荷（region_id 编辑时锁定，仅名称/编码/父级/排序可改） */
function toPayload() {
  return {
    region_id: form.value.region_id.trim(),
    region_code: form.value.region_code.trim(),
    region_name: form.value.region_name.trim(),
    parent_id: form.value.parent_id || null,
    sort: Number(form.value.sort) || 0,
  }
}

function validate(payload) {
  if (!payload.region_id) return '区域 ID 不能为空'
  if (!payload.region_code) return '区域编码不能为空'
  if (!payload.region_name) return '区域名称不能为空'
  if (dialog.value?.mode === 'edit' && payload.parent_id === payload.region_id) {
    return '上级区域不能是自己'
  }
  return ''
}

async function save() {
  const payload = toPayload()
  formError.value = validate(payload)
  if (formError.value) return
  saving.value = true
  try {
    if (dialog.value.mode === 'create') {
      await createRegion(payload)
    } else {
      await updateRegion(payload.region_id, payload)
    }
    dialog.value = null
    await load()
  } catch (e) {
    // 409 region_id/region_code 已存在 / 400 parent not found 等后端校验原样透出
    formError.value = errMsg(e, '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  // 删除守卫前置提示：有绑定时后端 400（detail 带各项计数），先讲明白再问
  const guard = []
  if (row.tenant_count) guard.push(`${row.tenant_count} 个学校`)
  if (row.user_count) guard.push(`${row.user_count} 个用户`)
  const hint = guard.length ? `该区域仍绑定 ${guard.join('、')}，删除会被后端拒绝（400）。` : ''
  if (!window.confirm(`确定删除区域「${row.region_name}」？${hint}`)) return
  try {
    await deleteRegion(row.region_id)
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
