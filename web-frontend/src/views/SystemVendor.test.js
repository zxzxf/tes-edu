import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SystemVendor from './SystemVendor.vue'
import { useAuthStore } from '../stores/auth'
import {
  listVendors, createVendor, updateVendor, deleteVendor,
  listInstances, createInstance, updateInstance, testVendor,
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
    region_id: 'r_henan', tenant_id: 'school_pilot_01',
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
  updateInstance.mockResolvedValue({})
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

  // ---------- 列表化改造（对齐 BYOM T-M5 / 275c272） ----------

  it('表格外层有 .table-scroll 横向滚动兜底，操作列不会被 overflow:hidden 裁掉', async () => {
    const wrapper = await mountVendor()
    // .tes-table-card 是 overflow:hidden，必须靠内层 .table-scroll 兜底
    expect(wrapper.find('.tes-table-card .table-scroll').exists()).toBe(true)
    expect(wrapper.find('.table-scroll table.vendor-table').exists()).toBe(true)
    // 操作列（连通测试/编辑/删除）必须仍在 DOM 中
    const ops = wrapper.findAll('tbody tr')[0].findAll('td.op .tes-btn.sm')
    expect(ops.map((b) => b.text())).toEqual(['连通测试', '编辑', '删除'])
  })

  it('状态列是可点 pill，点击即切换启停且回填全部字段', async () => {
    const wrapper = await mountVendor()
    const pill = wrapper.findAll('tbody tr')[0].find('.pill-btn')
    expect(pill.text()).toBe('启用')
    expect(pill.classes()).toContain('on')

    await pill.trigger('click')
    await flushPromises()

    expect(updateVendor).toHaveBeenCalledTimes(1)
    const [id, payload] = updateVendor.mock.calls[0]
    expect(id).toBe('v_aliyun')
    expect(payload.status).toBe(0)  // 启用 → 停用
    // 列表刷新
    expect(listVendors).toHaveBeenCalledTimes(2)
  })

  it('⚠️ 启停/编辑必须回填 region_id/tenant_id：后端 PUT 全量覆盖，缺键会被静默置 None', async () => {
    const wrapper = await mountVendor()
    await wrapper.findAll('tbody tr')[0].find('.pill-btn').trigger('click')
    await flushPromises()

    const payload = updateVendor.mock.calls[0][1]
    expect(payload).toMatchObject({ region_id: 'r_henan', tenant_id: 'school_pilot_01' })
    expect(payload.region_id).not.toBeNull()
  })

  it('编辑保存时归属字段原样带回（表单不显示也不丢）', async () => {
    const wrapper = await mountVendor()
    await wrapper.findAll('tbody tr')[0].findAll('.tes-btn.sm')[2].trigger('click') // 编辑
    // 只改名称，不碰凭证
    await wrapper.findAll('input[placeholder="展示名"]')[0].setValue('阿里云百炼（改名）')
    await wrapper.find('.dlg-actions .tes-btn-primary').trigger('click')
    await flushPromises()

    const payload = updateVendor.mock.calls[0][1]
    expect(payload.vendor_name).toBe('阿里云百炼（改名）')
    expect(payload.region_id).toBe('r_henan')
    expect(payload.tenant_id).toBe('school_pilot_01')
    // 留空凭证 = 不带该键（后端语义「不覆盖」）
    expect(payload.credential).toBeUndefined()
  })

  it('归属为空的厂商：region_id/tenant_id 显式传 null，不隐藏副行', async () => {
    const wrapper = await mountVendor()
    await wrapper.findAll('tbody tr')[1].find('.pill-btn').trigger('click') // 腾讯云（无归属）
    await flushPromises()

    const payload = updateVendor.mock.calls[0][1]
    expect('region_id' in payload).toBe(true)
    expect(payload.region_id).toBeNull()
    expect(payload.status).toBe(1)  // 停用 → 启用
  })

  it('连通测试横幅分行展示判定 + HTTP/耗时/诊断', async () => {
    const wrapper = await mountVendor()
    await wrapper.findAll('tbody tr')[0].findAll('.tes-btn.sm')[1].trigger('click') // 连通测试
    await flushPromises()

    const banner = wrapper.find('.tes-empty.probe.ok')
    expect(banner.exists()).toBe(true)
    expect(banner.find('.probe-head').text()).toContain('可达 ✅')
    expect(banner.find('.probe-line').text()).toContain('200')
    expect(banner.find('.probe-line').text()).toContain('120ms')
    expect(banner.find('.probe-line').text()).toContain('HTTP 200')
  })

  it('实例弹窗表格也有 .table-scroll 兜底 + 状态 pill 可点', async () => {
    const wrapper = await mountVendor()
    await wrapper.findAll('tbody tr')[0].findAll('.tes-btn.sm')[0].trigger('click') // 实例（2）
    await flushPromises()

    expect(wrapper.find('.wide-dlg .table-scroll table.inst-table').exists()).toBe(true)
    const instPill = wrapper.find('.wide-dlg tbody tr .pill-btn')
    expect(instPill.text()).toBe('启用')

    await instPill.trigger('click')
    await flushPromises()
    expect(updateInstance).toHaveBeenCalledTimes(1)
    expect(updateInstance.mock.calls[0][0]).toBe('v_aliyun')
    expect(updateInstance.mock.calls[0][1]).toBe('inst_main')
    expect(updateInstance.mock.calls[0][2]).toMatchObject({
      instance_id: 'inst_main', service_name: '主数据服务',
      data_namespace: 'ns_main', isolation_mode: 'logical', status: 0,
    })
  })
})
