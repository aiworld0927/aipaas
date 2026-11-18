#!/usr/bin/env python3
"""AIPaaS P0 契约 Mock Server（仅标准库，无第三方依赖）。

用途：前端与后端在真实实现就绪前独立联调；同时作为契约可执行样例。
- 覆盖 10 个功能域的核心读写路径；
- 统一信封 {code,message,data,traceId}、Bearer 鉴权、分页、幂等键；
- 支持 SSE（/chat/stream、/ingest/tasks/{id}/events）；
- 内存状态机可完成“上传 → 解析任务 → 检索 → 问答”全链路。

运行：
    python3 aipaas-contracts/mock/server.py --port 4010
    python3 aipaas-contracts/mock/smoke_upload_qa.py --base-url http://127.0.0.1:4010
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

API = "/api/v1"
TENANT = "default"

ALL_PERMISSIONS = [
    "kb:read", "kb:write", "doc:read", "doc:upload", "doc:write", "doc:delete",
    "ingest:read", "ingest:retry", "chunk:read", "meta:write", "index:read",
    "index:write", "index:build", "retrieval:query", "chat:use", "summary:generate",
    "export:create", "export:read", "audit:read", "obs:read", "security:read",
    "security:write", "security:check", "user:read", "user:write", "role:read",
    "role:write", "config:read", "config:write", "model:read", "model:invoke",
    "admin:read",
]

STATE_LOCK = threading.RLock()
STATE = {
    "tokens": {},        # access token -> user dict
    "refreshTokens": {}, # refresh token -> user dict（一次性轮换）
    "policies": None,    # 内容安全策略与规则
    "kb": {},            # id -> kb
    "docs": {},          # id -> doc
    "tasks": {},         # id -> task
    "chunks": {},        # doc_id -> [chunk]
    "uploads": {},       # upload_id -> session（P0 #1 分片上传）
    "exports": {},       # id -> export
    "audit": [],         # list
    "idempotency": {},   # (key, method, path, body_hash) -> (status, body, headers)
    "users": {},         # id -> user
    "roles": {},         # id -> role
    "seq": 0,
}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def envelope(code: str, message: str, data, trace_id: str):
    return {"code": code, "message": message, "data": data, "traceId": trace_id}


def error(code: str, message: str, trace_id: str, reason: str | None = None):
    data = {"retryable": False}
    if reason:
        data["reason"] = reason
    return envelope(code, message, data, trace_id)


def page(items, q):
    p = max(1, int(q.get("page", ["1"])[0] or 1))
    size = min(100, max(1, int(q.get("pageSize", ["20"])[0] or 20)))
    start = (p - 1) * size
    return {"items": items[start:start + size], "total": len(items), "page": p, "pageSize": size}


def parse_multipart(body: bytes, content_type: str):
    m = re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", content_type or "")
    if not m:
        return None, b""
    boundary = (m.group(1) or m.group(2)).strip().encode()
    parts = body.split(b"--" + boundary)
    for part in parts:
        if b"Content-Disposition" not in part:
            continue
        head, _, payload = part.partition(b"\r\n\r\n")
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        header = head.decode("utf-8", "replace")
        fn = re.search(r'filename="([^"]*)"', header)
        if fn:
            return fn.group(1), payload
    return None, b""


class Handler(BaseHTTPRequestHandler):
    server_version = "AipaasMock/1.0"

    # ---------------------------------------------------------------- utils
    def log_message(self, fmt, *args):  # quiet
        pass

    def trace_id(self) -> str:
        return self.headers.get("X-Request-Id") or f"tr_{uuid.uuid4().hex[:16]}"

    def send_json(self, status: int, payload, trace_id: str, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Trace-Id", trace_id)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "X-Trace-Id, Retry-After")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body_bytes(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def body_json(self):
        raw = getattr(self, "_cached_body", b"")
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    @staticmethod
    def public(obj):
        """剥离 mock 内部字段（下划线开头）。"""
        if isinstance(obj, dict):
            return {k: Handler.public(v) for k, v in obj.items() if not k.startswith("_")}
        if isinstance(obj, list):
            return [Handler.public(x) for x in obj]
        return obj

    def current_user(self):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        return STATE["tokens"].get(auth[7:].strip())

    def require(self, trace_id: str):
        """返回 user 或直接写 401。"""
        user = self.current_user()
        if not user:
            self.send_json(401, error("AUTH.UNAUTHENTICATED", "未提供有效凭证", trace_id), trace_id)
            return None
        return user

    def require_perm(self, user, perm: str, trace_id: str) -> bool:
        if perm in user["permissions"]:
            return True
        self.audit(user, "permission.denied", "permission", perm, "DENIED", trace_id,
                   {"required": perm})
        self.send_json(403, error("PERM.FORBIDDEN", "无权执行该操作", trace_id,
                                  f"missing permission: {perm}"), trace_id)
        return False

    def audit(self, user, action, obj_type, obj_id, result, trace_id, detail=None):
        with STATE_LOCK:
            STATE["seq"] += 1
            STATE["audit"].insert(0, {
                "id": new_id("audit"), "tenantId": TENANT,
                "actorId": user.get("userId", "anonymous"), "actorName": user.get("displayName", ""),
                "action": action, "objectType": obj_type, "objectId": obj_id,
                "result": result, "ip": self.client_address[0], "userAgent": self.headers.get("User-Agent"),
                "traceId": trace_id, "detail": detail or {}, "createdAt": now(),
            })

    def idempotency_lookup(self, body: bytes):
        key = self.headers.get("Idempotency-Key")
        if not key:
            return None
        k = (key, self.command, urlparse(self.path).path, hash(body))
        with STATE_LOCK:
            return STATE["idempotency"].get(k)

    def idempotency_store(self, body: bytes, status, payload, trace_id):
        key = self.headers.get("Idempotency-Key")
        if not key or status >= 300:
            return
        k = (key, self.command, urlparse(self.path).path, hash(body))
        with STATE_LOCK:
            STATE["idempotency"][k] = (status, payload, trace_id)

    # ---------------------------------------------------------------- HTTP entry
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization,Content-Type,Idempotency-Key,X-Request-Id,Last-Event-ID")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
        self.end_headers()

    def do_GET(self): self.dispatch("GET")
    def do_POST(self): self.dispatch("POST")
    def do_PUT(self): self.dispatch("PUT")
    def do_PATCH(self): self.dispatch("PATCH")
    def do_DELETE(self): self.dispatch("DELETE")

    def dispatch(self, method: str):
        trace = self.trace_id()
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)
        raw = b""
        if method in ("POST", "PUT", "PATCH"):
            raw = self.body_bytes()
        self._cached_body = raw

        if path in ("/healthz", "/readyz"):
            return self.health(path, trace)
        if path in (API + "/healthz", API + "/readyz"):
            return self.health(path.replace(API, ""), trace)
        if not path.startswith(API):
            return self.send_json(404, error("BIZ.NOT_FOUND", "路径不存在", trace), trace)

        rel = path[len(API):] or "/"
        if rel == "/auth/login" and method == "POST":
            return self.login(trace)
        if rel == "/auth/refresh" and method == "POST":
            return self.refresh(trace)

        user = self.require(trace)
        if not user:
            return

        # idempotent replay
        cached = self.idempotency_lookup(raw)
        if cached:
            status, payload, cached_trace = cached
            payload = dict(payload)
            payload["traceId"] = trace
            return self.send_json(status, payload, trace)

        for pattern, methods, fn in ROUTES:
            m = pattern.fullmatch(rel)
            if m and method in methods:
                try:
                    result = fn(self, user, trace, raw, q, *m.groups())
                except Exception as exc:  # mock 内部错误
                    return self.send_json(500, error("SYS.INTERNAL", f"mock error: {exc}", trace), trace)
                if result and result[0] in (200, 201, 202):
                    self.idempotency_store(raw, result[0], result[1], trace)
                return
        return self.send_json(501, error("SYS.NOT_IMPLEMENTED", "P0 契约已定义，Mock 未覆盖", trace), trace)

    def health(self, which, trace):
        if which == "/healthz":
            return self.send_json(200, {"status": "UP"}, trace)
        return self.send_json(200, {"status": "UP", "checks": {"mock": {"status": "UP"}}}, trace)

    # ---------------------------------------------------------------- auth
    def login(self, trace):
        body = self.body_json() or {}
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        if len(username) < 3 or len(password) < 8:
            self.audit({"userId": username or "unknown"}, "auth.login.failed", "user", username,
                       "FAILURE", trace, {"reason": "invalid payload"})
            return self.send_json(401, error("AUTH.INVALID_CREDENTIALS", "用户名或密码错误", trace), trace)
        viewer = username.lower() in ("viewer", "auditor")
        user = {
            "userId": "u_" + username, "tenantId": TENANT, "username": username,
            "displayName": username, "roles": ["VIEWER"] if viewer else ["PLATFORM_ADMIN"],
            "permissions": (["kb:read", "doc:read", "ingest:read", "retrieval:query"]
                            if viewer else ALL_PERMISSIONS),
            "defaultTenant": True,
        }
        token = "mock_" + uuid.uuid4().hex
        refresh_token = "mock_refresh_" + uuid.uuid4().hex + uuid.uuid4().hex
        with STATE_LOCK:
            STATE["tokens"][token] = user
            STATE["refreshTokens"][refresh_token] = user
        self.audit(user, "auth.login", "user", user["userId"], "SUCCESS", trace)
        return self.send_json(200, envelope("OK", "success", {
            "accessToken": token, "refreshToken": refresh_token, "tokenType": "Bearer",
            "expiresIn": 1800, "refreshExpiresIn": 604800, "user": user,
        }, trace), trace)

    def refresh(self, trace):
        body = self.body_json() or {}
        old_refresh = str(body.get("refreshToken") or "")
        if len(old_refresh) < 32 or len(old_refresh) > 256:
            return self.send_json(400, error("PARAM.INVALID", "refreshToken 长度不合法", trace), trace)
        with STATE_LOCK:
            user = STATE["refreshTokens"].pop(old_refresh, None)
        if not user:
            self.audit({"userId": "anonymous"}, "auth.token.refresh.failed", "session", "unknown",
                       "FAILURE", trace, {"reason": "refresh_token_invalid"})
            return self.send_json(401, error("AUTH.UNAUTHENTICATED", "刷新令牌无效或已使用", trace), trace)
        token = "mock_" + uuid.uuid4().hex
        new_refresh = "mock_refresh_" + uuid.uuid4().hex + uuid.uuid4().hex
        with STATE_LOCK:
            STATE["tokens"][token] = user
            STATE["refreshTokens"][new_refresh] = user
        self.audit(user, "auth.token.refresh", "session", user["userId"], "SUCCESS", trace, {"rotated": True})
        return self.send_json(200, envelope("OK", "success", {
            "accessToken": token, "refreshToken": new_refresh, "tokenType": "Bearer",
            "expiresIn": 1800, "refreshExpiresIn": 604800, "user": user,
        }, trace), trace)

    def logout(self, user, trace, raw, q):
        auth = self.headers.get("Authorization", "")
        with STATE_LOCK:
            STATE["tokens"].pop(auth[7:].strip(), None)
        self.audit(user, "auth.logout", "user", user["userId"], "SUCCESS", trace)
        return self.send_json(200, envelope("OK", "success", {"ok": True}, trace), trace)

    def me(self, user, trace, raw, q):
        return self.send_json(200, envelope("OK", "success", user, trace), trace)

    # ---------------------------------------------------------------- kb
    def list_kb(self, user, trace, raw, q):
        if not self.require_perm(user, "kb:read", trace): return None
        items = [k for k in STATE["kb"].values() if k["status"] != "DELETED"]
        kw = (q.get("keyword") or [""])[0]
        if kw:
            items = [k for k in items if kw in k["name"]]
        return self.send_json(200, envelope("OK", "success", page(items, q), trace), trace)

    def create_kb(self, user, trace, raw, q):
        if not self.require_perm(user, "kb:write", trace): return None
        body = self.body_json()
        if not body or not str(body.get("name") or "").strip():
            return self.send_json(400, error("PARAM.INVALID", "name 不能为空", trace), trace)
        kid = new_id("kb")
        ts = now()
        kb = {"id": kid, "tenantId": TENANT, "name": body["name"], "description": body.get("description", ""),
              "domain": body.get("domain"), "standardSystem": body.get("standardSystem"),
              "config": body.get("config") or {"chunkSize": 512, "chunkOverlap": 64, "retrievalTopK": 5},
              "status": "ACTIVE", "documentCount": 0, "chunkCount": 0,
              "indexStatus": "EMPTY", "version": None, "env": None, "createdAt": ts, "updatedAt": ts}
        STATE["kb"][kid] = kb
        self.audit(user, "kb.create", "knowledge_base", kid, "SUCCESS", trace)
        return self.send_json(201, envelope("OK", "created", kb, trace), trace)

    def get_kb(self, user, trace, raw, q, kid):
        if not self.require_perm(user, "kb:read", trace): return None
        kb = STATE["kb"].get(kid)
        if not kb:
            return self.send_json(404, error("BIZ.NOT_FOUND", "知识库不存在", trace), trace)
        return self.send_json(200, envelope("OK", "success", kb, trace), trace)

    def patch_kb(self, user, trace, raw, q, kid):
        if not self.require_perm(user, "kb:write", trace): return None
        kb = STATE["kb"].get(kid)
        if not kb:
            return self.send_json(404, error("BIZ.NOT_FOUND", "知识库不存在", trace), trace)
        body = self.body_json() or {}
        for f in ("name", "description", "domain", "standardSystem", "config"):
            if f in body:
                kb[f] = body[f]
        kb["updatedAt"] = now()
        self.audit(user, "kb.update", "knowledge_base", kid, "SUCCESS", trace, {"fields": list(body)})
        return self.send_json(200, envelope("OK", "success", kb, trace), trace)

    def delete_kb(self, user, trace, raw, q, kid):
        if not self.require_perm(user, "kb:write", trace): return None
        kb = STATE["kb"].get(kid)
        if not kb:
            return self.send_json(404, error("BIZ.NOT_FOUND", "知识库不存在", trace), trace)
        kb["status"] = "DELETED"
        kb["updatedAt"] = now()
        self.audit(user, "kb.delete", "knowledge_base", kid, "SUCCESS", trace)
        return self.send_json(200, envelope("OK", "success", {"ok": True}, trace), trace)

    # ---------------------------------------------------------------- upload / documents
    def upload(self, user, trace, raw, q, kid):
        if not self.require_perm(user, "doc:upload", trace): return None
        kb = STATE["kb"].get(kid)
        if not kb:
            return self.send_json(404, error("BIZ.NOT_FOUND", "知识库不存在", trace), trace)
        if not self.headers.get("Idempotency-Key"):
            return self.send_json(400, error("PARAM.IDEMPOTENCY_KEY_MISSING", "缺少 Idempotency-Key", trace), trace)
        filename, content = parse_multipart(raw, self.headers.get("Content-Type", ""))
        if not filename:
            return self.send_json(400, error("PARAM.INVALID", "缺少文件字段 file", trace), trace)
        ext = filename.rsplit(".", 1)[-1].upper() if "." in filename else "TXT"
        if ext not in ("PDF", "DOC", "DOCX", "XLS", "XLSX", "PPT", "PPTX", "MD", "TXT"):
            return self.send_json(400, error("PARAM.UNSUPPORTED_FORMAT", "不支持的文件格式", trace), trace)
        if len(content) > 100 * 1024 * 1024:
            return self.send_json(413, error("PARAM.FILE_TOO_LARGE", "文件超过 100MB", trace), trace)

        ts = now()
        did, tid = new_id("doc"), new_id("task")
        doc = {
            "id": did, "tenantId": TENANT, "kbId": kid, "fileName": filename, "format": ext,
            "sizeBytes": len(content), "status": "QUEUED", "indexStatus": "PENDING", "chunkCount": 0,
            "progress": 0, "standardNo": f"JR/T {9000 + len(STATE['docs'])}",
            "uploaderId": user["userId"], "source": "manual_upload", "pageCount": 12,
            "clauseCount": 3, "lastTaskId": tid, "errorCode": None, "errorMessage": None,
            "createdAt": ts, "updatedAt": ts, "_fileHash": hashlib.sha256(content).hexdigest(),
        }
        task = {
            "id": tid, "tenantId": TENANT, "documentId": did, "kbId": kid, "status": "QUEUED",
            "stage": "UPLOAD", "progress": 0, "retryCount": 0, "maxRetries": 2,
            "error": None, "queuedAt": ts, "startedAt": None, "finishedAt": None,
            "createdAt": ts, "updatedAt": ts, "_ts": time.time(),
        }
        with STATE_LOCK:
            STATE["docs"][did] = doc
            STATE["tasks"][tid] = task
            kb["documentCount"] += 1
            STATE["chunks"][did] = [self._chunk(did, kid, i) for i in range(3)]
            kb["chunkCount"] += 3
        self.audit(user, "doc.upload", "document", did, "SUCCESS", trace,
                   {"fileName": filename, "sizeBytes": len(content)})
        return self.send_json(202, envelope("OK", "accepted", {"document": doc, "ingestTask": task}, trace), trace)

    # ---------------------------------------------------------------- chunked upload (P0 #1)
    def _upload_view(self, session, duplicate=False, document_id=None):
        received = sorted(session["receivedParts"])
        missing = [i for i in range(1, session["totalParts"] + 1) if i not in session["receivedParts"]]
        offset = 0
        for part_no in received:
            if part_no != (offset // session["partSize"]) + 1:
                break
            offset += (session["sizeBytes"] - (session["totalParts"] - 1) * session["partSize"]
                       if part_no == session["totalParts"] else session["partSize"])
        view = {
            "uploadId": session["id"], "kbId": session["kbId"], "fileName": session["fileName"],
            "fileHash": session["fileHash"], "sizeBytes": session["sizeBytes"],
            "partSize": session["partSize"], "totalParts": session["totalParts"],
            "receivedParts": received, "missingParts": missing, "offset": offset,
            "status": session["status"], "duplicate": duplicate, "autoParse": session["autoParse"],
        }
        if document_id:
            view["documentId"] = document_id
        return view

    def init_upload(self, user, trace, raw, q, kid):
        if not self.require_perm(user, "doc:upload", trace): return None
        kb = STATE["kb"].get(kid)
        if not kb:
            return self.send_json(404, error("BIZ.NOT_FOUND", "知识库不存在", trace), trace)
        if not self.headers.get("Idempotency-Key"):
            return self.send_json(400, error("PARAM.IDEMPOTENCY_KEY_MISSING", "缺少 Idempotency-Key", trace), trace)
        body = self.body_json() or {}
        name, size, file_hash = body.get("fileName"), body.get("sizeBytes"), body.get("fileHash")
        if not name or not isinstance(size, int) or size <= 0 or size > 104857600:
            return self.send_json(413 if isinstance(size, int) and size > 104857600 else 400,
                                  error("PARAM.FILE_TOO_LARGE" if isinstance(size, int) and size > 104857600
                                        else "PARAM.INVALID", "文件信息不合法", trace), trace)
        if not re.fullmatch(r"[a-fA-F0-9]{64}", str(file_hash or "")):
            return self.send_json(400, error("PARAM.INVALID", "fileHash 必须是 SHA-256", trace), trace)
        with STATE_LOCK:
            for doc in STATE["docs"].values():
                if doc.get("kbId") == kid and doc.get("_fileHash") == file_hash.lower():
                    synthetic = {"id": None, "kbId": kid, "fileName": doc["fileName"], "fileHash": file_hash.lower(),
                                 "sizeBytes": doc["sizeBytes"], "partSize": 5242880,
                                 "totalParts": max(1, (doc["sizeBytes"] + 5242879) // 5242880),
                                 "receivedParts": set(), "status": "COMPLETED", "autoParse": True}
                    return self.send_json(200, envelope("OK", "success",
                                                        self._upload_view(synthetic, True, doc["id"]), trace), trace)
            for session in STATE["uploads"].values():
                if (session["kbId"] == kid and session["fileHash"] == file_hash.lower()
                        and session["status"] == "UPLOADING"):
                    return self.send_json(200, envelope("OK", "success", self._upload_view(session), trace), trace)
            total = int(body.get("totalParts") or max(1, (size + 5242879) // 5242880))
            session = {
                "id": new_id("upl"), "kbId": kid, "fileName": name, "fileHash": file_hash.lower(),
                "sizeBytes": size, "partSize": 5242880, "totalParts": total, "receivedParts": set(),
                "status": "UPLOADING", "autoParse": body.get("autoParse", True),
                "standardNo": body.get("standardNo"),
            }
            STATE["uploads"][session["id"]] = session
        return self.send_json(200, envelope("OK", "success", self._upload_view(session), trace), trace)

    def upload_status(self, user, trace, raw, q, kid, upload_id):
        if not self.require_perm(user, "doc:upload", trace): return None
        session = STATE["uploads"].get(upload_id)
        if not session or session["kbId"] != kid:
            return self.send_json(404, error("BIZ.NOT_FOUND", "上传会话不存在", trace), trace)
        return self.send_json(200, envelope("OK", "success", self._upload_view(session), trace), trace)

    def upload_part(self, user, trace, raw, q, kid, upload_id, part_no):
        if not self.require_perm(user, "doc:upload", trace): return None
        session = STATE["uploads"].get(upload_id)
        if not session or session["kbId"] != kid:
            return self.send_json(404, error("BIZ.NOT_FOUND", "上传会话不存在", trace), trace)
        part_no = int(part_no)
        if part_no < 1 or part_no > session["totalParts"]:
            return self.send_json(400, error("PARAM.INVALID", "分片序号越界", trace), trace)
        with STATE_LOCK:
            session["receivedParts"].add(part_no)
        return self.send_json(200, envelope("OK", "success", self._upload_view(session), trace), trace)

    def complete_upload(self, user, trace, raw, q, kid, upload_id):
        if not self.require_perm(user, "doc:upload", trace): return None
        if not self.headers.get("Idempotency-Key"):
            return self.send_json(400, error("PARAM.IDEMPOTENCY_KEY_MISSING", "缺少 Idempotency-Key", trace), trace)
        session = STATE["uploads"].get(upload_id)
        if not session or session["kbId"] != kid:
            return self.send_json(404, error("BIZ.NOT_FOUND", "上传会话不存在", trace), trace)
        missing = [i for i in range(1, session["totalParts"] + 1) if i not in session["receivedParts"]]
        if missing:
            return self.send_json(409, error("BIZ.CONFLICT", f"分片不完整，缺少 {len(missing)} 个分片", trace), trace)
        ts = now()
        did, tid = new_id("doc"), new_id("task")
        ext = session["fileName"].rsplit(".", 1)[-1].upper() if "." in session["fileName"] else "TXT"
        doc = {
            "id": did, "tenantId": TENANT, "kbId": kid, "fileName": session["fileName"], "format": ext,
            "sizeBytes": session["sizeBytes"], "status": "QUEUED", "indexStatus": "PENDING", "chunkCount": 0,
            "progress": 0, "standardNo": session.get("standardNo") or f"JR/T {9000 + len(STATE['docs'])}",
            "uploaderId": user["userId"], "source": "manual_upload", "pageCount": None,
            "clauseCount": None, "lastTaskId": tid, "errorCode": None, "errorMessage": None,
            "createdAt": ts, "updatedAt": ts, "_fileHash": session["fileHash"],
        }
        task = {
            "id": tid, "tenantId": TENANT, "documentId": did, "kbId": kid, "status": "QUEUED",
            "stage": "PARSE" if session["autoParse"] else "UPLOAD", "progress": 0, "retryCount": 0,
            "maxRetries": 2, "error": None, "queuedAt": ts, "startedAt": None, "finishedAt": None,
            "createdAt": ts, "updatedAt": ts, "_ts": time.time(),
        }
        with STATE_LOCK:
            STATE["docs"][did] = doc
            STATE["tasks"][tid] = task
            STATE["uploads"].pop(upload_id, None)
            kb = STATE["kb"].get(kid)
            if kb:
                kb["documentCount"] += 1
            STATE["chunks"][did] = [self._chunk(did, kid, i) for i in range(3)]
            if kb:
                kb["chunkCount"] += 3
        self.audit(user, "doc.upload", "document", did, "SUCCESS", trace,
                   {"fileName": session["fileName"], "sizeBytes": session["sizeBytes"], "chunked": True})
        return self.send_json(202, envelope("OK", "accepted", {"document": doc, "ingestTask": task}, trace), trace)

    def _chunk(self, did, kid, i):
        clauses = [("4.1.1", "数据安全总则"), ("4.1.2", "访问控制"), ("5.2", "风险监测")]
        no, title = clauses[i]
        return {
            "id": f"chunk_{did[4:]}_{i}", "tenantId": TENANT, "documentId": did, "kbId": kid,
            "chunkType": "CLAUSE", "chunkIndex": i,
            "text": f"{no} {title}：金融机构应建立与业务规模相适应的数据安全管理机制。",
            "tokenCount": 32, "clauseNo": no, "chapterPath": f"4 技术要求 / {no} {title}",
            "pageNo": 10 + i, "securityLevel": None, "domain": None, "effectiveAt": None,
            "indexVersion": None, "env": None, "metadata": {},
        }

    def _advance(self, tid):
        task = STATE["tasks"].get(tid)
        if not task:
            return
        elapsed = time.time() - task["_ts"]
        stages = [(0.0, "QUEUED", "UPLOAD", 0), (0.8, "RUNNING", "PARSE", 25),
                  (1.6, "RUNNING", "CLEAN", 45), (2.4, "RUNNING", "CHUNK", 65),
                  (3.2, "RUNNING", "EMBED", 80), (4.0, "RUNNING", "INDEX", 92),
                  (4.6, "SUCCEEDED", "DONE", 100)]
        cur = stages[0]
        for st in stages:
            if elapsed >= st[0]:
                cur = st
        if task["status"] == "SUCCEEDED":
            return
        _, status, stage, prog = cur
        task.update(status=status, stage=stage, progress=prog, updatedAt=now())
        doc = STATE["docs"].get(task["documentId"])
        if doc:
            doc["progress"] = prog
            doc["updatedAt"] = now()
            if status == "RUNNING":
                doc["status"] = "PARSING" if stage == "PARSE" else "PROCESSING"
        if status == "RUNNING" and not task["startedAt"]:
            task["startedAt"] = now()
        if status == "SUCCEEDED":
            task["finishedAt"] = now()
            if doc:
                doc["status"] = "READY"
                doc["indexStatus"] = "INDEXED"
                doc["progress"] = 100
                doc["chunkCount"] = len(STATE["chunks"].get(doc["id"], []))
                doc["updatedAt"] = now()
            kb = STATE["kb"].get(task["kbId"])
            if kb:
                kb["indexStatus"] = "READY"
                kb["updatedAt"] = now()

    def list_docs(self, user, trace, raw, q):
        if not self.require_perm(user, "doc:read", trace): return None
        items = list(STATE["docs"].values())
        if q.get("kbId"):
            items = [d for d in items if d["kbId"] == q["kbId"][0]]
        if q.get("status"):
            items = [d for d in items if d["status"] == q["status"][0]]
        return self.send_json(200, envelope("OK", "success", page(items, q), trace), trace)

    def get_doc(self, user, trace, raw, q, did):
        if not self.require_perm(user, "doc:read", trace): return None
        doc = STATE["docs"].get(did)
        if not doc:
            return self.send_json(404, error("BIZ.NOT_FOUND", "文档不存在", trace), trace)
        return self.send_json(200, envelope("OK", "success", doc, trace), trace)

    def delete_doc(self, user, trace, raw, q, did):
        if not self.require_perm(user, "doc:delete", trace): return None
        doc = STATE["docs"].get(did)
        if not doc:
            return self.send_json(404, error("BIZ.NOT_FOUND", "文档不存在", trace), trace)
        with STATE_LOCK:
            STATE["docs"].pop(did, None)
            STATE["chunks"].pop(did, None)
        self.audit(user, "doc.delete", "document", did, "SUCCESS", trace)
        return self.send_json(200, envelope("OK", "success", {"ok": True}, trace), trace)

    def parse_result(self, user, trace, raw, q, did):
        if did not in STATE["docs"]:
            return self.send_json(404, error("BIZ.NOT_FOUND", "文档不存在", trace), trace)
        data = {
            "documentId": did, "tenantId": TENANT, "parser": "pdf-native", "success": True,
            "failureCode": None, "retryCount": 0, "pages": 12,
            "sections": [{"sectionId": "s1", "title": "4 技术要求", "level": 1, "clauseNo": "4", "pageNo": 9,
                          "children": [{"sectionId": "s1-1", "title": "4.1 数据安全", "level": 2,
                                        "clauseNo": "4.1", "pageNo": 9, "children": []}]}],
            "tables": [{"tableId": "t1", "pageNo": 10, "caption": "控制项", "headerRows": 1,
                        "rows": [["条款", "要求"], ["4.1.1", "建立数据安全机制"]]}],
            "clauseAnchors": [
                {"clauseNo": "4.1.1", "title": "总则", "chapterPath": "4 技术要求 / 4.1 数据安全 / 4.1.1 总则",
                 "pageNo": 10, "level": 3},
                {"clauseNo": "4.1.2", "title": "访问控制", "chapterPath": "4 技术要求 / 4.1 数据安全 / 4.1.2 访问控制",
                 "pageNo": 11, "level": 3},
            ],
            "parsedAt": now(),
        }
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def cleaning_report(self, user, trace, raw, q, did):
        if did not in STATE["docs"]:
            return self.send_json(404, error("BIZ.NOT_FOUND", "文档不存在", trace), trace)
        data = {"documentId": did, "beforeChars": 52000, "afterChars": 49800, "removedGarbage": 210,
                "removedIllegalChars": 36, "collapsedBlankLines": 420, "clauseNoPreserved": True,
                "diffs": [{"type": "HEADER_FOOTER_REMOVED", "pageNo": 1, "lineNo": 1,
                           "before": "密级：内部", "after": ""}],
                "generatedAt": now()}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def get_meta(self, user, trace, raw, q, did):
        doc = STATE["docs"].get(did)
        if not doc:
            return self.send_json(404, error("BIZ.NOT_FOUND", "文档不存在", trace), trace)
        data = {"documentId": did, "tenantId": TENANT, "standardNo": doc["standardNo"],
                "standardName": doc["fileName"].rsplit(".", 1)[0], "issuer": "中国人民银行",
                "issuedAt": "2024-06-01", "effectiveAt": None, "replacedBy": None, "domain": None,
                "securityLevel": None, "clauseCount": 3, "confidence": 0.93, "source": "AUTO_EXTRACTED",
                "version": None, "env": None, "updatedAt": now()}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def put_meta(self, user, trace, raw, q, did):
        if not self.require_perm(user, "meta:write", trace): return None
        if did not in STATE["docs"]:
            return self.send_json(404, error("BIZ.NOT_FOUND", "文档不存在", trace), trace)
        self.audit(user, "meta.update", "document", did, "SUCCESS", trace)
        return self.get_meta(user, trace, raw, q, did)

    def chunks(self, user, trace, raw, q, did):
        items = STATE["chunks"].get(did)
        if items is None:
            return self.send_json(404, error("BIZ.NOT_FOUND", "文档不存在", trace), trace)
        if q.get("chunkType"):
            items = [c for c in items if c["chunkType"] == q["chunkType"][0]]
        return self.send_json(200, envelope("OK", "success", page(items, q), trace), trace)

    def chunk_preview(self, user, trace, raw, q, did):
        body = self.body_json() or {}
        if did not in STATE["docs"]:
            return self.send_json(404, error("BIZ.NOT_FOUND", "文档不存在", trace), trace)
        if not body.get("chunkType"):
            return self.send_json(400, error("PARAM.INVALID", "chunkType 必填", trace), trace)
        items = STATE["chunks"][did]
        data = {"documentId": did, "chunkType": body["chunkType"], "totalChunks": len(items),
                "clauseCount": len(items), "items": items}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def clauses(self, user, trace, raw, q, did):
        items = STATE["chunks"].get(did)
        if items is None:
            return self.send_json(404, error("BIZ.NOT_FOUND", "文档不存在", trace), trace)
        clauses = [{"clauseNo": c["clauseNo"], "tenantId": TENANT, "title": c["text"].split("：")[0],
                    "chapterPath": c["chapterPath"],
                    "text": c["text"], "pageNo": c["pageNo"], "level": 3, "chunkId": c["id"]} for c in items]
        return self.send_json(200, envelope("OK", "success", page(clauses, q), trace), trace)

    # ---------------------------------------------------------------- ingest tasks
    def list_tasks(self, user, trace, raw, q):
        if not self.require_perm(user, "ingest:read", trace): return None
        items = list(STATE["tasks"].values())
        if q.get("kbId"):
            items = [t for t in items if t["kbId"] == q["kbId"][0]]
        if q.get("documentId"):
            items = [t for t in items if t["documentId"] == q["documentId"][0]]
        if q.get("status"):
            items = [t for t in items if t["status"] == q["status"][0]]
        return self.send_json(200, envelope("OK", "success", page(self.public(items), q), trace), trace)

    def get_task(self, user, trace, raw, q, tid):
        if not self.require_perm(user, "ingest:read", trace): return None
        task = STATE["tasks"].get(tid)
        if not task:
            return self.send_json(404, error("BIZ.NOT_FOUND", "任务不存在", trace), trace)
        self._advance(tid)
        return self.send_json(200, envelope("OK", "success", self.public(task), trace), trace)

    def retry_task(self, user, trace, raw, q, tid):
        if not self.require_perm(user, "ingest:retry", trace): return None
        task = STATE["tasks"].get(tid)
        if not task:
            return self.send_json(404, error("BIZ.NOT_FOUND", "任务不存在", trace), trace)
        if task["status"] not in ("FAILED", "CANCELED"):
            return self.send_json(409, error("BIZ.TASK_NOT_RETRYABLE", "仅失败任务可重试", trace), trace)
        task.update(status="QUEUED", stage="UPLOAD", progress=0, retryCount=task["retryCount"] + 1,
                    error=None, startedAt=None, finishedAt=None, updatedAt=now(), _ts=time.time())
        self.audit(user, "ingest.retry", "ingest_task", tid, "SUCCESS", trace)
        return self.send_json(202, envelope("OK", "accepted", self.public(task), trace), trace)

    # ---------------------------------------------------------------- indexing
    def create_embedding_job(self, user, trace, raw, q):
        if not self.require_perm(user, "index:write", trace): return None
        body = self.body_json() or {}
        if not body.get("kbId"):
            return self.send_json(400, error("PARAM.MISSING", "kbId 必填", trace), trace)
        job = {"id": new_id("emb"), "tenantId": TENANT, "kbId": body["kbId"],
               "documentIds": body.get("documentIds", []), "status": "SUCCEEDED", "model": body.get("model", "bge-m3"),
               "dimension": 1024, "total": 3, "processed": 3, "failed": 0, "progress": 100,
               "failures": [], "error": None, "createdAt": now(), "updatedAt": now()}
        self.audit(user, "index.embed", "embedding_job", job["id"], "SUCCESS", trace)
        return self.send_json(202, envelope("OK", "accepted", job, trace), trace)

    def get_embedding_job(self, user, trace, raw, q, jid):
        if not self.require_perm(user, "index:read", trace): return None
        job = {"id": jid, "tenantId": TENANT, "kbId": "kb_mock", "documentIds": [], "status": "SUCCEEDED",
               "model": "bge-m3", "dimension": 1024, "total": 3, "processed": 3, "failed": 0,
               "progress": 100, "failures": [], "error": None, "createdAt": now(), "updatedAt": now()}
        return self.send_json(200, envelope("OK", "success", job, trace), trace)

    def job_action(self, user, trace, raw, q, jid, action):
        if not self.require_perm(user, "index:write", trace): return None
        self.audit(user, f"index.embed.{action}", "embedding_job", jid, "SUCCESS", trace)
        return self.get_embedding_job(user, trace, raw, q, jid)

    def create_index_build(self, user, trace, raw, q):
        if not self.require_perm(user, "index:build", trace): return None
        body = self.body_json() or {}
        if not body.get("kbId"):
            return self.send_json(400, error("PARAM.MISSING", "kbId 必填", trace), trace)
        build = {"id": new_id("idx"), "tenantId": TENANT, "kbId": body["kbId"], "status": "SUCCEEDED",
                 "totalChunks": 3, "indexedChunks": 3, "progress": 100, "durationMs": 1200,
                 "indexType": None, "metric": None, "params": None, "indexSlot": None, "alias": None,
                 "version": None, "env": None, "error": None,
                 "startedAt": now(), "finishedAt": now(), "createdAt": now(), "updatedAt": now()}
        self.audit(user, "index.build", "index_build", build["id"], "SUCCESS", trace)
        return self.send_json(202, envelope("OK", "accepted", build, trace), trace)

    def get_index_build(self, user, trace, raw, q, bid):
        if not self.require_perm(user, "index:read", trace): return None
        build = {"id": bid, "tenantId": TENANT, "kbId": "kb_mock", "status": "SUCCEEDED",
                 "totalChunks": 3, "indexedChunks": 3, "progress": 100, "durationMs": 1200,
                 "indexType": None, "metric": None, "params": None, "indexSlot": None, "alias": None,
                 "version": None, "env": None, "error": None, "startedAt": now(), "finishedAt": now(),
                 "createdAt": now(), "updatedAt": now()}
        return self.send_json(200, envelope("OK", "success", build, trace), trace)

    def retry_index_build(self, user, trace, raw, q, bid):
        if not self.require_perm(user, "index:build", trace): return None
        self.audit(user, "index.build.retry", "index_build", bid, "SUCCESS", trace)
        return self.get_index_build(user, trace, raw, q, bid)

    def index_status(self, user, trace, raw, q, kid):
        if not self.require_perm(user, "kb:read", trace): return None
        if kid not in STATE["kb"]:
            return self.send_json(404, error("BIZ.NOT_FOUND", "知识库不存在", trace), trace)
        kb = STATE["kb"][kid]
        data = {"kbId": kid, "indexStatus": kb["indexStatus"], "collection": f"kb_{kid}_v1",
                "dimension": 1024, "docCount": kb["documentCount"], "chunkCount": kb["chunkCount"],
                "lastBuildId": "idx_mock", "lastBuiltAt": now(), "version": None, "env": None}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    # ---------------------------------------------------------------- retrieval / chat
    def _ready_chunks(self):
        out = []
        for d in STATE["docs"].values():
            if d["status"] == "READY":
                out.extend(STATE["chunks"].get(d["id"], []))
        return out

    def retrieve(self, user, trace, raw, q):
        if not self.require_perm(user, "retrieval:query", trace): return None
        body = self.body_json() or {}
        query = str(body.get("query") or "").strip()
        if not query:
            return self.send_json(400, error("PARAM.QUERY_EMPTY", "query 不能为空", trace), trace)
        if len(query) > 2000:
            return self.send_json(400, error("PARAM.QUERY_TOO_LONG", "query 超长", trace), trace)
        kb_id, kb_ids = body.get("kbId"), body.get("kbIds") or []
        if kb_id and kb_ids:
            return self.send_json(400, error("PARAM.INVALID", "kbId 与 kbIds 互斥", trace), trace)
        hits = []
        for c in self._ready_chunks():
            if kb_id and c["kbId"] != kb_id:
                continue
            hits.append({"rank": 1, "score": 0.93, "chunkId": c["id"], "documentId": c["documentId"],
                         "kbId": c["kbId"], "clauseNo": c["clauseNo"], "chapterPath": c["chapterPath"],
                         "pageNo": c["pageNo"], "text": c["text"], "standardNo": "JR/T 0088",
                         "standardName": "金融数据安全 数据安全分级指南",
                         "citation": {"chunkId": c["id"], "documentId": c["documentId"],
                                      "standardNo": "JR/T 0088", "clauseNo": c["clauseNo"],
                                      "pageNo": None, "chapterPath": None, "quote": None, "score": 0.93}})
        top_k = int(body.get("topK") or 5)
        data = {"query": query, "normalizedQuery": query.strip(), "retrievalMode": "vector",
                "rerank": False, "hits": hits[:top_k], "latencyMs": 120, "degraded": False, "traceId": trace}
        self.audit(user, "retrieval.query", "knowledge_base", kb_id or "", "SUCCESS", trace,
                   {"hitCount": len(data["hits"])})
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def query_understanding(self, user, trace, raw, q):
        body = self.body_json() or {}
        query = str(body.get("query") or "")
        std = re.search(r"(JR/?T\s*\d+(?:\.\d+)?)", query, re.I)
        clause = re.search(r"\b(\d+(?:\.\d+){1,3})\b", query)
        warnings = []
        if not query.strip():
            warnings.append("空查询：请提供标准编号或问题描述")
        data = {"originalQuery": query, "normalizedQuery": " ".join(query.split()),
                "standardNo": std.group(1).upper().replace("JRT", "JR/T") if std else None,
                "clauseNo": clause.group(1) if clause else None,
                "tokens": [t for t in query.split() if t], "warnings": warnings}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def _answer(self, body, trace):
        query = str(body.get("query") or "").strip()
        kb_id = body.get("kbId")
        if not query:
            return 400, error("PARAM.QUERY_EMPTY", "query 不能为空", trace)
        if len(query) > 2000:
            return 400, error("PARAM.QUERY_TOO_LONG", "query 超长（≤2000 字符）", trace)
        if re.search(r"ignore\s+(all\s+)?previous|忽略(上述|以上|之前).*指令|you\s+are\s+now", query, re.I):
            return 422, error("BIZ.PROMPT_INJECTION", "检测到 Prompt 注入，已拦截", trace)
        if body.get("capability") == "SKILL":
            return 422, error("BIZ.UNSUPPORTED_CAPABILITY", "P0 仅支持 RAG 能力", trace)
        if body.get("kbId") and body.get("kbIds"):
            return 400, error("PARAM.INVALID", "kbId 与 kbIds 互斥", trace)
        chunks = self._ready_chunks()
        if kb_id:
            chunks = [c for c in chunks if c["kbId"] == kb_id]
        elif body.get("kbIds"):
            wanted = set(body["kbIds"])
            chunks = [c for c in chunks if c["kbId"] in wanted]
        if not chunks:
            answer = {"answerId": new_id("ans"), "tenantId": TENANT, "sessionId": body.get("sessionId"), "content": "",
                      "format": "MARKDOWN", "citations": [], "refused": True,
                      "refusalReason": "知识库中未检索到可支撑该问题的条款依据。",
                      "confidence": 0.0, "usage": {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0},
                      "finishReason": "REFUSED", "retrievalMode": "vector", "rerank": False,
                      "promptTemplateVersion": "rag-answer@3", "traceId": trace, "createdAt": now()}
            return 200, envelope("OK", "refused", answer, trace)
        c = chunks[0]
        citations = [{"chunkId": x["id"], "documentId": x["documentId"], "standardNo": "JR/T 0088",
                      "standardName": "金融数据安全 数据安全分级指南", "clauseNo": x["clauseNo"],
                      "pageNo": None, "chapterPath": None, "quote": None, "score": 0.93} for x in chunks[:3]]
        content = ("根据 JR/T 0088 第 {} 条，{}。\n\n"
                   "该条款要求金融机构建立与业务规模相适应的数据安全管理机制，并实施访问控制与风险监测。"
                   ).format(c["clauseNo"], c["text"].split("：", 1)[-1])
        answer = {"answerId": new_id("ans"), "tenantId": TENANT, "sessionId": body.get("sessionId"), "content": content,
                  "format": "MARKDOWN", "citations": citations, "refused": False, "refusalReason": None,
                  "confidence": 0.91, "usage": {"promptTokens": 860, "completionTokens": 96, "totalTokens": 956},
                  "finishReason": "STOP", "retrievalMode": "vector", "rerank": False,
                  "promptTemplateVersion": "rag-answer@3", "traceId": trace, "createdAt": now()}
        return 200, envelope("OK", "success", answer, trace)

    def chat(self, user, trace, raw, q):
        if not self.require_perm(user, "chat:use", trace): return None
        if not self.headers.get("Idempotency-Key"):
            return self.send_json(400, error("PARAM.IDEMPOTENCY_KEY_MISSING", "缺少 Idempotency-Key", trace), trace)
        status, payload = self._answer(self.body_json() or {}, trace)
        self.audit(user, "chat.query", "knowledge_base", (self.body_json() or {}).get("kbId", ""),
                   "SUCCESS" if status == 200 else "FAILURE", trace)
        return self.send_json(status, payload, trace)

    def chat_stream(self, user, trace, raw, q):
        if not self.require_perm(user, "chat:use", trace): return None
        body = self.body_json() or {}
        status, payload = self._answer(body, trace)
        if status != 200:
            return self.send_json(status, payload, trace)
        answer = payload["data"]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Trace-Id", trace)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.close_connection = True

        def emit(event, obj, seq=None):
            lines = []
            if seq is not None:
                lines.append(f"id: {seq}")
            lines += [f"event: {event}", f"data: {json.dumps(obj, ensure_ascii=False)}", ""]
            self.wfile.write(("\n".join(lines) + "\n").encode("utf-8"))
            self.wfile.flush()

        emit("meta", {"answerId": answer["answerId"], "sessionId": answer.get("sessionId"),
                      "traceId": trace, "retrievalMode": "vector", "startedAt": now()})
        if answer["citations"]:
            emit("citation", {"answerId": answer["answerId"], "citations": answer["citations"]})
        text = answer["content"] or "根据现有金融标准，未检索到可支撑该问题的条款依据。"
        seq = 0
        for i in range(0, len(text), 8):
            seq += 1
            emit("delta", {"answerId": answer["answerId"], "seq": seq, "delta": text[i:i + 8]}, seq)
            time.sleep(0.03)
        emit("done", {"answerId": answer["answerId"], "finishReason": answer["finishReason"],
                      "refused": answer["refused"], "refusalReason": answer.get("refusalReason"),
                      "citations": answer["citations"], "usage": answer["usage"]})
        self.audit(user, "chat.query", "knowledge_base", body.get("kbId", ""), "SUCCESS", trace,
                   {"stream": True})

    def ingest_events(self, user, trace, raw, q, tid):
        task = STATE["tasks"].get(tid)
        if not task:
            return self.send_json(404, error("BIZ.NOT_FOUND", "任务不存在", trace), trace)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Trace-Id", trace)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.close_connection = True
        for i in range(12):
            self._advance(tid)
            ev = {"taskId": tid, "status": task["status"], "stage": task["stage"],
                  "progress": task["progress"], "at": now()}
            name = "done" if task["status"] in ("SUCCEEDED", "FAILED") else ("meta" if i == 0 else "status")
            self.wfile.write((f"event: {name}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n").encode())
            self.wfile.flush()
            if name == "done":
                break
            time.sleep(0.8)

    # ---------------------------------------------------------------- summaries / prompts
    def generate_summary(self, user, trace, raw, q, did):
        if not self.require_perm(user, "summary:generate", trace): return None
        if did not in STATE["docs"]:
            return self.send_json(404, error("BIZ.NOT_FOUND", "文档不存在", trace), trace)
        data = {"id": new_id("sum"), "tenantId": TENANT, "documentId": did, "standardNo": "JR/T 0088",
                "standardName": "金融数据安全 数据安全分级指南", "scope": "适用于金融机构数据安全分级工作。",
                "mainContent": "规定了数据安全分级、安全要求与实施流程。",
                "elements": [
                    {"key": "standard_no", "label": "标准编号", "value": "JR/T 0088", "sourceClauseNo": None, "sourceQuote": None},
                    {"key": "scope", "label": "适用范围", "value": "金融机构数据安全分级", "sourceClauseNo": "1", "sourceQuote": "本文件适用于..."},
                    {"key": "main_content", "label": "主要技术内容", "value": "数据分类分级、安全保护要求", "sourceClauseNo": "4", "sourceQuote": "..."},
                    {"key": "effective_date", "label": "实施日期", "value": "2024-06-01", "sourceClauseNo": None, "sourceQuote": None},
                ],
                "effectiveAt": "2024-06-01", "generatedAt": now(), "model": "mock-llm",
                "promptTemplateVersion": "summary@2", "confidence": 0.9}
        self.audit(user, "summary.generate", "document", did, "SUCCESS", trace)
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def latest_summary(self, user, trace, raw, q, did):
        if not self.require_perm(user, "doc:read", trace): return None
        if did not in STATE["docs"]:
            return self.send_json(404, error("BIZ.NOT_FOUND", "文档不存在", trace), trace)
        data = {"id": "sum_mock", "tenantId": TENANT, "documentId": did, "standardNo": "JR/T 0088",
                "standardName": "金融数据安全 数据安全分级指南", "scope": "适用于金融机构数据安全分级工作。",
                "mainContent": "规定了数据安全分级、安全要求与实施流程。",
                "elements": [{"key": "standard_no", "label": "标准编号", "value": "JR/T 0088",
                              "sourceClauseNo": None, "sourceQuote": None}],
                "effectiveAt": "2024-06-01", "generatedAt": now(), "model": "mock-llm",
                "promptTemplateVersion": "summary@2", "confidence": 0.9}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def prompt_templates(self, user, trace, raw, q, name=None):
        items = [{"name": "rag-answer", "version": "3", "purpose": "ANSWER",
                  "variables": ["query", "context"], "contentHash": "sha256:mock", "active": True, "updatedAt": now()},
                 {"name": "summary", "version": "2", "purpose": "SUMMARY",
                  "variables": ["document"], "contentHash": "sha256:mock2", "active": True, "updatedAt": now()}]
        if name:
            items = [x for x in items if x["name"] == name]
            if not items:
                return self.send_json(404, error("BIZ.NOT_FOUND", "模板不存在", trace), trace)
        return self.send_json(200, envelope("OK", "success", {"items": items}, trace), trace)

    # ---------------------------------------------------------------- exports / audit
    def list_exports(self, user, trace, raw, q):
        if not self.require_perm(user, "export:read", trace): return None
        return self.send_json(200, envelope("OK", "success", page(list(STATE["exports"].values()), q), trace), trace)

    def create_export(self, user, trace, raw, q):
        if not self.require_perm(user, "export:create", trace): return None
        body = self.body_json() or {}
        if body.get("documentId") not in STATE["docs"]:
            return self.send_json(404, error("BIZ.NOT_FOUND", "文档不存在", trace), trace)
        job = {"id": new_id("exp"), "tenantId": TENANT, "documentId": body["documentId"],
               "type": body.get("type", "CLAUSES"), "format": body.get("format", "PDF"),
               "status": "SUCCEEDED", "progress": 100, "fileName": "export.pdf", "sizeBytes": 20480,
               "downloadUrl": f"{API}/exports/exp_mock/download", "error": None, "finishedAt": now(),
               "createdAt": now(), "updatedAt": now()}
        STATE["exports"][job["id"]] = job
        self.audit(user, "export.create", "export", job["id"], "SUCCESS", trace)
        return self.send_json(202, envelope("OK", "accepted", job, trace), trace)

    def get_export(self, user, trace, raw, q, eid):
        if not self.require_perm(user, "export:read", trace): return None
        job = STATE["exports"].get(eid)
        if not job:
            return self.send_json(404, error("BIZ.NOT_FOUND", "导出任务不存在", trace), trace)
        return self.send_json(200, envelope("OK", "success", job, trace), trace)

    def download_export(self, user, trace, raw, q, eid):
        if not self.require_perm(user, "export:read", trace): return None
        self.audit(user, "export.download", "export", eid, "SUCCESS", trace)
        body = b"%PDF-1.4\n% AIPaaS P0 mock export\n"
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", 'attachment; filename="export.pdf"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Trace-Id", trace)
        self.end_headers()
        self.wfile.write(body)

    def audit_logs(self, user, trace, raw, q):
        if not self.require_perm(user, "audit:read", trace): return None
        if (q.get("archive") or ["false"])[0].lower() == "true":
            return self.send_json(400, error("PARAM.INVALID", "P0 不支持 archive=true", trace), trace)
        items = list(STATE["audit"])
        if q.get("action"):
            items = [a for a in items if a["action"] == q["action"][0]]
        if (q.get("format") or ["json"])[0].lower() == "csv":
            self.audit(user, "export.audit", "audit_log", "p0", "SUCCESS", trace, {"rowCount": len(items)})
            output = io.StringIO()
            writer = csv.writer(output)
            fields = ["id", "tenantId", "actorId", "actorName", "action", "objectType", "objectId",
                      "result", "ip", "userAgent", "traceId", "detail", "createdAt"]
            writer.writerow(fields)
            for item in items:
                row = []
                for field in fields:
                    value = item.get(field)
                    if field == "detail":
                        value = json.dumps(value or {}, ensure_ascii=False)
                    text = "" if value is None else str(value)
                    row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
                writer.writerow(row)
            payload = output.getvalue().encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="audit-logs.csv"')
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-Trace-Id", trace)
            self.end_headers()
            self.wfile.write(payload)
            return None
        return self.send_json(200, envelope("OK", "success", page(items, q), trace), trace)

    # ---------------------------------------------------------------- security
    def posture(self, user, trace, raw, q):
        data = {"tls": {"enabled": True, "minVersion": "TLSv1.2", "certificateExpiresAt": "2027-01-01T00:00:00Z"},
                "storage": {"encryption": True, "algorithm": "AES-256-GCM", "keyManagement": "KMS",
                            "keyRotationEnabled": True, "lastRotatedAt": "2026-10-01T00:00:00Z"},
                "checkedAt": now()}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def policy_data(self):
        if STATE["policies"] is None:
            STATE["policies"] = {
                "inputContent": {"enabled": True, "action": "BLOCK", "ruleVersion": "p0-v1", "threshold": 1.0,
                                 "rules": [{"ruleId": "input.sensitive", "ruleType": "INPUT_CONTENT", "pattern": "绝密",
                                            "matchType": "CONTAINS", "description": "敏感词样例", "enabled": True, "weight": 1.0}]},
                "outputContent": {"enabled": True, "action": "REPLACE", "ruleVersion": "p0-v1", "threshold": 1.0,
                                  "rules": [{"ruleId": "output.sensitive", "ruleType": "OUTPUT_CONTENT", "pattern": "绝密",
                                             "matchType": "CONTAINS", "description": "输出敏感词样例", "enabled": True, "weight": 1.0}]},
                "promptInjection": {"enabled": True, "action": "BLOCK", "ruleVersion": "p0-v1", "threshold": 0.8,
                                    "rules": [{"ruleId": "prompt.ignore_previous", "ruleType": "PROMPT_INJECTION",
                                               "pattern": r"ignore\s+(all\s+)?previous|忽略(上述|以上|之前).*指令",
                                               "matchType": "REGEX", "description": "覆盖既有指令", "enabled": True, "weight": 1.0}]},
                "updatedAt": now(), "updatedBy": "u_admin",
            }
        return STATE["policies"]

    def get_policies(self, user, trace, raw, q):
        if not self.require_perm(user, "security:read", trace): return None
        return self.send_json(200, envelope("OK", "success", self.policy_data(), trace), trace)

    def put_policies(self, user, trace, raw, q):
        if not self.require_perm(user, "security:write", trace): return None
        body = self.body_json()
        if not isinstance(body, dict) or not body:
            return self.send_json(400, error("PARAM.INVALID", "至少更新一项安全策略", trace), trace)
        policies = self.policy_data()
        for prop in ("inputContent", "outputContent", "promptInjection"):
            if prop in body:
                policies[prop].update(body[prop])
        policies["updatedAt"] = now()
        policies["updatedBy"] = user["userId"]
        self.audit(user, "security.policy.update", "security_policy", "p0", "SUCCESS", trace)
        return self.send_json(200, envelope("OK", "success", policies, trace), trace)

    def security_check(self, user, trace, raw, q):
        if not self.require_perm(user, "security:check", trace): return None
        body = self.body_json() or {}
        typ, content = body.get("type"), str(body.get("content") or "")
        if typ not in ("INPUT_CONTENT", "OUTPUT_CONTENT", "PROMPT_INJECTION"):
            return self.send_json(400, error("PARAM.INVALID", "type 非法", trace), trace)
        injected = bool(re.search(r"ignore\s+previous|忽略(上述|以上).*指令|jailbreak", content, re.I))
        data = {"type": typ, "passed": not injected, "action": "BLOCK" if injected else "ALLOW",
                "riskTypes": ["PROMPT_INJECTION"] if injected else [], "score": 0.95 if injected else 0.0,
                "sanitizedContent": None, "ruleVersion": "r3", "traceId": trace}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    # ---------------------------------------------------------------- observability / admin / ui / models
    def request_logs(self, user, trace, raw, q):
        if not self.require_perm(user, "obs:read", trace): return None
        data = {"items": [{"requestId": new_id("req"), "traceId": trace, "tenantId": TENANT,
                           "userId": user["userId"], "method": "GET", "path": self.path,
                           "status": 200, "latencyMs": 42, "occurredAt": now()}],
                "total": 1, "page": 1, "pageSize": 20}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def trace(self, user, trace, raw, q, tid):
        if not self.require_perm(user, "obs:read", trace): return None
        data = {"traceId": tid, "tenantId": TENANT, "userId": user["userId"], "totalLatencyMs": 1680,
                "spans": [
                    {"spanId": "s1", "parentSpanId": None, "name": "auth", "stage": "AUTH",
                     "startTime": now(), "durationMs": 12, "status": "OK", "error": None},
                    {"spanId": "s2", "parentSpanId": "s1", "name": "retrieve", "stage": "RETRIEVE",
                     "startTime": now(), "durationMs": 120, "status": "OK", "error": None},
                    {"spanId": "s3", "parentSpanId": "s1", "name": "generate", "stage": "LLM",
                     "startTime": now(), "durationMs": 1540, "status": "OK", "error": None}],
                "status": "OK"}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def metrics(self, user, trace, raw, q):
        if not self.require_perm(user, "obs:read", trace): return None
        data = {"window": (q.get("window") or ["15m"])[0], "qps": 2.4, "latencyP95Ms": 1800,
                "errorRate": 0.01, "successRate": 0.99, "cpuUsage": 0.32, "memoryUsage": 0.51,
                "collectedAt": now()}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def overview(self, user, trace, raw, q):
        if not self.require_perm(user, "admin:read", trace): return None
        data = {"kbCount": len(STATE["kb"]), "documentCount": len(STATE["docs"]),
                "chunkCount": sum(len(v) for v in STATE["chunks"].values()), "userCount": 3,
                "qps": 2.4, "latencyP95Ms": 1800, "errorRate": 0.01, "updatedAt": now()}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def list_users(self, user, trace, raw, q):
        if not self.require_perm(user, "user:read", trace): return None
        items = [{"id": "u_admin", "tenantId": TENANT, "username": "admin", "displayName": "平台管理员",
                  "email": "admin@aipaas.local", "status": "ACTIVE", "roles": ["PLATFORM_ADMIN"],
                  "lastLoginAt": now(), "createdAt": now(), "updatedAt": now()}]
        return self.send_json(200, envelope("OK", "success", page(items, q), trace), trace)

    def create_user(self, user, trace, raw, q):
        if not self.require_perm(user, "user:write", trace): return None
        body = self.body_json() or {}
        if not body.get("username") or not body.get("password"):
            return self.send_json(400, error("PARAM.INVALID", "username/password 必填", trace), trace)
        u = {"id": new_id("u"), "tenantId": TENANT, "username": body["username"],
             "displayName": body.get("displayName", body["username"]), "email": body.get("email"),
             "status": "ACTIVE", "roles": body.get("roleCodes", ["USER"]), "lastLoginAt": None,
             "createdAt": now(), "updatedAt": now()}
        self.audit(user, "user.create", "user", u["id"], "SUCCESS", trace)
        return self.send_json(201, envelope("OK", "created", u, trace), trace)

    def update_user(self, user, trace, raw, q, uid):
        if not self.require_perm(user, "user:write", trace): return None
        body = self.body_json() or {}
        u = {"id": uid, "tenantId": TENANT, "username": "user", "displayName": body.get("displayName", "user"),
             "email": body.get("email"), "status": body.get("status", "ACTIVE"), "roles": body.get("roleCodes", ["USER"]),
             "lastLoginAt": None, "createdAt": now(), "updatedAt": now()}
        self.audit(user, "user.update", "user", uid, "SUCCESS", trace)
        return self.send_json(200, envelope("OK", "success", u, trace), trace)

    def system_config(self, user, trace, raw, q):
        if not self.require_perm(user, "config:read", trace): return None
        data = {"defaultCapability": "RAG",
                "supportedFormats": ["PDF", "DOC", "DOCX", "XLS", "XLSX", "PPT", "PPTX", "MD", "TXT"],
                "maxUploadSizeBytes": 104857600, "maxQueryLength": 2000, "streamingEnabled": True,
                "retrievalTopK": 5, "updatedAt": now()}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def put_system_config(self, user, trace, raw, q):
        if not self.require_perm(user, "config:write", trace): return None
        body = self.body_json() or {}
        data = {"defaultCapability": "RAG",
                "supportedFormats": ["PDF", "DOC", "DOCX", "XLS", "XLSX", "PPT", "PPTX", "MD", "TXT"],
                "maxUploadSizeBytes": 104857600, "maxQueryLength": 2000, "streamingEnabled": True,
                "retrievalTopK": 5, "updatedAt": now()}
        for key in ("defaultCapability", "maxUploadSizeBytes", "maxQueryLength", "streamingEnabled", "retrievalTopK"):
            if key in body:
                data[key] = body[key]
        self.audit(user, "config.update", "system_config", "p0", "SUCCESS", trace)
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def capabilities(self, user, trace, raw, q):
        data = {"capabilities": [
            {"code": "RAG", "name": "金融标准问答", "enabled": True, "availableFrom": "P0", "description": "知识库检索增强问答"},
            {"code": "SKILL", "name": "技能能力", "enabled": False, "availableFrom": "P3", "description": "P3 预留"}],
            "defaultCapability": "RAG", "skillEnabled": False}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def ui_config(self, user, trace, raw, q):
        data = {"supportedFormats": ["PDF", "DOC", "DOCX", "XLS", "XLSX", "PPT", "PPTX", "MD", "TXT"],
                "maxUploadSizeBytes": 104857600, "streamingEnabled": True, "defaultCapability": "RAG",
                "maxQueryLength": 2000, "showCitationPageNo": False}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def model_providers(self, user, trace, raw, q):
        if not self.require_perm(user, "model:read", trace): return None
        data = {"items": [
            {"code": "openai-compatible", "name": "OpenAI 兼容", "models": ["gpt-4o-mini"], "status": "UP", "latencyP95Ms": 800},
            {"code": "local-vllm", "name": "本地 vLLM", "models": ["qwen2.5-72b-instruct"], "status": "UP", "latencyP95Ms": 1200},
            {"code": "embedding-bge", "name": "BGE Embedding", "models": ["bge-m3"], "status": "UP", "latencyP95Ms": 60}]}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def model_chat(self, user, trace, raw, q):
        if not self.require_perm(user, "model:invoke", trace): return None
        body = self.body_json() or {}
        if not body.get("messages"):
            return self.send_json(400, error("PARAM.INVALID", "messages 必填", trace), trace)
        self.audit(user, "model.invoke", "model", "mock", "SUCCESS", trace)
        data = {"provider": "openai-compatible", "model": "gpt-4o-mini", "content": "mock answer",
                "usage": {"promptTokens": 20, "completionTokens": 5, "totalTokens": 25}, "latencyMs": 300}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def embeddings(self, user, trace, raw, q):
        if not self.require_perm(user, "model:invoke", trace): return None
        body = self.body_json() or {}
        texts = body.get("texts") or []
        if not texts:
            return self.send_json(400, error("PARAM.INVALID", "texts 必填", trace), trace)
        self.audit(user, "model.embed", "model", body.get("model", "bge-m3"), "SUCCESS", trace)
        data = {"model": body.get("model", "bge-m3"), "dimension": 8,
                "vectors": [[round(0.01 * (i + j), 4) for j in range(8)] for i in range(len(texts))],
                "usage": {"promptTokens": 10 * len(texts), "completionTokens": 0, "totalTokens": 10 * len(texts)}}
        return self.send_json(200, envelope("OK", "success", data, trace), trace)

    def model_health(self, user, trace, raw, q):
        if not self.require_perm(user, "model:read", trace): return None
        return self.model_providers(user, trace, raw, q)

    # ---------------------------------------------------------------- misc
    def permissions(self, user, trace, raw, q):
        items = [{"code": p, "name": p, "domain": p.split(":")[0], "tenantId": TENANT} for p in ALL_PERMISSIONS]
        return self.send_json(200, envelope("OK", "success", {"items": items}, trace), trace)

    def list_roles(self, user, trace, raw, q):
        if not self.require_perm(user, "role:read", trace): return None
        items = [{"id": "role_admin", "tenantId": TENANT, "code": "PLATFORM_ADMIN", "name": "平台管理员",
                  "builtIn": True, "permissions": ALL_PERMISSIONS, "grants": [], "userCount": 1,
                  "createdAt": now(), "updatedAt": now()}]
        return self.send_json(200, envelope("OK", "success", page(items, q), trace), trace)

    def create_role(self, user, trace, raw, q):
        if not self.require_perm(user, "role:write", trace): return None
        body = self.body_json() or {}
        if not body.get("code") or not body.get("permissions"):
            return self.send_json(400, error("PARAM.INVALID", "code/permissions 必填", trace), trace)
        role = {"id": new_id("role"), "tenantId": TENANT, "code": body["code"], "name": body.get("name", body["code"]),
                "builtIn": False, "permissions": body["permissions"], "grants": body.get("grants", []),
                "userCount": 0, "createdAt": now(), "updatedAt": now()}
        STATE["roles"][role["id"]] = role
        self.audit(user, "role.create", "role", role["id"], "SUCCESS", trace)
        return self.send_json(201, envelope("OK", "created", role, trace), trace)

    def put_role_permissions(self, user, trace, raw, q, rid):
        if not self.require_perm(user, "role:write", trace): return None
        body = self.body_json() or {}
        role = STATE["roles"].get(rid) or {"id": rid, "tenantId": TENANT, "code": "CUSTOM", "name": rid,
                                           "builtIn": False, "permissions": [], "grants": [], "userCount": 0,
                                           "createdAt": now(), "updatedAt": now()}
        role["permissions"] = body.get("permissions", role["permissions"])
        role["grants"] = body.get("grants", role["grants"])
        role["updatedAt"] = now()
        STATE["roles"][rid] = role
        self.audit(user, "role.permission.update", "role", rid, "SUCCESS", trace)
        return self.send_json(200, envelope("OK", "success", role, trace), trace)

    def reparse(self, user, trace, raw, q, did):
        if not self.require_perm(user, "doc:write", trace): return None
        doc = STATE["docs"].get(did)
        if not doc:
            return self.send_json(404, error("BIZ.NOT_FOUND", "文档不存在", trace), trace)
        tid = new_id("task")
        ts = now()
        task = {"id": tid, "tenantId": TENANT, "documentId": did, "kbId": doc["kbId"], "status": "QUEUED",
                "stage": "UPLOAD", "progress": 0, "retryCount": doc.get("retryCount", 0) + 1,
                "maxRetries": 2, "error": None, "queuedAt": ts, "startedAt": None, "finishedAt": None,
                "createdAt": ts, "updatedAt": ts, "_ts": time.time()}
        STATE["tasks"][tid] = task
        doc["status"] = "QUEUED"
        doc["lastTaskId"] = tid
        self.audit(user, "doc.reparse", "document", did, "SUCCESS", trace)
        return self.send_json(202, envelope("OK", "accepted", self.public(task), trace), trace)


ROUTES = [
    (re.compile(r"/auth/logout"), {"POST"}, Handler.logout),
    (re.compile(r"/auth/me"), {"GET"}, Handler.me),
    (re.compile(r"/system/info"), {"GET"}, lambda s, u, t, r, q: s.send_json(200, envelope("OK", "success", {
        "service": "mock-server", "version": "0.1.0", "apiVersion": "v1", "contractVersion": "1.1.0-p0",
        "contractStatus": "frozen", "env": "mock", "p0FeatureCount": 44,
        "domains": ["数据接入与解析", "知识构建与索引", "检索引擎", "生成与推理", "知识治理",
                    "安全与合规", "可观测与质量评估", "平台与管理", "用户界面与体验", "模型服务"],
        "capabilities": ["RAG"], "startedAt": now()}, t), t)),
    (re.compile(r"/permissions"), {"GET"}, Handler.permissions),
    (re.compile(r"/roles"), {"GET", "POST"}, lambda s, u, t, r, q: s.list_roles(u, t, r, q) if s.command == "GET" else s.create_role(u, t, r, q)),
    (re.compile(r"/roles/([^/]+)/permissions"), {"PUT"}, Handler.put_role_permissions),

    (re.compile(r"/knowledge-bases"), {"GET", "POST"}, lambda s, u, t, r, q: s.list_kb(u, t, r, q) if s.command == "GET" else s.create_kb(u, t, r, q)),
    (re.compile(r"/knowledge-bases/([^/]+)/documents/uploads:init"), {"POST"}, Handler.init_upload),
    (re.compile(r"/knowledge-bases/([^/]+)/documents/uploads/([^/]+)/parts/([^/]+)"), {"PUT"}, Handler.upload_part),
    (re.compile(r"/knowledge-bases/([^/]+)/documents/uploads/([^/]+):complete"), {"POST"}, Handler.complete_upload),
    (re.compile(r"/knowledge-bases/([^/]+)/documents/uploads/([^/]+)"), {"GET"}, Handler.upload_status),
    (re.compile(r"/knowledge-bases/([^/]+)/documents"), {"POST"}, Handler.upload),
    (re.compile(r"/knowledge-bases/([^/]+)/index-status"), {"GET"}, Handler.index_status),
    (re.compile(r"/knowledge-bases/([^/]+)"), {"GET", "PATCH", "DELETE"},
     lambda s, u, t, r, q, kid: {"GET": s.get_kb, "PATCH": s.patch_kb, "DELETE": s.delete_kb}[s.command](u, t, r, q, kid)),

    (re.compile(r"/documents"), {"GET"}, Handler.list_docs),
    (re.compile(r"/documents/([^/]+)/parse-result"), {"GET"}, Handler.parse_result),
    (re.compile(r"/documents/([^/]+)/cleaning-report"), {"GET"}, Handler.cleaning_report),
    (re.compile(r"/documents/([^/]+)/metadata"), {"GET", "PUT"}, lambda s, u, t, r, q, did: s.get_meta(u, t, r, q, did) if s.command == "GET" else s.put_meta(u, t, r, q, did)),
    (re.compile(r"/documents/([^/]+)/chunks:preview"), {"POST"}, Handler.chunk_preview),
    (re.compile(r"/documents/([^/]+)/chunks"), {"GET"}, Handler.chunks),
    (re.compile(r"/documents/([^/]+)/clauses"), {"GET"}, Handler.clauses),
    (re.compile(r"/documents/([^/]+)/summaries/latest"), {"GET"}, Handler.latest_summary),
    (re.compile(r"/documents/([^/]+)/summaries"), {"POST"}, Handler.generate_summary),
    (re.compile(r"/documents/([^/]+):reparse"), {"POST"}, Handler.reparse),
    (re.compile(r"/documents/([^/]+)"), {"GET", "DELETE"}, lambda s, u, t, r, q, did: s.get_doc(u, t, r, q, did) if s.command == "GET" else s.delete_doc(u, t, r, q, did)),

    (re.compile(r"/ingest/tasks/([^/]+):retry"), {"POST"}, Handler.retry_task),
    (re.compile(r"/ingest/tasks/([^/]+)/events"), {"GET"}, Handler.ingest_events),
    (re.compile(r"/ingest/tasks/([^/]+)"), {"GET"}, Handler.get_task),
    (re.compile(r"/ingest/tasks"), {"GET"}, Handler.list_tasks),

    (re.compile(r"/index/embedding-jobs/([^/]+):pause"), {"POST"}, lambda s, u, t, r, q, jid: s.job_action(u, t, r, q, jid, "pause")),
    (re.compile(r"/index/embedding-jobs/([^/]+):resume"), {"POST"}, lambda s, u, t, r, q, jid: s.job_action(u, t, r, q, jid, "resume")),
    (re.compile(r"/index/embedding-jobs/([^/]+)"), {"GET"}, Handler.get_embedding_job),
    (re.compile(r"/index/embedding-jobs"), {"POST"}, Handler.create_embedding_job),
    (re.compile(r"/index/builds/([^/]+):retry"), {"POST"}, Handler.retry_index_build),
    (re.compile(r"/index/builds/([^/]+)"), {"GET"}, Handler.get_index_build),
    (re.compile(r"/index/builds"), {"POST"}, Handler.create_index_build),

    (re.compile(r"/retrieve/query-understanding"), {"POST"}, Handler.query_understanding),
    (re.compile(r"/retrieve"), {"POST"}, Handler.retrieve),
    (re.compile(r"/chat/stream"), {"POST"}, Handler.chat_stream),
    (re.compile(r"/chat"), {"POST"}, Handler.chat),
    (re.compile(r"/prompts/templates/([^/]+)/versions"), {"GET"}, lambda s, u, t, r, q, name: s.prompt_templates(u, t, r, q, name)),
    (re.compile(r"/prompts/templates"), {"GET"}, Handler.prompt_templates),

    (re.compile(r"/exports/([^/]+)/download"), {"GET"}, Handler.download_export),
    (re.compile(r"/exports/([^/]+)"), {"GET"}, Handler.get_export),
    (re.compile(r"/exports"), {"GET", "POST"}, lambda s, u, t, r, q: s.list_exports(u, t, r, q) if s.command == "GET" else s.create_export(u, t, r, q)),
    (re.compile(r"/audit-logs"), {"GET"}, Handler.audit_logs),

    (re.compile(r"/security/posture"), {"GET"}, Handler.posture),
    (re.compile(r"/security/policies"), {"GET", "PUT"}, lambda s, u, t, r, q: s.get_policies(u, t, r, q) if s.command == "GET" else s.put_policies(u, t, r, q)),
    (re.compile(r"/security/checks"), {"POST"}, Handler.security_check),

    (re.compile(r"/observability/request-logs"), {"GET"}, Handler.request_logs),
    (re.compile(r"/observability/traces/([^/]+)"), {"GET"}, Handler.trace),
    (re.compile(r"/observability/metrics/summary"), {"GET"}, Handler.metrics),

    (re.compile(r"/admin/users/([^/]+)"), {"PATCH"}, Handler.update_user),
    (re.compile(r"/admin/users"), {"GET", "POST"}, lambda s, u, t, r, q: s.list_users(u, t, r, q) if s.command == "GET" else s.create_user(u, t, r, q)),
    (re.compile(r"/admin/system-config"), {"GET", "PUT"}, lambda s, u, t, r, q: s.system_config(u, t, r, q) if s.command == "GET" else s.put_system_config(u, t, r, q)),
    (re.compile(r"/admin/overview"), {"GET"}, Handler.overview),

    (re.compile(r"/ui/capabilities"), {"GET"}, Handler.capabilities),
    (re.compile(r"/ui/config"), {"GET"}, Handler.ui_config),

    (re.compile(r"/models/providers"), {"GET"}, Handler.model_providers),
    (re.compile(r"/models/chat"), {"POST"}, Handler.model_chat),
    (re.compile(r"/models/embeddings"), {"POST"}, Handler.embeddings),
    (re.compile(r"/models/health"), {"GET"}, Handler.model_health),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=4010)
    args = ap.parse_args()
    # 默认演示数据
    ts = now()
    STATE["kb"]["kb_demo"] = {
        "id": "kb_demo", "tenantId": TENANT, "name": "金融标准知识库（演示）",
        "description": "Mock 预置知识库", "domain": None, "status": "ACTIVE",
        "documentCount": 0, "chunkCount": 0, "indexStatus": "EMPTY", "version": None, "env": None,
        "createdAt": ts, "updatedAt": ts,
    }
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"AIPaaS mock server listening on http://{args.host}:{args.port} (base {API})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
