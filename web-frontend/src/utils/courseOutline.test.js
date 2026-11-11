import { describe, it, expect } from 'vitest'
import { flattenOutline, makeKpCode, MAX_KP_CODE_LEN } from './courseOutline'

/**
 * 真实 fixture：由 web-backend/app/services/course_pipeline.py::parse_outline
 * 对下面这段大纲实跑得到（2026-11-11 B 端联调）：
 *   # 第一章 数列 / ## 第一节 等差数列 / 等差数列的通项公式 / 等差数列的前n项和
 *   ## 第二节 等比数列 / 等比数列的通项公式
 *   # 第二章 立体几何 / ## 第一节 空间向量 / 空间向量的坐标运算
 * 刻意保留后端原始字段名（title/name、sections/points、code_hint、code），
 * 不再用前端臆想的 children/code 结构，避免 mock 掩盖契约漂移。
 */
export const REAL_PARSE_RESPONSE = {
  parser: 'textbook',
  chapters: [
    {
      title: '第一章 数列',
      code_hint: '第一章_数列',
      code: '第一章_数列_4ef3ec',
      sections: [
        {
          title: '第一节 等差数列',
          code_hint: '第一节_等差数列',
          code: '第一节_等差数列_b699cb',
          points: [
            { name: '等差数列的通项公式', code_hint: '等差数列的通项公式', code: '等差数列的通项公式_65001a' },
            { name: '等差数列的前n项和', code_hint: '等差数列的前n项和', code: '等差数列的前n项和_60b94c' }
          ]
        },
        {
          title: '第二节 等比数列',
          code_hint: '第二节_等比数列',
          code: '第二节_等比数列_76e1b8',
          points: [
            { name: '等比数列的通项公式', code_hint: '等比数列的通项公式', code: '等比数列的通项公式_78def8' }
          ]
        }
      ]
    },
    {
      title: '第二章 立体几何',
      code_hint: '第二章_立体几何',
      code: '第二章_立体几何_d4e6ce',
      sections: [
        {
          title: '第一节 空间向量',
          code_hint: '第一节_空间向量',
          code: '第一节_空间向量_66ef56',
          points: [
            { name: '空间向量的坐标运算', code_hint: '空间向量的坐标运算', code: '空间向量的坐标运算_f60692' }
          ]
        }
      ]
    }
  ],
  chapter_count: 2,
  point_count: 4
}

/** 旧镜像（未升级后端）响应：只有 code_hint / sections / points，没有 code */
const LEGACY_PARSE_RESPONSE = JSON.parse(
  JSON.stringify(REAL_PARSE_RESPONSE, (k, v) => (k === 'code' ? undefined : v))
)

describe('courseOutline：/course/parse → /course/kp-nodes 契约', () => {
  it('把章/节/点三层全部拍平，一个知识点都不丢', () => {
    const items = flattenOutline(REAL_PARSE_RESPONSE)
    expect(items).toHaveLength(9) // 2 章 + 3 节 + 4 点
    const names = items.map((i) => i.name)
    expect(names).toContain('第一章 数列')
    expect(names).toContain('第一节 等差数列')
    expect(names).toContain('等差数列的前n项和')
    expect(names).toContain('空间向量的坐标运算')
  })

  it('每个候选都有非空、≤32、全局唯一的 code（kp_node.code 是 String(32)）', () => {
    const items = flattenOutline(REAL_PARSE_RESPONSE)
    for (const item of items) {
      expect(item.code).toBeTruthy()
      expect(item.code.length).toBeLessThanOrEqual(MAX_KP_CODE_LEN)
    }
    expect(new Set(items.map((i) => i.code)).size).toBe(items.length)
  })

  it('父子成链：知识点的 parent_code 指向所属小节', () => {
    const items = flattenOutline(REAL_PARSE_RESPONSE)
    const byName = Object.fromEntries(items.map((i) => [i.name, i]))
    expect(byName['等差数列的通项公式'].parent_code).toBe(byName['第一节 等差数列'].code)
    expect(byName['第一节 等差数列'].parent_code).toBe(byName['第一章 数列'].code)
    expect(byName['第一章 数列'].parent_code).toBeNull()
  })

  it('学科由页面传入，不硬编码数学（后端解析结果不带 subject）', () => {
    const items = flattenOutline(REAL_PARSE_RESPONSE, '物理')
    expect(items.every((i) => i.subject === '物理')).toBe(true)
    expect(items.every((i) => i.source === 'crawled')).toBe(true)
  })

  it('同一份大纲重复拍平得到同一批 code（重复导入走后端幂等 skip）', () => {
    expect(flattenOutline(REAL_PARSE_RESPONSE)).toEqual(flattenOutline(REAL_PARSE_RESPONSE))
  })

  it('兼容未升级后端：无 code 时用 code_hint + 路径哈希兜底，仍合法且不丢点', () => {
    const items = flattenOutline(LEGACY_PARSE_RESPONSE)
    expect(items).toHaveLength(9)
    expect(items.every((i) => i.code && i.code.length <= MAX_KP_CODE_LEN)).toBe(true)
    expect(new Set(items.map((i) => i.code)).size).toBe(9)
    expect(items.map((i) => i.name)).toContain('空间向量的坐标运算')
  })

  it('同名节点在不同章节下不会撞 code', () => {
    const legacy = {
      chapters: [
        { title: '第一章', code_hint: '第一章', sections: [{ title: '小结', code_hint: '小结', points: [] }] },
        { title: '第二章', code_hint: '第二章', sections: [{ title: '小结', code_hint: '小结', points: [] }] }
      ]
    }
    const items = flattenOutline(legacy)
    expect(new Set(items.map((i) => i.code)).size).toBe(4)
  })

  it('makeKpCode 对超长 code 截断到 32 字符', () => {
    const long = '知'.repeat(60)
    expect(makeKpCode({ code: long }, '/x').length).toBe(MAX_KP_CODE_LEN)
  })

  it('空响应返回空数组，不抛异常', () => {
    expect(flattenOutline(null)).toEqual([])
    expect(flattenOutline({})).toEqual([])
  })
})
