import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Dev: the web app runs on :5173 and proxies /api to the FastAPI server on :8000.
// Point it at any other API (the live one, say) with
//   VITE_API_PROXY=https://brogrammers.cnh.radarhq.io npm run dev
// Prod: FastAPI serves the built files from web/dist on the same origin.
const apiTarget = process.env.VITE_API_PROXY || 'http://localhost:8000'
const proxy = { target: apiTarget, changeOrigin: true, secure: true }

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': proxy,
      '/healthz': proxy,
    },
  },
  test: {
    environment: 'node',
  },
})
