/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development, /api is proxied to the FastAPI service (uvicorn priorauth.api:app --port 8920).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    proxy: { '/api': { target: 'http://127.0.0.1:8920', rewrite: (p) => p.replace(/^\/api/, '') } },
  },
  test: { environment: 'jsdom', setupFiles: ['./src/setupTests.ts'] },
})
