import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:18200',
      '/ws': {
        target: 'ws://localhost:18200',
        ws: true,
      },
      '/health': 'http://localhost:18200',
      '/metrics': 'http://localhost:18200',
      '/config': 'http://localhost:18200',
    },
  },
})
