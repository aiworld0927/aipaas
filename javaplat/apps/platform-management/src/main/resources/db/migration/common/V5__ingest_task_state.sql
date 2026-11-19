-- P0 #10：接入任务状态机字段、解析产物版本记录与重试权限。

alter table ingest_tasks add column if not exists started_at timestamp with time zone;
alter table ingest_tasks add column if not exists finished_at timestamp with time zone;
alter table ingest_tasks add column if not exists error_code varchar(64);
alter table ingest_tasks add column if not exists error_message varchar(512);
alter table ingest_tasks add column if not exists retryable boolean;

create index if not exists idx_ingest_tasks_claim on ingest_tasks(status, queued_at);
create index if not exists idx_ingest_tasks_doc on ingest_tasks(tenant_id, document_id, created_at);

-- 解析产物版本：Python 解析服务把统一中间结构写入对象存储，这里记录版本与索引信息。
create table if not exists document_parse_results (
    id varchar(64) primary key,
    tenant_id varchar(64) not null,
    document_id varchar(64) not null,
    task_id varchar(64),
    version integer not null,
    parser varchar(32),
    success boolean not null,
    failure_code varchar(64),
    pages integer not null default 0,
    chunk_count integer not null default 0,
    artifact_key varchar(512),
    result_json text,
    parsed_at timestamp with time zone not null,
    created_at timestamp with time zone not null,
    unique (document_id, version)
);
create index if not exists idx_parse_results_doc on document_parse_results(tenant_id, document_id, version);

insert into sec_permissions(tenant_id, code, name, domain, description)
select 'default', 'ingest:retry', '重试接入任务', '文档', 'P0 任务状态机重试权限点'
where not exists (select 1 from sec_permissions where tenant_id = 'default' and code = 'ingest:retry');

insert into sec_role_permissions(tenant_id, role_id, permission_code)
select 'default', 'rol_kb_admin', 'ingest:retry'
where exists (select 1 from sec_roles where tenant_id = 'default' and id = 'rol_kb_admin')
  and not exists (select 1 from sec_role_permissions
                  where tenant_id = 'default' and role_id = 'rol_kb_admin' and permission_code = 'ingest:retry');
