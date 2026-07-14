import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// FastAPI 在本地 8777 服務 API；dev 時 /api 走 proxy，build 後由 FastAPI mount dist/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    proxy: { '/api': 'http://127.0.0.1:8777' },
  },
})
