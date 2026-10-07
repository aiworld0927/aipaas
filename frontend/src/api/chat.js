/**
 * 对话/查询相关 API（对应后端 8001 查询服务）
 * 逻辑与旧版 chat.html 保持一致：POST /query + SSE /stream + GET/DELETE /history
 */
import { QUERY_API_BASE } from './config.js';

/** 提交查询：返回 { session_id }（流式）或完整答案（非流式） */
export async function submitQuery({ query, sessionId, isStream }) {
  const res = await fetch(`${QUERY_API_BASE}/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query,
      session_id: sessionId,
      is_stream: isStream,
    }),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || '请求失败');
  }
  return res.json();
}

/** 建立 SSE 流式连接（与旧版逻辑一致，透传原生 EventSource 供组件监听事件） */
export function openStream(sessionId) {
  return new EventSource(`${QUERY_API_BASE}/stream/${sessionId}`);
}

/** 拉取某会话的历史消息 */
export async function fetchHistory(sessionId, limit = 50) {
  const res = await fetch(`${QUERY_API_BASE}/history/${sessionId}?limit=${limit}`);
  if (!res.ok) return { items: [] };
  const data = await res.json();
  return { items: Array.isArray(data.items) ? data.items : [] };
}

/** 清空某会话的历史消息 */
export async function clearHistory(sessionId) {
  const res = await fetch(`${QUERY_API_BASE}/history/${sessionId}`, { method: 'DELETE' });
  return res.ok;
}

/** 健康检查 */
export async function checkHealth() {
  try {
    const res = await fetch(`${QUERY_API_BASE}/health`);
    return res.ok;
  } catch {
    return false;
  }
}
