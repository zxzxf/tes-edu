import axios from 'axios'
import client from './client'

// 登录必须走根路径 /login：网关该路由 mode 空不鉴权，直通 web-backend 签发 RS256 JWT。
// 不能用 client 的 /api/admin 前缀——那会命中网关 rbac + require_tenant 路由，
// 未携带 token 时被 401 挡在网关层，根本到不了 web-backend 的签发逻辑
// （2026-10-24 线上修复：管理后台登录 401）。
// 注意手动解包一层：client 的响应拦截器（res => res.data）对普通 axios 实例不生效，
// 而 auth store 消费的是 res.access_token / res.role。
export function login(username, password) {
  return axios.post('/login', { username, password }).then((res) => res.data)
}
export function getMe() {
  return client.get('/me')
}

// 模块1 教研内容管理
export function listKnowledge() {
  return client.get('/knowledge')
}
export function createKnowledge(data) {
  return client.post('/knowledge', data)
}

// 模块2 学情看板
export function getAnalytics(studentId) {
  return client.get('/analytics/summary', { params: { student_id: studentId } })
}

// S5 老师端班级错因分布（group_by: error_type | question | step）
export function getErrorDistribution(params) {
  return client.get('/analytics/error-distribution', { params })
}

// 导出 CSV：带 Authorization 头走 axios（window.open 带不了头）
export async function downloadErrorDistributionCsv(params, filename) {
  const blob = await client.get('/analytics/error-distribution', {
    params: { ...params, format: 'csv' },
    responseType: 'blob'
  })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename || '错因分布.csv'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// 模块3 错题本
export function listWrongQuestions(studentId) {
  return client.get('/wrong-questions', { params: { student_id: studentId } })
}

// 模块4 自动组卷（作业闭环：真实落库，答案随卷快照）
// 注意 paper_type 三值：pure_choice / pure_blank / mixed；题源不足后端返回 409。
export function composePaper(data) {
  return client.post('/exam/papers', data)
}
export function listPapers() {
  return client.get('/exam/papers')
}
export function getPaper(paperId) {
  return client.get(`/exam/papers/${paperId}`)
}
// 发布前 dry-run：返回 violations / 人工量，前端据此决定是否允许发布
export function validatePaper(paperId) {
  return client.get(`/exam/papers/${paperId}/validation`)
}
export function publishPaper(paperId) {
  return client.post(`/exam/papers/${paperId}/publish`)
}
export function assignPaper(paperId, data) {
  return client.post(`/exam/papers/${paperId}/assign`, data)
}

// 模块5 教师批改 —— 人工评定工单（M2-5/M2-6）
// 学生提交时对「转人工」的题自动开单；判定结果回写学生端成绩并留痕。
export function listGradingTickets(params) {
  return client.get('/grading/tickets', { params })
}
export function getGradingTicket(ticketId) {
  return client.get(`/grading/tickets/${ticketId}`)
}
export function claimGradingTicket(ticketId, data = {}) {
  return client.post(`/grading/tickets/${ticketId}/claim`, data)
}
export function judgeGradingTicket(ticketId, data) {
  return client.post(`/grading/tickets/${ticketId}/judge`, data)
}
export function submitGrading(data) {
  return client.post('/grading/submit', data)
}

// M2-10 判分护栏：同一标答累计 K 次改判 → 冻结该题自动判分并告警
// freezes = 当前状态（默认只看已冻结）；alerts = 冻结/解冻流水（审计用）
export function listGradingFreezes(params) {
  return client.get('/grading/freezes', { params })
}
export function ackGradingFreeze(freezeId) {
  return client.post(`/grading/freezes/${freezeId}/ack`)
}
export function releaseGradingFreeze(freezeId, data) {
  return client.post(`/grading/freezes/${freezeId}/release`, data)
}
export function listGradingAlerts(params) {
  return client.get('/grading/alerts', { params })
}

// M2-7 判分指标看板：判据覆盖率 / 人工复核率 / 判据来源分布 / 时效 / 护栏
// days=0 取全量口径（看长期趋势）；paper_id 用于按卷下钻
export function getGradingMetrics(params) {
  return client.get('/grading/metrics', { params })
}

// 模块6 家长周报（period_days 为统计周期；week 参数后端未使用，保留兼容）
export function getWeeklyReport(studentId, week, periodDays = 7) {
  return client.get('/reports/weekly', { params: { student_id: studentId, week, period_days: periodDays } })
}

// 模块9 合规审核
export function listCompliance(status) {
  return client.get('/compliance', { params: status ? { status } : {} })
}
