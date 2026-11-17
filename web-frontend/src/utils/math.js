/**
 * 轻量数学公式渲染（零依赖）。
 *
 * 背景：题目录入框是纯 textarea，无法输入上标，老师/运营只能打出 "e^x"、"x^2" 这类
 * 线性写法，界面上又原样显示，看起来就是一堆符号。这里把常见的线性写法转成
 * <sup>/<sub> 展示态（只用于**预览和结果展示**，提交给后端的仍是原文）。
 *
 * 支持：
 *   e^x / x^2         → 上标
 *   x^{n+1}           → 多位上标
 *   a_1 / a_{n+1}     → 下标
 *   $e^x$ / $$e^x$$   → 去掉 LaTeX 定界符
 *   \frac{a}{b}       → (a)/(b)      \sqrt{x} → √(x)
 *   \pi \alpha \theta \times \div \cdot \le \ge \ne → 对应符号
 *
 * 安全：先做 HTML 转义再替换，返回值可直接 v-html。
 */

const ESCAPE_MAP = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ESCAPE_MAP[c])
}

const GREEK = {
  alpha: 'α', beta: 'β', gamma: 'γ', delta: 'Δ', epsilon: 'ε', theta: 'θ',
  lambda: 'λ', mu: 'μ', pi: 'π', rho: 'ρ', sigma: 'σ', phi: 'φ', omega: 'ω'
}

// 常见 LaTeX 命令 → 可读符号。题库里的真题题干大量带这些源码命令，
// 原样展示会让老师看到 "($\quad$)"、"\left\{a_{n}\right\}" 这种乱码感文本。
// 未收录的命令仍原样保留（不吞内容），但不再把定界符和排版命令抛给读者。
const LATEX_SYMBOLS = {
  ...GREEK,
  in: '∈', notin: '∉', subset: '⊂', supset: '⊃',
  subseteq: '⊆', supseteq: '⊇', cap: '∩', cup: '∪', emptyset: '∅',
  triangle: '△', angle: '∠', perp: '⊥', parallel: '∥',
  approx: '≈', equiv: '≡', propto: '∝', partial: '∂', nabla: '∇',
  quad: '\u2003', qquad: '\u2003\u2003',  // 排版空格：保留间距感，不显示源码
  to: '→', rightarrow: '→', leftrightarrow: '↔',
  therefore: '∴', because: '∵', degree: '°', circ: '°'
}

// 重音命令（共轭复数 \bar{z}、向量 \vec{x}、估计 \hat{x} 等）→ 组合记号拼在主体后。
// 原样保留会让学生看到 "\bar{z}" 这种源码（真实真题解题步骤踩过）。
// 必须在通用命令替换之前处理，否则 \bar 被当成普通命令留下、{z} 花括号也留着。
const ACCENT_MARKS = {
  bar: '\u0304', overline: '\u0304',   // 上横线（共轭复数）
  hat: '\u0302', widehat: '\u0302',
  tilde: '\u0303', widetilde: '\u0303',
  vec: '\u20D7', dot: '\u0307', ddot: '\u0308'
}
const ACCENT_RE = /\\(bar|overline|hat|widehat|tilde|widetilde|vec|dot|ddot)\s*\{([^{}]+)\}/g
const ACCENT_BARE_RE = /\\(bar|overline|hat|widehat|tilde|widetilde|vec|dot|ddot)\s*([A-Za-z0-9])\b/g

