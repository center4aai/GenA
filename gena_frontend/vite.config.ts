import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api/dataset': {
        target: 'http://localhost:8789',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/dataset/, ''),
      },
      '/api/agent': {
        target: 'http://localhost:8790',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/agent/, ''),
      },
      '/api/chunker': {
        target: 'http://localhost:8517',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/chunker/, ''),
      },
    },
  },
});
