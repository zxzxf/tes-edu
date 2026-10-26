import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// 学生网页端（T-S0，2026-10-26）：挂在 /student/ 子路径下，
// 生产由 edu-lb 剥前缀反代到本前端容器（与 /admin/ 同款范式）。
// 资源引用与路由链接均带 /student/ 前缀。
// API 经 edu-lb `location /` → 网关；容器内 nginx 亦把 /api /login /learning
// 代理到网关，保证直连容器（8082）也能调试。
export default defineConfig({
  base: '/student/',
  plugins: [vue()],
  server: {
    port: 5174,
    proxy: {
      '/api': { target: 'http://localhost:8080', changeOrigin: true },
      '/login': { target: 'http://localhost:8080', changeOrigin: true },
      '/learning': { target: 'http://localhost:8080', changeOrigin: true },
    }
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.js'],
  },
})
