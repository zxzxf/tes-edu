<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">数据字典</h2>

    <div class="tes-toolbar">
      <button v-if="canManage" class="tes-btn-primary" @click="openCreateCategory">新增分类</button>
      <button class="tes-btn" @click="load">刷新</button>
      <span v-if="!canManage" class="tes-tag">只读查看</span>
      <span class="tes-tag">{{ rows.length }} 个节点</span>
      <span v-if="loading" class="tes-tag">加载中…</span>
    </div>

    <p v-if="error" class="tes-empty err">{{ error }}</p>

    <div v-if="visibleRows.length" class="tes-table-card">
      <div class="table-scroll">
      <table class="tes-table dict-table">
        <thead>
          <tr>
            <th>名称</th><th>类型</th><th>分类码</th><th>编码</th>
            <th>值</th><th>排序</th><th>状态</th><th v-if="canManage" class="op">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in visibleRows" :key="row.dict_id">
            <td>
              <span class="indent" :style="{ paddingLeft: row.depth * 20 + 'px' }"></span>
              <span
                v-if="hasChildren(row)"
                class="twisty"
                @click="toggle(row)"
              >{{ collapsed.has(row.dict_id) ? '▸' : '▾' }}</span>
              <span v-else class="twisty ghost">·</span>
              <b>{{ row.dict_name }}</b>
              <code class="mid">{{ row.dict_id }}</code>
            </td>
            <td>
              <span class="tes-pill" :class="isCategory(row) ? 'info' : ''">
                {{ isCategory(row) ? '分类' : '条目' }}
              </span>
            </td>
            <td><code class="type">{{ row.dict_type }}</code></td>
            <td><code class="code">{{ row.dict_code }}</code></td>
            <td>{{ row.dict_value || '—' }}</td>
            <td>{{ row.sort }}</td>
            <td>
              <span class="tes-pill" :class="row.status === 1 ? '' : 'warn'">
                {{ row.status === 1 ? '启用' : '停用' }}
              </span>
            </td>
            <td v-if="canManage" class="op">
              <button v-if="isCategory(row)" class="tes-btn sm" @click="openCreateChild(row)">新增子项</button>
              <button class="tes-btn sm" @click="openEdit(row)">编辑</button>
              <button class="tes-btn sm danger" @click="remove(row)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      </div>
    </div>
    <p v-else-if="!loading && !error" class="tes-empty">
      暂无字典数据{{ canManage ? '，点击「新增分类」创建第一个字典分类' : '，请联系管理员配置' }}。
    </p>

    <!-- 新建 / 编辑对话框 -->
    <div v-if="dialog" class="mask" @click.self="dialog = null">
      <div class="tes-card dialog" role="dialog" aria-modal="true">
        <h3>{{ dialog.mode === 'create' ? (form.parent_id ? '新增字典条目' : '新增字典分类') : `编辑字典：${form.dict_name}` }}</h3>
        <div class="form-grid">
          <label>字典 ID
            <input v-model="form.dict_id" :disabled="dialog.mode === 'edit'" placeholder="如 dict_role / role_admin" />
          </label>
          <label>上级
            <select v-model="form.parent_id" :disabled="dialog.mode === 'create' && !form.parent_id_from_btn">
              <option :value="null">（根级 · 作为分类）</option>
              <option v-for="p in parentOptions" :key="p.dict_id" :value="p.dict_id">
                {{ p.dict_name }}（{{ p.dict_type }}）
              </option>
            </select>
          </label>
          <label>分类码
            <input v-model="form.dict_type" :disabled="!!form.parent_id" placeholder="如 role / user_status" />
          </label>
          <label>名称
            <input v-model="form.dict_name" placeholder="展示名，如 角色 / 超级管理员" />
          </label>
          <label>编码
            <input v-model="form.dict_code" :disabled="!!form.parent_id" placeholder="同类唯一键；分类默认同分类码" />
          </label>
          <label>值
            <input v-model="form.dict_value" placeholder="可选附加值（默认同编码）" />
          </label>
          <label>排序
            <input v-model.number="form.sort" type="number" />
          </label>
          <label>状态
            <select v-model.number="form.status">
              <option :value="1">启用</option>
              <option :value="0">停用</option>
            </select>
          </label>
          <label class="span2">备注
            <input v-model="form.remark" placeholder="可选说明" />
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
import { computed, onMounted, ref, watch } from 'vue'
import { createDict, deleteDict, listDict, updateDict } from '../api/dict'
import { errMsg } from '../utils/errmsg'
import { useAuthStore } from '../stores/auth'

