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

    <!-- M5 主链路单独做主推入口：不混进快速入口网格，避免 9 张卡片在 4 列下末行只剩 1 张 -->
    <router-link v-if="crawlEntry" :to="crawlEntry.to" class="crawl-entry">
      <span class="ce-ico">🧩</span>
      <span class="ce-text">
        <b>系统化任务采集</b>
        <i>课程源 → 采集任务 → 解析审核 → 去重入库，M5 全链路工作台</i>
      </span>
      <span class="ce-go">进入工作台 <span aria-hidden="true">→</span></span>
    </router-link>

    <!-- 功能模块：按授权渲染（最多 8 张），4 列排布；无授权的入口不出现，避免点了被守卫弹回 -->
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

    <p class="muted">提示：统计与列表数据来自后端实时接口；课程源、采集任务与知识点审核请从「系统化任务采集」进入。</p>
  </div>
</template>

<script setup>
import { reactive, computed, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { listKnowledge, listWrongQuestions, listCompliance, DEMO_STUDENT_ID } from '../api/admin'

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

// 采集工作台是 M5 主链路，独立主推入口（无采集授权则不显示，避免点了被守卫弹回）。
// 与快速入口网格分开，主推项不占卡片位，4 列排布不会因多一张卡露出单卡空行。
const crawlEntry = computed(() =>
  auth.hasRouteAccess('/course-intake/workbench')
    ? { to: '/course-intake/workbench' }
    : null
)

// 快捷入口与侧栏同源：按 /menu/my 授权过滤。此前 8 张卡片是硬编码的，
// 菜单里已经撤掉的页面（如 region_admin 的「教师批改」、parent 的多数入口）
// 仍会显示在首页，点了被路由守卫弹回概览且没有任何提示。
// hasRouteAccess 在授权未就绪时返回 true，首屏不会闪空白；菜单拉回来后 computed 自动重算。
const ALL_MODULES = [
  { to: '/knowledge', label: '教研内容管理', icon: '📚', grad: 'linear-gradient(135deg,#ff7a45,#ff3d81)' },
  { to: '/analytics', label: '学情看板', icon: '📈', grad: 'linear-gradient(135deg,#1fb6ff,#7c4dff)' },
  { to: '/wrong-questions', label: '错题本', icon: '📕', grad: 'linear-gradient(135deg,#ff5e62,#ff8e53)' },
  { to: '/exam', label: '自动组卷', icon: '📝', grad: 'linear-gradient(135deg,#00c48c,#1fb6ff)' },
  { to: '/grading', label: '教师批改', icon: '✅', grad: 'linear-gradient(135deg,#7c4dff,#ff3d81)' },
  { to: '/reports', label: '家长周报', icon: '💌', grad: 'linear-gradient(135deg,#ff8e53,#ffb020)' },
  { to: '/compliance', label: '合规审核', icon: '🛡️', grad: 'linear-gradient(135deg,#5b6cff,#1fb6ff)' },
  { to: '/practice', label: '在线练习', icon: '🎯', grad: 'linear-gradient(135deg,#ff3d81,#7c4dff)' },
]
const modules = computed(() => ALL_MODULES.filter((m) => auth.hasRouteAccess(m.to)))

onMounted(async () => {
  // 三个统计接口分属不同权限域：school 角色对三者均无权限。用 allSettled
  // 让各卡独立结算，避免任一 403 拖垮全部统计卡（此前整批 reject 导致三卡全 0
  // 且无任何提示）。错题接口 2026-11-14 已按 A 方案放行 region_admin 只读，
  // 与 /analytics/summary、/compliance 一致，不再需要为它单独兜 403。
  // 学生 ID 与错题本页共用 DEMO_STUDENT_ID：写死 student_001 时库里 0 行，卡片恒 0。
  const [k, w, c] = await Promise.allSettled([
    listKnowledge(),
    listWrongQuestions(DEMO_STUDENT_ID),
    listCompliance('pending'),
  ])
  const pick = (r) => (r.status === 'fulfilled' ? r.value.length : '—')
  statList[0].num = pick(k)
  statList[1].num = pick(w)
  statList[2].num = pick(c)
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

.stats { margin-bottom: 20px; }

/* 主推入口：横向一条，与下方卡片网格拉开层级，末行不再留单卡 */
.crawl-entry {
  display: flex; align-items: center; gap: 16px;
  padding: 16px 22px; margin-bottom: 28px;
  border-radius: var(--r-lg);
  background: linear-gradient(100deg, #fff 0%, #fff 52%, var(--bg-2) 100%);
  border: 1px solid var(--line-strong);
  box-shadow: var(--shadow-sm);
  text-decoration: none; color: var(--ink);
  transition: transform .18s ease, box-shadow .25s ease, border-color .2s ease;
}
.crawl-entry:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow);
  border-color: var(--brand);
}
.ce-ico {
  width: 46px; height: 46px; flex-shrink: 0;
  border-radius: 14px; display: grid; place-items: center;
  font-size: 22px; background: var(--brand-grad); color: #fff;
  box-shadow: 0 6px 16px rgba(255, 61, 129, .28);
}
.ce-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.ce-text b { font-size: 15px; font-weight: 800; }
.ce-text i { font-style: normal; font-size: 12.5px; color: var(--ink-2); }
.ce-go { margin-left: auto; flex-shrink: 0; font-size: 13px; font-weight: 700; color: var(--brand-2); }
.ce-go span { margin-left: 4px; }

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

@media (max-width: 700px) {
  .crawl-entry { padding: 14px 16px; gap: 12px; }
  .ce-text i { display: none; }
  .ce-go span { display: none; }
}
</style>
