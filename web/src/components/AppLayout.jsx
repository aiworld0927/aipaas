import { useState, useRef, useEffect } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { getUser, logout } from '../utils/auth.js';
import { listSessions, createSession, deleteSession, touchSession } from '../utils/sessions.js';

/**
 * 统一业务布局：顶部导航栏 + 左侧对话历史侧边栏 + 内容区
 * 侧边栏通过 SessionContext 向子页面（Chat）提供"新建对话/选中会话"能力
 */
export const SessionContext = { current: null };

export default function AppLayout() {
  const navigate = useNavigate();
  const user = getUser() || {};
  const [sessions, setSessions] = useState(() => listSessions());
  const [collapsed, setCollapsed] = useState(false);
  const sessionApiRef = useRef(null);

  // 窗口过窄时自动收起侧边栏
  useEffect(() => {
    const onResize = () => setCollapsed(window.innerWidth < 900);
    onResize();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const refreshSessions = () => setSessions(listSessions());

  const handleNewChat = () => {
    const s = createSession();
    refreshSessions();
    navigate(`/chat/${s.id}`);
  };

  const handleDelete = (e, id) => {
    e.preventDefault();
    e.stopPropagation();
    if (!window.confirm('确定删除该对话吗？将同时清空服务端历史记录。')) return;
    deleteSession(id);
    // 后端接口就绪后此处应调用 clearHistory(id)
    refreshSessions();
    if (location.pathname === `/chat/${id}`) navigate('/chat');
  };

  const sessionApi = {
    sessions,
    refreshSessions,
    touchSession,
    handleNewChat,
    registerChat: (fn) => { sessionApiRef.current = fn; },
  };
  SessionContext.current = sessionApi;

  const handleLogout = async () => {
    if (!window.confirm('确定退出登录吗？')) return;
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="layout">
      <TopNav user={user} onLogout={handleLogout} onToggleSidebar={() => setCollapsed((c) => !c)} />

      <div className="layout-body">
        {/* ===== 左侧侧边栏（DeepSeek 风格） ===== */}
        <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
          <button className="sidebar-new" onClick={handleNewChat}>
            <span className="icon-plus">＋</span> 新建对话
          </button>

          <div className="sidebar-label">对话历史</div>
          <nav className="sidebar-list">
            {sessions.length === 0 && <div className="sidebar-empty">暂无历史对话</div>}
            {sessions.map((s) => (
              <NavLink
                key={s.id}
                to={`/chat/${s.id}`}
                className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}
                title={s.title}
              >
                <span className="sidebar-item-title">{s.title}</span>
                <button className="sidebar-item-del" title="删除对话" onClick={(e) => handleDelete(e, s.id)}>
                  ✕
                </button>
              </NavLink>
            ))}
          </nav>

          {/* ===== 底部：文件导入入口 ===== */}
          <div className="sidebar-footer">
            <NavLink to="/knowledge" className="sidebar-foot-link" title="知识库管理">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16.5A1.5 1.5 0 0 0 18.5 21H6.5A2.5 2.5 0 0 1 4 18.5V5.5Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/>
                <path d="M8 7.5h8M8 11h8M8 14.5h5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
              </svg>
              知识库管理
            </NavLink>
            <NavLink to="/import" className="sidebar-foot-link" title="文件导入">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M6.5 19C4.01472 19 2 16.9853 2 14.5C2 12.1569 3.69897 10.1989 5.94868 9.84519C6.32828 6.70055 9.03032 4.26172 12.2857 4.26172C15.4572 4.26172 18.1041 6.58233 18.5907 9.61412C20.5869 9.99892 22 11.7409 22 13.8462C22 16.2359 20.0966 18.1731 17.75 18.1731H15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M12 20.7383V11.5M12 11.5L8.8 14.7M12 11.5L15.2 14.7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
              文件导入
            </NavLink>
            <NavLink to="/exports" className="sidebar-foot-link" title="简介与导出">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M12 3v11m0 0 4-4m-4 4-4-4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M4 15v3.5A2.5 2.5 0 0 0 6.5 21h11a2.5 2.5 0 0 0 2.5-2.5V15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
              </svg>
              简介与导出
            </NavLink>
          </div>
        </aside>

        {/* ===== 内容区 ===== */}
        <main className="layout-main">
          <Outlet context={sessionApi} />
        </main>
      </div>

      <style>{`
        .layout { height: 100%; display: flex; flex-direction: column; overflow: hidden; }

        /* ---- 顶部导航 ---- */
        .topnav {
          flex: 0 0 auto;
          display: flex; align-items: center; gap: 12px;
          padding: 10px clamp(14px, 3vw, 28px);
          background: rgba(255,255,255,.72);
          backdrop-filter: blur(14px);
          -webkit-backdrop-filter: blur(14px);
          border-bottom: 1px solid var(--border);
          z-index: 20;
        }
        .topnav-logo {
          width: 34px; height: 34px; border-radius: 10px;
          background: linear-gradient(135deg, var(--brand), var(--brand2));
          color: #fff; display: flex; align-items: center; justify-content: center;
          font-weight: 700; font-size: 16px;
          box-shadow: 0 6px 16px rgba(61,123,255,.3);
        }
        .topnav-title { font-size: 15px; font-weight: 700; letter-spacing: .2px; }
        .topnav-sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
        .topnav-right { margin-left: auto; display: flex; align-items: center; gap: 10px; }
        .topnav-user {
          display: flex; align-items: center; gap: 8px;
          padding: 5px 12px 5px 5px; border-radius: 999px;
          border: 1px solid var(--border); background: #fff;
          color: var(--text); font-size: 13px; font-weight: 600;
          cursor: pointer; transition: box-shadow .2s, border-color .2s;
        }
        .topnav-user:hover { border-color: rgba(61,123,255,.5); box-shadow: 0 4px 14px rgba(61,123,255,.12); }
        .topnav-avatar {
          width: 28px; height: 28px; border-radius: 50%;
          background: linear-gradient(135deg, var(--brand), var(--brand2));
          color: #fff; display: flex; align-items: center; justify-content: center;
          font-size: 13px; font-weight: 700;
        }
        .btn-logout {
          padding: 8px 14px; font-size: 13px;
          background: transparent; color: var(--muted);
          border: 1px solid var(--border); border-radius: 10px; cursor: pointer;
          transition: all .2s; font-weight: 600;
        }
        .btn-logout:hover { color: var(--danger); border-color: rgba(220,38,38,.35); background: #fff5f5; }

        /* ---- 布局主体 ---- */
        .layout-body { flex: 1; display: flex; min-height: 0; }

        /* ---- 侧边栏 ---- */
        .sidebar {
          width: 262px; flex: 0 0 auto;
          display: flex; flex-direction: column;
          background: var(--sidebar-bg);
          border-right: 1px solid var(--border);
          transition: width .25s ease, margin-left .25s ease;
          overflow: hidden;
        }
        .sidebar.collapsed { width: 0; border-right: none; }
        .sidebar-new {
          margin: 14px 14px 8px; padding: 11px 14px;
          display: flex; align-items: center; justify-content: center; gap: 8px;
          border: 1px solid rgba(61,123,255,.4);
          border-radius: 12px; background: #fff; color: var(--brand);
          font-size: 14px; font-weight: 600; cursor: pointer;
          transition: all .2s;
        }
        .sidebar-new:hover { background: rgba(61,123,255,.06); box-shadow: 0 6px 16px rgba(61,123,255,.14); }
        .icon-plus { font-size: 16px; line-height: 1; }
        .sidebar-label {
          padding: 10px 20px 6px; font-size: 12px; color: var(--muted); font-weight: 600;
        }
        .sidebar-list { flex: 1; overflow-y: auto; padding: 0 10px 10px; }
        .sidebar-empty { padding: 16px 12px; font-size: 13px; color: var(--muted); text-align: center; }
        .sidebar-item {
          display: flex; align-items: center; gap: 6px;
          padding: 9px 12px; margin: 2px 0;
          border-radius: 10px; color: #4a5570; font-size: 13px;
          cursor: pointer; transition: background .15s, color .15s;
        }
        .sidebar-item:hover { background: rgba(61,123,255,.08); }
        .sidebar-item.active { background: rgba(61,123,255,.12); color: var(--brand); font-weight: 600; }
        .sidebar-item-title {
          flex: 1; min-width: 0;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .sidebar-item-del {
          flex: 0 0 auto; width: 20px; height: 20px;
          border: none; background: transparent; color: var(--muted);
          border-radius: 6px; font-size: 10px; cursor: pointer;
          opacity: 0; transition: all .15s;
        }
        .sidebar-item:hover .sidebar-item-del { opacity: 1; }
        .sidebar-item-del:hover { color: var(--danger); background: rgba(220,38,38,.1); }

        .sidebar-footer {
          border-top: 1px solid var(--border);
          padding: 10px;
        }
        .sidebar-foot-link {
          display: flex; align-items: center; gap: 10px;
          padding: 10px 12px; border-radius: 10px;
          color: #4a5570; font-size: 13px; font-weight: 600;
          cursor: pointer; transition: background .15s, color .15s;
        }
        .sidebar-foot-link:hover { background: rgba(61,123,255,.08); color: var(--brand); }
        .sidebar-foot-link.active { background: rgba(61,123,255,.12); color: var(--brand); }

        .layout-main { flex: 1; min-width: 0; display: flex; flex-direction: column; overflow: hidden; }
      `}</style>
    </div>
  );
}

/** 顶部导航栏：Logo + 个人中心入口 + 退出登录 */
function TopNav({ user, onLogout, onToggleSidebar }) {
  return (
    <header className="topnav">
      <button className="btn-ghost" style={{ padding: '6px 10px', fontSize: 16, lineHeight: 1 }} onClick={onToggleSidebar} title="展开/收起侧边栏">
        ☰
      </button>
      <div className="topnav-logo">金</div>
      <div>
        <div className="topnav-title">金融标准化知识库</div>
        <div className="topnav-sub">智能问答 · 文档导入 · 知识检索</div>
      </div>
      <div className="topnav-right">
        <NavLink to="/user" className="topnav-user" title="个人中心">
          <span className="topnav-avatar">{(user.nickname || user.username || 'U').slice(0, 1).toUpperCase()}</span>
          {user.nickname || user.username || '用户'}
        </NavLink>
        <button className="btn-logout" onClick={onLogout}>退出登录</button>
      </div>
    </header>
  );
}
