import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    open: true,
    proxy: {
      '/api/planning': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/api/auth': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/api/accounts': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/api/risk': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/api/monitoring': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})

