<template>
  <div class="app-shell">
    <!-- 顶部一级导航：品牌 + 一级业务域 + 用户区 -->
    <header class="app-top">
      <div class="brand">
        <span class="brand-mark">T</span>
        <span class="brand-text">
          <b>Tes Edu</b>
          <i>智慧助学平台</i>
        </span>
      </div>

      <nav class="top-nav" aria-label="一级导航">
        <button
          v-for="d in domains"
          :key="d.key"
          type="button"
          class="top-tab"
          :class="{ on: d.key === currentDomain.key }"
          :title="d.label"
          @click="go(d)"
        >{{ d.label }}</button>
      </nav>

      <div class="top-right">
        <span class="role-chip">{{ roleText }}</span>
        <span class="user-chip">
          <span class="avatar">{{ (user?.username || 'U').slice(0, 1).toUpperCase() }}</span>
          {{ user?.username }}
        </span>
        <button class="logout" @click="onLogout">退出</button>
      </div>
    </header>

    <div class="app-body">
      <!-- 左侧二级菜单：只显示当前一级域，授权菜单 + 采集工作域共用一套渲染 -->
      <aside class="app-side">
        <button
          type="button"
          class="side-title nav-group"
          :class="{ closed: isClosed(currentDomain.key) }"
          :title="currentDomain.label"
          :aria-expanded="isClosed(currentDomain.key) ? 'false' : 'true'"
          @click="toggleGroup(currentDomain.key)"
        >
          <span class="nav-group-label">{{ currentDomain.label }}</span>
          <span class="side-count">{{ readyCount }} 项可用</span>
        </button>

        <nav class="side-nav nav" ref="navEl">
          <template v-if="!isClosed(currentDomain.key)">
            <template
              v-for="it in currentDomain.items"
              :key="it.divider ? `divider-${it.key}` : (it.path || it.to || it.label)"
            >
              <!-- 授权菜单的 DIR 分组在概览域里降为二级分组标题，不占顶栏一级域 -->
              <div v-if="it.divider" class="side-divider">{{ it.label }}</div>
              <router-link
                v-else-if="!it.planned"
                :to="it.path || it.to"
                class="side-item nav-item"
                :title="it.label"
              >
                <span class="side-left">
                  <span v-if="it.icon" class="nav-ico">{{ it.icon }}</span>
                  <span class="side-label nav-label">{{ it.label }}</span>
                </span>
                <span v-if="it.m5" class="m5">{{ it.m5 }}</span>
              </router-link>
              <div
                v-else
                class="side-item planned"
                :title="`${it.m5 || ''} 规划中，尚未接入`"
              >
                <span class="side-left">
                  <span class="side-label nav-label">{{ it.label }}</span>
                </span>
                <span class="plan-tag">规划中</span>
              </div>
            </template>
          </template>
        </nav>

        <div class="side-foot">
          <div class="fire">🔥 为梦想而战</div>
          <div class="ver">v0.1.0 · 让每一分努力都被看见</div>
        </div>
      </aside>

      <!-- 右侧工作区：面包屑 + 页面 -->
      <main class="app-main">
        <div class="crumb">
          <span>首页</span>
          <span class="crumb-sep">/</span>
          <span>{{ currentDomain.label }}</span>
          <template v-if="currentTitle !== currentDomain.label">
            <span class="crumb-sep">/</span>
            <b>{{ currentTitle }}</b>
          </template>
          <span v-if="crumbM5" class="crumb-m5">{{ crumbM5 }}</span>
        </div>
        <div class="app-content">
          <router-view />
        </div>
      </main>
    </div>
  </div>
</template>

