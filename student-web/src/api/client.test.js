import { describe, it, expect, vi, beforeEach } from 'vitest'
import axios from 'axios'

// 用极简假 store 替代真实 Pinia store（只暴露本模块用到的最小接口）
const fakeAuth = {
  sessionExpired: false,
  token: '',
}
vi.mock('../stores/auth', () => ({
  useAuthStore: () => fakeAuth,
}))

import client, { flushUnauthorized, cancelUnauthorized } from '../api/client'

// 等待 axios 异步链路（adapter + 拦截器）跑完一个 tick
const tick = () => new Promise((r) => setTimeout(r, 0))

describe('client 401 登录时效处理', () => {
  beforeEach(() => {
    fakeAuth.sessionExpired = false
    fakeAuth.token = ''
    localStorage.removeItem('token')
    // 自定义 adapter：模拟后端鉴权（请求拦截器会把 localStorage 的 token 注入 Authorization）
    client.defaults.adapter = async (config) => {
      const url = config.url
      const auth = config.headers && config.headers.Authorization
      const make = (status, data) => ({
        data,
        status,
        statusText: status === 200 ? 'OK' : 'Err',
        headers: {},
        config,
      })
      let resp
      if (url === '/secure') {
        resp = auth === 'Bearer NEW' ? make(200, { ok: true }) : make(401, null)
      } else if (url === '/secure2') {
        resp = auth === 'Bearer NEW' ? make(200, { name: 'A' }) : make(401, null)
      } else {
        resp = make(200, {})
      }
      // 真实 adapter 通过 validateStatus 把非 2xx 转成 AxiosError，这里手动模拟，
      // 确保错误拦截器（401 处理）能被触发。
      if (resp.status < 200 || resp.status >= 300) {
        const err = new axios.AxiosError('Request failed', axios.AxiosError.ERR_BAD_REQUEST, config, null, resp)
        err.response = resp
        throw err
      }
      return resp
    }
  })

  // 模拟「重新登录成功」：login() 会把新 token 写进 localStorage（请求拦截器读取处）
  function simulateReauth(token = 'NEW') {
    localStorage.setItem('token', token)
    fakeAuth.token = token
  }

  it('401 触发 sessionExpired 并挂起请求；flush 后以真实响应兑现原调用', async () => {
    const p = client.get('/secure')
    await tick() // 让 401 链路跑完，sessionExpired 置位、请求入队
    expect(fakeAuth.sessionExpired).toBe(true)

    simulateReauth()
    flushUnauthorized()

    const res = await p
    expect(res).toEqual({ ok: true }) // 原调用拿到的是重放后的真实数据
  })

  it('并发 401 只弹一次窗（sessionExpired 不重复置位），全部请求被重放', async () => {
    const p1 = client.get('/secure')
    const p2 = client.get('/secure2')
    await tick()
    expect(fakeAuth.sessionExpired).toBe(true)

    simulateReauth()
    flushUnauthorized()

    expect(await p1).toEqual({ ok: true })
    expect(await p2).toEqual({ name: 'A' })
  })

  it('cancelUnauthorized 拒绝所有挂起请求', async () => {
    const p = client.get('/secure')
    await tick()
    cancelUnauthorized()
    await expect(p).rejects.toThrow('re-auth cancelled')
  })

  it('重放后仍 401 不陷入死循环（直接拒绝）', async () => {
    const p = client.get('/secure')
    await tick()
    simulateReauth('BAD') // 重新登录但 token 仍无效（模拟续接失败）
    flushUnauthorized()
    await expect(p).rejects.toThrow('re-auth still unauthorized')
  })
})
