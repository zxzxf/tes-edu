import { describe, it, expect } from 'vitest'
import { renderMath, renderMathText } from './math'

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

  it('度数 ^\\circ 合成 °，不再漏源码（立体几何真题）', () => {
    const q = '$\\angle ACB=90^\\circ$，$CC_1=2$，$45^\\circ$'
    const out = renderMath(q)
    expect(out).toBe('∠ ACB=90°，CC<sub>1</sub>=2，45°')
    expect(out).not.toContain('\\circ')
    expect(out).not.toContain('^')
    expect(renderMathText(q)).toBe('∠ ACB=90°，CC1=2，45°')
  })

  it('^\\circ 允许花括号和空格写法', () => {
    expect(renderMath('$90^{\\circ}$')).toBe('90°')
    expect(renderMath('$90^ \\circ$')).toBe('90°')
  })

  it('未收录命令原样保留，不吞题干内容', () => {
    expect(renderMath('\\unknowncmd x')).toContain('\\unknowncmd')
  })

  it('先做 HTML 转义，v-html 渲染安全', () => {
    expect(renderMath('<script>alert(1)</script>')).not.toContain('<script>')
  })
})

describe('renderMathText：给 <option> / :title 用的纯文本版', () => {
  it('转符号但不产生标签', () => {
    const out = renderMathText('$x^{2}+\\alpha_1$')
    expect(out).toBe('x2+α1')
    expect(out).not.toContain('<sup>')
    expect(out).not.toContain('<sub>')
  })

  it('同样去掉 LaTeX 定界符与源码命令', () => {
    const out = renderMathText('集合 $x \\in \\mathrm{R}$')
    expect(out).not.toContain('$')
    expect(out).not.toContain('\\in')
    expect(out).toContain('∈')
  })
})
