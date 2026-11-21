import { useCallback, useEffect, useState } from 'react';
import {
  listUsers,
  createUser,
  updateUser,
  getSystemConfig,
  updateSystemConfig,
  getAdminOverview,
  listRoles,
  listPermissions,
  createRole,
  updateRolePermissions,
  listTenants,
  createTenant,
  updateTenant,
} from '../api/p0Client.js';

const PAGE_SIZE = 10;
const STATUS_LABEL = { ACTIVE: '启用', DISABLED: '停用', LOCKED: '锁定' };
const ROLE_LABEL = {
  PLATFORM_ADMIN: '平台管理员',
  KB_ADMIN: '知识库管理员',
  KB_UPLOADER: '知识库上传员',
  AUDITOR: '安全审计员',
};

function describe(error) {
  if (!error) return '操作失败';
  const code = error.code ? `（${error.code}${error.traceId ? ` · ${error.traceId}` : ''}）` : '';
  return `${error.message || '操作失败'}${code}`;
}

function formatDate(value) {
  if (!value) return '-';
  try {
    return new Date(value).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return String(value);
  }
}

/** 系统管理：概览 / 用户与权限 / 系统配置，全部走 gateway /api/v1/admin/* */
export default function Admin() {
  const [tab, setTab] = useState('overview');
  return (
    <div className="admin-page">
      <header className="admin-head">
        <div>
          <h1>系统管理</h1>
          <p>平台用户与权限、系统运行参数与使用概览</p>
        </div>
        <div className="admin-tabs">
          {[
            ['overview', '管理概览'],
            ['users', '用户与权限'],
            ['tenants', '租户管理'],
            ['roles', '角色授权'],
            ['config', '系统配置'],
          ].map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`admin-tab ${tab === key ? 'active' : ''}`}
              onClick={() => setTab(key)}
            >
              {label}
            </button>
          ))}
        </div>
      </header>

      {tab === 'overview' && <OverviewPanel />}
      {tab === 'users' && <UsersPanel />}
      {tab === 'tenants' && <TenantsPanel />}
      {tab === 'roles' && <RolesPanel />}
      {tab === 'config' && <ConfigPanel />}

      <style>{`
        .admin-page { flex: 1; overflow-y: auto; padding: clamp(16px, 2.4vw, 30px); }
        .admin-head { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 14px; margin-bottom: 18px; }
        .admin-head h1 { margin: 0; font-size: 21px; }
        .admin-head p { margin: 5px 0 0; font-size: 13px; color: var(--muted); }
        .admin-tabs { margin-left: auto; display: flex; gap: 6px; background: #fff; border: 1px solid var(--border); border-radius: 12px; padding: 4px; }
        .admin-tab { border: none; background: transparent; padding: 8px 14px; border-radius: 9px; font-size: 13px; font-weight: 600; color: #4a5570; cursor: pointer; }
        .admin-tab.active { background: rgba(61,123,255,.12); color: var(--brand); }
        .admin-card { background: #fff; border: 1px solid var(--border); border-radius: 16px; padding: 18px 20px; margin-bottom: 16px; box-shadow: var(--shadow); }
        .admin-card h2 { margin: 0 0 14px; font-size: 15px; }
        .admin-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 16px; }
        .admin-stat { background: #fff; border: 1px solid var(--border); border-radius: 16px; padding: 16px 18px; box-shadow: var(--shadow); }
        .admin-stat .label { font-size: 12px; color: var(--muted); font-weight: 600; }
        .admin-stat .value { margin-top: 8px; font-size: 26px; font-weight: 700; letter-spacing: .5px; }
        .admin-toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
        .admin-toolbar .input { width: 210px; }
        .admin-toolbar select.input { width: 130px; }
        .admin-toolbar .spacer { margin-left: auto; }
        .admin-table { width: 100%; border-collapse: collapse; font-size: 13px; }
        .admin-table th, .admin-table td { text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--border); vertical-align: middle; }
        .admin-table th { color: var(--muted); font-size: 12px; font-weight: 600; }
        .admin-table tbody tr:hover { background: rgba(61,123,255,.04); }
        .admin-badge { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 12px; font-weight: 600; background: rgba(61,123,255,.1); color: var(--brand); margin: 1px 4px 1px 0; }
        .admin-badge.ok { background: rgba(16,185,129,.12); color: #059669; }
        .admin-badge.bad { background: rgba(220,38,38,.1); color: var(--danger); }
        .admin-badge.warn { background: rgba(245,158,11,.14); color: #b45309; }
        .admin-pager { display: flex; align-items: center; gap: 10px; margin-top: 12px; font-size: 13px; color: var(--muted); }
        .admin-error { background: #fdecec; color: var(--danger); border-radius: 10px; padding: 9px 12px; font-size: 13px; margin-bottom: 12px; }
        .admin-notice { background: rgba(16,185,129,.1); color: #047857; border-radius: 10px; padding: 9px 12px; font-size: 13px; margin-bottom: 12px; }
        .admin-empty { padding: 26px; text-align: center; color: var(--muted); font-size: 13px; }
        .admin-form { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }
        .admin-form .field { margin: 0; }
        .admin-form .field label { display: block; font-size: 13px; font-weight: 600; color: #4a5570; margin-bottom: 6px; }
        .admin-form .full { grid-column: 1 / -1; }
        .admin-check { display: flex; flex-wrap: wrap; gap: 10px; }
        .admin-check label { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: #4a5570; }
        .admin-actions { display: flex; gap: 8px; margin-top: 16px; }
        .admin-modal { position: fixed; inset: 0; background: rgba(15,23,42,.42); display: flex; align-items: center; justify-content: center; padding: 20px; z-index: 60; }
        .admin-modal-card { width: 100%; max-width: 560px; max-height: 88vh; overflow-y: auto; background: #fff; border-radius: 18px; padding: 22px; box-shadow: 0 24px 60px rgba(15,23,42,.28); }
        .admin-modal-card h3 { margin: 0 0 16px; font-size: 16px; }
        .admin-hint { font-size: 12px; color: var(--muted); margin-top: 4px; }
      `}</style>
    </div>
  );
}

