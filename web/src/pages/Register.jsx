import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { register } from '../utils/auth.js';

/** 开放注册：成功后直接登录并进入问答页。 */
export default function Register() {
  const navigate = useNavigate();
  const [form, setForm] = useState({ username: '', displayName: '', email: '', password: '', confirm: '', tenantId: '' });
  const [showPwd, setShowPwd] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const update = (key) => (event) => setForm((prev) => ({ ...prev, [key]: event.target.value }));

  const onSubmit = async (event) => {
    event.preventDefault();
    if (loading) return;
    setError('');
    if (form.password !== form.confirm) {
      setError('两次输入的密码不一致');
      return;
    }
    setLoading(true);
    try {
      await register({
        username: form.username.trim(),
        password: form.password,
        displayName: form.displayName.trim() || form.username.trim(),
        email: form.email.trim() || undefined,
        tenantId: form.tenantId.trim() || undefined,
      });
      navigate('/chat', { replace: true });
    } catch (err) {
      setError(err.message || '注册失败，请重试');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card fade-in-up">
        <div className="login-logo">金</div>
        <h1>创建账号</h1>
        <p className="login-sub">注册后即可使用知识问答与文档上传</p>

        <form onSubmit={onSubmit}>
          <div className="field">
            <label>用户名</label>
            <input className="input" placeholder="3-64 位字母、数字或 _ . -" value={form.username}
              autoComplete="username" onChange={update('username')} />
          </div>
          <div className="field">
            <label>显示名称</label>
            <input className="input" placeholder="选填，默认与用户名相同" value={form.displayName}
              onChange={update('displayName')} />
          </div>
          <div className="field">
            <label>邮箱</label>
            <input className="input" placeholder="选填" value={form.email} autoComplete="email"
              onChange={update('email')} />
          </div>
          <div className="field">
            <label>密码</label>
            <div className="pwd-wrap">
              <input className="input" type={showPwd ? 'text' : 'password'} placeholder="至少 12 位，不含用户名"
                value={form.password} autoComplete="new-password" onChange={update('password')} />
              <button type="button" className="pwd-toggle" onClick={() => setShowPwd((v) => !v)}>
                {showPwd ? '隐藏' : '显示'}
              </button>
            </div>
          </div>
          <div className="field">
            <label>确认密码</label>
            <input className="input" type={showPwd ? 'text' : 'password'} placeholder="再次输入密码"
              value={form.confirm} autoComplete="new-password" onChange={update('confirm')} />
          </div>
          <div className="field">
            <label>租户</label>
            <input className="input" placeholder="留空加入默认租户" value={form.tenantId}
              onChange={update('tenantId')} />
          </div>

          {error && <div className="login-error">{error}</div>}

          <button className="btn btn-primary login-btn" type="submit" disabled={loading}>
            {loading ? '注册中…' : '注 册'}
          </button>
        </form>

        <div className="login-tip">
          已有账号？<Link to="/login">返回登录</Link>
        </div>
      </div>

      <style>{`
        .login-page {
          height: 100%; display: flex; align-items: center; justify-content: center; padding: 20px;
          background:
            radial-gradient(1100px 520px at 12% -10%, rgba(61,123,255,.14), transparent 60%),
            radial-gradient(900px 480px at 92% -6%, rgba(122,92,255,.14), transparent 55%),
            var(--bg);
        }
        .login-card {
          width: 100%; max-width: 430px; background: #fff; border: 1px solid var(--border);
          border-radius: 22px; padding: 34px 36px 28px; box-shadow: var(--shadow); text-align: center;
          max-height: 94vh; overflow-y: auto;
        }
        .login-logo {
          width: 60px; height: 60px; margin: 0 auto 16px; border-radius: 18px;
          background: linear-gradient(135deg, var(--brand), var(--brand2));
          color: #fff; font-size: 26px; font-weight: 700;
          display: flex; align-items: center; justify-content: center;
          box-shadow: 0 14px 30px rgba(61,123,255,.32);
        }
        .login-card h1 { margin: 0 0 8px; font-size: 22px; letter-spacing: .5px; }
        .login-sub { margin: 0 0 22px; color: var(--muted); font-size: 13px; }
        .field { text-align: left; margin-bottom: 14px; }
        .field label { display: block; font-size: 13px; font-weight: 600; color: #4a5570; margin-bottom: 6px; }
        .pwd-wrap { position: relative; }
        .pwd-toggle {
          position: absolute; right: 10px; top: 50%; transform: translateY(-50%);
          border: none; background: transparent; color: var(--muted);
          font-size: 12px; cursor: pointer; padding: 4px 6px; border-radius: 6px;
        }
        .login-error {
          background: #fdecec; color: var(--danger); font-size: 13px;
          border-radius: 10px; padding: 9px 12px; margin-bottom: 14px; text-align: left;
        }
        .login-btn { width: 100%; padding: 12px; font-size: 15px; letter-spacing: 4px; margin-top: 4px; }
        .login-tip { margin-top: 16px; font-size: 12px; color: var(--muted); }
      `}</style>
    </div>
  );
}
