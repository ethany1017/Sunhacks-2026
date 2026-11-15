import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const backend = `http://localhost:${process.env.BACKEND_PORT ?? 8100}` // run_demo.sh sets BACKEND_PORT

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, 'src') } },
  server: {
    port: 5180,
    strictPort: true,
    proxy: {
      '/ws': { target: backend, ws: true },
      '/api': backend,
    },
  },
})
