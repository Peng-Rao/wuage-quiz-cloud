import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  // 解析服务（server/）默认监听 8000，前端开发服务器通过此代理访问；
  // Docker 开发模式（docker-compose.dev.yml）中由 API_PROXY_TARGET 指向 app 容器
  server: {
    proxy: { '/api': { target: process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: true } },
  },
})
