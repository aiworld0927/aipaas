import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 开发期代理：后端双服务分别挂在 /query-api(8001) 与 /import-api(8000)
    proxy: {
      '/query-api': {
        target: 'http://127.0.0.1:8002',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/query-api/, ''),
      },
      '/import-api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/import-api/, ''),
      },
      // P0 契约入口：/api/v1/** 直通 platform-api（保留前缀，不做 rewrite）
      // 联调 mock：PLATFORM_API_TARGET=http://127.0.0.1:4010 npm run dev
      '/api': {
        target: process.env.PLATFORM_API_TARGET || 'http://127.0.0.1:8081',
        changeOrigin: true,
      },
    },
  },
});
