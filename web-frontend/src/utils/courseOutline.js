/**
 * M5-2 → M5-3 前后端契约层。
 *
 * `POST /api/admin/course/parse` 的真实响应是**三层**结构：
 *   chapters[].sections[].points[]，节点字段为 `title`（章/节）或 `name`（点），
 *   且每层都带后端签发的稳定短码 `code`（≤32 字符，见
 *   web-backend/app/services/course_pipeline.py::_candidate_code）。
 *
 * 旧版 CourseIntake.vue 按 `n.code` + `n.children` 处理，而旧响应只有
 * `code_hint` / `sections` / `points`：结果 code 全为 undefined（后端 422），
 * 知识点层整层丢失（2026-11-11 B 端联调实测）。这里把拍平逻辑独立成纯函数，
 * 单测直接用**真实后端响应形状**做 fixture，杜绝再次被 mock 掩盖。
 *
 * 兼容策略：后端已升级时直接用 `code`；仍是旧镜像时用 `code_hint`+路径哈希
 * 兜底生成合法短码，保证前端先行发布也能跑通。
 */

/** kp_node.code / parent_code 均为 String(32)，超长会 DB 报错 */
export const MAX_KP_CODE_LEN = 32

/** FNV-1a 32 位：给旧后端响应生成稳定、可重放的短码后缀 */
function fnv1a(value) {
  let h = 0x811c9dc5
  for (let i = 0; i < value.length; i += 1) {
    h ^= value.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h.toString(16).padStart(8, '0')
}

/**
 * 生成单个候选 code。
 * @param {object} node 章节/小节/知识点节点
 * @param {string} path 从根到父节点的名称路径（保证同名节点不撞车）
 * @param {Set<string>} seen 本次拍平已用过的 code
 */
export function makeKpCode(node, path, seen = new Set()) {
  const name = String(node.title || node.name || '').trim()
  let code = String(node.code || '').trim()
  if (!code) {
    const hint = String(node.code_hint || name || '').trim()
    const base =
      hint
        .replace(/[^0-9A-Za-z\u4e00-\u9fa5]+/g, '_')
        .replace(/^_+|_+$/g, '')
        .slice(0, 18) || 'kp'
    code = `${base}_${fnv1a(`${path}/${name}`).slice(0, 6)}`
  }
  code = code.slice(0, MAX_KP_CODE_LEN)
  let final = code
  let n = 2
  while (seen.has(final)) {
    const tail = `_${n}`
    n += 1
    final = code.slice(0, MAX_KP_CODE_LEN - tail.length) + tail
  }
  seen.add(final)
  return final
}

/**
 * 章节树 → POST /course/kp-nodes 的候选数组（章/节/点全部保留，父子成链）。
 * @param {object} parsed /course/parse 的响应
 * @param {string} subject 学科（用户在页面选择，后端解析结果不带学科）
 */
export function flattenOutline(parsed, subject = '数学') {
  const items = []
  const seen = new Set()
  const visit = (node, parentCode, path) => {
    if (!node || typeof node !== 'object') return
    const name = String(node.title || node.name || '').trim()
    if (!name) return
    const code = makeKpCode(node, path, seen)
    items.push({
      code,
      name,
      parent_code: parentCode || null,
      subject: subject || '数学',
      source: 'crawled'
    })
    const nextPath = `${path}/${name}`
    const kids = [].concat(node.sections || [], node.points || [], node.children || [])
    for (const child of kids) visit(child, code, nextPath)
  }
  for (const chapter of (parsed && (parsed.chapters || parsed.outline)) || []) {
    visit(chapter, null, '')
  }
  return items
}
