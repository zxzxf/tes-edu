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

// M0-1 题库录题：单题 / 批量 / 最近列表
// 落库恒为 draft，发布走审核接口（POST /exam/questions/:id/review）
export function createQuestion(data) {
  return client.post('/questions', data)
}
export function createQuestionsBatch(list) {
  return client.post('/questions/batch', list)
}
export function listQuestions(limit = 20) {
  return client.get('/questions', { params: { limit } })
}
// M4-1 手动出卷筛选：按 kp/难度/题型/学科/年级挑题，answerable=1 只要可入卷的题
export function searchQuestions(params) {
  return client.get('/questions/search', { params })
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
// 后台没有学生列表接口（学生是 App 端流水账号，无 students 表），演示环境
// 统一用这个真实有错题的账号。线上 student_001 一行数据都没有，写死它会让
// 错题本页和首页「待关注错题」卡片恒为 0（2026-11-14 线上实测）。
export const DEMO_STUDENT_ID = 'student@tes.edu'

export function listWrongQuestions(studentId) {
  return client.get('/wrong-questions', { params: { student_id: studentId } })
}

// 模块4 自动组卷（作业闭环：真实落库，答案随卷快照）
// 注意 paper_type 三值：pure_choice / pure_blank / mixed；题源不足后端返回 409。
export function composePaper(data) {
  return client.post('/exam/papers', data)
}
export function listReviewQuestions(status) {
  return client.get('/exam/questions', { params: { status } })
}
export function reviewQuestion(questionId, status) {
  return client.post(`/exam/questions/${questionId}/review`, { status })
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

// M4-1 手动出卷：从题库选题 + 配分，存草稿（publish=true 仍须过发布闸门）
export function createManualPaper(data) {
  return client.post('/exam/papers/manual', data)
}
// M4-5 复制/另存：新 id、新快照、草稿态（改副本不动原卷已下发判据）
export function copyPaper(paperId, title) {
  return client.post(`/exam/papers/${paperId}/copy`, null, {
    params: title ? { title } : {}
  })
}
// M4-3 LLM 辅助组卷：只出草稿（status=draft，需教师审核后才可发布）
// 503 = 本租户未配置/不可用模型；422 = 输出不合规（都被答案/题型校验拦下）
export function llmGeneratePaper(data) {
  return client.post('/exam/papers/llm-generate', data)
}

// M3-3 题↔知识点批量标注 + 覆盖度
export function bindQuestionKp(data) {
  return client.post('/exam/question-kp/bind', data)
}
export function backfillQuestionKp(questionIds) {
  return client.post('/exam/question-kp/backfill', { question_ids: questionIds || null })
}
export function getQuestionKpCoverage() {
  return client.get('/exam/question-kp/coverage')
}
// M3-4 按卷知识点子图（教师端）
export function getPaperGraph(paperId) {
  return client.get(`/exam/papers/${paperId}/graph`)
}
// M3-1 整卷解析（教师始终可见；generate=true 才现场生成）
export function getPaperSolutions(paperId, generate = false) {
  return client.get(`/exam/papers/${paperId}/solutions`, {
    params: generate ? { generate: true } : {}
  })
}

// ------------------------------------------------------------------ M5 课程采集
// M5-1 课程源
export function listCourseSources() {
  return client.get('/course/sources')
}
export function createCourseSource(data) {
  return client.post('/course/sources', data)
}
export function setCourseSourceStatus(sourceId, status) {
  return client.post(`/course/sources/${sourceId}/status`, null, { params: { status } })
}
export function deleteCourseSource(sourceId) {
  return client.delete(`/course/sources/${sourceId}`)
}
// M5-7 待审源审核：approve=转 active 并启用草稿采集任务；reject=清理待审源与草稿任务
export function reviewCourseSource(sourceId, action) {
  return client.post(`/course/sources/${sourceId}/review`, { action })
}

// M3-8/M4-4 采集任务（path=P1 整卷入库 / P2 拆题入库）
export function listCrawlJobs() {
  return client.get('/course/jobs')
}
export function createCrawlJob(data) {
  return client.post('/course/jobs', data)
}
// M3-8 任务启停：只有 active 会被后台调度循环取到
export function setCrawlJobStatus(jobId, status) {
  return client.post(`/course/jobs/${jobId}/status`, null, { params: { status } })
}
export function runCrawlJob(jobId) {
  return client.post(`/course/jobs/${jobId}/run`)
}
export function retryCrawlJob(jobId) {
  return client.post(`/course/jobs/${jobId}/retry`)
}
// M4-4 一键回退本次采集（只撤 draft/auto_crawl）
export function rollbackCrawlRun(runId) {
  return client.post(`/course/runs/${runId}/rollback`)
}
// M5-6 采集可观测
export function getCrawlMetrics() {
  return client.get('/course/metrics')
}

// M5-2 课程解析 pipeline（不落库，先看结构化大纲）
export function parseCourseSource(data) {
  return client.post('/course/parse', data)
}
// M5-3/4 知识点候选入库 + 审核
export function listKpNodes(status) {
  return client.get('/course/kp-nodes', { params: status ? { status } : {} })
}
export function upsertKpNodes(items) {
  return client.post('/course/kp-nodes', items)
}
export function reviewKpNode(code, status) {
  // code 允许中文（后端按标题生成稳定短码），必须编码后再拼进 REST 路径，
  // 否则中文/特殊字符会被浏览器截断或误解析（B 端联调 2026-11-11）。
  return client.post(`/course/kp-nodes/${encodeURIComponent(code)}/review`, { status })
}

// M2-9 / G1 答案双源交叉校验：答案是判分唯一依据，没验证过的答案不许发布
// 结论分五档：verified（双源一致）/ mechanical_pass（代入检验通过）/
// teacher_confirmed（教师确认）可发布；conflict / unverified 必须教师处理。
export function getAnswerChecks(paperId) {
  return client.get(`/exam/papers/${paperId}/answer-checks`)
}
export function runAnswerChecks(paperId, llm = false) {
  return client.post(`/exam/papers/${paperId}/answer-checks/run`, null, {
    params: llm ? { llm: true } : {}
  })
}
// seqs 为空 = 本卷所有待确认题；answers 可顺手修正答案（已发布卷会 409）
export function confirmAnswerChecks(paperId, data) {
  return client.post(`/exam/papers/${paperId}/answer-checks/confirm`, data)
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

// 模块9 合规审核（0022：待审项由录题/采集入库/网关拦截自动落库）
// status: pending | approved | rejected；不传 = 全部
export function listCompliance(status) {
  return client.get('/compliance', { params: status ? { status } : {} })
}
// 审核结论只接受 approved / rejected；后端按租户校验并留痕
export function reviewCompliance(itemId, status) {
  return client.put(`/compliance/${itemId}/review`, null, { params: { status } })
}
export function createCompliance(data) {
  return client.post('/compliance', data)
}
