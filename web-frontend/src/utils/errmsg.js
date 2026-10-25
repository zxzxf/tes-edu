/**
 * axios 错误 → 可展示文案（T-AM3/T-AM4 管理页共用）。
 *
 * 优先保留后端 FastAPI 的 detail（中文，如 "menu has children, delete
 * them first"），保证前端提示与后端日志/网关一致，排障时能对齐；
 * pydantic 422 的 detail 是 [{loc,msg,type}] 数组，取首条压平；
 * 网络错误回退 err.message。
 */
export function errMsg(e, fallback = '操作失败，请稍后重试') {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
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
