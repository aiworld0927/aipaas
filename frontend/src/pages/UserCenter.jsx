import { useState } from 'react';
import { getUser, getToken } from '../utils/auth.js';

/** 个人中心：展示当前用户信息（后端 /api/user/me 就绪后替换数据来源） */
export default function UserCenter() {
  const user = getUser() || {};
  const [nickname, setNickname] = useState(user.nickname || user.username || '');
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  const onSave = async (e) => {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    // TODO: 替换为 PUT /api/user/profile
    await new Promise((r) => setTimeout(r, 500));
    localStorage.setItem(
      'aipaas_user',
      JSON.stringify({ ...user, nickname: nickname.trim() || user.username })
    );
    setSaved(true);
    setSaving(false);
    setTimeout(() => setSaved(false), 2000);
  };

  const avatarLetter = (user.nickname || user.username || 'U').slice(0, 1).toUpperCase();

  return (
    <div className="user-page">
      <div className="user-inner">
        <h1>个人中心</h1>

        <div className="user-card fade-in-up">
          <div className="user-head">
            <div className="user-avatar">{avatarLetter}</div>
            <div>
              <div className="user-name">{user.nickname || user.username || '未命名用户'}</div>
              <div className="user-role">{user.role === 'admin' ? '管理员' : '普通用户'}</div>
            </div>
          </div>

          <form onSubmit={onSave}>
            <div className="field">
              <label>用户名</label>
              <input className="input" value={user.username || ''} disabled />
            </div>
            <div className="field">
              <label>昵称</label>
              <input
                className="input"
                value={nickname}
                placeholder="请输入昵称"
                onChange={(e) => setNickname(e.target.value)}
              />
            </div>
            <div className="field">
              <label>注册时间</label>
              <input
                className="input"
                value={user.createdAt ? new Date(user.createdAt).toLocaleString('zh-CN') : '-'}
                disabled
              />
            </div>
            <div className="field">
              <label>Token（调试）</label>
              <input className="input mono" value={getToken()} disabled title={getToken()} />
            </div>

            <div className="form-actions">
              <button className="btn btn-primary" type="submit" disabled={saving}>
                {saving ? '保存中…' : '保存资料'}
              </button>
              {saved && <span className="save-ok">已保存 ✓</span>}
            </div>
          </form>
        </div>

        <div className="user-tip">
          当前为演示数据：后端补充 /api/user/me 与 /api/user/profile 接口后自动切换为真实数据
          （详见 docs/05_待办事项.md）。
        </div>
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
          padding: 28px; box-shadow: var(--shadow);
        }
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
        .field { margin-bottom: 16px; }
        .field label { display: block; font-size: 13px; font-weight: 600; color: #4a5570; margin-bottom: 6px; }
        .input:disabled { background: #f7f9fd; color: var(--muted); cursor: not-allowed; }
        .input.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; }
        .form-actions { display: flex; align-items: center; gap: 14px; margin-top: 20px; }
        .form-actions .btn { padding: 10px 22px; font-size: 14px; }
        .save-ok { color: #16a34a; font-size: 13px; font-weight: 600; }
        .user-tip {
          margin-top: 18px; font-size: 12px; color: var(--muted);
          line-height: 1.7; text-align: center;
        }
      `}</style>
    </div>
  );
}
