/**
 * API 地址配置
 * 前端统一经 platform-gateway 调用：开发走 Vite 代理 /api，生产走 nginx /api/ 反代。
 * 不再直连 app-query-worker / app-ingest-worker（旧的 /query-api、/import-api 旁路已下线）。
 */
/**
 * P0 契约网关入口（BasePath /api/v1 已包含在 p0Client 的路径拼装里）
 * - 开发：Vite 代理 /api → 127.0.0.1:8081（platform-gateway 统一网关）
 * - 联调：把 VITE_PLATFORM_API_BASE 指向 mock（http://127.0.0.1:4010/api/v1）
 * - 生产：nginx /api/ 反代到 platform-gateway
 */
export const PLATFORM_API_BASE =
  import.meta.env.VITE_PLATFORM_API_BASE || '/api/v1';