export function renderMath(text) {
  if (text === null || text === undefined) return ''
  let s = escapeHtml(text)

  // 1) 去掉 LaTeX 数学定界符（$$...$$ 先处理，避免被 $...$ 吃掉一半）
  s = s.replace(/\$\$([\s\S]+?)\$\$/g, '$1').replace(/\$([^$\n]+?)\$/g, '$1')
  // 列表/预览常先 slice() 截断再渲染，成对 $...$ 可能被切成落单的 $；
  // 落单的 $ 已无定界含义，直接清掉，避免预览里露出 "$AC=BC" 这种源码。
  s = s.replace(/\$/g, '')

  // 1b) 度数：^\circ / ^{\circ} / ^\degree 直接合成单个 ° 字符。
  //     度数符号本身就在右上角，不必再套 <sup>；而且上标规则只认「^ + 单字符」，
  //     ^\circ 会被漏成源码或裸 ^，必须先于命令替换和上标替换处理。
  s = s.replace(/\^\s*\{\s*\\(?:circ|degree)\b\s*\}/g, '°')
  s = s.replace(/\^\s*\\(?:circ|degree)\b/g, '°')

  // 2) 常见 LaTeX 命令 → Unicode / 线性写法
  s = s.replace(/\\frac\{([^{}]+)\}\{([^{}]+)\}/g, '($1)/($2)')
  s = s.replace(/\\sqrt\{([^{}]+)\}/g, '√($1)').replace(/\\sqrt\s*([A-Za-z0-9])/g, '√$1')
  // 命令后必须跟单词边界：否则 \le 会把 \left 吃掉变成 "≤ft"（真实真题踩过）
  s = s.replace(/\\times\b/g, '×').replace(/\\div\b/g, '÷').replace(/\\cdot\b/g, '·')
  s = s.replace(/\\leq?\b/g, '≤').replace(/\\geq?\b/g, '≥').replace(/\\neq\b/g, '≠')
  s = s.replace(/\\infty\b/g, '∞').replace(/\\pm\b/g, '±')

  // 2b) 结构命令：定界符/字体/箭头/数组环境都不是「题干内容」，转成可读形态。
  //     必须先于通用 \(... 命令替换，否则 \left 等会被当成普通命令原样留下。
  s = s.replace(/\\left/g, '').replace(/\\right/g, '')
  // 重音命令：\bar{z}→z̄、\vec{x}→x⃗；先花括号后单字符，须早于通用命令替换
  s = s.replace(ACCENT_RE, (m, name, body) => body + ACCENT_MARKS[name])
  s = s.replace(ACCENT_BARE_RE, (m, name, ch) => ch + ACCENT_MARKS[name])
  s = s.replace(/\\\{/g, '{').replace(/\\\}/g, '}')
  s = s.replace(/\\overrightarrow\{([^{}]+)\}/g,
    (m, body) => body.replace(/\s+/g, '') + '\u20D7')
  s = s.replace(/\\mathrm\{R\}/g, 'ℝ')
  s = s.replace(/\\(?:mathrm|text|mathbf|mathbb)\{([^{}]+)\}/g, '$1')
  s = s.replace(/\\begin\{[^}]+\}/g, '').replace(/\\end\{[^}]+\}/g, '')
  s = s.replace(/\\\\/g, ' ')       // array 内的 \\ 换行
  s = s.replace(/\\[,;!:\s]/g, ' ') // \, \; \! \: 及 \(空格) 排版间距 → 空格
  s = s.replace(/\\([a-zA-Z]+)/g, (m, name) => LATEX_SYMBOLS[name] || m)
  // 兜底：删除「反斜杠后跟非字母/非花括号」的孤立反斜杠（\%、\(空格) 等噪声），
  // 与 student 端 preprocessMath 同口径。必须在通用命令替换之后，避免吞掉已知命令。
  s = s.replace(/\\(?![A-Za-z{}])/g, '')

  // 3) 上标：x^{n+1} 先处理，再处理单字符 x^2 / e^x
  s = s.replace(/\^\{([^{}]+)\}/g, '<sup>$1</sup>')
  s = s.replace(/\^([A-Za-z0-9+\-−])/g, '<sup>$1</sup>')

  // 4) 下标：a_{n+1} 先处理，再处理单字符 a_1
  s = s.replace(/_\{([^{}]+)\}/g, '<sub>$1</sub>')
  s = s.replace(/_([A-Za-z0-9])/g, '<sub>$1</sub>')

  return s
}

/**
 * 纯文本版渲染：转符号但不产生 <sup>/<sub> 标签。
 * 用于 <option>、:title、textarea 等无法渲染 HTML 的位置，避免显示 \frac 等源码。
 */
export function renderMathText(text) {
  return renderMath(text).replace(/<[^>]+>/g, '')
}

/** 是否含公式写法（用于决定要不要显示预览区，避免空白框噪音）。 */
export function hasMath(text) {
  return /\^|_\{|_[$0-9A-Za-z]|\\[a-zA-Z]+|\$/.test(String(text || ''))
}