<script setup>
/**
 * 全站统一三段式壳子（2026-11-12）：顶部一级导航 + 左侧二级菜单 + 右侧工作区。
 *
 * 之前主站是「左侧紫渐变侧栏 + 右上白顶栏」，采集工作台另有一套 CrawlLayout，
 * 两套布局割裂。现在两者合并：顶栏一级域 = 概览 + 采集工作台三个业务域；
 * 后端 /menu/my 的 DIR 分组（区域运营等）降为概览域左侧的二级分组标题，
 * 排在常驻「概览」项下面。左侧只渲染当前域的二级项，采集规划项继续灰显「规划中」。
 * 视觉沿用 Tes Edu 设计系统（紫渐变顶栏、橙粉品牌色、白卡片、粉白底），
 * 不引入新配色。采集子路由仍由后端 require_role/require_perm 兜底。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { toNavGroups } from '../utils/menu'
import { CRAWL_NAV, findCrawlNav } from '../utils/crawlNav'
// 六页采集工作台的 cw-* 页面样式原先由 CrawlLayout 注入；统一壳子后改由本壳子
// 承载（cw- 前缀只作用于采集页，不污染主站其它页面）。
import '../styles/crawlPages.css'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const user = auth.user

const roleText = computed(() => {
  const map = {
    admin: '管理员', region_admin: '区域管理员', school: '学校',
    teacher: '教师', parent: '家长',
  }
  return map[user?.role] || user?.role || ''
})

const navGroups = computed(() => toNavGroups(auth.menuTree))
// 无采集菜单授权的账号不显示三个采集域，避免「看得见、点了被守卫弹回」
const canCrawl = computed(() => auth.hasRouteAccess('/course-intake/workbench'))

/**
 * 一级域：概览 + 采集三域（顺序即顶栏顺序）。
 * 后端 /menu/my 的 DIR 分组（区域运营 / 系统设置…）不再占顶栏一级域，
 * 而是降为「概览」域左侧菜单里的二级分组标题，排在常驻「概览」项下面。
 */
const domains = computed(() => {
  const homeItems = [{ label: '概览', to: '/dashboard', path: '/dashboard', icon: '🏠' }]
  for (const g of navGroups.value) {
    homeItems.push({ divider: true, key: g.key, label: g.label })
    for (const it of g.items) homeItems.push({ ...it, path: it.to })
  }
  const list = [{ key: 'home', label: '概览', items: homeItems }]
  if (canCrawl.value) {
    for (const g of CRAWL_NAV) {
      list.push({
        key: `crawl-${g.key}`,
        label: g.label,
        crawl: true,
        items: g.items.map((it) => ({ ...it, to: it.path })),
      })
    }
  }
  return list
})

/** 当前一级域：采集子页精确命中优先（否则 /ops/course-intake 会抢走高亮） */
const currentDomain = computed(() => {
  const path = route.path
  const crawlHit = domains.value.find(
    (d) => d.crawl && d.items.some((it) => it.path === path)
  )
  if (crawlHit) return crawlHit
  const hit = domains.value.find((d) => !d.crawl && d.items.some((it) => {
    const p = it.path || it.to
    return p && (path === p || path.startsWith(`${p}/`))
  }))
  return hit || domains.value[0]
})

const readyCount = computed(
  () => currentDomain.value.items.filter((it) => !it.planned && !it.divider).length
)
const crawlItem = computed(
  () => currentDomain.value.crawl ? findCrawlNav(route.path).item : null
)
const currentTitle = computed(
  () => crawlItem.value?.label || route.meta.title || currentDomain.value.label
)
const crumbM5 = computed(() => crawlItem.value?.m5 || '')

function firstReady(d) {
  const hit = d.items.find((it) => !it.planned && !it.divider && (it.path || it.to))
  return hit ? (hit.path || hit.to) : null
}
function go(d) {
  const to = firstReady(d)
  if (to && to !== route.path) router.push(to)
}

// 菜单项可能超出侧栏高度（.nav 独立滚动）：路由变化 / 菜单加载完成后，
// 把当前激活项滚入可见区。只滚动 .nav 容器自身，不连带滚动主区。
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

// 分组折叠：点侧栏域标题收起本域二级菜单（默认全展开）。key 用 domain.key，
// 换角色/换租户不会串组；只记视觉偏好，不进 store。localStorage 不可用静默降级。
const GROUPS_KEY = 'tes_nav_closed_groups'
const closedGroups = ref([])
try {
  const raw = JSON.parse(localStorage.getItem(GROUPS_KEY) || '[]')
  closedGroups.value = Array.isArray(raw) ? raw.filter((k) => typeof k === 'string') : []
} catch (e) {
  closedGroups.value = []
}
function isClosed(key) {
  return closedGroups.value.includes(key)
}
function persistGroups() {
  try {
    localStorage.setItem(GROUPS_KEY, JSON.stringify(closedGroups.value))
  } catch (e) {
    /* 隐私模式/配额满：仅当次生效 */
  }
}
function toggleGroup(key) {
  closedGroups.value = isClosed(key)
    ? closedGroups.value.filter((k) => k !== key)
    : [...closedGroups.value, key]
  persistGroups()
}
// 深链进入被收起的域时自动展开该域，否则「看不到自己在哪」
function openActiveGroup() {
  const key = currentDomain.value.key
  if (isClosed(key)) {
    closedGroups.value = closedGroups.value.filter((k) => k !== key)
    persistGroups()
  }
}
onMounted(() => {
  openActiveGroup()
  nextTick(revealActive)
})
watch(() => auth.menuTree, () => {
  openActiveGroup()
  nextTick(revealActive)
})
watch(() => route.path, () => {
  openActiveGroup()
  nextTick(revealActive)
})

