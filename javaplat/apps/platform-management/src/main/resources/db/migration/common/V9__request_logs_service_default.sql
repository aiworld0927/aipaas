-- 网关与管理拆分：请求日志 service 列默认值从旧单体名 platform-api 改为 platform-management。
-- 不修改已应用的 V8，新增迁移以保持 Flyway checksum 兼容已部署库。
alter table request_logs alter column service set default 'platform-management';
