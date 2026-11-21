import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { login } from '../utils/auth.js';

/** 演示账号（真实后端）：走 /api/v1/auth/login，拥有平台管理员权限 */
const DEMO_ACCOUNT = { username: 'demo', password: 'Fin@Rag2026#Kb' };

/** 登录页：登录成功自动跳转 /chat */
export default function Login() {
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPwd, setShowPwd] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const onSubmit = async (e) => {
    e.preventDefault();
    if (loading) return;
    setError('');
    setLoading(true);
    try {
      await login(username, password);
      navigate('/chat', { replace: true });
    } catch (err) {
      setError(err.message || '登录失败，请重试');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card fade-in-up">
        <div className="login-logo">金</div>
        <h1>金融标准化知识库</h1>
        <p className="login-sub">智能问答 · 文档导入 · 知识检索</p>

        <form onSubmit={onSubmit}>
          <div className="field">
            <label>用户名</label>
            <input
              className="input"
              placeholder="请输入用户名"
              value={username}
              autoComplete="username"
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>
          <div className="field">
            <label>密码</label>
            <div className="pwd-wrap">
              <input
                className="input"
                type={showPwd ? 'text' : 'password'}
                placeholder="请输入密码"
                value={password}
                autoComplete="current-password"
                onChange={(e) => setPassword(e.target.value)}
              />
              <button type="button" className="pwd-toggle" onClick={() => setShowPwd((v) => !v)}>
                {showPwd ? '隐藏' : '显示'}
              </button>
            </div>
          </div>

          {error && <div className="login-error">{error}</div>}

          <button className="btn btn-primary login-btn" type="submit" disabled={loading}>
            {loading ? '登录中…' : '登 录'}
          </button>
        </form>

        <button
          type="button"
          className="login-demo"
          onClick={() => {
            setUsername(DEMO_ACCOUNT.username);
            setPassword(DEMO_ACCOUNT.password);
            setError('');
          }}
        >
          <span className="login-demo-title">演示账号</span>
          <span className="login-demo-cred">
            {DEMO_ACCOUNT.username} / {DEMO_ACCOUNT.password}
          </span>
          <span className="login-demo-fill">一键填入</span>
        </button>

        <div className="login-tip">
          还没有账号？<Link to="/register">立即注册</Link>
          <span style={{ margin: '0 8px', color: 'var(--border)' }}>|</span>
          也可使用平台分配的账号密码登录
        </div>
      </div>

      <style>{`
        .login-page {
          height: 100%;
          display: flex; align-items: center; justify-content: center;
          padding: 20px;
          background:
            radial-gradient(1100px 520px at 12% -10%, rgba(61,123,255,.14), transparent 60%),
            radial-gradient(900px 480px at 92% -6%, rgba(122,92,255,.14), transparent 55%),
            var(--bg);
        }
        .login-card {
          width: 100%; max-width: 400px;
          background: #fff;
          border: 1px solid var(--border);
          border-radius: 22px;
          padding: 40px 36px 32px;
          box-shadow: var(--shadow);
          text-align: center;
        }
        .login-logo {
          width: 60px; height: 60px; margin: 0 auto 18px;
          border-radius: 18px;
          background: linear-gradient(135deg, var(--brand), var(--brand2));
          color: #fff; font-size: 26px; font-weight: 700;
          display: flex; align-items: center; justify-content: center;
          box-shadow: 0 14px 30px rgba(61,123,255,.32);
        }
        .login-card h1 { margin: 0 0 8px; font-size: 22px; letter-spacing: .5px; }
        .login-sub { margin: 0 0 28px; color: var(--muted); font-size: 13px; }
        .field { text-align: left; margin-bottom: 16px; }
        .field label { display: block; font-size: 13px; font-weight: 600; color: #4a5570; margin-bottom: 6px; }
        .pwd-wrap { position: relative; }
        .pwd-toggle {
          position: absolute; right: 10px; top: 50%; transform: translateY(-50%);
          border: none; background: transparent; color: var(--muted);
          font-size: 12px; cursor: pointer; padding: 4px 6px; border-radius: 6px;
        }
        .pwd-toggle:hover { color: var(--brand); }
        .login-error {
          background: #fdecec; color: var(--danger);
          font-size: 13px; border-radius: 10px;
          padding: 9px 12px; margin-bottom: 14px; text-align: left;
        }
        .login-btn { width: 100%; padding: 12px; font-size: 15px; letter-spacing: 4px; margin-top: 4px; }
        .login-demo {
          margin-top: 18px; width: 100%;
          display: flex; align-items: center; gap: 8px;
          padding: 10px 12px; border-radius: 12px;
          border: 1px dashed rgba(61,123,255,.45); background: rgba(61,123,255,.05);
          color: #4a5570; font-size: 12px; cursor: pointer;
          transition: background .2s, border-color .2s;
        }
        .login-demo:hover { background: rgba(61,123,255,.1); border-color: rgba(61,123,255,.7); }
        .login-demo-title { font-weight: 700; color: var(--brand); }
        .login-demo-cred { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
        .login-demo-fill { margin-left: auto; color: var(--brand); font-weight: 600; }
        .login-tip { margin-top: 14px; font-size: 12px; color: var(--muted); }
      `}</style>
    </div>
  );
}
