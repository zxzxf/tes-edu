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

      <nav class="nav">
        <!-- 概览无对应后端菜单节点（所有角色可见），常驻首位 -->
        <router-link to="/dashboard" class="nav-item">
          <span class="nav-ico">🏠</span>
          <span class="nav-label">概览</span>
        </router-link>
        <!-- T-AM7：其余导航按 /menu/my 授权动态渲染；无前端页面的菜单
             （如 /system/* 管理页）经映射表过滤，不出死链 -->
        <template v-for="g in navGroups" :key="g.key">
          <div class="nav-group">{{ g.label }}</div>
          <router-link
            v-for="item in g.items"
            :key="item.to"
            :to="item.to"
            class="nav-item"
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
import { computed } from 'vue'
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
}
.tes-side::after {
  content: "";
  position: absolute;
  top: -40px; right: -40px;
  width: 160px; height: 160px;
  background: radial-gradient(circle, rgba(255, 122, 69, .55), transparent 70%);
  filter: blur(8px);
}
.brand { display: flex; align-items: center; gap: 12px; margin-bottom: 26px; }
.brand-mark {
  width: 40px; height: 40px;
  display: grid; place-items: center;
  border-radius: 12px;
  background: var(--brand-grad);
  font-weight: 900; font-size: 20px; color: #fff;
  box-shadow: 0 6px 16px rgba(255, 61, 129, .45);
}
.brand-text { display: flex; flex-direction: column; line-height: 1.2; }
.brand-text b { font-size: 17px; letter-spacing: .5px; }
.brand-text i { font-style: normal; font-size: 11px; color: rgba(255,255,255,.6); }

.nav { display: flex; flex-direction: column; gap: 4px; position: relative; z-index: 1; }
.nav-group {
  font-size: 11px; font-weight: 700; letter-spacing: 1px;
  color: rgba(255, 255, 255, .45);
  padding: 14px 14px 6px;
}
.nav-item {
  display: flex; align-items: center; gap: 12px;
  padding: 11px 14px;
  border-radius: 12px;
  color: rgba(255, 255, 255, .72);
  font-size: 14px; font-weight: 600;
  text-decoration: none;
  transition: background .2s, color .2s, transform .12s;
}
.nav-item:hover { background: rgba(255, 255, 255, .08); color: #fff; }
.nav-item.router-link-active {
  background: var(--brand-grad);
  color: #fff;
  box-shadow: 0 8px 18px rgba(255, 61, 129, .35);
}
.nav-ico { font-size: 17px; width: 22px; text-align: center; }
.nav-label { white-space: nowrap; }

.side-foot { margin-top: auto; position: relative; z-index: 1; }
.fire {
  font-size: 13px; font-weight: 700; color: #ffd0a3;
  background: rgba(255, 255, 255, .08);
  border: 1px solid rgba(255, 255, 255, .12);
  border-radius: 999px;
  padding: 8px 14px; text-align: center;
}
.ver { font-size: 11px; color: rgba(255, 255, 255, .45); margin-top: 10px; text-align: center; }

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
</style>
