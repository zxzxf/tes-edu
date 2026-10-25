import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// 开发时把 /api 代理到 Go 网关（:8080），由网关分流到 web-backend。
// 生产由 Nginx 反向代理时同理配置。
export default defineConfig({
  // 生产挂在 /admin 子路径（T-E1，2026-10-24）：edu-lb 按此前缀剥离子路径
  // 反代到本前端容器，资源引用与路由链接均带 /admin/ 前缀。
  // dev 模式同样生效（访问 /admin/xxx 由 vite 处理），行为不变。
  base: '/admin/',
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true
      }
    }
  },
  // T-AM7：前端单测（vitest + jsdom + @vue/test-utils）
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.js'],
  },
})
