/**
 * 对话历史（会话）本地管理
 * 注意：后端缺少 /api/sessions 接口，当前会话列表暂存 localStorage。
 * 后端接口就绪后仅需替换本文件内部实现，组件层无需改动。
 */

const SESSIONS_KEY = 'aipaas_sessions';

export function listSessions() {
  try {
    const raw = localStorage.getItem(SESSIONS_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

function saveSessions(list) {
  localStorage.setItem(SESSIONS_KEY, JSON.stringify(list));
}

export function createSession() {
  const session = {
    id: 'sess-' + Math.random().toString(36).slice(2) + Date.now().toString(36),
    title: '新对话',
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
  const list = [session, ...listSessions()];
  saveSessions(list);
  return session;
}

/** 用首条用户消息更新会话标题（DeepSeek 式自动命名） */
export function touchSession(id, firstUserText) {
  const list = listSessions().map((s) => {
    if (s.id !== id) return s;
    return {
      ...s,
      title: firstUserText && s.title === '新对话' ? firstUserText.slice(0, 20) : s.title,
      updatedAt: Date.now(),
    };
  });
  saveSessions(list);
}

export function deleteSession(id) {
  saveSessions(listSessions().filter((s) => s.id !== id));
  return id;
}
