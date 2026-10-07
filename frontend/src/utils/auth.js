/**
 * 登录态管理工具
 * 注意：当前后端缺少 /api/auth/* 接口，登录态暂存 localStorage（Mock）。
 * 后端接口就绪后仅需替换 login/logout/fetchProfile 内部实现。
 */

const TOKEN_KEY = 'aipaas_token';
const USER_KEY = 'aipaas_user';

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || '';
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
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function isLoggedIn() {
  return Boolean(getToken());
}

/** Mock 登录：任意用户名/密码（非空）均可登录 */
export async function login(username, password) {
  if (!username.trim() || !password.trim()) {
    throw new Error('请输入用户名和密码');
  }
  // TODO: 替换为真实接口
  // const res = await fetch(`${QUERY_API_BASE}/api/auth/login`, {...})
  await new Promise((r) => setTimeout(r, 600));
  const user = {
    username: username.trim(),
    nickname: username.trim(),
    role: 'user',
    avatar: '',
    createdAt: Date.now(),
  };
  const token = 'mock-token-' + Math.random().toString(36).slice(2);
  saveAuth(token, user);
  return { token, user };
}

export async function logout() {
  // TODO: 调用后端登出接口使 token 失效
  clearAuth();
}
