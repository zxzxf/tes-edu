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

  it('截断后落单的 $ 定界符被清掉（列表预览 slice 到 $...$ 中间）', () => {
    // 复现标注待校页：shortStem 截到 $AC=BC 处，第二个 $ 被切掉
    const clipped = '（13分）如图，在直三棱柱 ABC-A1B1C1 中，∠ACB=90°，$AC=BC…'
    const out = renderMath(clipped)
    expect(out).not.toContain('$')
    expect(out).toContain('AC=BC')
    expect(renderMathText('$AC=BC')).toBe('AC=BC')
  })

  it('反斜杠空格 \\ 与孤立反斜杠被清掉（抛物线真题），未知命令保留', () => {
    const q = '$C_1:y^2=2p_1x\\ (p_1>0)$'
    const out = renderMath(q)
    expect(out).not.toContain('\\')
    expect(out).toBe('C<sub>1</sub>:y<sup>2</sup>=2p<sub>1</sub>x (p<sub>1</sub>&gt;0)')
    expect(renderMath('$50\\%$')).toBe('50%')
    // 已知命令与未知命令的反斜杠不能被兜底吞掉
    expect(renderMath('\\frac{1}{2}')).toBe('(1)/(2)')
    expect(renderMath('\\unknowncmd x')).toContain('\\unknowncmd')
  })

  it('重音命令 \\bar/\\vec/\\hat 转组合记号（共轭复数解题步骤）', () => {
    expect(renderMath('由共轭复数定义知 \\bar{z}=-1+i')).toBe('由共轭复数定义知 z̄=-1+i')
    expect(renderMath('z+\\bar{z}=-2')).toBe('z+z̄=-2')
    expect(renderMath('\\vec{x}')).toBe('x⃗')
    expect(renderMath('\\hat{x}')).toBe('x̂')
    expect(renderMath('\\bar{z}')).not.toContain('\\')
  })

  it('数学函数名 \\log/\\ln 等去反斜杠保留名字（对数比较真题）', () => {
    const q = '已知 $2+\\log_2 x=3+\\log_3 y=5+\\log_5 z$'
    const out = renderMath(q)
    expect(out).not.toContain('\\')
    expect(out).toBe('已知 2+log<sub>2</sub> x=3+log<sub>3</sub> y=5+log<sub>5</sub> z')
    expect(renderMathText(q)).toBe('已知 2+log2 x=3+log3 y=5+log5 z')
    expect(renderMath('$\\ln x+\\sin x+\\tan x$')).toBe('ln x+sin x+tan x')
    // 未知命令仍原样保留，不被函数名集合误吞
    expect(renderMath('\\unknowncmd x')).toContain('\\unknowncmd')
  })

  it('卡方公式：\\chi、\\dfrac 与实体反转义（tooltip 纯文本）', () => {
    const src = '$\\chi^2=\\dfrac{n(ad-bc)^2}{(a+b)(c+d)(a+c)(b+d)}$，$P(\\chi^2\\ge k)$，$r>0$'
    const out = renderMathText(src)
    expect(out).not.toContain('\\')
    expect(out).toContain('χ')
    expect(out).toContain('(n(ad-bc)2)/((a+b)(c+d)(a+c)(b+d))')
    expect(out).toContain('r>0')
    expect(out).not.toContain('&gt;')
    expect(out).not.toContain('&amp;')
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
