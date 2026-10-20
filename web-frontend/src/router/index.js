import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

import Login from '../views/Login.vue'
import Layout from '../views/Layout.vue'
import Dashboard from '../views/Dashboard.vue'
import Knowledge from '../views/Knowledge.vue'
import Analytics from '../views/Analytics.vue'
import WrongQuestions from '../views/WrongQuestions.vue'
import Exam from '../views/Exam.vue'
import Grading from '../views/Grading.vue'
import Reports from '../views/Reports.vue'
import Compliance from '../views/Compliance.vue'

const routes = [
  { path: '/login', name: 'login', component: Login, meta: { public: true } },
  {
    path: '/',
    component: Layout,
    redirect: '/dashboard',
    children: [
      { path: 'dashboard', name: 'dashboard', component: Dashboard, meta: { title: '概览' } },
      { path: 'knowledge', name: 'knowledge', component: Knowledge, meta: { title: '教研内容管理', role: ['admin', 'researcher'] } },
      { path: 'analytics', name: 'analytics', component: Analytics, meta: { title: '学情看板', role: ['admin', 'teacher', 'parent'] } },
      { path: 'wrong-questions', name: 'wrong-questions', component: WrongQuestions, meta: { title: '错题本', role: ['admin', 'teacher', 'parent'] } },
      { path: 'exam', name: 'exam', component: Exam, meta: { title: '自动组卷', role: ['admin', 'teacher', 'researcher'] } },
      { path: 'grading', name: 'grading', component: Grading, meta: { title: '教师批改', role: ['admin', 'teacher'] } },
      { path: 'reports', name: 'reports', component: Reports, meta: { title: '家长周报', role: ['admin', 'teacher', 'parent'] } },
      { path: 'compliance', name: 'compliance', component: Compliance, meta: { title: '合规审核', role: ['admin', 'researcher'] } }
    ]
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 登录守卫：未登录跳登录页
router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.token) {
    return { name: 'login' }
  }
})

export default router
