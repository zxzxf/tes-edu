import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

import Login from '../views/Login.vue'
import Layout from '../views/Layout.vue'
import Dashboard from '../views/Dashboard.vue'
import Knowledge from '../views/Knowledge.vue'
import Questions from '../views/Questions.vue'
import Analytics from '../views/Analytics.vue'
import WrongQuestions from '../views/WrongQuestions.vue'
import Exam from '../views/Exam.vue'
import PaperDispatch from '../views/PaperDispatch.vue'
import Grading from '../views/Grading.vue'
import Reports from '../views/Reports.vue'
import Compliance from '../views/Compliance.vue'
import Practice from '../views/Practice.vue'
import ErrorDistribution from '../views/ErrorDistribution.vue'
// 系统化任务采集工作台：独立顶层壳子（顶栏一级 + 左侧二级 + 右侧工作区），
// 不经过主站 Layout，避免出现双层侧栏。原 CourseIntake.vue 保留作回滚/单测基线。
import CrawlLayout from '../views/CrawlLayout.vue'
import CrawlWorkbench from '../views/crawl/Workbench.vue'
import CrawlSources from '../views/crawl/Sources.vue'
import CrawlJobs from '../views/crawl/Jobs.vue'
import CrawlParse from '../views/crawl/Parse.vue'
import CrawlReview from '../views/crawl/Review.vue'
import CrawlRuns from '../views/crawl/Runs.vue'
import SystemMenu from '../views/SystemMenu.vue'
import SystemRole from '../views/SystemRole.vue'
import SystemRegion from '../views/SystemRegion.vue'
import SystemVendor from '../views/SystemVendor.vue'
import SystemFeature from '../views/SystemFeature.vue'
import ModelConfig from '../views/ModelConfig.vue'
import SystemUser from '../views/SystemUser.vue'
import Tenant from '../views/Tenant.vue'

// 路由访问控制（T-AM7）：不再用 meta.role 硬编码角色矩阵——授权以后端
// sys_role_menu 聚合的 /menu/my 为唯一权威，守卫经 auth.hasRouteAccess 执行
// （见 stores/auth.js + utils/menu.js 的 MENU_ROUTE_MAP）。
// meta.title 保留给 Layout 面包屑。
const routes = [
  { path: '/login', name: 'login', component: Login, meta: { public: true } },
  {
    path: '/',
    component: Layout,
    redirect: '/dashboard',
    children: [
      { path: 'dashboard', name: 'dashboard', component: Dashboard, meta: { title: '概览' } },
      { path: 'knowledge', name: 'knowledge', component: Knowledge, meta: { title: '教研内容管理' } },
      // M0-1 教师录题页：菜单节点 /ops/questions（rbac_seed m_ops_questions）
      { path: 'questions', name: 'questions', component: Questions, meta: { title: '题库录题' } },
      { path: 'analytics', name: 'analytics', component: Analytics, meta: { title: '学情看板' } },
      { path: 'error-distribution', name: 'error-distribution', component: ErrorDistribution, meta: { title: '班级错因分布' } },
      { path: 'wrong-questions', name: 'wrong-questions', component: WrongQuestions, meta: { title: '错题本' } },
      { path: 'exam', name: 'exam', component: Exam, meta: { title: '自动组卷' } },
      // 试卷列表/发布/下发从自动组卷拆出（2026-11-04），独立菜单 m_ops_exam_dispatch
      { path: 'papers', name: 'papers', component: PaperDispatch, meta: { title: '试卷下发' } },
      { path: 'grading', name: 'grading', component: Grading, meta: { title: '教师批改' } },
      { path: 'reports', name: 'reports', component: Reports, meta: { title: '家长周报' } },
      { path: 'compliance', name: 'compliance', component: Compliance, meta: { title: '合规审核' } },
      { path: 'practice', name: 'practice', component: Practice, meta: { title: '在线练习（过程采集）' } },
      // T-AM3/T-AM4/T-AM5/T-AM6 管理页：授权可达性由 auth.hasRouteAccess 按 /menu/my 判定
      // （见 utils/menu.js 的 MENU_ROUTE_MAP），此处不硬编码角色
      { path: 'system/menu', name: 'system-menu', component: SystemMenu, meta: { title: '菜单管理' } },
      { path: 'system/role', name: 'system-role', component: SystemRole, meta: { title: '角色管理' } },
      { path: 'system/region', name: 'system-region', component: SystemRegion, meta: { title: '区域管理' } },
      { path: 'system/vendor', name: 'system-vendor', component: SystemVendor, meta: { title: '系统服务（MCP厂商）' } },
      { path: 'system/feature', name: 'system-feature', component: SystemFeature, meta: { title: '功能开关' } },
      { path: 'system/model-config', name: 'system-model-config', component: ModelConfig, meta: { title: '模型配置（BYOM）' } },
      { path: 'system/user', name: 'system-user', component: SystemUser, meta: { title: '用户管理' } },
      { path: 'tenant', name: 'tenant', component: Tenant, meta: { title: '学校管理' } }
    ]
  },
  // 系统化任务采集工作台（M5）：顶层独立路由，自带 CrawlLayout 壳子。
  // 主站侧栏菜单 /ops/course-intake 仍映射 /course-intake，点进去重定向到工作台；
  // 工作台提供「返回主站」入口。子路由由 auth.hasRouteAccess 按父路径前缀放行。
  {
    path: '/course-intake',
    component: CrawlLayout,
    redirect: '/course-intake/workbench',
    meta: { title: '课程采集与知识点' },
    children: [
      { path: 'workbench', name: 'crawl-workbench', component: CrawlWorkbench, meta: { title: '采集工作台' } },
      { path: 'sources', name: 'crawl-sources', component: CrawlSources, meta: { title: '课程源管理' } },
      { path: 'jobs', name: 'crawl-jobs', component: CrawlJobs, meta: { title: '采集任务' } },
      { path: 'parse', name: 'crawl-parse', component: CrawlParse, meta: { title: '解析预览' } },
      { path: 'review', name: 'crawl-review', component: CrawlReview, meta: { title: '知识点审核' } },
      { path: 'runs', name: 'crawl-runs', component: CrawlRuns, meta: { title: '运行日志' } },
    ]
  }
]

const router = createRouter({
  // 与 vite base 对齐（T-E1，2026-10-24）：管理后台经 edu-lb 挂在 /admin/
  // 子路径下，浏览器地址栏与路由链接均以 /admin/ 为基准。
  history: createWebHistory('/admin/'),
  routes
})

// 登录守卫（T-AM7 升级为异步）：
// 1) 未登录跳登录页；
// 2) 刷新场景（localStorage 有 token 但 store 未加载授权）先拉 /menu/my
//    再放行——否则动态菜单与 v-has-permission 在首屏无数据；
// 3) 菜单级路由门禁：目标路由若映射了后端菜单节点且不在授权内 → 回首页
//    （UX 层体验优化，真正的安全边界在后端 require_role/require_perm）。
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.public) return
  if (!auth.token) {
    return { name: 'login' }
  }
  if (!auth.menusLoaded) {
    try {
      await auth.loadMenus()
    } catch {
      // 拉取失败（网络/会话过期）：不拦路由，由页面内 API 调用暴露错误，
      // 避免把用户锁死在首页却看不到原因
    }
  }
  if (!auth.hasRouteAccess(to.path)) {
    return { path: '/dashboard' }
  }
})

export default router
