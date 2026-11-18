#!/usr/bin/env python3
"""P0 契约串联冒烟：登录 → 建库 → 上传 → 解析任务 → 分块 → 问答（含 SSE）。

仅依赖标准库：
    python3 aipaas-contracts/mock/smoke_upload_qa.py --base-url http://127.0.0.1:4010
退出码 0 表示全链路通过。
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid

BOUNDARY = "----aipaas" + uuid.uuid4().hex
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" :: {detail}" if detail and not cond else ""))


def call(base, method, path, token=None, body=None, headers=None, raw=False):
    url = base.rstrip("/") + path
    data = None
    hdrs = {"Accept": "application/json"}
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode()
            hdrs["Content-Type"] = "application/json"
        else:
            data = body
    hdrs.update(headers or {})
    if token:
        hdrs["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read()
            return resp.status, (payload if raw else json.loads(payload or b"{}")), dict(resp.headers)
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            parsed = payload if raw else json.loads(payload or b"{}")
        except Exception:
            parsed = payload
        return e.code, parsed, dict(e.headers)


def upload(base, token, kb_id):
    content = "%PDF-1.4\n% AIPaaS smoke test standard document\n1 范围\n4.1.1 数据安全总则\n".encode("utf-8")
    body = (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="JR-T-0088-smoke.pdf"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode() + content + (
        f"\r\n--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="standardNo"\r\n\r\n'
        "JR/T 0088\r\n"
        f"--{BOUNDARY}--\r\n"
    ).encode()
    return call(base, "POST", f"/api/v1/knowledge-bases/{kb_id}/documents", token, body, {
        "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
        "Idempotency-Key": "smoke-upload-" + uuid.uuid4().hex,
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:4010")
    args = ap.parse_args()
    base = args.base_url

    st, health, _ = call(base, "GET", "/healthz")
    check("healthz=200", st == 200 and health.get("status") == "UP", str(health))
    st, ready, _ = call(base, "GET", "/readyz")
    check("readyz=200", st == 200, str(ready))

    st, login, _ = call(base, "POST", "/api/v1/auth/login", body={
        "username": "admin", "password": "admin12345"})
    check("login=200", st == 200 and login["code"] == "OK", str(login))
    token = login.get("data", {}).get("accessToken")
    refresh_token = login.get("data", {}).get("refreshToken")
    check("token pair present", bool(token) and bool(refresh_token))

    st, refreshed, _ = call(base, "POST", "/api/v1/auth/refresh", body={"refreshToken": refresh_token})
    new_refresh = (refreshed.get("data") or {}).get("refreshToken")
    check("refresh rotates token pair", st == 200 and bool(new_refresh) and new_refresh != refresh_token, str(refreshed))
    token = (refreshed.get("data") or {}).get("accessToken") or token
    st, replay, _ = call(base, "POST", "/api/v1/auth/refresh", body={"refreshToken": refresh_token})
    check("old refresh token replay=401", st == 401, str(replay))

    st, kb, _ = call(base, "POST", "/api/v1/knowledge-bases", token, {
        "name": "冒烟知识库 " + uuid.uuid4().hex[:6]})
    check("create-kb=201", st == 201 and kb["code"] == "OK", str(kb))
    kb_id = kb.get("data", {}).get("id")

    st, up, _ = upload(base, token, kb_id)
    check("upload=202", st == 202, str(up))
    doc = up.get("data", {}).get("document", {})
    task = up.get("data", {}).get("ingestTask", {})
    check("upload returns document+task", bool(doc.get("id")) and bool(task.get("id")))
    task_id = task.get("id")

    deadline = time.time() + 20
    status = None
    while time.time() < deadline:
        st, t, _ = call(base, "GET", f"/api/v1/ingest/tasks/{task_id}", token)
        status = (t.get("data") or {}).get("status")
        if status in ("SUCCEEDED", "FAILED"):
            break
        time.sleep(0.7)
    check("ingest task SUCCEEDED", status == "SUCCEEDED", f"last={status}")
    check("no internal fields leaked", "_ts" not in (t.get("data") or {}))

    st, chunks, _ = call(base, "GET", f"/api/v1/documents/{doc['id']}/chunks?page=1&pageSize=10", token)
    check("chunks=200", st == 200 and chunks["data"]["total"] >= 1, str(chunks))

    st, ans, _ = call(base, "POST", "/api/v1/chat", token, {
        "query": "JR/T 0088 对数据安全机制有什么要求？", "kbId": kb_id, "topK": 5,
    }, {"Idempotency-Key": "smoke-chat-" + uuid.uuid4().hex})
    check("chat=200", st == 200 and ans["code"] == "OK", str(ans))
    answer = ans.get("data") or {}
    check("answer not refused", answer.get("refused") is False, str(answer.get("refusalReason")))
    check("answer has citation", len(answer.get("citations") or []) >= 1)
    check("citation has clauseNo", bool((answer.get("citations") or [{}])[0].get("clauseNo")))

    # SSE 串联
    st, stream_text, hdrs = call(base, "POST", "/api/v1/chat/stream", token, {
        "query": "数据安全分级要求是什么？", "kbId": kb_id,
    }, {"Idempotency-Key": "smoke-stream-" + uuid.uuid4().hex}, raw=True)
    text = stream_text.decode("utf-8", "replace") if isinstance(stream_text, bytes) else str(stream_text)
    check("chat/stream=200", st == 200, text[:200])
    check("SSE meta event", "event: meta" in text)
    check("SSE delta event", "event: delta" in text)
    check("SSE done event", "event: done" in text)

    # 越权：viewer 上传应 403（P0 #252）
    st, vlogin, _ = call(base, "POST", "/api/v1/auth/login", body={
        "username": "viewer", "password": "viewer12345"})
    vtoken = (vlogin.get("data") or {}).get("accessToken")
    st, denied, _ = upload(base, vtoken, kb_id)
    check("viewer upload=403", st == 403, str(denied))
    check("denied error code PERM.*", str((denied or {}).get("code", "")).startswith("PERM."), str(denied))

    st, audits, _ = call(base, "GET", "/api/v1/audit-logs?page=1&pageSize=20", token)
    actions = [a["action"] for a in (audits.get("data") or {}).get("items", [])]
    check("audit has doc.upload", "doc.upload" in actions, str(actions))
    check("audit has chat.query", "chat.query" in actions, str(actions))
    check("audit has permission.denied", "permission.denied" in actions, str(actions))

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
