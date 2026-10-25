<template>
  <div class="tes-page tes-anim">
    <h2 class="tes-page-title">角色管理</h2>

    <p v-if="error" class="tes-empty err">{{ error }}</p>

    <div class="role-layout">
      <!-- 左：角色列表（点击切换授权对象） -->
      <div class="tes-table-card role-list">
        <table class="tes-table">
          <thead><tr><th>角色</th><th>编码</th></tr></thead>
          <tbody>
            <tr v-for="r in roles" :key="r.role_id"
                :class="{ active: r.role_id === current?.role_id }"
                @click="selectRole(r)">
              <td><b>{{ r.role_name }}</b></td>
              <td><code>{{ r.role_code }}</code></td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 右：菜单授权树（含 BUTTON 权限码，三态复选框） -->
      <div class="tes-card auth-panel">
        <template v-if="current">
          <h3>
            菜单授权：{{ current.role_name }}
            <span class="tes-tag">已选 {{ checked.size }} / {{ authRows.length }} 节点</span>
          </h3>
          <p class="hint">
            勾选即授权（BUTTON 为按钮级权限码）；父节点联动整棵子树。
            保存后对该角色<b>全量替换</b>——未勾选的授权将被收回。
          </p>

          <div class="auth-tree">
            <label v-for="row in authRows" :key="row.menu_id" class="node-row"
                   :style="{ paddingLeft: row.depth * 22 + 'px' }">
              <input type="checkbox"
                     :checked="stateOf(row) === 'checked'"
                     :indeterminate.prop="stateOf(row) === 'indeterminate'"
                     @change="onToggle(row)" />
              <span class="tes-pill" :class="typeClass(row.menu_type)">{{ typeText(row.menu_type) }}</span>
              <b>{{ row.menu_name }}</b>
              <code v-if="row.permission_code">{{ row.permission_code }}</code>
            </label>
          </div>

          <div class="dlg-actions">
            <button class="tes-btn" @click="selectRole(current)">重置</button>
            <!-- T-AM4：授权提交走后端 system:role:auth 按钮码 -->
            <button class="tes-btn-primary" v-has-permission="'system:role:auth'" :disabled="saving" @click="save">
              {{ saving ? '保存中…' : '保存授权' }}
            </button>
          </div>
        </template>
        <p v-else class="tes-empty">选择左侧角色进行菜单授权。</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { getRoleMenus, listMenus, listRoles, updateRoleMenus } from '../api/rbac'
import { buildTree, checkState, flattenTree, toggleSubtree, withAncestors } from '../utils/tree'
import { errMsg } from '../utils/errmsg'

// 类型徽标：DIR 紫 / MENU 绿 / BUTTON 金（复用全局 tes-pill 变体）
const TYPE_TEXT = { DIR: '目录', MENU: '菜单', BUTTON: '按钮' }
const TYPE_CLASS = { DIR: 'info', MENU: '', BUTTON: 'warn' }
function typeText(t) { return TYPE_TEXT[t] || t }
function typeClass(t) { return TYPE_CLASS[t] ?? '' }

const roles = ref([])
const current = ref(null)
const flatMenus = ref([])  // GET /menu 全量（含 BUTTON）——提交时补齐祖先用
const authTree = ref([])
const authRows = computed(() => flattenTree(authTree.value))
const checked = ref(new Set())
const saving = ref(false)
const error = ref('')

async function load() {
  error.value = ''
  try {
    const [roleRows, menuRows] = await Promise.all([listRoles(), listMenus()])
    roles.value = roleRows
    flatMenus.value = menuRows
    authTree.value = buildTree(menuRows)
    // 默认选中首个角色（admin），授权树立即可见
    if (roles.value.length && !current.value) {
      await selectRole(roles.value[0])
    }
  } catch (e) {
    error.value = errMsg(e, '角色或菜单加载失败')
  }
}

async function selectRole(r) {
  current.value = r
  error.value = ''
  try {
    // PUT 是全量替换语义——必须先读回当前授权，否则保存即清空
    const res = await getRoleMenus(r.role_id)
    checked.value = new Set(res.menu_ids || [])
  } catch (e) {
    error.value = errMsg(e, '授权加载失败')
    checked.value = new Set()
  }
}

function stateOf(row) {
  return checkState(row, checked.value)
}

function onToggle(row) {
  checked.value = toggleSubtree(authTree.value, row.menu_id, checked.value)
}

async function save() {
  saving.value = true
  error.value = ''
  try {
    // 补齐祖先：/menu/my 按 role_menu 直接关联取数，MENU 的父 DIR 若未授权，
    // 前端导航会把该 MENU 挂到根级（无分组），故提交时自动带上祖先链
    const ids = [...withAncestors(flatMenus.value, checked.value)]
    await updateRoleMenus(current.value.role_id, ids)
    // 回显补齐后的真实授权态
    const res = await getRoleMenus(current.value.role_id)
    checked.value = new Set(res.menu_ids || [])
  } catch (e) {
    error.value = errMsg(e, '授权保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.role-layout {
  display: grid; grid-template-columns: 300px 1fr; gap: 18px; align-items: start;
}
@media (max-width: 900px) {
  .role-layout { grid-template-columns: 1fr; }
}
.role-list tbody tr { cursor: pointer; }
.role-list tbody tr.active { background: #fff0f6; }
.role-list tbody tr.active td { color: var(--brand-2); }

.auth-panel h3 { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.hint { color: var(--muted); font-size: 13px; margin: 0 0 14px; }
.auth-tree {
  max-height: 460px; overflow: auto;
  border: 1px solid var(--line); border-radius: var(--r-md); padding: 8px 0;
}
.node-row {
  display: flex; align-items: center; gap: 10px;
  padding: 7px 16px; cursor: pointer; border-radius: var(--r-sm);
}
.node-row:hover { background: #fff7fb; }
.node-row input { width: auto; flex-shrink: 0; }
.node-row code { font-size: 11px; color: var(--muted); }
.dlg-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
.tes-empty.err { background: #ffe9ee; border-color: #ffd6de; color: var(--danger); text-align: left; }
</style>
