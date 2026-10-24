import client from './client'

// 登录 / 当前用户
export function login(username, password) {
  return client.post('/login', { username, password })
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

// 模块4 自动组卷
export function generateExam(data) {
  return client.post('/exam/generate', data)
}

// 模块5 教师批改
export function submitGrading(data) {
  return client.post('/grading/submit', data)
}

// 模块6 家长周报（period_days 为统计周期；week 参数后端未使用，保留兼容）
export function getWeeklyReport(studentId, week, periodDays = 7) {
  return client.get('/reports/weekly', { params: { student_id: studentId, week, period_days: periodDays } })
}

// 模块9 合规审核
export function listCompliance(status) {
  return client.get('/compliance', { params: status ? { status } : {} })
}
