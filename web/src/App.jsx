import { Routes, Route, Navigate } from 'react-router-dom';
import RequireAuth from './components/RequireAuth.jsx';
import AppLayout from './components/AppLayout.jsx';
import Login from './pages/Login.jsx';
import Register from './pages/Register.jsx';
import Chat from './pages/Chat.jsx';
import Import from './pages/Import.jsx';
import Knowledge from './pages/Knowledge.jsx';
import Exports from './pages/Exports.jsx';
import UserCenter from './pages/UserCenter.jsx';
import Admin from './pages/Admin.jsx';

export default function App() {
  return (
    <Routes>
      {/* 登录页独立布局 */}
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />

      {/* 业务页面统一布局（顶栏 + 侧边栏），需登录 */}
      <Route
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/chat/:sessionId" element={<Chat />} />
        <Route path="/import" element={<Import />} />
        <Route path="/knowledge" element={<Knowledge />} />
        <Route path="/exports" element={<Exports />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="/user" element={<UserCenter />} />
      </Route>

      <Route path="*" element={<Navigate to="/chat" replace />} />
    </Routes>
  );
}
