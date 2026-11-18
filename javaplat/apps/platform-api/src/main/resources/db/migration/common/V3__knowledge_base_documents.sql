-- P0 #130 / #134 / #1：知识库分类配置、文档幂等字段、分片上传会话与索引清理任务。

alter table knowledge_bases add column if not exists description varchar(1024);
alter table knowledge_bases add column if not exists domain varchar(64);
alter table knowledge_bases add column if not exists standard_system varchar(128);
alter table knowledge_bases add column if not exists config_json text;
alter table knowledge_bases add column if not exists index_status varchar(16) not null default 'EMPTY';
alter table knowledge_bases add column if not exists created_by varchar(64);
alter table knowledge_bases add column if not exists deleted_at timestamp with time zone;

alter table documents add column if not exists file_hash varchar(64);
alter table documents add column if not exists index_status varchar(16) not null default 'PENDING';
alter table documents add column if not exists chunk_count integer not null default 0;
alter table documents add column if not exists error_code varchar(64);
alter table documents add column if not exists error_message varchar(512);
alter table documents add column if not exists deleted_at timestamp with time zone;
create index if not exists idx_documents_hash on documents(tenant_id, kb_id, file_hash);
create index if not exists idx_documents_kb_status on documents(tenant_id, kb_id, status, deleted_at);

-- 分片上传会话：fileHash/totalParts 支持 P1 #253 断点续传，路径与字段不变。
create table if not exists document_upload_sessions (
    id varchar(64) primary key,
    tenant_id varchar(64) not null,
    kb_id varchar(64) not null,
    file_name_ciphertext text not null,
    file_hash varchar(64) not null,
    size_bytes bigint not null,
    part_size bigint not null,
    total_parts integer not null,
    standard_no_ciphertext text,
    auto_parse boolean not null default true,
    status varchar(16) not null,
    object_key varchar(512) not null,
    created_by varchar(64) not null,
    created_at timestamp with time zone not null,
    updated_at timestamp with time zone not null
);
create index if not exists idx_upload_sessions_hash
    on document_upload_sessions(tenant_id, kb_id, file_hash, status);

create table if not exists document_upload_parts (
    upload_id varchar(64) not null,
    part_no integer not null,
    offset_bytes bigint not null,
    size_bytes bigint not null,
    object_key varchar(512) not null,
    part_hash varchar(64),
    created_at timestamp with time zone not null,
    primary key (upload_id, part_no),
    foreign key (upload_id) references document_upload_sessions(id) on delete cascade
);

-- 软删除后的异步索引/对象清理，失败可重试。
create table if not exists index_cleanup_tasks (
    id varchar(64) primary key,
    tenant_id varchar(64) not null,
    document_id varchar(64) not null,
    kb_id varchar(64) not null,
    object_key varchar(512),
    status varchar(16) not null,
    attempts integer not null default 0,
    max_attempts integer not null default 2,
    last_error varchar(512),
    next_attempt_at timestamp with time zone,
    created_at timestamp with time zone not null,
    updated_at timestamp with time zone not null
);
create index if not exists idx_cleanup_pending on index_cleanup_tasks(status, next_attempt_at);