// 字典列表对所有已登录角色可读（后端已放通），但增删改仍仅 admin。
// 与后端 require_role(ADMIN) 对齐：非 admin 隐藏写入口，避免出现
// 「看得到按钮、点了却报权限不足」的二次不一致。
const auth = useAuthStore()
const canManage = computed(() => auth.isAdmin)

// ---- 本地树工具（结构同 utils/tree.js，但键为 dict_id / parent_id）----
function buildDictTree(flat) {
  const nodes = flat || []
  const byId = new Map()
  for (const d of nodes) byId.set(d.dict_id, { ...d, children: [] })
  const roots = []
  for (const d of nodes) {
    const node = byId.get(d.dict_id)
    if (d.parent_id && byId.has(d.parent_id)) byId.get(d.parent_id).children.push(node)
    else roots.push(node)
  }
  const sortRec = (list) => {
    list.sort((a, b) => (a.sort ?? 0) - (b.sort ?? 0))
    list.forEach((n) => sortRec(n.children))
  }
  sortRec(roots)
  return roots
}
function flattenDictTree(tree) {
  const rows = []
  const walk = (nodes, depth) => {
    for (const n of nodes) {
      rows.push({ ...n, depth })
      walk(n.children, depth + 1)
    }
  }
  walk(tree, 0)
  return rows
}
function findDictNode(tree, id) {
  for (const n of tree || []) {
    if (n.dict_id === id) return n
    const h = findDictNode(n.children, id)
    if (h) return h
  }
  return null
}
function dictSubtreeIds(node) {
  const ids = [node.dict_id]
  for (const c of node.children || []) ids.push(...dictSubtreeIds(c))
  return ids
}

const tree = ref([])
const rows = ref([])           // 全量扁平（建树后）
const collapsed = ref(new Set())
const loading = ref(false)
const error = ref('')
const dialog = ref(null)       // { mode: 'create' | 'edit' }
const saving = ref(false)
const formError = ref('')
const form = ref(blankForm())

function blankForm() {
  return {
    dict_id: '', parent_id: null, dict_type: '', dict_name: '',
    dict_code: '', dict_value: '', sort: 0, status: 1, remark: '',
    parent_id_from_btn: false,
  }
}

const isCategory = (n) => !n.parent_id
const hasChildren = (n) => (n.children && n.children.length > 0)

// 折叠：被折叠分类的后代整支隐藏
const visibleRows = computed(() => {
  const out = []
  const walk = (nodes, hidden) => {
    for (const n of nodes) {
      if (!hidden) out.push({ ...n })
      walk(n.children, hidden || collapsed.value.has(n.dict_id))
    }
  }
  walk(tree.value, false)
  return out
})

// 上级候选：根分类（编辑时排除自身及其子孙防环）
const parentOptions = computed(() => {
  const roots = rows.value.filter((r) => !r.parent_id)
  if (dialog.value?.mode === 'edit') {
    const self = findDictNode(tree.value, form.value.dict_id)
    const exclude = self ? new Set(dictSubtreeIds(self)) : new Set()
    return roots.filter((n) => !exclude.has(n.dict_id))
  }
  return roots
})

function toggle(node) {
  const s = new Set(collapsed.value)
  if (s.has(node.dict_id)) s.delete(node.dict_id)
  else s.add(node.dict_id)
  collapsed.value = s
}

// 选上级时自动继承其分类码；作为分类时编码默认同步分类码
watch(() => form.value.parent_id, (pid) => {
  if (pid) {
    const p = findDictNode(tree.value, pid)
    if (p) form.value.dict_type = p.dict_type
  }
})
watch(() => form.value.dict_type, (t) => {
  if (!form.value.parent_id) form.value.dict_code = t
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const flat = await listDict()
    tree.value = buildDictTree(flat)
    rows.value = flattenDictTree(tree.value)
  } catch (e) {
    error.value = errMsg(e, '字典加载失败')
  } finally {
    loading.value = false
  }
}

