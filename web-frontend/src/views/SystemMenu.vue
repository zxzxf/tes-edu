<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">菜单管理</h2>

    <div class="tes-toolbar">
      <!-- T-AM3：按钮级权限（后端 require_perm 同步拦截，这里只做显隐） -->
      <button class="tes-btn-primary" v-has-permission="'system:menu:add'" @click="openCreate">新建菜单</button>
      <button class="tes-btn" @click="load">刷新</button>
      <span class="tes-tag">{{ rows.length }} 个节点</span>
      <span v-if="loading" class="tes-tag">加载中…</span>
    </div>

    <p v-if="error" class="tes-empty err">{{ error }}</p>

    <div v-if="rows.length" class="tes-table-card">
      <table class="tes-table">
        <thead>
          <tr>
            <th>名称</th><th>类型</th><th>路径</th><th>权限码</th>
            <th>排序</th><th>可见</th><th class="op">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.menu_id">
            <td>
              <span class="indent" :style="{ paddingLeft: row.depth * 18 + 'px' }"></span>
              <b>{{ row.menu_name }}</b>
              <code class="mid">{{ row.menu_id }}</code>
            </td>
            <td><span class="tes-pill" :class="typeClass(row.menu_type)">{{ typeText(row.menu_type) }}</span></td>
            <td>{{ row.path || '—' }}</td>
            <td><code v-if="row.permission_code">{{ row.permission_code }}</code><span v-else class="muted">—</span></td>
            <td>{{ row.sort }}</td>
            <td>{{ row.visible === false ? '否' : '是' }}</td>
            <td class="op">
              <button class="tes-btn sm" v-has-permission="'system:menu:edit'" @click="openEdit(row)">编辑</button>
              <button class="tes-btn sm danger" v-has-permission="'system:menu:delete'" @click="remove(row)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else-if="!loading" class="tes-empty">暂无菜单，点击「新建菜单」创建首个节点。</p>

    <!-- 新建 / 编辑对话框 -->
    <div v-if="dialog" class="mask" @click.self="dialog = null">
      <div class="tes-card dialog" role="dialog" aria-modal="true">
        <h3>{{ dialog.mode === 'create' ? '新建菜单' : `编辑菜单：${form.menu_name}` }}</h3>
        <div class="form-grid">
          <label>菜单 ID
            <input v-model="form.menu_id" :disabled="dialog.mode === 'edit'" placeholder="如 m_ops_demo" />
          </label>
          <label>上级菜单
            <select v-model="form.parent_id">
              <option :value="null">（根级）</option>
              <option v-for="p in parentOptions" :key="p.menu_id" :value="p.menu_id">
                {{ p.menu_name }}（{{ typeText(p.menu_type) }}）
              </option>
            </select>
          </label>
          <label>菜单名称
            <input v-model="form.menu_name" placeholder="展示名" />
          </label>
          <label>类型
            <select v-model="form.menu_type">
              <option value="DIR">目录（DIR）</option>
              <option value="MENU">菜单（MENU）</option>
              <option value="BUTTON">按钮（BUTTON）</option>
            </select>
          </label>
          <label>路由路径
            <input v-model="form.path" placeholder="/ops/xxx（BUTTON 可空）" />
          </label>
          <label>权限码
            <input v-model="form.permission_code" placeholder="system:menu:add（BUTTON 必填）" />
          </label>
          <label>组件
            <input v-model="form.component" placeholder="前端组件路径（可空）" />
          </label>
          <label>图标
            <input v-model="form.icon" placeholder="语义串，如 menu / role / school" />
          </label>
          <label>排序
            <input v-model.number="form.sort" type="number" />
          </label>
          <label class="chk">
            <input v-model="form.visible" type="checkbox" /> 侧栏可见
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
import { createMenu, deleteMenu, listMenus, updateMenu } from '../api/rbac'
import { buildTree, findNode, flattenTree, subtreeIds } from '../utils/tree'
import { errMsg } from '../utils/errmsg'

