import { ref, onUnmounted } from 'vue'
import { useFeatureStore } from '../stores/features'

// 学习防疲劳计时器（#3）。进入学习会话开始累计，达 interval_min 弹提醒；
// 受 anti_fatigue 功能开关与配置管控。enforce 模式仅作提醒（不强制锁屏）。
export function useAntiFatigue() {
  const feature = useFeatureStore()
  const showRemind = ref(false)
  const remaining = ref(0)
  let timer = null
  let accum = 0 // 已累计毫秒

  function limitMs() {
    return (feature.antiFatigue.interval_min || 25) * 60 * 1000
  }

  function tick() {
    accum += 1000
    const left = Math.max(0, limitMs() - accum)
    remaining.value = Math.ceil(left / 1000)
    if (accum >= limitMs()) {
      showRemind.value = true
      stop()
    }
  }

  function start() {
    stop()
    if (!feature.isOn('anti_fatigue') || !feature.antiFatigue.enabled) return
    accum = 0
    remaining.value = Math.ceil(limitMs() / 1000)
    timer = setInterval(tick, 1000)
  }

  function stop() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  // 用户确认休息后：关闭提醒并重新开始计时（下次仍按 interval 提醒）
  function dismiss() {
    showRemind.value = false
    start()
  }

  onUnmounted(stop)

  return { showRemind, remaining, start, stop, dismiss, config: feature.antiFatigue }
}
