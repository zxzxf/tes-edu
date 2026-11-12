/**
 * 采集工作台各页共享的请求态（busy / error / msg）与错误文案归一。
 *
 * 由原 CourseIntake.vue 抽出的 guard/errText 逻辑，拆页后避免六份重复；
 * 「权限不足」文案沿用 B 端 2026-11-11 联调口径，测试锁死不改。
 */
import { ref } from 'vue'

export function errText(e) {
  const d = e?.response?.data?.detail
  if (d === 'insufficient role') return '权限不足：该操作仅管理员 / 学校管理员可执行'
  if (typeof d === 'string') return d
  if (d?.message) return d.message
  return e?.message || '请求失败'
}

export function useCrawlRequest() {
  const busy = ref(false)
  const error = ref('')
  const msg = ref('')

  async function guard(fn) {
    busy.value = true
    error.value = ''
    msg.value = ''
    try {
      return await fn()
    } catch (e) {
      error.value = errText(e)
      return undefined
    } finally {
      busy.value = false
    }
  }

  return { busy, error, msg, guard, errText }
}