/* ------------------------------ 概览 ------------------------------ */

function OverviewPanel() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await getAdminOverview();
      setData(result);
      setError('');
    } catch (err) {
      setError(describe(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      {error && <div className="admin-error">{error}</div>}
      <div className="admin-stats">
        <Stat label="知识库" value={data ? data.kbCount : '-'} />
        <Stat label="文档数" value={data ? data.documentCount : '-'} />
        <Stat label="切片数" value={data ? data.chunkCount : '-'} />
        <Stat label="用户数" value={data ? data.userCount : '-'} />
      </div>
      <div className="admin-card">
        <h2>运行指标</h2>
        <div className="admin-stats" style={{ marginBottom: 0 }}>
          <Stat label="QPS" value={data ? data.qps : '-'} />
          <Stat label="P95 延迟(ms)" value={data ? data.latencyP95Ms : '-'} />
          <Stat label="错误率" value={data ? `${(Number(data.errorRate) * 100).toFixed(2)}%` : '-'} />
        </div>
        <div className="admin-hint" style={{ marginTop: 12 }}>
          更新时间：{data ? formatDate(data.updatedAt) : '-'}
          {loading ? ' · 加载中…' : ''}
        </div>
        <div className="admin-actions">
          <button type="button" className="btn btn-ghost" onClick={load} disabled={loading}>
            刷新
          </button>
        </div>
      </div>
    </>
  );
}

function Stat({ label, value }) {
  return (
    <div className="admin-stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  );
}

/* ---------------------------- 用户与权限 ---------------------------- */

function UsersPanel() {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState('');
  const [status, setStatus] = useState('');
  const [tenantId, setTenantId] = useState('');
  const [tenants, setTenants] = useState([]);
  const [roles, setRoles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [editor, setEditor] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listUsers({ page, pageSize: PAGE_SIZE, keyword, status, tenantId: tenantId || undefined });
      setItems(data.items || []);
      setTotal(data.total || 0);
      setError('');
    } catch (err) {
      setError(describe(err));
    } finally {
      setLoading(false);
    }
  }, [page, keyword, status, tenantId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    listTenants()
      .then((data) => setTenants(data.items || []))
      .catch(() => setTenants([]));
  }, []);

  useEffect(() => {
    listRoles({ page: 1, pageSize: 50, tenantId: tenantId || undefined })
      .then((data) => setRoles(data.items || []))
      .catch(() => setRoles([]));
  }, [tenantId]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const onSaved = (message) => {
    setEditor(null);
    setNotice(message);
    setError('');
    setTimeout(() => setNotice(''), 2600);
    load();
  };

  return (
    <>
      {error && <div className="admin-error">{error}</div>}
      {notice && <div className="admin-notice">{notice}</div>}
      <div className="admin-card">
        <div className="admin-toolbar">
          <input
            className="input"
            placeholder="搜索用户名 / 显示名"
            value={keyword}
            onChange={(e) => {
              setKeyword(e.target.value);
              setPage(1);
            }}
          />
          <select
            className="input"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
          >
            <option value="">全部状态</option>
            <option value="ACTIVE">启用</option>
            <option value="DISABLED">停用</option>
            <option value="LOCKED">锁定</option>
          </select>
          <select
            className="input"
            value={tenantId}
            onChange={(e) => {
              setTenantId(e.target.value);
              setPage(1);
            }}
          >
            <option value="">全部租户</option>
            {tenants.map((tenant) => (
              <option key={tenant.id} value={tenant.id}>
                {tenant.name}（{tenant.id}）
              </option>
            ))}
          </select>
          <button type="button" className="btn btn-ghost" onClick={load} disabled={loading}>
            刷新
          </button>
          <span className="spacer" />
          <button type="button" className="btn btn-primary" onClick={() => setEditor({ mode: 'create' })}>
            新建用户
          </button>
        </div>

        <table className="admin-table">
          <thead>
            <tr>
              <th>用户名</th>
              <th>显示名</th>
              <th>租户</th>
              <th>状态</th>
              <th>角色</th>
              <th>最近登录</th>
              <th style={{ width: 80 }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((user) => (
              <tr key={user.id}>
                <td>{user.username}</td>
                <td>{user.displayName}</td>
                <td><code>{user.tenantId || 'default'}</code></td>
                <td>
                  <span className={`admin-badge ${user.status === 'ACTIVE' ? 'ok' : user.status === 'LOCKED' ? 'warn' : 'bad'}`}>
                    {STATUS_LABEL[user.status] || user.status}
                  </span>
                </td>
                <td>
                  {(user.roles || []).length === 0
                    ? '-'
                    : user.roles.map((code) => (
                        <span key={code} className="admin-badge">
                          {ROLE_LABEL[code] || code}
                        </span>
                      ))}
                </td>
                <td>{formatDate(user.lastLoginAt)}</td>
                <td>
                  <button type="button" className="btn btn-ghost" onClick={() => setEditor({ mode: 'edit', user })}>
                    编辑
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {items.length === 0 && !loading && <div className="admin-empty">暂无用户</div>}

        <div className="admin-pager">
          <span>
            共 {total} 条 · 第 {page}/{totalPages} 页
          </span>
          <span className="spacer" style={{ marginLeft: 'auto' }} />
          <button type="button" className="btn btn-ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            上一页
          </button>
          <button
            type="button"
            className="btn btn-ghost"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </button>
        </div>
      </div>

      {editor && (
        <UserEditor
          mode={editor.mode}
          user={editor.user}
          roles={roles}
          tenants={tenants}
          defaultTenantId={tenantId}
          onClose={() => setEditor(null)}
          onSaved={onSaved}
        />
      )}
    </>
  );
}

function UserEditor({ mode, user, roles, tenants = [], defaultTenantId, onClose, onSaved }) {
  const isCreate = mode === 'create';
  const [form, setForm] = useState(() => ({
    username: user ? user.username : '',
    displayName: user ? user.displayName || '' : '',
    password: '',
    email: user ? user.email || '' : '',
    status: user ? user.status || 'ACTIVE' : 'ACTIVE',
    roleCodes: user ? [...(user.roles || [])] : [],
    tenantId: user ? user.tenantId || 'default' : defaultTenantId || tenants[0]?.id || 'default',
  }));
  const [roleOptions, setRoleOptions] = useState(roles);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // 跨租户建用户时，角色选项跟随目标租户刷新。
  useEffect(() => {
    if (!isCreate) {
      setRoleOptions(roles);
      return;
    }
    listRoles({ page: 1, pageSize: 100, tenantId: form.tenantId || undefined })
      .then((data) => {
        const items = data.items || [];
        setRoleOptions(items);
        setForm((prev) => ({
          ...prev,
          roleCodes: prev.roleCodes.filter((code) => items.some((item) => item.code === code)),
        }));
      })
      .catch(() => setRoleOptions([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isCreate, form.tenantId, roles]);

  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));
  const toggleRole = (code) =>
    setForm((f) => ({
      ...f,
      roleCodes: f.roleCodes.includes(code) ? f.roleCodes.filter((c) => c !== code) : [...f.roleCodes, code],
    }));

  const submit = async (e) => {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    setError('');
    try {
      if (isCreate) {
        await createUser({
          username: form.username.trim(),
          displayName: form.displayName.trim(),
          password: form.password,
          email: form.email.trim() || undefined,
          roleCodes: form.roleCodes,
          tenantId: form.tenantId,
        });
        onSaved('用户已创建');
      } else {
        await updateUser(user.id, {
          displayName: form.displayName.trim(),
          email: form.email.trim(),
          status: form.status,
          roleCodes: form.roleCodes,
        });
        onSaved('用户已更新');
      }
    } catch (err) {
      setError(describe(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="admin-modal" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="admin-modal-card">
        <h3>{isCreate ? '新建用户' : `编辑用户 · ${user.username}`}</h3>
        <form onSubmit={submit}>
          <div className="admin-form">
            {isCreate && (
              <div className="field">
                <label>所属租户</label>
                <select className="input" value={form.tenantId} onChange={(e) => set('tenantId', e.target.value)}>
                  {tenants.map((tenant) => (
                    <option key={tenant.id} value={tenant.id}>
                      {tenant.name}（{tenant.id}）
                    </option>
                  ))}
                </select>
              </div>
            )}
            {!isCreate && (
              <div className="field">
                <label>所属租户</label>
                <input className="input" value={form.tenantId} disabled />
              </div>
            )}
            {isCreate && (
              <div className="field">
                <label>用户名</label>
                <input className="input" value={form.username} onChange={(e) => set('username', e.target.value)} required />
                <div className="admin-hint">3–64 位，创建后不可修改</div>
              </div>
            )}
            <div className="field">
              <label>显示名</label>
              <input className="input" value={form.displayName} onChange={(e) => set('displayName', e.target.value)} required />
            </div>
            {isCreate && (
              <div className="field">
                <label>初始密码</label>
                <input
                  className="input"
                  type="password"
                  value={form.password}
                  onChange={(e) => set('password', e.target.value)}
                  required
                  autoComplete="new-password"
                />
                <div className="admin-hint">12–128 位，且不能包含用户名或常见弱口令</div>
              </div>
            )}
            <div className="field">
              <label>邮箱</label>
              <input className="input" type="email" value={form.email} onChange={(e) => set('email', e.target.value)} />
            </div>
            {!isCreate && (
              <div className="field">
                <label>状态</label>
                <select className="input" value={form.status} onChange={(e) => set('status', e.target.value)}>
                  <option value="ACTIVE">启用</option>
                  <option value="DISABLED">停用</option>
                  <option value="LOCKED">锁定</option>
                </select>
              </div>
            )}
            <div className="field full">
              <label>角色</label>
              <div className="admin-check">
                {(roleOptions.length ? roleOptions : Object.keys(ROLE_LABEL).map((code) => ({ code, name: ROLE_LABEL[code] }))).map(
                  (role) => (
                    <label key={role.code}>
                      <input
                        type="checkbox"
                        checked={form.roleCodes.includes(role.code)}
                        onChange={() => toggleRole(role.code)}
                      />
                      {role.name || ROLE_LABEL[role.code] || role.code}
                    </label>
                  ),
                )}
              </div>
            </div>
          </div>

          {error && <div className="admin-error" style={{ marginTop: 14 }}>{error}</div>}

          <div className="admin-actions">
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? '保存中…' : '保存'}
            </button>
            <button type="button" className="btn btn-ghost" onClick={onClose} disabled={saving}>
              取消
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/* ---------------------------- 租户管理 ---------------------------- */

function TenantsPanel() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ id: '', name: '', description: '' });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listTenants();
      setItems(data.items || []);
      setError('');
    } catch (err) {
      setError(describe(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const submit = async (e) => {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    setError('');
    try {
      await createTenant({ id: form.id.trim(), name: form.name.trim(), description: form.description.trim() });
      setForm({ id: '', name: '', description: '' });
      setNotice('租户已创建，已自动初始化权限与默认知识库');
      setTimeout(() => setNotice(''), 3200);
      load();
    } catch (err) {
      setError(describe(err));
    } finally {
      setSaving(false);
    }
  };

  const toggle = async (tenant) => {
    try {
      await updateTenant(tenant.id, { status: tenant.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE' });
      load();
    } catch (err) {
      setError(describe(err));
    }
  };

  return (
    <>
      {error && <div className="admin-error">{error}</div>}
      {notice && <div className="admin-notice">{notice}</div>}

      <div className="admin-card">
        <h2>新建租户</h2>
        <form onSubmit={submit}>
          <div className="admin-form">
            <div className="field">
              <label>租户标识</label>
              <input className="input" value={form.id} placeholder="如 bank-a（小写字母/数字/-）" required
                onChange={(e) => setForm((f) => ({ ...f, id: e.target.value }))} />
              <div className="admin-hint">创建后不可修改，用户与数据按该标识隔离</div>
            </div>
            <div className="field">
              <label>租户名称</label>
              <input className="input" value={form.name} required
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            </div>
            <div className="field full">
              <label>描述</label>
              <input className="input" value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
            </div>
          </div>
          <div className="admin-actions">
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? '创建中…' : '创建租户'}
            </button>
          </div>
        </form>
      </div>

      <div className="admin-card">
        <h2>租户列表</h2>
        <table className="admin-table">
          <thead>
            <tr>
              <th>标识</th>
              <th>名称</th>
              <th>状态</th>
              <th>用户数</th>
              <th>创建时间</th>
              <th style={{ width: 100 }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((tenant) => (
              <tr key={tenant.id}>
                <td><code>{tenant.id}</code></td>
                <td>{tenant.name}</td>
                <td>
                  <span className={`admin-badge ${tenant.status === 'ACTIVE' ? 'ok' : 'bad'}`}>
                    {tenant.status === 'ACTIVE' ? '启用' : '停用'}
                  </span>
                </td>
                <td>{tenant.userCount}</td>
                <td>{formatDate(tenant.createdAt)}</td>
                <td>
                  <button type="button" className="btn btn-ghost" onClick={() => toggle(tenant)}
                    disabled={tenant.id === 'default'}>
                    {tenant.status === 'ACTIVE' ? '停用' : '启用'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {items.length === 0 && !loading && <div className="admin-empty">暂无租户</div>}
      </div>
    </>
  );
}

/* ---------------------------- 角色授权 ---------------------------- */

function RolesPanel() {
  const [roles, setRoles] = useState([]);
  const [permissions, setPermissions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [editor, setEditor] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [roleData, permissionData] = await Promise.all([
        listRoles({ page: 1, pageSize: 100 }),
        listPermissions(),
      ]);
      setRoles(roleData.items || []);
      setPermissions(permissionData.items || []);
      setError('');
    } catch (err) {
      setError(describe(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      {error && <div className="admin-error">{error}</div>}
      {notice && <div className="admin-notice">{notice}</div>}
      <div className="admin-card">
        <div className="admin-toolbar">
          <h2 style={{ margin: 0 }}>角色与权限矩阵</h2>
          <span className="spacer" />
          <button type="button" className="btn btn-ghost" onClick={load} disabled={loading}>刷新</button>
          <button type="button" className="btn btn-primary" onClick={() => setEditor({ mode: 'create' })}>
            新建角色
          </button>
        </div>
        <table className="admin-table">
          <thead>
            <tr>
              <th>角色</th>
              <th>编码</th>
              <th>类型</th>
              <th>权限数</th>
              <th>用户数</th>
              <th>更新时间</th>
              <th style={{ width: 90 }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {roles.map((role) => (
              <tr key={role.id}>
                <td>{role.name}</td>
                <td><code>{role.code}</code></td>
                <td>{role.builtIn ? <span className="admin-badge">内置</span> : '自定义'}</td>
                <td>{(role.permissions || []).length}</td>
                <td>{role.userCount}</td>
                <td>{formatDate(role.updatedAt)}</td>
                <td>
                  <button type="button" className="btn btn-ghost" onClick={() => setEditor({ mode: 'edit', role })}>
                    授权
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {roles.length === 0 && !loading && <div className="admin-empty">暂无角色</div>}
      </div>

      {editor && (
        <RoleEditor
          mode={editor.mode}
          role={editor.role}
          permissions={permissions}
          onClose={() => setEditor(null)}
          onSaved={(message) => {
            setEditor(null);
            setNotice(message);
            setTimeout(() => setNotice(''), 2600);
            load();
          }}
        />
      )}
    </>
  );
}

function RoleEditor({ mode, role, permissions, onClose, onSaved }) {
  const isCreate = mode === 'create';
  const [form, setForm] = useState(() => ({
    code: role ? role.code : '',
    name: role ? role.name : '',
    permissionCodes: role ? [...(role.permissions || [])] : [],
  }));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const toggle = (code) =>
    setForm((f) => ({
      ...f,
      permissionCodes: f.permissionCodes.includes(code)
        ? f.permissionCodes.filter((c) => c !== code)
        : [...f.permissionCodes, code],
    }));

  const groups = permissions.reduce((acc, item) => {
    const domain = item.domain || '其他';
    acc[domain] = acc[domain] || [];
    acc[domain].push(item);
    return acc;
  }, {});

  const submit = async (e) => {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    setError('');
    try {
      if (isCreate) {
        await createRole({
          code: form.code.trim(),
          name: form.name.trim(),
          permissions: form.permissionCodes,
          grants: [],
        });
        onSaved('角色已创建');
      } else {
        const grants = (role.grants || []).filter((g) => form.permissionCodes.includes(g.permission));
        await updateRolePermissions(role.id, form.permissionCodes, grants);
        onSaved('角色权限已更新');
      }
    } catch (err) {
      setError(describe(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="admin-modal" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="admin-modal-card">
        <h3>{isCreate ? '新建角色' : `角色授权 · ${role.name}`}</h3>
        <form onSubmit={submit}>
          <div className="admin-form">
            <div className="field">
              <label>角色编码</label>
              <input className="input" value={form.code} required disabled={!isCreate}
                placeholder="如 REPORT_VIEWER"
                onChange={(e) => setForm((f) => ({ ...f, code: e.target.value.toUpperCase() }))} />
            </div>
            <div className="field">
              <label>角色名称</label>
              <input className="input" value={form.name} required disabled={!isCreate}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            </div>
            <div className="field full">
              <label>权限点（共 {form.permissionCodes.length} 项）</label>
              {Object.entries(groups).map(([domain, list]) => (
                <div key={domain} style={{ marginBottom: 12 }}>
                  <div className="admin-hint" style={{ marginBottom: 6 }}>{domain}</div>
                  <div className="admin-check">
                    {list.map((item) => (
                      <label key={item.code}>
                        <input type="checkbox" checked={form.permissionCodes.includes(item.code)}
                          onChange={() => toggle(item.code)} />
                        {item.name}（{item.code}）
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {!isCreate && role.code === 'PLATFORM_ADMIN' && (
            <div className="admin-hint">平台管理员为内置超级角色，不可降权。</div>
          )}
          {error && <div className="admin-error" style={{ marginTop: 14 }}>{error}</div>}

          <div className="admin-actions">
            <button type="submit" className="btn btn-primary" disabled={saving || (!isCreate && role.code === 'PLATFORM_ADMIN')}>
              {saving ? '保存中…' : '保存'}
            </button>
            <button type="button" className="btn btn-ghost" onClick={onClose} disabled={saving}>
              取消
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/* ---------------------------- 系统配置 ---------------------------- */

function ConfigPanel() {
  const [form, setForm] = useState(null);
  const [supportedFormats, setSupportedFormats] = useState([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getSystemConfig();
      setForm({
        defaultCapability: data.defaultCapability || 'RAG',
        maxUploadSizeMb: Math.round((data.maxUploadSizeBytes || 0) / (1024 * 1024)) || 100,
        maxQueryLength: data.maxQueryLength || 2000,
        streamingEnabled: Boolean(data.streamingEnabled),
        retrievalTopK: data.retrievalTopK || 5,
      });
      setSupportedFormats(data.supportedFormats || []);
      setError('');
    } catch (err) {
      setError(describe(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  const submit = async (e) => {
    e.preventDefault();
    if (saving || !form) return;
    setSaving(true);
    setError('');
    try {
      const data = await updateSystemConfig({
        defaultCapability: form.defaultCapability,
        maxUploadSizeBytes: Math.round(Number(form.maxUploadSizeMb)) * 1024 * 1024,
        maxQueryLength: Number(form.maxQueryLength),
        streamingEnabled: form.streamingEnabled,
        retrievalTopK: Number(form.retrievalTopK),
      });
      setSupportedFormats(data.supportedFormats || supportedFormats);
      setNotice('系统配置已保存');
      setTimeout(() => setNotice(''), 2600);
    } catch (err) {
      setError(describe(err));
    } finally {
      setSaving(false);
    }
  };

  if (!form) {
    return (
      <>
        {error && <div className="admin-error">{error}</div>}
        <div className="admin-card admin-empty">{loading ? '加载中…' : '暂无配置数据'}</div>
      </>
    );
  }

  return (
    <>
      {error && <div className="admin-error">{error}</div>}
      {notice && <div className="admin-notice">{notice}</div>}
      <div className="admin-card">
        <h2>运行参数</h2>
        <form onSubmit={submit}>
          <div className="admin-form">
            <div className="field">
              <label>默认能力</label>
              <input className="input" value={form.defaultCapability} onChange={(e) => set('defaultCapability', e.target.value)} />
            </div>
            <div className="field">
              <label>单文件上限（MB）</label>
              <input
                className="input"
                type="number"
                min="1"
                max="100"
                value={form.maxUploadSizeMb}
                onChange={(e) => set('maxUploadSizeMb', e.target.value)}
              />
            </div>
            <div className="field">
              <label>问答最大长度</label>
              <input
                className="input"
                type="number"
                min="1"
                max="8000"
                value={form.maxQueryLength}
                onChange={(e) => set('maxQueryLength', e.target.value)}
              />
            </div>
            <div className="field">
              <label>检索 TopK</label>
              <input
                className="input"
                type="number"
                min="1"
                max="50"
                value={form.retrievalTopK}
                onChange={(e) => set('retrievalTopK', e.target.value)}
              />
            </div>
            <div className="field">
              <label>流式输出</label>
              <label className="admin-check" style={{ paddingTop: 8 }}>
                <input
                  type="checkbox"
                  checked={form.streamingEnabled}
                  onChange={(e) => set('streamingEnabled', e.target.checked)}
                />
                启用 SSE 流式问答
              </label>
            </div>
            {supportedFormats.length > 0 && (
              <div className="field full">
                <label>支持的文件格式</label>
                <div>
                  {supportedFormats.map((fmt) => (
                    <span key={fmt} className="admin-badge">
                      {fmt}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="admin-actions">
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? '保存中…' : '保存配置'}
            </button>
            <button type="button" className="btn btn-ghost" onClick={load} disabled={saving || loading}>
              重新加载
            </button>
          </div>
        </form>
      </div>
    </>
  );
}
