import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SystemVendor from './SystemVendor.vue'
import { useAuthStore } from '../stores/auth'
import {
  listVendors, createVendor, updateVendor, deleteVendor,
  listInstances, createInstance, testVendor,
} from '../api/vendor'

vi.mock('../api/vendor', () => ({
  listVendors: vi.fn(),
  getVendor: vi.fn(),
  createVendor: vi.fn(),
  updateVendor: vi.fn(),
  deleteVendor: vi.fn(),
  listInstances: vi.fn(),
  createInstance: vi.fn(),
  updateInstance: vi.fn(),
  deleteInstance: vi.fn(),
  testVendor: vi.fn(),
}))

const VENDORS = [
  {
    vendor_id: 'v_aliyun', vendor_name: '阿里云百炼', vendor_type: 'aliyun',
    base_url: 'https://dashscope.aliyuncs.com', auth_type: 'apikey',
    status: 1, has_credential: true, instance_count: 2,
  },
  {
    vendor_id: 'v_tencent', vendor_name: '腾讯云', vendor_type: 'tencent',
    base_url: null, auth_type: 'oauth',
    status: 0, has_credential: false, instance_count: 0,
  },
]

const INSTANCES = [
  {
    instance_id: 'inst_main', vendor_id: 'v_aliyun', service_name: '主数据服务',
    data_namespace: 'ns_main', isolation_mode: 'logical', status: 1, has_connection: true,
  },
]

