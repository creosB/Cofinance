import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    host: '127.0.0.1',
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8878',
        changeOrigin: true,
        configure(proxy) {
          proxy.on('proxyReq', (request, incoming) => {
            // Only the exact dev origin is rewritten; arbitrary origins stay rejected.
            const origin = incoming.headers.origin
            if (origin === 'http://127.0.0.1:5173') request.setHeader('Origin', 'http://127.0.0.1:8878')
          })
        },
      },
    },
  },
  build: { sourcemap: false, assetsDir: 'assets' },
})
