import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SystemRegion from './SystemRegion.vue'
import { useAuthStore } from '../stores/auth'
import { listRegions, createRegion, updateRegion, deleteRegion } from '../api/rbac'

vi.mock('../api/rbac', () => ({
  listRegions: vi.fn(),
  createRegion: vi.fn(),
  updateRegion: vi.fn(),
  deleteRegion: vi.fn(),
}))

// 三大区样本：华东（0 学校/4 用户）、华北（2 学校/0 用户）、华北-北京（挂华北下）
const REGIONS = [
  { region_id: 'rgn_east', region_code: 'east', region_name: '华东', parent_id: null, sort: 1, tenant_count: 0, user_count: 4 },
  { region_id: 'rgn_north', region_code: 'north', region_name: '华北', parent_id: null, sort: 2, tenant_count: 2, user_count: 0 },
  { region_id: 'rgn_north_bj', region_code: 'north_bj', region_name: '华北-北京', parent_id: 'rgn_north', sort: 1, tenant_count: 0, user_count: 0 },
]

async function mountRegion() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.$patch({
    user: { username: 'admin@tes.edu', role: 'admin' },
    perms: ['system:region:add', 'system:region:edit', 'system:region:delete'],
    menusLoaded: true,
  })
  const wrapper = mount(SystemRegion, { global: { plugins: [pinia] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  listRegions.mockResolvedValue(REGIONS)
  createRegion.mockResolvedValue({})
  updateRegion.mockResolvedValue({})
  deleteRegion.mockResolvedValue('')
})

describe('SystemRegion 区域管理页（T-AM5）', () => {
  it('表格渲染区域行：绑定计数徽标 + 父级名称解析', async () => {
    const wrapper = await mountRegion()
    const rows = wrapper.findAll('tbody tr')
    expect(rows).toHaveLength(3)
    expect(rows[0].find('b').text()).toBe('华东')
    expect(rows[0].find('code.mid').text()).toBe('rgn_east')
    // 未绑定学校显示 0；绑定用户显示 warn 徽标 4
    expect(rows[0].find('.tes-pill.warn').text()).toBe('4')
    // 华北绑定 2 个学校
    expect(rows[1].find('.tes-pill.warn').text()).toBe('2')
    // 父级名称解析：华北-北京 的上级列显示「华北」
    expect(rows[2].findAll('td')[2].text()).toBe('华北')
    expect(wrapper.find('.tes-toolbar .tes-tag').text()).toBe('3 个区域')
  })

  it('新建对话框：空表单前端拦截，补齐后提交载荷', async () => {
    const wrapper = await mountRegion()
    await wrapper.find('.tes-btn-primary').trigger('click')

    // 空表单直接保存 → 前端拦截，不发请求
    await wrapper.find('.dlg-actions .tes-btn-primary').trigger('click')
    expect(wrapper.find('.dialog .tes-empty.err').text()).toContain('区域 ID')
    expect(createRegion).not.toHaveBeenCalled()

    await wrapper.find('input[placeholder="如 rgn_east"]').setValue('rgn_test')
    await wrapper.find('input[placeholder="如 east（唯一）"]').setValue('test')
    await wrapper.findAll('input[placeholder="展示名，如 华东"]')[0].setValue('测试区域')
    await wrapper.find('input[type="number"]').setValue(9)

    await wrapper.find('.dlg-actions .tes-btn-primary').trigger('click')
    await flushPromises()

    expect(createRegion).toHaveBeenCalledTimes(1)
    expect(createRegion.mock.calls[0][0]).toMatchObject({
      region_id: 'rgn_test', region_code: 'test', region_name: '测试区域',
      parent_id: null, sort: 9,
    })
  })

  it('编辑对话框回显当前行（region_id 锁定；父级下拉排除自身与子孙）', async () => {
    const wrapper = await mountRegion()
    // 第 2 行（华北）点编辑
    await wrapper.findAll('tbody tr')[1].find('.tes-btn.sm').trigger('click')

    expect(wrapper.find('.dialog h3').text()).toContain('编辑区域：华北')
    const idInput = wrapper.find('input[placeholder="如 rgn_east"]')
    expect(idInput.element.value).toBe('rgn_north')
    expect(idInput.attributes('disabled')).toBeDefined()
    // 父级候选：排除自身（rgn_north）与子孙（rgn_north_bj），只剩「根级 + 华东」
    const options = wrapper.findAll('.dialog select option')
    expect(options.map((o) => o.text())).toEqual(['（根级）', '华东'])
  })

  it('删除需确认；有绑定时确认文案带守卫提示', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = await mountRegion()
    await wrapper.findAll('tbody tr')[1].find('.tes-btn.sm.danger').trigger('click')
    await flushPromises()

    expect(confirmSpy.mock.calls[0][0]).toContain('2 个学校')
    expect(deleteRegion).toHaveBeenCalledWith('rgn_north')
    confirmSpy.mockRestore()
  })

  it('删除被取消不发请求；后端守卫 400 原样透出', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const wrapper = await mountRegion()
    await wrapper.findAll('tbody tr')[1].find('.tes-btn.sm.danger').trigger('click')
    await flushPromises()
    expect(deleteRegion).not.toHaveBeenCalled()
    confirmSpy.mockRestore()

    // 后端守卫：仍有绑定时 400，detail 透传到页顶错误条
    const spy2 = vi.spyOn(window, 'confirm').mockReturnValue(true)
    deleteRegion.mockRejectedValue({
      response: { data: { detail: 'region in use: 2 tenants, 0 users, 0 sub-regions' } },
    })
    await wrapper.findAll('tbody tr')[1].find('.tes-btn.sm.danger').trigger('click')
    await flushPromises()
    expect(wrapper.find('.tes-empty.err').text()).toContain('region in use: 2 tenants')
    spy2.mockRestore()
  })
})
