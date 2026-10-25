import { describe, it, expect, beforeEach, vi } from 'vitest'
import client from './client'
import {
  listMenus, createMenu, updateMenu, deleteMenu,
  listRoles, getRoleMenus, updateRoleMenus,
} from './rbac'

// mock 掉 axios 实例：锁定「URL + 方法 + 载荷」契约，防止前后端漂移
vi.mock('./client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('T-AM3 菜单管理 API（/menu）', () => {
  it('listMenus → GET /menu', async () => {
    client.get.mockResolvedValue([{ menu_id: 'm_sys' }])
    const rows = await listMenus()
    expect(client.get).toHaveBeenCalledWith('/menu')
    expect(rows).toEqual([{ menu_id: 'm_sys' }])
  })

  it('createMenu → POST /menu 带载荷', async () => {
    const body = { menu_id: 'm_test', menu_name: '测试', menu_type: 'MENU' }
    client.post.mockResolvedValue({ ...body, parent_id: null })
    await createMenu(body)
    expect(client.post).toHaveBeenCalledWith('/menu', body)
  })

  it('updateMenu → PUT /menu/{id} 带载荷', async () => {
    const body = { menu_id: 'm_test', menu_name: '改个名', menu_type: 'MENU' }
    client.put.mockResolvedValue(body)
    await updateMenu('m_test', body)
    expect(client.put).toHaveBeenCalledWith('/menu/m_test', body)
  })

  it('deleteMenu → DELETE /menu/{id}', async () => {
    client.delete.mockResolvedValue('')
    await deleteMenu('m_test')
    expect(client.delete).toHaveBeenCalledWith('/menu/m_test')
  })

  it('menu_id 含特殊字符时转义（防路径注入）', async () => {
    client.put.mockResolvedValue({})
    await updateMenu('a/b c', { menu_id: 'a/b c' })
    expect(client.put).toHaveBeenCalledWith('/menu/a%2Fb%20c', { menu_id: 'a/b c' })
  })
})

describe('T-AM4 角色管理 API（/role）', () => {
  it('listRoles → GET /role', async () => {
    client.get.mockResolvedValue([{ role_id: 'role_admin', role_code: 'admin', role_name: '管理员' }])
    const rows = await listRoles()
    expect(client.get).toHaveBeenCalledWith('/role')
    expect(rows[0].role_code).toBe('admin')
  })

  it('getRoleMenus → GET /role/{id}/menus（授权回显）', async () => {
    client.get.mockResolvedValue({ role_id: 'role_teacher', menu_ids: ['m_ops'] })
    const res = await getRoleMenus('role_teacher')
    expect(client.get).toHaveBeenCalledWith('/role/role_teacher/menus')
    expect(res.menu_ids).toEqual(['m_ops'])
  })

  it('updateRoleMenus → PUT /role/{id}/menus，载荷 { menu_ids }', async () => {
    client.put.mockResolvedValue('')
    await updateRoleMenus('role_teacher', ['m_ops', 'm_ops_reports'])
    expect(client.put).toHaveBeenCalledWith(
      '/role/role_teacher/menus',
      { menu_ids: ['m_ops', 'm_ops_reports'] },
    )
  })
})
