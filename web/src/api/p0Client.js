/**
 * P0 契约客户端（对齐 aipaas-contracts/openapi/aipaas-openapi-v1.yaml）
 *
 * 约定：
 *   - 统一 BasePath：/api/v1（开发走 vite 代理，生产走 nginx /api/ 反代）
 *   - 统一响应体：{ code, message, data, traceId }；成功 code === 'OK'
 *   - 鉴权：Bearer <token>，token 由 /auth/login 换取
 *   - 写接口：POST/PUT/PATCH/DELETE 自动携带 Idempotency-Key（调用方可传入以复用）
 *   - SSE：POST /chat/stream、GET /ingest/tasks/{id}/events，事件类型 meta/delta/citation/done/error/ping
 *
 * 该文件只做「契约搬运」，不含业务逻辑；mock server 与真实后端可无缝切换。
 */
import { PLATFORM_API_BASE } from './config.js';

const TOKEN_KEY = 'aipaas.p0.token';
const TRACE_KEY = 'aipaas.p0.lastTraceId';

/* ------------------------------------------------------------------ */
/* 基础设施                                                            */
/* ------------------------------------------------------------------ */

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || '';
  } catch {
    return '';
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore quota/private mode */
  }
}

export function getLastTraceId() {
  try {
    return localStorage.getItem(TRACE_KEY) || '';
  } catch {
    return '';
  }
}

/** 生成幂等键：浏览器有 randomUUID 优先用它 */
export function newIdempotencyKey() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/** 契约错误：保留分段错误码与 traceId，便于前端提示与排障 */
export class ApiError extends Error {
  constructor(code, message, traceId, details) {
    super(message || code);
    this.name = 'ApiError';
    this.code = code;
    this.traceId = traceId;
    this.details = details;
  }
}

function newTraceId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return `web-${crypto.randomUUID()}`;
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function buildHeaders(extra, body, traceId) {
  const headers = { Accept: 'application/json', 'X-Request-Id': traceId || newTraceId(), ...(extra || {}) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
  return headers;
}

/** 解包 {code,message,data,traceId}；失败抛 ApiError */
function unwrap(payload) {
  if (payload && typeof payload === 'object' && 'code' in payload) {
    if (payload.traceId) {
      try {
        localStorage.setItem(TRACE_KEY, payload.traceId);
      } catch {
        /* ignore */
      }
    }
    if (payload.code === 'OK') return payload.data;
    const info = payload.data || {};
    throw new ApiError(payload.code, payload.message, payload.traceId, info.details);
  }
  return payload;
}

/**
 * 统一请求。
 * @param {string} path   以 / 开头的契约路径（不含 /api/v1 前缀）
 * @param {object} options { method, body, query, headers, idempotencyKey, raw }
 */
export async function request(path, options = {}) {
  const { method = 'GET', body, query, headers, idempotencyKey, raw } = options;
  const url = new URL(`${PLATFORM_API_BASE}${path}`, window.location.origin);
  if (query) {
    Object.entries(query).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
    });
  }

  const upper = method.toUpperCase();
  const isWrite = upper !== 'GET' && upper !== 'HEAD';
  const traceId = newTraceId();
  const finalHeaders = buildHeaders(headers, body, traceId);
  if (isWrite) finalHeaders['Idempotency-Key'] = idempotencyKey || newIdempotencyKey();

  const res = await fetch(url.toString(), {
    method: upper,
    headers: finalHeaders,
    body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
  });

  if (raw) {
    if (!res.ok) throw new ApiError(`HTTP.${res.status}`, res.statusText);
    return res;
  }

  const text = await res.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError(`SYS.BAD_RESPONSE`, `响应不是合法 JSON（HTTP ${res.status}）`);
  }
  if (!res.ok && payload && payload.code === undefined) {
    throw new ApiError(`HTTP.${res.status}`, `HTTP ${res.status}`);
  }
  return unwrap(payload);
}

/* ------------------------------------------------------------------ */
/* SSE（契约事件：meta / delta / citation / done / error / ping）        */
/* ------------------------------------------------------------------ */

/**
 * 解析 SSE 流。返回 abort 函数。
 * @param {string} path
 * @param {object} options { body, idempotencyKey, lastEventId, onEvent({event,data,id}), onError, signal }
 */
