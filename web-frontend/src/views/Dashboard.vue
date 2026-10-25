<template>
  <div class="tes-page tes-anim">
    <!-- 英雄横幅 -->
    <div class="hero-banner">
      <div class="hero-text">
        <div class="hello">{{ greeting }}，{{ user?.username }} 👋</div>
        <h1>今日宜拼搏，每一题都是向上的台阶</h1>
        <p>步骤级归因 · 个性化提分 · 让努力看得见</p>
      </div>
      <div class="hero-fire">🔥</div>
    </div>

    <!-- 统计卡 -->
    <div class="tes-grid cols-3 stats">
      <div class="tes-stat" v-for="s in statList" :key="s.lbl">
        <div class="num" :style="{ color: s.color }">{{ s.num }}</div>
        <div class="lbl">{{ s.lbl }}</div>
      </div>
    </div>

    <!-- 功能模块 -->
    <h2 class="sec-title">快速入口</h2>
    <div class="tes-grid cols-4 modules">
      <router-link
        v-for="m in modules"
        :key="m.to"
        :to="m.to"
        class="module"
        :style="{ '--g': m.grad }"
      >
        <span class="m-ico">{{ m.icon }}</span>
        <span class="m-label">{{ m.label }}</span>
        <span class="m-go">进入 →</span>
      </router-link>
    </div>

    <p class="muted">提示：当前后端为骨架占位，页面数据来自内存示例；接入 PostgreSQL 后展示真实数据。</p>
  </div>
</template>

<script setup>
import { reactive, computed, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { listKnowledge, listWrongQuestions, listCompliance } from '../api/admin'

const auth = useAuthStore()
const user = auth.user

const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
})

const statList = reactive([
  { num: 0, lbl: '知识点', color: '#ff5e62' },
  { num: 0, lbl: '待关注错题', color: '#ffb020' },
  { num: 0, lbl: '待审内容', color: '#7c4dff' },
])

const modules = [
  { to: '/knowledge', label: '教研内容管理', icon: '📚', grad: 'linear-gradient(135deg,#ff7a45,#ff3d81)' },
  { to: '/analytics', label: '学情看板', icon: '📈', grad: 'linear-gradient(135deg,#1fb6ff,#7c4dff)' },
  { to: '/wrong-questions', label: '错题本', icon: '📕', grad: 'linear-gradient(135deg,#ff5e62,#ff8e53)' },
  { to: '/exam', label: '自动组卷', icon: '📝', grad: 'linear-gradient(135deg,#00c48c,#1fb6ff)' },
  { to: '/grading', label: '教师批改', icon: '✅', grad: 'linear-gradient(135deg,#7c4dff,#ff3d81)' },
  { to: '/reports', label: '家长周报', icon: '💌', grad: 'linear-gradient(135deg,#ff8e53,#ffb020)' },
  { to: '/compliance', label: '合规审核', icon: '🛡️', grad: 'linear-gradient(135deg,#5b6cff,#1fb6ff)' },
  { to: '/practice', label: '在线练习', icon: '🎯', grad: 'linear-gradient(135deg,#ff3d81,#7c4dff)' },
]

onMounted(async () => {
  try {
    const [k, w, c] = await Promise.all([
      listKnowledge(),
      listWrongQuestions('student_001'),
      listCompliance('pending'),
    ])
    statList[0].num = k.length
    statList[1].num = w.length
    statList[2].num = c.length
  } catch (e) {
    // 演示环境下忽略统计失败
  }
})
</script>

<style scoped>
.hero-banner {
  position: relative;
  display: flex; align-items: center; justify-content: space-between;
  background: linear-gradient(135deg, #ff7a45 0%, #ff3d81 60%, #7c4dff 120%);
  background-size: 200% 200%;
  animation: tes-grad-shift 16s ease infinite;
  border-radius: var(--r-lg);
  padding: 26px 32px;
  color: #fff;
  overflow: hidden;
  margin-bottom: 24px;
  box-shadow: var(--shadow);
}
.hero-banner::after {
  content: ""; position: absolute;
  width: 260px; height: 260px;
  right: -40px; bottom: -80px;
  background: radial-gradient(circle, rgba(255,255,255,.3), transparent 70%);
}
.hello { font-size: 14px; font-weight: 600; opacity: .95; }
.hero-text h1 { font-size: 24px; margin: 8px 0 6px; color: #fff; }
.hero-text p { margin: 0; font-size: 13px; opacity: .9; }
.hero-fire { font-size: 56px; filter: drop-shadow(0 6px 12px rgba(0,0,0,.2)); z-index: 1; }

.stats { margin-bottom: 30px; }

.sec-title { font-size: 18px; margin: 6px 0 16px; }
.modules { margin-bottom: 26px; }
.module {
  position: relative;
  display: flex; flex-direction: column; gap: 10px;
  padding: 20px;
  border-radius: var(--r-lg);
  background: #fff;
  border: 1px solid var(--line);
  text-decoration: none;
  color: var(--ink);
  overflow: hidden;
  transition: transform .18s ease, box-shadow .25s ease;
}
.module::before {
  content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 5px;
  background: var(--g);
}
.module:hover { transform: translateY(-4px); box-shadow: var(--shadow); }
.m-ico {
  width: 44px; height: 44px; border-radius: 13px;
  display: grid; place-items: center; font-size: 22px;
  background: var(--g); color: #fff;
  box-shadow: 0 6px 14px rgba(0,0,0,.12);
}
.m-label { font-weight: 700; font-size: 15px; }
.m-go { font-size: 12px; font-weight: 700; color: var(--brand-2); }

.muted { color: var(--muted); font-size: 13px; margin-top: 8px; }
</style>
