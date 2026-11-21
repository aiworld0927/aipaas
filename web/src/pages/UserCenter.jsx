import { useEffect, useState } from 'react';
import { changePassword, me, updateProfile } from '../api/p0Client.js';
import { getToken, getUser, saveAuth } from '../utils/auth.js';
import { getStreamPref, setStreamPref } from '../utils/prefs.js';

const ROLE_LABEL = {
  PLATFORM_ADMIN: '平台管理员',
  KB_ADMIN: '知识库管理员',
  KB_UPLOADER: '知识库上传员',
  AUDITOR: '安全审计员',
};

function describe(error) {
  if (!error) return '操作失败';
  return `${error.message || '操作失败'}${error.code ? `（${error.code}）` : ''}`;
}

/** 个人中心：资料维护、租户/角色展示与密码修改，全部对接 /api/v1/auth/*。 */
export default function UserCenter() {
  const cached = getUser() || {};
  const [profile, setProfile] = useState(cached);
  const [form, setForm] = useState({ displayName: cached.displayName || '', email: cached.email || '' });
  const [pwd, setPwd] = useState({ oldPassword: '', newPassword: '', confirm: '' });
  const [streamPref, setStreamPrefState] = useState(getStreamPref);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [changing, setChanging] = useState(false);

  useEffect(() => {
    me()
      .then((data) => {
        setProfile(data);
        setForm({ displayName: data.displayName || '', email: data.email || '' });
        saveAuth(getToken(), data);
      })
      .catch(() => {
        /* 保持本地缓存展示 */
      });
  }, []);

  const onSave = async (event) => {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const data = await updateProfile({ displayName: form.displayName.trim(), email: form.email.trim() });
      setProfile(data);
      saveAuth(getToken(), data);
      setNotice('资料已保存');
    } catch (err) {
      setError(describe(err));
    } finally {
      setSaving(false);
    }
  };

  const onChangePassword = async (event) => {
    event.preventDefault();
    if (changing) return;
    setError('');
    setNotice('');
    if (pwd.newPassword !== pwd.confirm) {
      setError('两次输入的新密码不一致');
      return;
    }
    setChanging(true);
    try {
      await changePassword(pwd.oldPassword, pwd.newPassword);
      setPwd({ oldPassword: '', newPassword: '', confirm: '' });
      setNotice('密码已更新，请妥善保管');
    } catch (err) {
      setError(describe(err));
    } finally {
      setChanging(false);
    }
  };

  const name = profile.displayName || profile.username || '未命名用户';
  const roles = Array.isArray(profile.roles) ? profile.roles : [];

  return (
    <div className="user-page">
      <div className="user-inner">
        <h1>个人中心</h1>

        <div className="user-card fade-in-up">
          <div className="user-head">
            <div className="user-avatar">{name.slice(0, 1).toUpperCase()}</div>
            <div>
              <div className="user-name">{name}</div>
              <div className="user-role">
                租户：{profile.tenantId || 'default'}
                {roles.length > 0 && <span className="user-roles">{roles.map((r) => ROLE_LABEL[r] || r).join(' / ')}</span>}
              </div>
            </div>
          </div>

          <form onSubmit={onSave}>
            <div className="field">
              <label>用户名</label>
              <input className="input" value={profile.username || ''} disabled />
            </div>
            <div className="field">
              <label>显示名称</label>
              <input className="input" value={form.displayName} placeholder="请输入显示名称"
                onChange={(e) => setForm((prev) => ({ ...prev, displayName: e.target.value }))} />
            </div>
            <div className="field">
              <label>邮箱</label>
              <input className="input" value={form.email} placeholder="选填"
                onChange={(e) => setForm((prev) => ({ ...prev, email: e.target.value }))} />
            </div>
            <div className="field">
              <label>注册时间</label>
              <input className="input"
                value={profile.createdAt ? new Date(profile.createdAt).toLocaleString('zh-CN', { hour12: false }) : '-'}
                disabled />
            </div>

            <div className="form-actions">
              <button className="btn btn-primary" type="submit" disabled={saving}>
                {saving ? '保存中…' : '保存资料'}
              </button>
            </div>
          </form>
        </div>

        <div className="user-card fade-in-up">
          <h2 className="user-card-title">对话设置</h2>
          <label className="user-switch">
            <input
              type="checkbox"
              checked={streamPref}
              onChange={(event) => {
                setStreamPrefState(event.target.checked);
                setStreamPref(event.target.checked);
                setNotice('对话偏好已保存');
              }}
            />
            默认使用流式输出（关闭后等待完整回答再一次性展示）
          </label>
        </div>

        <div className="user-card fade-in-up">
          <h2 className="user-card-title">修改密码</h2>
          <form onSubmit={onChangePassword}>
            <div className="field">
              <label>原密码</label>
              <input className="input" type="password" autoComplete="current-password" value={pwd.oldPassword}
                onChange={(e) => setPwd((prev) => ({ ...prev, oldPassword: e.target.value }))} />
            </div>
            <div className="field">
              <label>新密码</label>
              <input className="input" type="password" autoComplete="new-password" value={pwd.newPassword}
                placeholder="至少 12 位，不能包含用户名" onChange={(e) => setPwd((prev) => ({ ...prev, newPassword: e.target.value }))} />
            </div>
            <div className="field">
              <label>确认新密码</label>
              <input className="input" type="password" autoComplete="new-password" value={pwd.confirm}
                onChange={(e) => setPwd((prev) => ({ ...prev, confirm: e.target.value }))} />
            </div>
            <div className="form-actions">
              <button className="btn btn-primary" type="submit" disabled={changing}>
                {changing ? '提交中…' : '更新密码'}
              </button>
            </div>
          </form>
        </div>

        {notice && <div className="user-notice">{notice}</div>}
        {error && <div className="user-error">{error}</div>}
      </div>

      <style>{`
        .user-page {
          height: 100%; overflow-y: auto;
          background:
            radial-gradient(1100px 520px at 10% -10%, rgba(61,123,255,.10), transparent 60%),
            radial-gradient(900px 480px at 95% -6%, rgba(122,92,255,.10), transparent 55%),
            var(--bg);
        }
        .user-inner {
          max-width: 560px; margin: 0 auto;
          padding: clamp(20px,4vh,44px) clamp(14px,4vw,32px) 48px;
        }
        .user-inner h1 { font-size: 22px; margin: 0 0 20px; letter-spacing: .5px; }
        .user-card {
          background: #fff; border: 1px solid var(--border); border-radius: 20px;
          padding: 28px; box-shadow: var(--shadow); margin-bottom: 18px;
        }
        .user-card-title { margin: 0 0 18px; font-size: 16px; }
        .user-head { display: flex; align-items: center; gap: 16px; margin-bottom: 24px; }
        .user-avatar {
          width: 58px; height: 58px; border-radius: 50%;
          background: linear-gradient(135deg, var(--brand), var(--brand2));
          color: #fff; font-size: 24px; font-weight: 700;
          display: flex; align-items: center; justify-content: center;
          box-shadow: 0 10px 24px rgba(61,123,255,.3);
        }
        .user-name { font-size: 17px; font-weight: 700; }
        .user-role { font-size: 12px; color: var(--muted); margin-top: 4px; }
        .user-roles { margin-left: 10px; color: var(--brand); font-weight: 600; }
        .field { margin-bottom: 16px; }
        .field label { display: block; font-size: 13px; font-weight: 600; color: #4a5570; margin-bottom: 6px; }
        .input:disabled { background: #f7f9fd; color: var(--muted); cursor: not-allowed; }
        .form-actions { display: flex; align-items: center; gap: 14px; margin-top: 20px; }
        .user-switch { display: flex; align-items: center; gap: 10px; font-size: 13px; color: #4a5570; cursor: pointer; }
        .user-switch input { width: 16px; height: 16px; accent-color: var(--brand); }
        .form-actions .btn { padding: 10px 22px; font-size: 14px; }
        .user-notice {
          background: rgba(16,185,129,.1); color: #047857; border-radius: 10px;
          padding: 10px 14px; font-size: 13px; margin-bottom: 12px;
        }
        .user-error {
          background: #fdecec; color: var(--danger); border-radius: 10px;
          padding: 10px 14px; font-size: 13px; margin-bottom: 12px;
        }
      `}</style>
    </div>
  );
}
