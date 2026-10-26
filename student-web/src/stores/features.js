import { defineStore } from 'pinia'
import { ref } from 'vue'
import client from '../api/client'

// 学生端功能开关 store（#2 总闸消费端）。
// 启动拉取当前生效集 + 防疲劳配置，各页面/入口据此显隐。
export const useFeatureStore = defineStore('features', () => {
  const features = ref({}) // { feature_key: bool }
  const antiFatigue = ref({
    enabled: true,
    interval_min: 25,
    break_min: 5,
    mode: 'remind',
    message: '该休息一下啦，远眺放松眼睛，保护视力~',
  })
  const loaded = ref(false)

  async function load(platform = 'web') {
    try {
      const f = await client.get('/features', { params: { platform } })
      features.value = (f && f.features) || {}
      const a = await client.get('/anti-fatigue')
      if (a) antiFatigue.value = { ...antiFatigue.value, ...a }
    } catch (e) {
      // 静默降级：保留默认（全开 + 默认防疲劳），不阻断页面渲染
    }
    loaded.value = true
  }

  // 未加载前默认 true（避免闪烁隐藏）；加载后按生效集
  function isOn(key) {
    if (!loaded.value) return true
    return !!features.value[key]
  }

  return { features, antiFatigue, loaded, load, isOn }
})
