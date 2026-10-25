import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SystemRole from './SystemRole.vue'
import { useAuthStore } from '../stores/auth'
import { listRoles, listMenus, getRoleMenus, updateRoleMenus } from '../api/rbac'

vi.mock('../api/rbac', () => ({
  listRoles: vi.fn(),
  listMenus: vi.fn(),
  getRoleMenus: vi.fn(),
  updateRoleMenus: vi.fn(),
}))

const ROLES = [
  { role_id: 'role_admin', role_code: 'admin', role_name: '管理员' },
  { role_id: 'role_teacher', role_code: 'teacher', role_name: '教师' },
]
const MENUS = [
  { menu_id: 'm_sys', parent_id: null, menu_name: '系统设置', menu_type: 'DIR', path: '/system', sort: 1 },
  { menu_id: 'm_sys_menu', parent_id: 'm_sys', menu_name: '菜单管理', menu_type: 'MENU', path: '/system/menu', sort: 1 },
  { menu_id: 'm_sys_menu_add', parent_id: 'm_sys_menu', menu_name: '新建菜单', menu_type: 'BUTTON', path: '', sort: 1, permission_code: 'system:menu:add' },
  { menu_id: 'm_ops', parent_id: null, menu_name: '区域运营', menu_type: 'DIR', path: '/ops', sort: 2 },
  { menu_id: 'm_ops_reports', parent_id: 'm_ops', menu_name: '家长周报', menu_type: 'MENU', path: '/ops/reports', sort: 7 },
]

async function mountRole() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.$patch({ user: { username: 'admin@tes.edu', role: 'admin' }, menusLoaded: true })
  const wrapper = mount(SystemRole, { global: { plugins: [pinia] } })
  await flushPromises()
  return wrapper
}

const rowLabels = (wrapper) =>
  wrapper.findAll('.node-row').map((el) => el.text())
const rowInputs = (wrapper) =>
  wrapper.findAll('.node-row input[type="checkbox"]')

beforeEach(() => {
  vi.clearAllMocks()
  listRoles.mockResolvedValue(ROLES)
  listMenus.mockResolvedValue(MENUS)
  getRoleMenus.mockResolvedValue({ role_id: 'role_admin', menu_ids: ['m_ops', 'm_ops_reports'] })
  updateRoleMenus.mockResolvedValue('')
})

describe('SystemRole 角色管理页（T-AM4）', () => {
  it('渲染角色列表 + 全量授权树（含 BUTTON 权限码），默认选中首个角色', async () => {
    const wrapper = await mountRole()

    expect(wrapper.findAll('.role-list tbody tr')).toHaveLength(2)
    expect(wrapper.find('.role-list tr.active b').text()).toBe('管理员')
    // 授权树按 DFS 顺序含 DIR/MENU/BUTTON，BUTTON 行带权限码
    expect(rowLabels(wrapper)).toEqual([
      '目录系统设置', '菜单菜单管理', '按钮新建菜单system:menu:add',
      '目录区域运营', '菜单家长周报',
    ])
    expect(getRoleMenus).toHaveBeenCalledWith('role_admin')
    // 回显当前授权：已选 2 / 5 节点
    expect(wrapper.find('h3 .tes-tag').text()).toBe('已选 2 / 5 节点')
  })

  it('勾选父节点联动整棵子树（三态复选框）', async () => {
    const wrapper = await mountRole()
    const inputs = rowInputs(wrapper)

    // 初始：m_sys 未选、m_sys_menu 未选
    expect(inputs[0].element.checked).toBe(false)
    // 勾选「菜单管理」→ 子孙 BUTTON 一并选中，父「系统设置」变半选
    await inputs[1].trigger('change')
    expect(inputs[1].element.checked).toBe(true)
    expect(inputs[2].element.checked).toBe(true)
    expect(inputs[0].element.indeterminate).toBe(true)
    expect(wrapper.find('h3 .tes-tag').text()).toBe('已选 4 / 5 节点')
  })

  it('保存授权：全量替换 + 自动补齐祖先链', async () => {
    const wrapper = await mountRole()
    const inputs = rowInputs(wrapper)
    await inputs[1].trigger('change') // 勾选菜单管理子树

    await wrapper.find('.dlg-actions .tes-btn-primary').trigger('click')
    await flushPromises()

    expect(updateRoleMenus).toHaveBeenCalledTimes(1)
    const [roleId, ids] = updateRoleMenus.mock.calls[0]
    expect(roleId).toBe('role_admin')
    // 勾选项 + 补齐的祖先 m_sys（/menu/my 按直接关联取数，父 DIR 必须带上）
    expect([...ids].sort()).toEqual(
      ['m_ops', 'm_ops_reports', 'm_sys', 'm_sys_menu', 'm_sys_menu_add'],
    )
    // 保存后回显真实授权态
    expect(getRoleMenus).toHaveBeenCalledTimes(2)
  })

  it('切换角色重新拉取该角色授权', async () => {
    const wrapper = await mountRole()
    getRoleMenus.mockResolvedValue({ role_id: 'role_teacher', menu_ids: ['m_ops_reports'] })
    await wrapper.findAll('.role-list tbody tr')[1].trigger('click')
    await flushPromises()

    expect(getRoleMenus).toHaveBeenLastCalledWith('role_teacher')
    expect(wrapper.find('h3 .tes-tag').text()).toBe('已选 1 / 5 节点')
  })

  it('保存按钮挂 system:role:auth（admin 恒可见）', async () => {
    const wrapper = await mountRole()
    const btn = wrapper.find('.dlg-actions .tes-btn-primary')
    expect(btn.isVisible()).toBe(true)
  })
})
