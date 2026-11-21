/**
 * 登录态管理：统一走 platform-gateway 的 /api/v1/auth/* 契约。
 * token 存储与 p0Client 共享同一 localStorage 键（aipaas.p0.token），
 * 确保登录后所有 p0Client 请求自动携带 Bearer token；登出时一并清理。
 */
import {
  login as apiLogin,
  register as apiRegister,
  logout as apiLogout,
  me as apiMe,
  getToken as apiGetToken,
  setToken as apiSetToken,
} from '../api/p0Client.js';

const USER_KEY = 'aipaas_user';

export function getToken() {
  return apiGetToken();
}

export function getUser() {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function saveAuth(token, user) {
  apiSetToken(token);
  try {
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  } catch {
    /* ignore quota / private mode */
  }
}

export function clearAuth() {
  apiSetToken('');
  try {
    localStorage.removeItem(USER_KEY);
  } catch {
    /* ignore */
  }
}

export function isLoggedIn() {
  return Boolean(getToken());
}

/** 是否具备平台管理权限：登录响应中的 permissions 含 admin:read 或通配 *，或拥有 PLATFORM_ADMIN 角色 */
export function hasAdminAccess(user = getUser()) {
  if (!user) return false;
  const roles = user.roles || [];
  const permissions = user.permissions || [];
  return permissions.includes('admin:read') || permissions.includes('*') || roles.includes('PLATFORM_ADMIN');
}

/** 真实登录：调用网关 /auth/login，成功后保存 token 与用户资料 */
export async function login(username, password) {
  if (!username.trim() || !password.trim()) {
    throw new Error('请输入用户名和密码');
  }
  const data = await apiLogin(username.trim(), password);
  const token = data.accessToken || data.token || '';
  if (!token) throw new Error('登录响应缺少令牌');
  const user = data.user || { username: username.trim(), displayName: username.trim() };
  saveAuth(token, user);
  return { token, user };
}

/** 开放注册：注册成功即登录，默认加入 default 租户并授予 KB_UPLOADER。 */
export async function register({ username, password, displayName, email, tenantId }) {
  const data = await apiRegister({ username, password, displayName, email, tenantId });
  const token = data.accessToken || '';
  if (!token) throw new Error('注册响应缺少令牌');
  const user = data.user || { username, displayName: displayName || username };
  saveAuth(token, user);
  return { token, user };
}

export async function logout() {
  try {
    await apiLogout();
  } catch {
    /* 后端登出失败不阻塞本地清理 */
  }
  clearAuth();
}

/** 拉取当前用户信息（可选，用于刷新用户资料） */
export async function fetchProfile() {
  const data = await apiMe();
  saveAuth(getToken(), data);
  return data;
}
