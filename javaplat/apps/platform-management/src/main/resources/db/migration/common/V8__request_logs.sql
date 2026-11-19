-- P0 #173：结构化请求日志；#175 Trace 本身存储在内存，不落库。
-- 日志只保留排障必需字段：时间、用户、接口、状态、耗时、TraceID；
-- 不记录请求体、响应体、查询串、Authorization、Cookie 等敏感内容。

create table if not exists request_logs (
    id varchar(64) primary key,
    tenant_id varchar(64) not null,
    user_id varchar(64),
    method varchar(16) not null,
    path varchar(512) not null,
    status integer not null,
    outcome varchar(16) not null,
    duration_ms integer not null,
    service varchar(64) not null default 'platform-api',
    trace_id varchar(128) not null,
    client_ip_hash varchar(64),
    created_at timestamp with time zone not null
);

create index if not exists idx_request_logs_time on request_logs(tenant_id, created_at desc);
create index if not exists idx_request_logs_trace on request_logs(trace_id);
create index if not exists idx_request_logs_user on request_logs(tenant_id, user_id, created_at desc);
create index if not exists idx_request_logs_status on request_logs(tenant_id, status, created_at desc);
create index if not exists idx_request_logs_retention on request_logs(created_at);
