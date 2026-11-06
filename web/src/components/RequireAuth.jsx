import { Navigate } from 'react-router-dom';
import { isLoggedIn } from '../utils/auth.js';

/** 路由守卫：未登录一律跳转登录页 */
export default function RequireAuth({ children }) {
  if (!isLoggedIn()) {
    return <Navigate to="/login" replace />;
  }
  return children;
}