export function openSse(path, options = {}) {
  const { body, idempotencyKey, lastEventId, onEvent, onError, onClose, signal } = options;
  const controller = new AbortController();
  if (signal) signal.addEventListener('abort', () => controller.abort());

  const headers = buildHeaders({ Accept: 'text/event-stream' }, body, newTraceId());
  if (body !== undefined) headers['Idempotency-Key'] = idempotencyKey || newIdempotencyKey();
  // 断线续传：把最后收到的事件 id 回传给服务端，避免重复生成已推送内容。
  if (lastEventId) headers['Last-Event-ID'] = lastEventId;

  (async () => {
    const res = await fetch(`${PLATFORM_API_BASE}${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });

    if (!res.ok || !res.body) {
      const text = await res.text().catch(() => '');
      let code = `HTTP.${res.status}`;
      let message = text;
      try {
        const env = JSON.parse(text);
        code = env.code || code;
        message = env.message || message;
      } catch {
        /* plain text */
      }
      const err = new ApiError(code, message);
      if (onError) onError(err);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let event = 'message';
    let id = '';
    let dataLines = [];

    const flush = () => {
      if (!dataLines.length) {
        event = 'message';
        id = '';
        return;
      }
      const raw = dataLines.join('\n');
      let data = raw;
      try {
        data = JSON.parse(raw);
      } catch {
        /* 容忍非 JSON 的 data */
      }
      if (onEvent) onEvent({ event, data, id });
      event = 'message';
      id = '';
      dataLines = [];
    };

    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, idx).replace(/\r$/, '');
          buffer = buffer.slice(idx + 1);
          if (line === '') {
            flush();
            continue;
          }
          if (line.startsWith(':')) continue; // 注释行/心跳
          const colon = line.indexOf(':');
          const field = colon === -1 ? line : line.slice(0, colon);
          const v = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '');
          if (field === 'event') event = v;
          else if (field === 'data') dataLines.push(v);
          else if (field === 'id') id = v;
        }
      }
      flush();
      if (onClose) onClose();
    } catch (e) {
      if (e && e.name !== 'AbortError' && onError) onError(e);
    }
  })().catch((e) => {
    if (onError) onError(e);
  });

  return () => controller.abort();
}

/* ------------------------------------------------------------------ */
/* 系统                                                                */
/* ------------------------------------------------------------------ */

export const healthz = () => request('/healthz', { raw: false });
export const readyz = () => request('/readyz', { raw: false });
export const systemInfo = () => request('/system/info');

/* ------------------------------------------------------------------ */
/* 安全与合规：登录 / 当前用户 / 权限                                    */
/* ------------------------------------------------------------------ */

export const login = (username, password) =>
  request('/auth/login', { method: 'POST', body: { username, password } });

export const logout = () => request('/auth/logout', { method: 'POST', body: {} });
export const me = () => request('/auth/me');
export const listPermissions = () => request('/permissions');
export const listRoles = (query) => request('/roles', { query });
export const createRole = (body) => request('/roles', { method: 'POST', body });
export const updateRolePermissions = (roleId, permissionCodes) =>
  request(`/roles/${encodeURIComponent(roleId)}/permissions`, {
    method: 'PUT',
    body: { permissions: permissionCodes },
  });

/* ------------------------------------------------------------------ */
/* 知识治理：知识库                                                     */
/* ------------------------------------------------------------------ */

export const listKnowledgeBases = (query) => request('/knowledge-bases', { query });
export const createKnowledgeBase = (body) => request('/knowledge-bases', { method: 'POST', body });
export const getKnowledgeBase = (kbId) => request(`/knowledge-bases/${encodeURIComponent(kbId)}`);
export const updateKnowledgeBase = (kbId, body) =>
  request(`/knowledge-bases/${encodeURIComponent(kbId)}`, { method: 'PATCH', body });
export const deleteKnowledgeBase = (kbId) =>
  request(`/knowledge-bases/${encodeURIComponent(kbId)}`, { method: 'DELETE' });

/* ------------------------------------------------------------------ */
/* 数据接入与解析：文档 / 任务                                           */
/* ------------------------------------------------------------------ */

/** 上传文档（multipart）：字段名 file，可选 standardNo */
export function uploadDocument(kbId, file, extra = {}) {
  const form = new FormData();
  form.append('file', file, file.name);
  Object.entries(extra).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') form.append(k, v);
  });
  return request(`/knowledge-bases/${encodeURIComponent(kbId)}/documents`, {
    method: 'POST',
    body: form,
    idempotencyKey: extra.idempotencyKey,
  });
}

export const listDocuments = (query) => request('/documents', { query });
export const getDocument = (documentId) => request(`/documents/${encodeURIComponent(documentId)}`);
export const deleteDocument = (documentId) =>
  request(`/documents/${encodeURIComponent(documentId)}`, { method: 'DELETE' });

/* ---------- 分片上传（P0 100MB，P1 #253 断点续传复用同一路径） ---------- */

export const initDocumentUpload = (kbId, body) =>
  request(`/knowledge-bases/${encodeURIComponent(kbId)}/documents/uploads:init`, { method: 'POST', body });

export const getDocumentUpload = (kbId, uploadId) =>
  request(`/knowledge-bases/${encodeURIComponent(kbId)}/documents/uploads/${encodeURIComponent(uploadId)}`);

export const completeDocumentUpload = (kbId, uploadId) =>
  request(`/knowledge-bases/${encodeURIComponent(kbId)}/documents/uploads/${encodeURIComponent(uploadId)}:complete`, {
    method: 'POST',
    body: {},
  });

/** 单个分片 PUT，XHR 提供真实进度；offset 与 missingParts 对齐后端契约。 */
export function uploadDocumentPart(kbId, uploadId, partNo, blob, options = {}) {
  const { offset, onProgress, signal } = options;
  return new Promise((resolve, reject) => {
    const url = new URL(
      `${PLATFORM_API_BASE}/knowledge-bases/${encodeURIComponent(kbId)}/documents/uploads/${encodeURIComponent(uploadId)}/parts/${partNo}`,
      window.location.origin,
    );
    if (offset !== undefined) url.searchParams.set('offset', String(offset));
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url.toString());
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.setRequestHeader('X-Request-Id', newTraceId());
    const token = getToken();
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    if (xhr.upload && onProgress) {
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
      };
    }
    xhr.onload = () => {
      let payload = null;
      try {
        payload = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        reject(new ApiError('SYS.BAD_RESPONSE', `分片响应不是合法 JSON（HTTP ${xhr.status}）`));
        return;
      }
      try {
        resolve(unwrap(payload));
      } catch (error) {
        reject(error);
      }
    };
    xhr.onerror = () => reject(new ApiError('NETWORK.ERROR', '网络错误，分片上传失败'));
    xhr.onabort = () => reject(new ApiError('NETWORK.ABORTED', '上传已取消'));
    if (signal) signal.addEventListener('abort', () => xhr.abort());
    xhr.send(blob);
  });
}
export const reparseDocument = (documentId, body) =>
  request(`/documents/${encodeURIComponent(documentId)}:reparse`, { method: 'POST', body: body || {} });
export const getParseResult = (documentId) =>
  request(`/documents/${encodeURIComponent(documentId)}/parse-result`);
export const getCleaningReport = (documentId) =>
  request(`/documents/${encodeURIComponent(documentId)}/cleaning-report`);

export const listIngestTasks = (query) => request('/ingest/tasks', { query });
export const getIngestTask = (taskId) => request(`/ingest/tasks/${encodeURIComponent(taskId)}`);
export const retryIngestTask = (taskId, body) =>
  request(`/ingest/tasks/${encodeURIComponent(taskId)}:retry`, { method: 'POST', body: body || {} });

/** 订阅导入任务事件（SSE）：meta / delta / done / error */
export const streamIngestTaskEvents = (taskId, handlers) =>
  openSse(`/ingest/tasks/${encodeURIComponent(taskId)}/events`, handlers);

/* ------------------------------------------------------------------ */
/* 知识构建与索引                                                       */
/* ------------------------------------------------------------------ */

export const getDocumentMetadata = (documentId) =>
  request(`/documents/${encodeURIComponent(documentId)}/metadata`);
export const updateDocumentMetadata = (documentId, body) =>
  request(`/documents/${encodeURIComponent(documentId)}/metadata`, { method: 'PUT', body });
export const listDocumentChunks = (documentId, query) =>
  request(`/documents/${encodeURIComponent(documentId)}/chunks`, { query });
export const previewDocumentChunks = (documentId, body) =>
  request(`/documents/${encodeURIComponent(documentId)}/chunks:preview`, { method: 'POST', body });
export const listDocumentClauses = (documentId, query) =>
  request(`/documents/${encodeURIComponent(documentId)}/clauses`, { query });

export const createEmbeddingJob = (body) => request('/index/embedding-jobs', { method: 'POST', body });
export const getEmbeddingJob = (jobId) => request(`/index/embedding-jobs/${encodeURIComponent(jobId)}`);
export const pauseEmbeddingJob = (jobId) =>
  request(`/index/embedding-jobs/${encodeURIComponent(jobId)}:pause`, { method: 'POST', body: {} });
export const resumeEmbeddingJob = (jobId) =>
  request(`/index/embedding-jobs/${encodeURIComponent(jobId)}:resume`, { method: 'POST', body: {} });

export const createIndexBuild = (body) => request('/index/builds', { method: 'POST', body });
export const getIndexBuild = (buildId) => request(`/index/builds/${encodeURIComponent(buildId)}`);
export const retryIndexBuild = (buildId) =>
  request(`/index/builds/${encodeURIComponent(buildId)}:retry`, { method: 'POST', body: {} });
export const getKnowledgeBaseIndexStatus = (kbId) =>
  request(`/knowledge-bases/${encodeURIComponent(kbId)}/index-status`);

/* ------------------------------------------------------------------ */
/* 检索引擎 / 生成与推理                                                */
/* ------------------------------------------------------------------ */

/** P0 仅支持 retrievalMode=vector、rerank=false（P1 扩展点已预留） */
export const retrieve = (body) => request('/retrieve', { method: 'POST', body });
export const understandQuery = (body) => request('/retrieve/query-understanding', { method: 'POST', body });

export const chat = (body, idempotencyKey) => request('/chat', { method: 'POST', body, idempotencyKey });

/** 流式问答：handlers.onEvent({event,data,id})，event ∈ meta|delta|citation|done|error|ping */
export const chatStream = (body, handlers) => openSse('/chat/stream', { body, ...handlers });

export const generateSummary = (documentId, body) =>
  request(`/documents/${encodeURIComponent(documentId)}/summaries`, { method: 'POST', body: body || {} });
export const getLatestSummary = (documentId) =>
  request(`/documents/${encodeURIComponent(documentId)}/summaries/latest`);

export const listPromptTemplates = (query) => request('/prompts/templates', { query });
export const listPromptTemplateVersions = (templateName) =>
  request(`/prompts/templates/${encodeURIComponent(templateName)}/versions`);

/* ------------------------------------------------------------------ */
/* 导出 / 审计                                                          */
/* ------------------------------------------------------------------ */

export const listExports = (query) => request('/exports', { query });
export const createExport = (body) => request('/exports', { method: 'POST', body });
/** #406 资源化入口：POST /kbs/{kbId}/exports */
export const createKbExport = (kbId, body, idempotencyKey) =>
  request(`/kbs/${encodeURIComponent(kbId)}/exports`, { method: 'POST', body, idempotencyKey });
export const getExport = (exportId) => request(`/exports/${encodeURIComponent(exportId)}`);
export const retryExport = (exportId) =>
  request(`/exports/${encodeURIComponent(exportId)}:retry`, { method: 'POST', body: {} });
export const downloadExport = async (exportId) => {
  const res = await request(`/exports/${encodeURIComponent(exportId)}/download`, { raw: true });
  return res.blob();
};

export const listAuditLogs = (query) => request('/audit-logs', { query });

/* ------------------------------------------------------------------ */
/* 安全 / 可观测 / 平台管理                                             */
/* ------------------------------------------------------------------ */

export const getSecurityPosture = () => request('/security/posture');
export const getSecurityPolicies = () => request('/security/policies');
export const updateSecurityPolicies = (body) =>
  request('/security/policies', { method: 'PUT', body });
export const runSecurityCheck = (body) => request('/security/checks', { method: 'POST', body });

export const listRequestLogs = (query) => request('/observability/request-logs', { query });
export const getTrace = (traceId) => request(`/observability/traces/${encodeURIComponent(traceId)}`);
export const getMetricsSummary = (query) => request('/observability/metrics/summary', { query });

export const listUsers = (query) => request('/admin/users', { query });
export const createUser = (body) => request('/admin/users', { method: 'POST', body });
export const updateUser = (userId, body) =>
  request(`/admin/users/${encodeURIComponent(userId)}`, { method: 'PATCH', body });
export const getSystemConfig = () => request('/admin/system-config');
export const updateSystemConfig = (body) => request('/admin/system-config', { method: 'PUT', body });
export const getAdminOverview = () => request('/admin/overview');

export const getCapabilities = () => request('/ui/capabilities');
export const getUIConfig = () => request('/ui/config');

export const listModelProviders = () => request('/models/providers');
export const invokeModelChat = (body) => request('/models/chat', { method: 'POST', body });
export const createEmbeddings = (body) => request('/models/embeddings', { method: 'POST', body });
export const getModelHealth = () => request('/models/health');

export default {
  request,
  openSse,
  healthz,
  readyz,
  systemInfo,
  login,
  logout,
  me,
  listKnowledgeBases,
  createKnowledgeBase,
  uploadDocument,
  listIngestTasks,
  getIngestTask,
  retryIngestTask,
  streamIngestTaskEvents,
  listDocumentChunks,
  previewDocumentChunks,
  listDocumentClauses,
  retrieve,
  chat,
  chatStream,
  listAuditLogs,
  setToken,
  getToken,
  newIdempotencyKey,
  ApiError,
};
