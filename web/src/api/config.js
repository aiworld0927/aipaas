/**
 * API 地址配置
 * 开发环境走 Vite 代理（见 vite.config.js），生产环境通过 .env 配置
 */
export const QUERY_API_BASE = import.meta.env.VITE_QUERY_API_BASE || '/query-api';
export const IMPORT_API_BASE = import.meta.env.VITE_IMPORT_API_BASE || '/import-api';

/**
 * P0 契约网关入口（BasePath /api/v1 已包含在 p0Client 的路径拼装里）
 * - 开发：Vite 代理 /api → 127.0.0.1:8081（platform-api）
 * - 联调：把 VITE_PLATFORM_API_BASE 指向 mock（http://127.0.0.1:4010/api/v1）
 * - 生产：nginx /api/ 反代到 platform-api
 */
export const PLATFORM_API_BASE =
  import.meta.env.VITE_PLATFORM_API_BASE || '/api/v1';
