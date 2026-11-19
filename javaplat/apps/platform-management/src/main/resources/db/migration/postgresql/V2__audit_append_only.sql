create or replace function enforce_audit_append_only() returns trigger as $$
declare
    retention_days integer;
begin
    if tg_op = 'UPDATE' then
        raise exception 'audit log is append-only';
    end if;
    retention_days := coalesce(nullif(current_setting('aipaas.audit_retention_days', true), '')::integer, 180);
    if old.created_at >= current_timestamp - make_interval(days => retention_days) then
        raise exception 'audit log is immutable during retention period';
    end if;
    return old;
end;
$$ language plpgsql;

create trigger sec_audit_logs_append_only
before update or delete on sec_audit_logs
for each row execute function enforce_audit_append_only();

revoke update, delete, truncate on sec_audit_logs from public;

create or replace function reject_audit_truncate() returns trigger as $$
begin
    raise exception 'audit log cannot be truncated';
end;
$$ language plpgsql;

create trigger sec_audit_logs_no_truncate
before truncate on sec_audit_logs
for each statement execute function reject_audit_truncate();
