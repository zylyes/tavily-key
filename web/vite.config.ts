import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// FastAPI 托管约定：base './' 相对路径（可挂载在任意前缀下）；
// dev server 把 /api、/logo.png、/favicon.ico 代理到本地后端（默认 8000 端口）。
export default defineConfig({
  base: './',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/logo.png': 'http://127.0.0.1:8000',
      '/favicon.ico': 'http://127.0.0.1:8000',
    },
  },
})
