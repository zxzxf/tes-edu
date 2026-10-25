import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SystemMenu from './SystemMenu.vue'
import { useAuthStore } from '../stores/auth'
import { listMenus, createMenu, updateMenu, deleteMenu } from '../api/rbac'

vi.mock('../api/rbac', () => ({
  listMenus: vi.fn(),
  createMenu: vi.fn(),
  updateMenu: vi.fn(),
  deleteMenu: vi.fn(),
}))

const MENUS = [
  { menu_id: 'm_sys', parent_id: null, menu_name: '系统设置', menu_type: 'DIR', path: '/system', sort: 1, visible: true },
  { menu_id: 'm_sys_menu', parent_id: 'm_sys', menu_name: '菜单管理', menu_type: 'MENU', path: '/system/menu', sort: 1, visible: true },
  { menu_id: 'm_sys_menu_add', parent_id: 'm_sys_menu', menu_name: '新建菜单', menu_type: 'BUTTON', path: '', sort: 1, permission_code: 'system:menu:add', visible: true },
  { menu_id: 'm_ops', parent_id: null, menu_name: '区域运营', menu_type: 'DIR', path: '/ops', sort: 2, visible: true },
]

async function mountMenu() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.$patch({ user: { username: 'admin@tes.edu', role: 'admin' }, menusLoaded: true })
  const wrapper = mount(SystemMenu, { global: { plugins: [pinia] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  listMenus.mockResolvedValue(MENUS)
  createMenu.mockResolvedValue({})
  updateMenu.mockResolvedValue({})
  deleteMenu.mockResolvedValue('')
})

describe('SystemMenu 菜单管理页（T-AM3）', () => {
  it('扁平表格按层级渲染（含 BUTTON 行与类型徽标）', async () => {
    const wrapper = await mountMenu()
    const rows = wrapper.findAll('tbody tr')
    expect(rows).toHaveLength(4)
    expect(rows[0].find('b').text()).toBe('系统设置')
    expect(rows[0].find('.tes-pill').text()).toBe('目录')
    // BUTTON 行展示权限码
    expect(rows[2].text()).toContain('system:menu:add')
    expect(rows[2].find('.tes-pill').text()).toBe('按钮')
    expect(wrapper.find('.tes-toolbar .tes-tag').text()).toBe('4 个节点')
  })

  it('新建对话框：BUTTON 缺权限码时前端拦截，不发请求', async () => {
    const wrapper = await mountMenu()
    await wrapper.find('.tes-btn-primary').trigger('click') // 新建菜单

    await wrapper.find('input[placeholder="如 m_ops_demo"]').setValue('m_test')
    await wrapper.findAll('input[placeholder="展示名"]')[0].setValue('测试菜单')
    await wrapper.findAll('select')[1].setValue('BUTTON') // 类型

    await wrapper.find('.dlg-actions .tes-btn-primary').trigger('click')
    await flushPromises()

    expect(wrapper.find('.dialog .tes-empty.err').text()).toContain('权限码')
    expect(createMenu).not.toHaveBeenCalled()
  })

  it('新建对话框：MENU 正常提交载荷', async () => {
    const wrapper = await mountMenu()
    await wrapper.find('.tes-btn-primary').trigger('click')

    await wrapper.find('input[placeholder="如 m_ops_demo"]').setValue('m_test')
    await wrapper.findAll('input[placeholder="展示名"]')[0].setValue('测试菜单')
    await wrapper.findAll('select')[1].setValue('MENU')
    await wrapper.find('input[placeholder="/ops/xxx（BUTTON 可空）"]').setValue('/ops/test')
    await wrapper.find('input[type="number"]').setValue(5)

    await wrapper.find('.dlg-actions .tes-btn-primary').trigger('click')
    await flushPromises()

    expect(createMenu).toHaveBeenCalledTimes(1)
    const payload = createMenu.mock.calls[0][0]
    expect(payload).toMatchObject({
      menu_id: 'm_test', menu_name: '测试菜单', menu_type: 'MENU',
      path: '/ops/test', sort: 5, visible: true, parent_id: null,
    })
  })

  it('编辑对话框回显当前行（menu_id 锁定不可改）', async () => {
    const wrapper = await mountMenu()
    await wrapper.findAll('tbody tr')[1].find('.tes-btn.sm').trigger('click') // 编辑

    expect(wrapper.find('.dialog h3').text()).toContain('编辑菜单')
    const idInput = wrapper.find('input[placeholder="如 m_ops_demo"]')
    expect(idInput.element.value).toBe('m_sys_menu')
    expect(idInput.attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('input[placeholder="展示名"]')[0].element.value).toBe('菜单管理')
    // 父级下拉当前值 = m_sys
    expect(wrapper.findAll('select')[0].element.value).toBe('m_sys')
  })

  it('删除需确认；确认后调 DELETE', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = await mountMenu()
    await wrapper.findAll('tbody tr')[3].find('.tes-btn.sm.danger').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(deleteMenu).toHaveBeenCalledWith('m_ops')
    confirmSpy.mockRestore()
  })

  it('删除被用户取消时不发请求', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const wrapper = await mountMenu()
    await wrapper.findAll('tbody tr')[3].find('.tes-btn.sm.danger').trigger('click')
    await flushPromises()

    expect(deleteMenu).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })
})