function openCreateCategory() {
  form.value = blankForm()
  formError.value = ''
  dialog.value = { mode: 'create' }
}
function openCreateChild(node) {
  form.value = {
    dict_id: '', parent_id: node.dict_id, dict_type: node.dict_type,
    dict_name: '', dict_code: '', dict_value: '', sort: 0, status: 1,
    remark: '', parent_id_from_btn: true,
  }
  formError.value = ''
  dialog.value = { mode: 'create' }
}
function openEdit(node) {
  form.value = {
    dict_id: node.dict_id, parent_id: node.parent_id || null,
    dict_type: node.dict_type, dict_name: node.dict_name,
    dict_code: node.dict_code, dict_value: node.dict_value || '',
    sort: node.sort ?? 0, status: node.status ?? 1,
    remark: node.remark || '', parent_id_from_btn: !!node.parent_id,
  }
  formError.value = ''
  dialog.value = { mode: 'edit' }
}

function toPayload() {
  const asCategory = !form.value.parent_id
  return {
    dict_id: form.value.dict_id.trim(),
    parent_id: form.value.parent_id || null,
    dict_type: form.value.dict_type.trim(),
    dict_name: form.value.dict_name.trim(),
    dict_code: (asCategory ? form.value.dict_type.trim() : form.value.dict_code.trim()),
    dict_value: form.value.dict_value.trim() || null,
    sort: Number(form.value.sort) || 0,
    status: Number(form.value.status) || 0,
    remark: form.value.remark.trim() || null,
  }
}

function validate(p) {
  if (!p.dict_id) return '字典 ID 不能为空'
  if (!p.dict_type) return '分类码不能为空'
  if (!p.dict_name) return '名称不能为空'
  if (!asCategory(form.value) && !p.dict_code) return '条目的编码不能为空'
  if (dialog.value?.mode === 'edit' && p.parent_id === p.dict_id) return '上级不能是自己'
  return ''
}
function asCategory(f) { return !f.parent_id }

async function save() {
  const payload = toPayload()
  formError.value = validate(payload)
  if (formError.value) return
  saving.value = true
  try {
    if (dialog.value.mode === 'create') await createDict(payload)
    else await updateDict(payload.dict_id, payload)
    dialog.value = null
    await load()
  } catch (e) {
    formError.value = errMsg(e, '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  if (!window.confirm(`确定删除字典项「${row.dict_name}」？有子节点时后端会拒绝（400）。`)) return
  try {
    await deleteDict(row.dict_id)
    await load()
  } catch (e) {
    error.value = errMsg(e, '删除失败')
  }
}

onMounted(load)
</script>

<style scoped>
.tes-table.dict-table { min-width: 920px; }
.mid { margin-left: 8px; font-size: 11px; color: var(--muted); }
.type, .code { font-size: 12px; color: var(--muted); }
.indent { display: inline-block; width: 0; }
.twisty { display: inline-block; width: 16px; text-align: center; cursor: pointer; color: var(--muted); user-select: none; }
.twisty.ghost { cursor: default; opacity: .35; }
td.op, th.op { white-space: nowrap; text-align: right; }
.tes-btn.sm { padding: 5px 12px; font-size: 12px; border-radius: var(--r-sm); }
.tes-btn.sm.danger { color: var(--danger); border-color: #ffd6de; }
.tes-btn.sm.danger:hover { background: #fff0f3; color: var(--danger); }
.tes-empty.err { background: #ffe9ee; border-color: #ffd6de; color: var(--danger); text-align: left; }

.mask {
  position: fixed; inset: 0; z-index: 50;
  background: rgba(31, 27, 46, .45);
  display: grid; place-items: center; padding: 24px;
}
.dialog { width: 720px; max-width: 100%; max-height: 88vh; overflow: auto; }
.dialog h3 { margin-bottom: 16px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; }
.form-grid label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; font-weight: 600; color: var(--ink-2); }
.form-grid .span2 { grid-column: 1 / -1; }
.dlg-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
</style>
