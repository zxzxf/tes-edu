import { describe, it, expect } from 'vitest'
import { errMsg } from './errmsg'

describe('errMsg（axios 错误 → 展示文案）', () => {
  it('透传后端 FastAPI detail 字符串', () => {
    const e = { response: { data: { detail: 'menu has children, delete them first' } } }
    expect(errMsg(e)).toBe('menu has children, delete them first')
  })

  it('后端英文鉴权码 insufficient role 统一汉化', () => {
    const e = { response: { data: { detail: 'insufficient role' } } }
    expect(errMsg(e)).toBe('权限不足：该操作仅管理员 / 学校管理员可执行')
  })

  it('pydantic 422 数组取首条并带定位', () => {
    const e = {
      response: {
        data: { detail: [{ loc: ['body', 'menu_name'], msg: 'field required', type: 'missing' }] },
      },
    }
    expect(errMsg(e)).toBe('menu_name: field required')
  })

  it('网络错误回退 err.message', () => {
    expect(errMsg({ message: 'Network Error' })).toBe('Network Error')
  })

  it('什么都没有时用兜底文案', () => {
    expect(errMsg({})).toBe('操作失败，请稍后重试')
    expect(errMsg(null, '加载失败')).toBe('加载失败')
  })
})
