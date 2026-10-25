import { useAuthStore } from '../stores/auth'

/**
 * v-has-permission 指令（T-AM7 按钮级权限）。
 *
 * 用法：<button v-has-permission="'system:menu:add'">新建</button>
 *       <button v-has-permission="['system:menu:edit', 'system:menu:delete']">…</button>
 *
 * 语义（与后端 require_perm 对齐，见 stores/auth.js::hasPerm）：
 * - admin 恒放行（*:*）
 * - 值缺省/空数组不拦截（纯展示元素无需权限码）
 * - 数组为「任一命中」语义
 *
 * 实现说明：用 display 切换而非移除 DOM——mounted/updated 两个钩子都会重评，
 * 授权后到（如角色切换）可恢复显示；路由守卫保证首屏渲染前权限已加载
 * （刷新场景见 router/index.js）。
 */

function allowed(binding) {
  const auth = useAuthStore()
  const codes = Array.isArray(binding.value) ? binding.value : [binding.value]
  if (codes.length === 0) return true
  if (codes.some((c) => !c)) return true
  return codes.some((c) => auth.hasPerm(c))
}

function apply(el, binding) {
  el.style.display = allowed(binding) ? '' : 'none'
}

export const permissionDirective = {
  mounted: apply,
  updated: apply,
}

export default permissionDirective
