import { describe, it, expect } from 'vitest'
import { renderMath } from './math'

describe('renderMath：真题题干里的 LaTeX 源码转成可读文本', () => {
  it('去掉 $ 定界符，\\quad 不把源码抛给老师', () => {
    const out = renderMath('轨迹是 ($\\quad$)')
    expect(out).not.toContain('$')
    expect(out).not.toContain('\\quad')
    expect(out).toContain('轨迹是 (')
  })

  it('关系/字体/定界命令转成符号', () => {
    expect(renderMath('$x \\in \\mathrm{R}$')).toBe('x ∈ ℝ')
    expect(renderMath('$\\left\\{a_{n}\\right\\}$')).toBe('{a<sub>n</sub>}')
    expect(renderMath('$\\triangle A B C$')).toBe('△ A B C')
  })

  it('上下标和希腊字母仍然正常', () => {
    expect(renderMath('$x^{2}+\\alpha_1$')).toBe('x<sup>2</sup>+α<sub>1</sub>')
  })

  it('未收录命令原样保留，不吞题干内容', () => {
    expect(renderMath('\\unknowncmd x')).toContain('\\unknowncmd')
  })

  it('先做 HTML 转义，v-html 渲染安全', () => {
    expect(renderMath('<script>alert(1)</script>')).not.toContain('<script>')
  })
})
