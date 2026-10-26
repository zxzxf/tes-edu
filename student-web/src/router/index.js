import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import Login from '../views/Login.vue'
import Home from '../views/Home.vue'
import Projects from '../views/Projects.vue'

const routes = [
  { path: '/login', name: 'login', component: Login, meta: { public: true } },
  { path: '/', name: 'home', component: Home },
  { path: '/projects', name: 'projects', component: Projects },
]

const router = createRouter({
  // 与 vite base 对齐：学生端经 edu-lb 挂在 /student/ 子路径下。
  history: createWebHistory('/student/'),
  routes,
})

// 登录守卫：未登录跳登录页（授权由后端把关，前端只做路由可达性体验优化）。
router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.meta.public) return
  if (!auth.token) {
    return { name: 'login' }
  }
})

export default router
