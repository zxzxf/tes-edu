<template>
  <div class="tes-layout">
    <aside class="tes-side">
      <div class="brand">
        <span class="brand-mark">T</span>
        <span class="brand-text">
          <b>Tes Edu</b>
          <i>智慧助学平台</i>
        </span>
      </div>

      <nav class="nav" ref="navEl">
        <!-- 概览无对应后端菜单节点（所有角色可见），常驻首位 -->
        <router-link to="/dashboard" class="nav-item" title="概览">
          <span class="nav-ico">🏠</span>
          <span class="nav-label">概览</span>
        </router-link>
        <!-- T-AM7：其余导航按 /menu/my 授权动态渲染；无前端页面的菜单
             （如 /system/* 管理页）经映射表过滤，不出死链 -->
        <template v-for="g in navGroups" :key="g.key">
          <div class="nav-group" :title="g.label">{{ g.label }}</div>
          <router-link
            v-for="item in g.items"
            :key="item.to"
            :to="item.to"
            class="nav-item"
            :title="item.label"
          >
            <span class="nav-ico">{{ item.icon }}</span>
            <span class="nav-label">{{ item.label }}</span>
          </router-link>
        </template>
      </nav>

      <div class="side-foot">
        <div class="fire">🔥 为梦想而战</div>
        <div class="ver">v0.1.0 · 让每一分努力都被看见</div>
      </div>
    </aside>

    <div class="tes-main">
      <header class="tes-top">
        <div class="crumb">
          <span class="crumb-home">首页</span>
          <span class="crumb-sep">/</span>
          <span class="crumb-cur">{{ currentTitle }}</span>
        </div>
        <div class="top-right">
          <span class="role-chip">{{ roleText }}</span>
          <span class="user-chip">
            <span class="avatar">{{ (user?.username || 'U').slice(0, 1).toUpperCase() }}</span>
            {{ user?.username }}
          </span>
          <button class="logout" @click="onLogout">退出</button>
        </div>
      </header>

      <section class="tes-content">
        <router-view />
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { toNavGroups } from '../utils/menu'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const user = auth.user
const currentTitle = computed(() => route.meta.title || 'Tes Edu')

// T-AM7：角色文案补齐五角色（原静态表缺 region_admin/school）
const roleText = computed(() => {
  const map = {
    admin: '管理员', region_admin: '区域管理员', school: '学校',
    teacher: '教师', parent: '家长',
  }
  return map[user?.role] || user?.role || ''
})

// 动态菜单分组（源自 /menu/my 授权；BUTTON 节点与无前端页面的菜单已过滤）
const navGroups = computed(() => toNavGroups(auth.menuTree))

// 菜单项可能超出侧栏高度（.nav 独立滚动）：路由变化 / 菜单加载完成后，
// 把当前激活项滚入可见区，避免深链进入时「看不到自己在哪」。
// 只滚动 .nav 容器自身，不用 scrollIntoView（后者会连带滚动页面主区）。
const navEl = ref(null)
function revealActive() {
  const nav = navEl.value
  if (!nav) return
  const active = nav.querySelector('.router-link-active')
  if (!active) return
  const navTop = nav.scrollTop
  const navBottom = navTop + nav.clientHeight
  const itemTop = active.offsetTop
  const itemBottom = itemTop + active.offsetHeight
  if (itemTop < navTop) {
    nav.scrollTop = itemTop - 8
  } else if (itemBottom > navBottom) {
    nav.scrollTop = itemBottom - nav.clientHeight + 8
  }
}
onMounted(() => nextTick(revealActive))
watch(() => route.path, () => nextTick(revealActive))
watch(() => auth.menuTree, () => nextTick(revealActive))

function onLogout() {
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.tes-layout { display: flex; height: 100vh; overflow: hidden; }

/* ---------- 侧栏：深邃渐变，青春活力 ---------- */
.tes-side {
  width: 232px;
  flex-shrink: 0;
  background: linear-gradient(180deg, #241b3a 0%, #3a1e5c 58%, #5b2a8c 100%);
  color: #fff;
  display: flex;
  flex-direction: column;
  padding: 22px 16px;
  position: relative;
  min-height: 0; /* 关键：允许内部 nav 收缩出滚动区，防止菜单过多被 100vh+overflow:hidden 裁掉 */
}
.tes-side::after {
  content: "";
  position: absolute;
  top: -40px; right: -40px;
  width: 160px; height: 160px;
  background: radial-gradient(circle, rgba(255, 122, 69, .55), transparent 70%);
  filter: blur(8px);
}
.brand { display: flex; align-items: center; gap: 12px; margin-bottom: 26px; flex-shrink: 0; min-width: 0; }
.brand-mark {
  width: 40px; height: 40px;
  flex-shrink: 0;
  display: grid; place-items: center;
  border-radius: 12px;
  background: var(--brand-grad);
  font-weight: 900; font-size: 20px; color: #fff;
  box-shadow: 0 6px 16px rgba(255, 61, 129, .45);
}
.brand-text { display: flex; flex-direction: column; line-height: 1.2; min-width: 0; }
.brand-text b { font-size: 17px; letter-spacing: .5px; }
.brand-text i { font-style: normal; font-size: 11px; color: rgba(255,255,255,.6); }

/* 导航区：独立滚动，菜单再多也不撑破侧栏、不遮挡页脚 */
.nav {
  display: flex; flex-direction: column; gap: 4px;
  position: relative; z-index: 1;
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  margin: 0 -6px;      /* 抵消滚动条宽度，保持视觉居中 */
  padding: 0 6px;
  scrollbar-width: thin;                 /* Firefox */
  scrollbar-color: rgba(255,255,255,.28) transparent;
}
/* WebKit 细滚动条：不占宽度、悬停才显眼 */
.nav::-webkit-scrollbar { width: 5px; }
.nav::-webkit-scrollbar-track { background: transparent; }
.nav::-webkit-scrollbar-thumb {
  background: rgba(255,255,255,.22);
  border-radius: 999px;
}
.nav:hover::-webkit-scrollbar-thumb { background: rgba(255,255,255,.38); }

.nav-group {
  font-size: 11px; font-weight: 700; letter-spacing: 1px;
  color: rgba(255, 255, 255, .45);
  padding: 14px 14px 6px;
  flex-shrink: 0;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
/* 首个分组标题无需上方大间距，避免滚动时顶部空洞 */
.nav > .nav-group:first-of-type { padding-top: 4px; }
.nav-item {
  display: flex; align-items: center; gap: 12px;
  padding: 11px 14px;
  border-radius: 12px;
  color: rgba(255, 255, 255, .72);
  font-size: 14px; font-weight: 600;
  text-decoration: none;
  transition: background .2s, color .2s, transform .12s;
  flex-shrink: 0;   /* 关键：不被 flex 压缩变形，保持等高可点 */
  min-width: 0;
}
.nav-item:hover { background: rgba(255, 255, 255, .08); color: #fff; }
.nav-item.router-link-active {
  background: var(--brand-grad);
  color: #fff;
  box-shadow: 0 8px 18px rgba(255, 61, 129, .35);
}
.nav-ico { font-size: 17px; width: 22px; text-align: center; flex-shrink: 0; }
/* 长菜单名（如「系统服务(MCP厂商)」）省略号截断，悬停看 title 全文 */
.nav-label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.side-foot { margin-top: 12px; flex-shrink: 0; position: relative; z-index: 1; }
.fire {
  font-size: 13px; font-weight: 700; color: #ffd0a3;
  background: rgba(255, 255, 255, .08);
  border: 1px solid rgba(255, 255, 255, .12);
  border-radius: 999px;
  padding: 8px 14px; text-align: center;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ver {
  font-size: 11px; color: rgba(255, 255, 255, .45);
  margin-top: 10px; text-align: center;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

/* ---------- 主区 ---------- */
.tes-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.tes-top {
  height: 64px; flex-shrink: 0;
  background: rgba(255, 255, 255, .85);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--line);
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 28px;
}
.crumb { display: flex; align-items: center; gap: 10px; font-size: 14px; }
.crumb-home { color: var(--muted); }
.crumb-sep { color: var(--line-strong); }
.crumb-cur { font-weight: 800; }
.top-right { display: flex; align-items: center; gap: 12px; }
.role-chip {
  font-size: 12px; font-weight: 700;
  padding: 4px 12px; border-radius: 999px;
  background: #eef9f3; color: var(--success);
}
.user-chip { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 14px; }
.avatar {
  width: 30px; height: 30px; border-radius: 50%;
  display: grid; place-items: center;
  background: var(--brand-grad); color: #fff; font-size: 13px; font-weight: 800;
}
.logout {
  background: #fff; color: var(--danger);
  border: 1.5px solid #ffd6de; border-radius: 10px;
  padding: 6px 14px; font-size: 13px;
}
.logout:hover { background: #fff0f3; }

.tes-content { flex: 1; overflow: auto; padding: 28px; }

/* ---------- 窄屏自适应：侧栏收窄，优先保证菜单可滚动可达 ---------- */
@media (max-width: 1280px) {
  .tes-side { width: 208px; padding: 18px 12px; }
  .nav-item { padding: 10px 12px; gap: 10px; font-size: 13.5px; }
  .nav-ico { width: 20px; font-size: 16px; }
  .brand { margin-bottom: 20px; gap: 10px; }
  .brand-mark { width: 36px; height: 36px; font-size: 18px; }
  .brand-text b { font-size: 15px; }
  .tes-content { padding: 22px; }
}
@media (max-width: 1100px) {
  .tes-side { width: 186px; }
  .nav-item { padding: 9px 10px; gap: 8px; font-size: 13px; }
  .brand-text i { display: none; } /* 副标题先让位，保住菜单空间 */
  .side-foot .ver { display: none; }
}
</style>
