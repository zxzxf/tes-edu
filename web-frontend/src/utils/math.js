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

export function renderMath(text) {
  if (text === null || text === undefined) return ''
  let s = escapeHtml(text)

  // 1) 去掉 LaTeX 数学定界符（$$...$$ 先处理，避免被 $...$ 吃掉一半）
  s = s.replace(/\$\$([\s\S]+?)\$\$/g, '$1').replace(/\$([^$\n]+?)\$/g, '$1')

  // 2) 常见 LaTeX 命令 → Unicode / 线性写法
  s = s.replace(/\\frac\{([^{}]+)\}\{([^{}]+)\}/g, '($1)/($2)')
  s = s.replace(/\\sqrt\{([^{}]+)\}/g, '√($1)').replace(/\\sqrt\s*([A-Za-z0-9])/g, '√$1')
  s = s.replace(/\\times/g, '×').replace(/\\div/g, '÷').replace(/\\cdot/g, '·')
  s = s.replace(/\\leq?/g, '≤').replace(/\\geq?/g, '≥').replace(/\\neq/g, '≠')
  s = s.replace(/\\infty/g, '∞').replace(/\\pm/g, '±')
  s = s.replace(/\\([a-zA-Z]+)/g, (m, name) => GREEK[name] || m)  // 未收录的命令原样保留

  // 3) 上标：x^{n+1} 先处理，再处理单字符 x^2 / e^x
  s = s.replace(/\^\{([^{}]+)\}/g, '<sup>$1</sup>')
  s = s.replace(/\^([A-Za-z0-9+\-−])/g, '<sup>$1</sup>')

  // 4) 下标：a_{n+1} 先处理，再处理单字符 a_1
  s = s.replace(/_\{([^{}]+)\}/g, '<sub>$1</sub>')
  s = s.replace(/_([A-Za-z0-9])/g, '<sub>$1</sub>')

  return s
}

/** 是否含公式写法（用于决定要不要显示预览区，避免空白框噪音）。 */
export function hasMath(text) {
  return /\^|_\{|_[$0-9A-Za-z]|\\[a-zA-Z]+|\$/.test(String(text || ''))
}
