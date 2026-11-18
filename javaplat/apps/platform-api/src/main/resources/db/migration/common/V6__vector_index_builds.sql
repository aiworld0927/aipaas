-- P0 #49：HNSW 向量索引构建记录（Java 控制面）。
-- 集合名/别名编码 kbId + 模型 + 索引类型，为 P1 #45 多模型与 #50/#51 多索引类型预留。

create table if not exists vector_index_builds (
    id varchar(64) primary key,
    tenant_id varchar(64) not null,
    kb_id varchar(64) not null,
    idempotency_key varchar(128),
    status varchar(16) not null,
    progress integer not null default 0,
    index_type varchar(16) not null default 'HNSW',
    metric varchar(16) not null default 'COSINE',
    params_json text,
    index_slot varchar(16) not null default 'auto',
    resolved_slot varchar(16),
    alias varchar(128) not null,
    model varchar(64) not null default 'bge-m3',
    version varchar(64),
    env varchar(32),
    collection varchar(196),
    previous_collection varchar(196),
    version_num integer not null default 1,
    dimension integer not null default 1024,
    total_chunks integer not null default 0,
    indexed_chunks integer not null default 0,
    duration_ms bigint,
    resource_json text,
    document_ids_json text,
    attempt integer not null default 1,
    rolled_back boolean not null default false,
    error_message varchar(512),
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    created_at timestamp with time zone not null,
    updated_at timestamp with time zone not null
);

create index if not exists idx_index_builds_kb on vector_index_builds(tenant_id, kb_id, created_at);
create index if not exists idx_index_builds_alias on vector_index_builds(tenant_id, alias, version_num);
create unique index if not exists uq_index_builds_idem on vector_index_builds(tenant_id, idempotency_key);
