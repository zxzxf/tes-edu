/**
 * axios 错误 → 可展示文案（T-AM3/T-AM4 管理页共用）。
 *
 * 后端 FastAPI 的 detail 多数已是中文（如 "menu has children, delete
 * them first"），但少数鉴权错误仍返回英文状态码（如 insufficient role），
 * 这里统一做汉化收口，与 CourseIntake / useCrawlRequest 既有的中文文案保持一致；
 * pydantic 422 的 detail 是 [{loc,msg,type}] 数组，取首条压平；
 * 网络错误回退 err.message。
 */
// 后端英文状态码 → 中文展示文案（仅兜底汉化，不覆盖后端已返回的中文 detail）
const KNOWN_CODES = {
  'insufficient role': '权限不足：该操作仅管理员 / 学校管理员可执行',
}

export function errMsg(e, fallback = '操作失败，请稍后重试') {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string' && detail) {
    if (KNOWN_CODES[detail]) return KNOWN_CODES[detail]
    return detail
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0]
    const loc = Array.isArray(first?.loc)
      ? first.loc.filter((x) => x !== 'body' && typeof x === 'string').join('.')
      : ''
    const msg = first?.msg || '参数不合法'
    return loc ? `${loc}: ${msg}` : msg
  }
  if (e?.message) return e.message
  return fallback
}
