<template>
  <div class="crawl-shell">
    <header class="crawl-top">
      <div class="brand">
        <span class="brand-mark">T</span>
        <span class="brand-text">
          <b>Tes Edu</b>
          <i>系统化任务采集</i>
        </span>
      </div>

      <nav class="top-nav" aria-label="采集系统一级导航">
        <button
          v-for="g in groups"
          :key="g.key"
          type="button"
          class="top-tab"
          :class="{ on: g.key === group.key }"
          @click="go(g)"
        >{{ g.label }}</button>
      </nav>

      <div class="top-right">
        <span class="role-chip">{{ roleText }}</span>
        <span class="user-chip">
          <span class="avatar">{{ (user?.username || 'U').slice(0, 1).toUpperCase() }}</span>
          {{ user?.username }}
        </span>
        <button type="button" class="logout" @click="onLogout">退出</button>
        <router-link to="/dashboard" class="back-main" title="返回主站概览">返回主站</router-link>
      </div>
    </header>

    <div class="crawl-body">
      <aside class="crawl-side">
        <div class="side-title">
          <span>{{ group.label }}</span>
          <span class="side-count">{{ readyCount }} 项可用</span>
        </div>
        <nav class="side-nav">
          <template v-for="it in group.items" :key="it.label">
            <router-link
              v-if="!it.planned"
              :to="it.path"
              class="side-item"
            >
              <span class="side-label">{{ it.label }}</span>
              <span class="m5">{{ it.m5 }}</span>
            </router-link>
            <div v-else class="side-item planned" :title="`${it.m5} 规划中，尚未接入`">
              <span class="side-label">{{ it.label }}</span>
              <span class="plan-tag">规划中</span>
            </div>
          </template>
        </nav>
        <div class="side-foot">M5 课程采集与知识体系拆分</div>
      </aside>

      <main class="crawl-main">
        <div class="crumb">
          <span>{{ group.label }}</span>
          <span class="crumb-sep">/</span>
          <b>{{ item.label }}</b>
          <span v-if="item.m5" class="crumb-m5">{{ item.m5 }}</span>
        </div>
        <div class="crawl-content">
          <router-view />
        </div>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { CRAWL_NAV, findCrawlNav, firstReadyPath } from '../utils/crawlNav'
import '../styles/crawlPages.css'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const user = auth.user

const groups = CRAWL_NAV
const nav = computed(() => findCrawlNav(route.path))
const group = computed(() => nav.value.group)
const item = computed(() => nav.value.item)
const readyCount = computed(() => group.value.items.filter((it) => !it.planned).length)

const roleText = computed(() => {
  const map = {
    admin: '管理员', region_admin: '区域管理员', school: '学校',
    teacher: '教师', parent: '家长',
  }
  return map[user?.role] || user?.role || ''
})

function go(g) {
  const to = firstReadyPath(g)
  if (to) router.push(to)
}

function onLogout() {
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.crawl-shell { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

/* 顶栏：沿用现有侧栏的紫渐变与品牌色，只是把一级导航从侧栏移到顶部 */
.crawl-top {
  height: 60px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 22px;
  padding: 0 22px;
  background: linear-gradient(90deg, #241b3a 0%, #3a1e5c 58%, #5b2a8c 100%);
  color: #fff;
}
.brand { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.brand-mark {
  width: 36px; height: 36px;
  display: grid; place-items: center;
  border-radius: 11px;
  background: var(--brand-grad);
  font-weight: 900; font-size: 18px; color: #fff;
}
.brand-text { display: flex; flex-direction: column; line-height: 1.2; white-space: nowrap; }
.brand-text b { font-size: 15px; letter-spacing: .5px; }
.brand-text i { font-style: normal; font-size: 11px; color: rgba(255, 255, 255, .6); }

.top-nav { display: flex; align-items: center; gap: 6px; flex: 1; min-width: 0; }
.top-tab {
  font: inherit; font-size: 14px; font-weight: 600;
  color: rgba(255, 255, 255, .72);
  background: transparent; border: 0;
  padding: 8px 14px; border-radius: 10px;
  cursor: pointer; white-space: nowrap;
  transition: background .18s, color .18s;
}
.top-tab:hover { background: rgba(255, 255, 255, .08); color: #fff; }
.top-tab.on { background: var(--brand-grad); color: #fff; }

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
}
.logout {
  background: #fff; color: var(--danger);
  border: 1.5px solid #ffd6de; border-radius: 10px;
  padding: 6px 12px; font-size: 13px; cursor: pointer;
}
.logout:hover { background: #fff0f3; }
.back-main {
  color: rgba(255, 255, 255, .72); font-size: 12px;
  text-decoration: none; white-space: nowrap;
  border-left: 1px solid rgba(255, 255, 255, .2); padding-left: 10px;
}
.back-main:hover { color: #fff; }

.crawl-body { flex: 1; display: flex; min-height: 0; }

/* 左侧二级菜单：白底，选中态沿用品牌浅粉 */
.crawl-side {
  width: 208px; flex-shrink: 0;
  background: var(--surface);
  border-right: 1px solid var(--line);
  display: flex; flex-direction: column;
  padding: 18px 12px; min-height: 0;
}
.side-title {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 10px 12px; font-size: 14px; font-weight: 800; color: var(--ink);
}
.side-count { font-size: 11px; font-weight: 600; color: var(--muted); }
.side-nav { display: flex; flex-direction: column; gap: 3px; overflow-y: auto; min-height: 0; }
.side-item {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 10px 12px; border-radius: 10px;
  color: var(--ink-2); font-size: 13px; font-weight: 600;
  text-decoration: none; transition: background .18s, color .18s;
}
.side-item:hover { background: var(--bg-2); color: var(--ink); }
.side-item.router-link-active {
  background: var(--brand-grad-soft); color: var(--brand-2);
}
.side-label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.m5 { font-size: 10px; font-weight: 600; color: var(--muted); white-space: nowrap; }
.side-item.router-link-active .m5 { color: var(--brand-2); }
.side-item.planned { color: var(--muted); cursor: not-allowed; }
.side-item.planned:hover { background: transparent; color: var(--muted); }
.plan-tag { font-size: 10px; padding: 2px 6px; border-radius: 6px; background: var(--bg-2); white-space: nowrap; }
.side-foot { margin-top: auto; padding: 12px 10px 0; font-size: 11px; color: var(--muted); }

.crawl-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
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
.crawl-content { flex: 1; overflow: auto; padding: 22px 24px; background: var(--bg); }

@media (max-width: 1180px) {
  .crawl-top { gap: 12px; padding: 0 14px; }
  .top-tab { padding: 7px 10px; font-size: 13px; }
  .crawl-side { width: 178px; }
  .role-chip, .back-main { display: none; }
}
</style>