async function mountVendor() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.$patch({
    user: { username: 'admin@tes.edu', role: 'admin' },
    perms: ['system:vendor:manage'],
    menusLoaded: true,
  })
  const wrapper = mount(SystemVendor, { global: { plugins: [pinia] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  listVendors.mockResolvedValue(VENDORS)
  createVendor.mockResolvedValue({})
  updateVendor.mockResolvedValue({})
  deleteVendor.mockResolvedValue('')
  listInstances.mockResolvedValue(INSTANCES)
  createInstance.mockResolvedValue({})
  testVendor.mockResolvedValue({ ok: true, status_code: 200, latency_ms: 120, detail: 'HTTP 200' })
})

describe('SystemVendor 厂商管理页（T-AM6）', () => {
  it('表格渲染厂商行：凭证布尔徽标 + 实例计数 + 状态', async () => {
    const wrapper = await mountVendor()
    const rows = wrapper.findAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0].find('b').text()).toBe('阿里云百炼')
    expect(rows[0].find('code.mid').text()).toBe('v_aliyun')
    // 凭证只回显布尔：已存=已加密保存，未存=未保存
    expect(rows[0].text()).toContain('已加密保存')
    expect(rows[1].text()).toContain('未保存')
    // 实例计数按钮
    expect(rows[0].text()).toContain('实例（2）')
    // 停用状态 pill
    expect(rows[1].text()).toContain('停用')
    expect(wrapper.find('.tes-toolbar .tes-tag').text()).toBe('2 个厂商')
  })

  it('注册对话框：缺凭证前端拦截，补齐后提交载荷', async () => {
    const wrapper = await mountVendor()
    await wrapper.find('.tes-btn-primary').trigger('click')

    // 只填 ID/名称，不填凭证 → 拦截
    await wrapper.find('input[placeholder="如 v_aliyun"]').setValue('v_test')
    await wrapper.findAll('input[placeholder="展示名"]')[0].setValue('测试厂商')
    await wrapper.find('.dlg-actions .tes-btn-primary').trigger('click')
    expect(wrapper.find('.dialog .tes-empty.err').text()).toContain('凭证')
    expect(createVendor).not.toHaveBeenCalled()

    await wrapper.find('input[type="password"]').setValue('sk-test-1')
    await wrapper.find('.dlg-actions .tes-btn-primary').trigger('click')
    await flushPromises()

    expect(createVendor).toHaveBeenCalledTimes(1)
    expect(createVendor.mock.calls[0][0]).toMatchObject({
      vendor_id: 'v_test', vendor_name: '测试厂商', vendor_type: 'aliyun',
      auth_type: 'apikey', credential: 'sk-test-1', status: 1,
    })
  })

  it('编辑对话框回显（凭证输入为空且占位说明留空保持不变）；空凭证不发该键', async () => {
    const wrapper = await mountVendor()
    // 行内 .tes-btn.sm 顺序：实例 / 连通测试 / 编辑 / 删除
    await wrapper.findAll('tbody tr')[0].findAll('.tes-btn.sm')[2].trigger('click') // 编辑

    expect(wrapper.find('.dialog h3').text()).toContain('编辑厂商：阿里云百炼')
    const idInput = wrapper.find('input[placeholder="如 v_aliyun"]')
    expect(idInput.element.value).toBe('v_aliyun')
    expect(idInput.attributes('disabled')).toBeDefined()
    // 凭证输入框必须为空（不回显），且占位提示「留空保持不变」
    const pwd = wrapper.find('input[type="password"]')
    expect(pwd.element.value).toBe('')
    expect(pwd.attributes('placeholder')).toContain('留空保持不变')

    await wrapper.find('.dlg-actions .tes-btn-primary').trigger('click')
    await flushPromises()
    expect(updateVendor).toHaveBeenCalledTimes(1)
    expect(updateVendor.mock.calls[0][0].credential).toBeUndefined()
  })

  it('连通测试：调用 testVendor 并展示结果横幅', async () => {
    const wrapper = await mountVendor()
    await wrapper.findAll('tbody tr')[0].findAll('.tes-btn.sm')[1].trigger('click') // 连通测试
    await flushPromises()

    expect(testVendor).toHaveBeenCalledWith('v_aliyun')
    const banner = wrapper.find('.tes-empty.ok')
    expect(banner.text()).toContain('可达')
    expect(banner.text()).toContain('HTTP 200')
    expect(banner.text()).toContain('120ms')
  })

  it('连通测试失败（ok=false）用错误样式横幅', async () => {
    testVendor.mockResolvedValue({ ok: false, status_code: null, latency_ms: 30, detail: 'ConnectError: boom' })
    const wrapper = await mountVendor()
    await wrapper.findAll('tbody tr')[0].findAll('.tes-btn.sm')[1].trigger('click')
    await flushPromises()

    const banner = wrapper.find('.tes-empty.err')
    expect(banner.text()).toContain('不可达')
    expect(banner.text()).toContain('ConnectError')
  })

  it('实例管理子对话框：加载实例 + 新建实例提交', async () => {
    const wrapper = await mountVendor()
    await wrapper.findAll('tbody tr')[0].findAll('.tes-btn.sm')[0].trigger('click') // 实例（2）
    await flushPromises()

    expect(listInstances).toHaveBeenCalledWith('v_aliyun')
    expect(wrapper.find('.dialog h3').text()).toContain('数据服务实例：阿里云百炼')
    // 作用域限定在实例对话框内（底层厂商表仍在 DOM 中）
    const instRows = wrapper.findAll('.wide-dlg tbody tr')
    expect(instRows).toHaveLength(1)
    expect(instRows[0].text()).toContain('主数据服务')
    expect(instRows[0].text()).toContain('ns_main')

    await wrapper.find('.inst-toolbar .tes-btn-primary').trigger('click') // 新建实例
    await wrapper.find('input[placeholder="如 inst_aliyun_main"]').setValue('inst_new')
    await wrapper.findAll('input[placeholder="如 主数据服务"]')[0].setValue('新服务')
    await wrapper.find('input[placeholder="schema / keyspace / 存储前缀"]').setValue('ns_new')
    await wrapper.find('.sub-dialog .dlg-actions .tes-btn-primary').trigger('click')
    await flushPromises()

    expect(createInstance).toHaveBeenCalledTimes(1)
    expect(createInstance.mock.calls[0][0]).toBe('v_aliyun')  // vendor_id 路径参数
    expect(createInstance.mock.calls[0][1]).toMatchObject({
      instance_id: 'inst_new', service_name: '新服务', data_namespace: 'ns_new',
      isolation_mode: 'logical', status: 1,
    })
  })

  it('删除厂商需确认；名下有实例时确认文案带守卫提示', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = await mountVendor()
    await wrapper.findAll('tbody tr')[0].findAll('.tes-btn.sm.danger')[0].trigger('click')
    await flushPromises()

    expect(confirmSpy.mock.calls[0][0]).toContain('2 个数据服务实例')
    expect(deleteVendor).toHaveBeenCalledWith('v_aliyun')
    confirmSpy.mockRestore()
  })
})