function onLogout() {
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.app-shell { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

/* ---------- 顶栏：沿用原侧栏紫渐变，一级导航由侧栏移到这里 ---------- */
.app-top {
  height: 60px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 0 22px;
  background: linear-gradient(90deg, #241b3a 0%, #3a1e5c 58%, #5b2a8c 100%);
  color: #fff;
}
.brand {
  display: flex; align-items: center; gap: 10px; flex-shrink: 0;
  padding-right: 20px;
  border-right: 1px solid rgba(255, 255, 255, .16);
}
.brand-mark {
  width: 36px; height: 36px;
  display: grid; place-items: center;
  border-radius: 11px;
  background: var(--brand-grad);
  font-weight: 900; font-size: 18px; color: #fff;
  box-shadow: 0 6px 16px rgba(255, 61, 129, .45);
}
.brand-text { display: flex; flex-direction: column; line-height: 1.2; white-space: nowrap; }
.brand-text b { font-size: 15px; letter-spacing: .5px; }
.brand-text i { font-style: normal; font-size: 11px; color: rgba(255, 255, 255, .6); }

.top-nav {
  display: flex; align-items: center; gap: 4px;
  flex: 1; min-width: 0; overflow-x: auto; overflow-y: hidden;
  scrollbar-width: none;
}
.top-nav::-webkit-scrollbar { display: none; }
.top-tab {
  font: inherit; font-size: 14px; font-weight: 600;
  color: rgba(255, 255, 255, .72);
  background: transparent; border: 0;
  padding: 8px 14px; border-radius: 10px;
  cursor: pointer; white-space: nowrap; flex-shrink: 0;
  transition: background .18s, color .18s;
}
.top-tab:hover { background: rgba(255, 255, 255, .08); color: #fff; }
.top-tab.on { background: var(--brand-grad); color: #fff; box-shadow: 0 6px 16px rgba(255, 61, 129, .35); }

.top-right { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.role-chip {
  font-size: 12px; font-weight: 700;
  padding: 4px 12px; border-radius: 999px;
  background: #eef9f3; color: var(--success);
}
.user-chip { display: flex; align-items: center; gap: 7px; font-weight: 600; font-size: 13px; white-space: nowrap; }
.avatar {
  width: 28px; height: 28px; border-radius: 50%;
  display: grid; place-items: center;
  background: var(--brand-grad); color: #fff; font-size: 12px; font-weight: 800;
  box-shadow: 0 0 0 2px rgba(255, 255, 255, .32);
}
/* 退出改成顶栏幽灵按钮：白底红字实心块在深紫顶栏里过于抢眼 */
.logout {
  background: rgba(255, 255, 255, .10); color: rgba(255, 255, 255, .9);
  border: 1px solid rgba(255, 255, 255, .28); border-radius: 10px;
  padding: 6px 12px; font-size: 13px; cursor: pointer;
}
.logout:hover { background: #fff; color: var(--danger); border-color: #ffd6de; }

.app-body { flex: 1; display: flex; min-height: 0; }

/* ---------- 左侧二级菜单：白底，选中态沿用品牌浅粉 ---------- */
.app-side {
  width: 208px; flex-shrink: 0;
  background: var(--surface);
  border-right: 1px solid var(--line);
  display: flex; flex-direction: column;
  padding: 16px 12px; min-height: 0;
}
.side-title {
  width: 100%;
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 10px 12px;
  font: inherit; font-size: 14px; font-weight: 800; color: var(--ink);
  background: transparent; border: 0; text-align: left; cursor: pointer;
}
.side-title:hover { color: var(--brand-2); }
.side-title:focus-visible { outline: 2px solid var(--brand); outline-offset: -2px; border-radius: 8px; }
.nav-group-label { min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.side-title::after {
  content: "▾"; flex-shrink: 0; font-size: 9px; opacity: .6;
  margin-left: 6px; transition: transform .18s;
}
.side-title.closed::after { transform: rotate(-90deg); }
.side-count { font-size: 11px; font-weight: 600; color: var(--muted); margin-left: auto; white-space: nowrap; }
.side-nav {
  display: flex; flex-direction: column; gap: 3px;
  overflow-y: auto; overflow-x: hidden; min-height: 0; flex: 1 1 auto;
  scrollbar-width: thin; scrollbar-color: var(--line-strong) transparent;
}
.side-nav::-webkit-scrollbar { width: 5px; }
.side-nav::-webkit-scrollbar-track { background: transparent; }
.side-nav::-webkit-scrollbar-thumb { background: var(--line-strong); border-radius: 999px; }
.side-divider {
  margin-top: 6px; padding: 14px 12px 5px;
  border-top: 1px solid var(--line);
  font-size: 11px; font-weight: 700; letter-spacing: 1px;
  color: var(--muted); flex-shrink: 0;
}
.side-item {
  position: relative;
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 10px 12px; border-radius: 10px;
  color: var(--ink-2); font-size: 13px; font-weight: 600;
  text-decoration: none; flex-shrink: 0;
  transition: background .18s, color .18s;
}
.side-item:hover { background: var(--bg-2); color: var(--ink); }
.side-item.router-link-active { background: var(--brand-grad-soft); color: var(--brand-2); }
.side-item.router-link-active::before {
  content: ""; position: absolute; left: 0; top: 8px; bottom: 8px;
  width: 3px; border-radius: 0 3px 3px 0; background: var(--brand-grad);
}
.side-left { display: flex; align-items: center; gap: 8px; min-width: 0; }
.nav-ico { font-size: 15px; width: 20px; text-align: center; flex-shrink: 0; }
.side-label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.m5 { font-size: 10px; font-weight: 600; color: var(--muted); white-space: nowrap; }
.side-item.router-link-active .m5 { color: var(--brand-2); }
.side-item.planned { color: var(--muted); cursor: not-allowed; }
.side-item.planned:hover { background: transparent; color: var(--muted); }
.plan-tag { font-size: 10px; padding: 2px 6px; border-radius: 6px; background: var(--bg-2); white-space: nowrap; }
.side-foot { margin-top: auto; padding: 12px 8px 0; flex-shrink: 0; }
.fire {
  font-size: 12px; font-weight: 700; color: var(--brand-2);
  background: var(--bg-2); border-radius: 999px;
  padding: 7px 10px; text-align: center;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ver { font-size: 11px; color: var(--muted); margin-top: 8px; text-align: center; }

/* ---------- 右侧工作区 ---------- */
.app-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.crumb {
  height: 46px; flex-shrink: 0;
  display: flex; align-items: center; gap: 8px;
  padding: 0 24px;
  background: rgba(255, 255, 255, .85);
  border-bottom: 1px solid var(--line);
  font-size: 13px; color: var(--muted);
}
.crumb b { color: var(--ink); }
.crumb-sep { color: var(--line-strong); }
.crumb-m5 { margin-left: auto; font-size: 11px; color: var(--muted); }
.app-content { flex: 1; overflow: auto; padding: 24px 28px; }
/* 超宽屏不把卡片/表格拉到无限宽：页面内容限宽并居中（只作用于 .tes-page /
   .cw-page 根容器，不改各页内部网格，也不影响需要整宽的页面） */
.app-content :deep(.tes-page) { max-width: 1280px; margin-inline: auto; }
.app-content :deep(.cw-page) { margin-inline: auto; }

@media (max-width: 1280px) {
  .app-top { gap: 12px; padding: 0 14px; }
  .top-tab { padding: 7px 10px; font-size: 13px; }
  .app-side { width: 186px; }
  .app-content { padding: 20px 22px; }
}
@media (max-width: 1100px) {
  .app-side { width: 168px; padding: 14px 8px; }
  .brand-text i, .role-chip, .side-foot .ver { display: none; }
  .side-item { padding: 9px 10px; font-size: 12.5px; }
}
@media (max-width: 900px) {
  .brand { padding-right: 12px; }
  .app-top { gap: 10px; }
  .app-side { width: 150px; }
  .crumb { padding: 0 16px; }
  .app-content { padding: 18px 16px; }
}
</style>
