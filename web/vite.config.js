import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 开发期代理：前端统一经 platform-gateway（/api → 127.0.0.1:8081），不再直连 svc-query / svc-ingest
    proxy: {
      // 统一入口：/api/v1/** 直通 platform-gateway（保留前缀，不做 rewrite）
      // 联调 mock：PLATFORM_API_TARGET=http://127.0.0.1:4010 npm run dev
      '/api': {
        target: process.env.PLATFORM_API_TARGET || 'http://127.0.0.1:8081',
        changeOrigin: true,
      },
    },
  },
});
