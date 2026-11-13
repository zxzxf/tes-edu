import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '../../stores/auth'
import { errText } from '../../composables/useCrawlRequest'
import Workbench from './Workbench.vue'
import Sources from './Sources.vue'
import Jobs from './Jobs.vue'
import Parse from './Parse.vue'
import Review from './Review.vue'
import Runs from './Runs.vue'
import {
  createCourseSource, createCrawlJob, deleteCourseSource, getCrawlMetrics,
  listCourseSources, listCrawlJobs, listKpNodes, parseCourseSource, retryCrawlJob,
  reviewKpNode, rollbackCrawlRun, runCrawlJob, setCourseSourceStatus,
  setCrawlJobStatus, upsertKpNodes,
} from '../../api/admin'

vi.mock('../../api/admin', () => ({
  createCourseSource: vi.fn(), createCrawlJob: vi.fn(), deleteCourseSource: vi.fn(),
  getCrawlMetrics: vi.fn(), listCourseSources: vi.fn(), listCrawlJobs: vi.fn(),
  listKpNodes: vi.fn(), parseCourseSource: vi.fn(), retryCrawlJob: vi.fn(),
  reviewKpNode: vi.fn(), rollbackCrawlRun: vi.fn(), runCrawlJob: vi.fn(),
  setCourseSourceStatus: vi.fn(), setCrawlJobStatus: vi.fn(), upsertKpNodes: vi.fn(),
}))

const EMPTY_METRICS = {
  success_rate: 1, dedup_rate: 0, runs: 0, failed: 0, stopped: 0, recent: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  listCourseSources.mockResolvedValue([])
  listCrawlJobs.mockResolvedValue([])
  listKpNodes.mockResolvedValue([])
  getCrawlMetrics.mockResolvedValue(EMPTY_METRICS)
})

async function mountPage(comp) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().$patch({
    user: { username: 'pilot@school01.edu', role: 'school' },
    menus: [], perms: [], menusLoaded: true,
  })
  const wrapper = mount(comp, {
    // 自定义 stub 保留默认插槽，否则断言看不到链接文字
    global: {
      plugins: [pinia],
      stubs: { 'router-link': { template: '<a><slot /></a>' } },
    },
  })
  await flushPromises()
  return wrapper
}

describe('系统化采集页面拆分', () => {
  it('六个页面均可挂载并渲染各自标题', async () => {
    const cases = [
      [Workbench, '采集工作台'], [Sources, '课程源管理'], [Jobs, '采集任务'],
      [Parse, '课程解析预览'], [Review, '知识点审核与发布'], [Runs, '运行日志与回滚'],
    ]
    for (const [comp, title] of cases) {
      const wrapper = await mountPage(comp)
      expect(wrapper.find('.cw-title').text()).toBe(title)
      expect(wrapper.find('.err').exists()).toBe(false)
      wrapper.unmount()
    }
  })

  it('课程源页渲染真实返回的数据行', async () => {
    listCourseSources.mockResolvedValue([
      { id: 1, name: '数学题库包', source_type: 'bank_pack', uri: 'bank.json', status: 'active' },
    ])
    const wrapper = await mountPage(Sources)
    expect(wrapper.text()).toContain('数学题库包')
    expect(wrapper.text()).toContain('bank.json')
    expect(wrapper.find('.cw-ghost-danger').exists()).toBe(true)
  })

  it('课程源页可登记视频字幕来源', async () => {
    const wrapper = await mountPage(Sources)
    const type = wrapper.find('select')
    expect(type.find('option[value="video"]').exists()).toBe(true)
    await type.setValue('video')
    const inputs = wrapper.findAll('input')
    await inputs[0].setValue('数学课程')
    await inputs[1].setValue('https://media.example.edu/lesson.vtt')
    await wrapper.find('.tes-btn-primary').trigger('click')
    await flushPromises()
    expect(createCourseSource).toHaveBeenCalledWith(expect.objectContaining({
      source_type: 'video', uri: 'https://media.example.edu/lesson.vtt',
    }))
  })

  it('采集任务执行失败时直接显示运行原因', async () => {
    listCrawlJobs.mockResolvedValue([{
      id: 7, name: '字幕采集', source_id: 2, path: 'P2',
      status: 'paused', spent: 0, budget_limit: 10,
    }])
    runCrawlJob.mockResolvedValue({
      id: 7, last_run_status: 'failed', last_run_error: '源合规校验失败：未授权',
    })
    const wrapper = await mountPage(Jobs)
    const runButton = wrapper.findAll('button').find((button) => button.text() === '执行一次')
    await runButton.trigger('click')
    await flushPromises()
    expect(wrapper.find('.err').text()).toContain('未授权')
  })

  it('工作台按 metrics 与 draft 知识点汇总待办', async () => {
    getCrawlMetrics.mockResolvedValue({
      ...EMPTY_METRICS, runs: 9, dedup_rate: 0.18, failed: 2,
      recent: [{ id: 3, job_id: 7, status: 'ok', inserted: 5, duplicated: 1, failed: 0 }],
    })
    listKpNodes.mockResolvedValue([{ code: 'a' }, { code: 'b' }])
    const wrapper = await mountPage(Workbench)
    expect(wrapper.text()).toContain('18.0%')
    expect(wrapper.text()).toContain('知识点待审核 2 条')
    expect(wrapper.text()).toContain('任务 #7')
  })

  it('运行日志展示失败原因，school 角色可见回退按钮', async () => {
    getCrawlMetrics.mockResolvedValue({
      ...EMPTY_METRICS, failed: 1,
      recent: [{ id: 8, job_id: 2, status: 'failed', error: '源抓取失败：403', inserted: 0, duplicated: 0, failed: 1 }],
    })
    const wrapper = await mountPage(Runs)
    expect(wrapper.text()).toContain('源抓取失败：403')
    expect(wrapper.text()).toContain('回退本次')
  })

  it('单个接口失败不拖垮工作台，且给出可读原因', async () => {
    getCrawlMetrics.mockRejectedValue(new Error('网络异常'))
    const wrapper = await mountPage(Workbench)
    expect(wrapper.find('.err').text()).toContain('网络异常')
    expect(wrapper.find('.cw-title').text()).toBe('采集工作台')
  })
})

describe('共享请求错误文案', () => {
  it('权限不足沿用联调口径', () => {
    expect(errText({ response: { data: { detail: 'insufficient role' } } }))
      .toBe('权限不足：该操作仅管理员 / 学校管理员可执行')
  })
  it('普通错误优先取 detail 字符串，兜底 message', () => {
    expect(errText({ response: { data: { detail: 'cron 非法' } } })).toBe('cron 非法')
    expect(errText(new Error('boom'))).toBe('boom')
  })
})