// 类型徽标：DIR 紫 / MENU 绿 / BUTTON 金（复用全局 tes-pill 变体）
const TYPE_TEXT = { DIR: '目录', MENU: '菜单', BUTTON: '按钮' }
const TYPE_CLASS = { DIR: 'info', MENU: '', BUTTON: 'warn' }
function typeText(t) { return TYPE_TEXT[t] || t }
function typeClass(t) { return TYPE_CLASS[t] ?? '' }

const rows = ref([])      // flattenTree 结果（附 depth）
const tree = ref([])      // 建树结果（父级下拉 / 编辑防环用）
const loading = ref(false)
const error = ref('')
const dialog = ref(null)  // { mode: 'create' | 'edit' }
const saving = ref(false)
const formError = ref('')
const form = ref(blankForm())

function blankForm() {
  return {
    menu_id: '', parent_id: null, menu_name: '', menu_type: 'MENU',
    path: '', component: '', icon: '', permission_code: '', sort: 0, visible: true,
  }
}

// 父级候选：DIR/MENU 可选（BUTTON 不做父级）；编辑时排除自身与子孙防环
const parentOptions = computed(() => {
  const self = dialog.value?.mode === 'edit' && form.value.menu_id
    ? findNode(tree.value, form.value.menu_id)
    : null
  const exclude = new Set(self ? subtreeIds(self) : [])
  return rows.value.filter((m) => m.menu_type !== 'BUTTON' && !exclude.has(m.menu_id))
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const flat = await listMenus()
    tree.value = buildTree(flat)
    rows.value = flattenTree(tree.value)
  } catch (e) {
    error.value = errMsg(e, '菜单加载失败')
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
    menu_id: row.menu_id,
    parent_id: row.parent_id || null,
    menu_name: row.menu_name,
    menu_type: row.menu_type,
    path: row.path || '',
    component: row.component || '',
    icon: row.icon || '',
    permission_code: row.permission_code || '',
    sort: row.sort ?? 0,
    visible: row.visible !== false,
  }
  formError.value = ''
  dialog.value = { mode: 'edit' }
}

/** 表单 → MenuIn 载荷。注意 MenuOut 不含 status，编辑时走后端默认值 1
 *  （status 目前无任何读取方，/menu/my 也不按它过滤，见 rbac_api.py）。 */
function toPayload() {
  return {
    menu_id: form.value.menu_id.trim(),
    parent_id: form.value.parent_id || null,
    menu_name: form.value.menu_name.trim(),
    menu_type: form.value.menu_type,
    path: form.value.path.trim() || null,
    component: form.value.component.trim() || null,
    icon: form.value.icon.trim() || null,
    permission_code: form.value.permission_code.trim() || null,
    sort: Number(form.value.sort) || 0,
    visible: form.value.visible,
  }
}

function validate(payload) {
  if (!payload.menu_id) return '菜单 ID 不能为空'
  if (!payload.menu_name) return '菜单名称不能为空'
  if (payload.menu_type === 'BUTTON' && !payload.permission_code) {
    return '按钮类型必须填写权限码（如 system:menu:add）'
  }
  if (dialog.value?.mode === 'edit' && payload.parent_id === payload.menu_id) {
    return '上级菜单不能是自己'
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
      await createMenu(payload)
    } else {
      await updateMenu(payload.menu_id, payload)
    }
    dialog.value = null
    await load()
  } catch (e) {
    // 409 menu_id 已存在 / 400 parent not found 等后端校验原样透出
    formError.value = errMsg(e, '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  if (!window.confirm(`确定删除菜单「${row.menu_name}」？有子节点时后端会拒绝（400）。`)) return
  try {
    await deleteMenu(row.menu_id)
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
.indent { display: inline-block; width: 0; }
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
.dialog { width: 720px; max-width: 100%; max-height: 88vh; overflow: auto; }
.dialog h3 { margin-bottom: 16px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; }
.form-grid label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; font-weight: 600; color: var(--ink-2); }
.form-grid .chk { flex-direction: row; align-items: center; gap: 8px; align-self: end; }
.form-grid .chk input { width: auto; }
.dlg-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
</style>
