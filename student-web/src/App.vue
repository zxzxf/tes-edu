<template>
  <router-view />

  <!-- 学习防疲劳提醒（#3）：达阈值弹窗，受 anti_fatigue 功能开关管控 -->
  <div v-if="af.showRemind" class="af-mask">
    <div class="af-modal">
      <div class="af-emoji">👀</div>
      <p class="af-msg">{{ feature.antiFatigue.message }}</p>
      <div class="af-sub">建议休息 {{ feature.antiFatigue.break_min }} 分钟，远眺放松眼睛</div>
      <div class="af-actions">
        <button class="af-btn" @click="af.dismiss">好的，休息一下</button>
        <button class="af-link" @click="af.snooze">本次学习不再提醒</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useFeatureStore } from './stores/features'
import { useAntiFatigue } from './composables/useAntiFatigue'

const feature = useFeatureStore()
const af = useAntiFatigue()
const router = useRouter()

onMounted(async () => {
  await feature.load('web')
  af.start()
})

// 切换页面重置学习会话计时（新页面视为新会话起点）
router.afterEach(() => {
  af.stop()
  af.start()
})
</script>

<style scoped>
.af-mask {
  position: fixed; inset: 0; background: rgba(20, 12, 28, .45);
  display: grid; place-items: center; z-index: 100;
}
.af-modal {
  width: 360px; max-width: 90vw; background: #fff; border-radius: 18px;
  padding: 28px; text-align: center; box-shadow: 0 20px 60px rgba(0, 0, 0, .25);
}
.af-emoji { font-size: 40px; }
.af-msg { font-size: 17px; font-weight: 700; margin: 12px 0 6px; }
.af-sub { color: var(--muted); font-size: 13px; margin-bottom: 18px; }
.af-actions { display: flex; flex-direction: column; align-items: center; gap: 10px; }
.af-btn {
  background: var(--brand-grad); color: #fff; border: none; border-radius: 10px;
  padding: 10px 18px; font-weight: 700; cursor: pointer;
}
.af-btn:hover { opacity: .92; }
.af-link {
  background: none; border: none; color: var(--muted); font-size: 13px;
  cursor: pointer; text-decoration: underline;
}
.af-link:hover { color: var(--brand, #7c5cff); }
</style>
