/**
 * API 地址配置
 * 开发环境走 Vite 代理（见 vite.config.js），生产环境通过 .env 配置
 */
export const QUERY_API_BASE = import.meta.env.VITE_QUERY_API_BASE || '/query-api';
export const IMPORT_API_BASE = import.meta.env.VITE_IMPORT_API_BASE || '/import-api';
