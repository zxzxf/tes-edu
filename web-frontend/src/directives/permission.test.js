import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { permissionDirective } from './permission'
import { useAuthStore } from '../stores/auth'

const TestComp = {
  template: `<div>
    <button class="b-exam" v-has-permission="'exam:generate'">生成</button>
    <button class="b-grade" v-has-permission="['grading:submit', 'grading:review']">批改</button>
    <button class="b-none" v-has-permission="''">无码</button>
  </div>`,
}

function mountWith(role, perms, menusLoaded = true) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.$patch({ user: { username: 'u', role }, perms, menusLoaded })
  return mount(TestComp, {
    global: {
      plugins: [pinia],
      directives: { 'has-permission': permissionDirective },
    },
  })
}

const shown = (wrapper, sel) => wrapper.find(sel).element.style.display !== 'none'

describe('v-has-permission 指令', () => {
  it('admin 恒放行（*:* 语义，无需按钮码）', () => {
    const w = mountWith('admin', [])
    expect(shown(w, '.b-exam')).toBe(true)
    expect(shown(w, '.b-grade')).toBe(true)
  })

  it('持码角色可见对应按钮', () => {
    const w = mountWith('teacher', ['exam:generate', 'grading:submit'])
    expect(shown(w, '.b-exam')).toBe(true)
    expect(shown(w, '.b-grade')).toBe(true)
  })

  it('无码角色按钮隐藏', () => {
    const w = mountWith('school', [])
    expect(shown(w, '.b-exam')).toBe(false)
    expect(shown(w, '.b-grade')).toBe(false)
  })

  it('数组为任一命中语义', () => {
    const w = mountWith('teacher', ['grading:review'])
    expect(shown(w, '.b-grade')).toBe(true)
    expect(shown(w, '.b-exam')).toBe(false)
  })

  it('通配前缀放行（exam:* → exam:generate）', () => {
    const w = mountWith('teacher', ['exam:*'])
    expect(shown(w, '.b-exam')).toBe(true)
  })

  it('空权限码不拦截（纯展示元素）', () => {
    const w = mountWith('school', [])
    expect(shown(w, '.b-none')).toBe(true)
  })

  it('授权未就绪（menusLoaded=false）不误伤', () => {
    const w = mountWith('teacher', [], false)
    expect(shown(w, '.b-exam')).toBe(true)
  })
})
