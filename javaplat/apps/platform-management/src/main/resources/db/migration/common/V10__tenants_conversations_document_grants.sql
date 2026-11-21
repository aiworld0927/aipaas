-- 多租户台账、对话持久化与文档级 ACL。

create table if not exists tenants (
    id varchar(64) primary key,
    name varchar(128) not null,
    status varchar(16) not null default 'ACTIVE',
    description varchar(512),
    created_at timestamp with time zone not null,
    updated_at timestamp with time zone not null
);

insert into tenants(id, name, status, description, created_at, updated_at)
values ('default', '默认租户', 'ACTIVE', '系统默认租户', now(), now())
on conflict (id) do nothing;

create table if not exists conversations (
    id varchar(64) primary key,
    tenant_id varchar(64) not null,
    user_id varchar(64) not null,
    title varchar(200) not null,
    created_at timestamp with time zone not null,
    updated_at timestamp with time zone not null,
    deleted_at timestamp with time zone
);
create index if not exists idx_conversations_user
    on conversations(tenant_id, user_id, updated_at desc);

create table if not exists conversation_messages (
    id varchar(64) primary key,
    tenant_id varchar(64) not null,
    conversation_id varchar(64) not null,
    user_id varchar(64) not null,
    role varchar(16) not null,
    content text not null,
    citations_json text,
    created_at timestamp with time zone not null,
    foreign key (conversation_id) references conversations(id)
);
create index if not exists idx_conversation_messages
    on conversation_messages(conversation_id, created_at);

create table if not exists document_grants (
    id varchar(64) primary key,
    tenant_id varchar(64) not null,
    document_id varchar(64) not null,
    subject_type varchar(16) not null,
    subject_id varchar(64) not null,
    permission varchar(16) not null,
    granted_by varchar(64) not null,
    created_at timestamp with time zone not null,
    unique (tenant_id, document_id, subject_type, subject_id, permission)
);
create index if not exists idx_document_grants_doc
    on document_grants(tenant_id, document_id);
