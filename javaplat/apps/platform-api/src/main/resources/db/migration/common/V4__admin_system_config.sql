create table if not exists system_configs (
    tenant_id varchar(64) primary key,
    config_json text not null,
    updated_by varchar(64),
    updated_at timestamp with time zone not null
);
