-- P0 #406 条目化导出：异步任务、重试、有效期与文件指纹。
-- 导出文件落对象存储（AES-GCM），库中只保留 object_key / hash / 有效期等元数据。

create table if not exists export_jobs (
    id varchar(64) primary key,
    tenant_id varchar(64) not null,
    kb_id varchar(64) not null,
    document_id varchar(64) not null,
    export_type varchar(32) not null,
    format varchar(16) not null,
    status varchar(16) not null,
    progress integer not null default 0,
    scope varchar(256) not null,
    clause_count integer not null default 0,
    retry_count integer not null default 0,
    max_retries integer not null default 2,
    actor_id varchar(64) not null,
    idempotency_key varchar(128),
    object_key varchar(512),
    file_name varchar(256),
    size_bytes bigint not null default 0,
    file_hash varchar(64),
    error_code varchar(64),
    error_message varchar(512),
    expires_at timestamp with time zone,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    created_at timestamp with time zone not null,
    updated_at timestamp with time zone not null
);

create index if not exists idx_export_jobs_tenant on export_jobs(tenant_id, created_at desc);
create index if not exists idx_export_jobs_doc on export_jobs(tenant_id, document_id, created_at desc);
create index if not exists idx_export_jobs_expiry on export_jobs(status, expires_at);
create unique index if not exists uk_export_jobs_idempotency
    on export_jobs(tenant_id, actor_id, idempotency_key)
    where idempotency_key is not null;

-- 创建导出与下载导出为新增权限点；查询复用 export:read。
insert into sec_permissions(tenant_id, code, name, domain, description)
select 'default', 'export:create', '创建导出任务', '导出', 'P0 #406 条目化导出'
where not exists (select 1 from sec_permissions where tenant_id = 'default' and code = 'export:create');

insert into sec_role_permissions(tenant_id, role_id, permission_code)
select 'default', 'rol_kb_admin', 'export:create'
where exists (select 1 from sec_roles where tenant_id = 'default' and id = 'rol_kb_admin')
  and not exists (select 1 from sec_role_permissions
                  where tenant_id = 'default' and role_id = 'rol_kb_admin' and permission_code = 'export:create');

insert into sec_role_permissions(tenant_id, role_id, permission_code)
select 'default', 'rol_kb_admin', 'export:read'
where exists (select 1 from sec_roles where tenant_id = 'default' and id = 'rol_kb_admin')
  and not exists (select 1 from sec_role_permissions
                  where tenant_id = 'default' and role_id = 'rol_kb_admin' and permission_code = 'export:read');

insert into sec_role_permissions(tenant_id, role_id, permission_code)
select 'default', 'rol_kb_admin', 'export:download'
where exists (select 1 from sec_roles where tenant_id = 'default' and id = 'rol_kb_admin')
  and not exists (select 1 from sec_role_permissions
                  where tenant_id = 'default' and role_id = 'rol_kb_admin' and permission_code = 'export:download');
