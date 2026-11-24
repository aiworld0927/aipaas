#!/usr/bin/env bash
# 按真实调用顺序演示 app-ingest-worker 四个 router 的全部公开接口。
#
# 终端 1：启动不带 Nacos 的本地演示服务（不会修改正式 app.py）
#   cd /coding/aipaas/pymodules
#   .venv/bin/python apps/app-ingest-worker/tests/standalone_router_server.py
#
# 终端 2：执行本脚本
#   cd /coding/aipaas/pymodules
#   apps/app-ingest-worker/tests/curl_routers_demo.sh
#
# 也可测试其他已经挂载这四个 router 的服务：
#   BASE_URL=http://127.0.0.1:8001 apps/app-ingest-worker/tests/curl_routers_demo.sh
#
# 可调变量：BASE_URL、DOC_ID、KB_ID、TENANT_ID、PYTHON。
# 依赖：bash、curl、python3（仅用于格式化和读取 JSON，不需要 jq）。

set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:18081}"
BASE_URL="${BASE_URL%/}"
DOC_ID="${DOC_ID:-curl-doc-001}"
KB_ID="${KB_ID:-curl-kb-001}"
TENANT_ID="${TENANT_ID:-tenant-a}"
PYTHON="${PYTHON:-python3}"

command -v curl >/dev/null 2>&1 || { echo "缺少命令：curl" >&2; exit 1; }
command -v "$PYTHON" >/dev/null 2>&1 || { echo "缺少 Python：$PYTHON" >&2; exit 1; }

SAMPLE_FILE="$(mktemp)"
BODY_FILE="$(mktemp)"
OPENAPI_FILE="$(mktemp)"
trap 'rm -f "$SAMPLE_FILE" "$BODY_FILE" "$OPENAPI_FILE"' EXIT

cat >"$SAMPLE_FILE" <<'MARKDOWN'
# GB/T 22239-2019 信息安全技术 网络安全等级保护基本要求

## 1 范围

本标准规定了网络安全等级保护的第一级到第四级等级保护对象的安全通用要求和安全扩展要求。

## 2 规范性引用文件

下列文件对于本文件的应用是必不可少的。

## 3 安全物理环境

机房应选择在具有防震、防风和防雨等能力的建筑内。
MARKDOWN

TOTAL=0
FAILED=0
HTTP_STATUS=""

print_body() {
    "$PYTHON" - "$BODY_FILE" <<'PY'
import json
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
try:
    print(json.dumps(json.loads(text), ensure_ascii=False, indent=2))
except (json.JSONDecodeError, UnicodeDecodeError):
    print(text)
PY
}

# call <预期HTTP状态，可逗号分隔> <方法> <路径> <说明> [curl 参数...]
call() {
    local expected="$1"
    local method="$2"
    local path="$3"
    local title="$4"
    shift 4

    TOTAL=$((TOTAL + 1))
    printf '\n============================================================\n'
    printf '%s\n' "$title"
    printf '命令:'
    printf ' %q' curl -X "$method" "$BASE_URL$path" "$@"
    printf '\n'

    if ! HTTP_STATUS="$(curl -sS --connect-timeout 5 --max-time 120 \
        -o "$BODY_FILE" -w '%{http_code}' -X "$method" "$BASE_URL$path" "$@")"; then
        echo "[FAIL] curl 请求失败"
        FAILED=$((FAILED + 1))
        return 0
    fi

    printf 'HTTP %s\n' "$HTTP_STATUS"
    print_body
    if [[ ",$expected," == *",$HTTP_STATUS,"* ]]; then
        echo "[PASS] 状态码符合预期：$expected"
    else
        echo "[FAIL] 预期状态码 $expected，实际 $HTTP_STATUS"
        FAILED=$((FAILED + 1))
    fi
}

printf '被测服务：%s\n文档 ID：%s\n租户 ID：%s\n' "$BASE_URL" "$DOC_ID" "$TENANT_ID"

# 预检避免在正式 app.py 仍注释 router 时连续出现 26 个 404。
if ! curl -fsS --connect-timeout 5 --max-time 15 "$BASE_URL/openapi.json" -o "$OPENAPI_FILE"; then
    echo "无法连接服务或读取 /openapi.json，请先启动演示服务。" >&2
    exit 1
fi
if ! "$PYTHON" - "$OPENAPI_FILE" <<'PY'
import json
import sys

paths = json.load(open(sys.argv[1], encoding="utf-8"))["paths"]
required = {
    "/api/v1/documents/{document_id}:parse",
    "/api/v1/documents/{document_id}/metadata",
    "/api/v1/index/embedding-jobs",
    "/api/v1/documents/{document_id}/summaries",
}
missing = required - paths.keys()
if missing:
    print("服务未挂载所需 router：" + ", ".join(sorted(missing)), file=sys.stderr)
    raise SystemExit(1)
PY
then
    echo "请使用 standalone_router_server.py，或在其他测试服务中启用四个 router。" >&2
    exit 1
fi
echo "[PASS] 服务在线，四个 router 均已挂载。"

# ───────────────────────────────
# 一、parse.router：文档解析、清洗、分块
# ───────────────────────────────
call 200 POST "/api/v1/documents/$DOC_ID:parse" \
    "1. 上传 Markdown，同步解析、清洗并生成正式 chunks" \
    -F "file=@$SAMPLE_FILE;filename=sample.md;type=text/markdown" \
    -F "tenantId=$TENANT_ID" \
    -F "standardNo=GB/T 22239-2019" \
    -F "cleanEnabled=true" \
    -F "chunkStrategy=structured"

call 200 GET "/api/v1/documents/$DOC_ID/parse-result" \
    "2. 查询最新解析产物（章节、条款、表格、告警、版本号）"
call 200 GET "/api/v1/documents/$DOC_ID/parse-result/versions" \
    "3. 查询历史解析版本；重复解析会新增版本"
call 200 GET "/api/v1/documents/$DOC_ID/parse-result/clean" \
    "4. 查询清洗后的中间结构，不覆盖原始解析产物"
call 200 GET "/api/v1/documents/$DOC_ID/parse-result/clean-diff" \
    "5. 查看清洗规则、字段变化与保护统计"
call 200 POST "/api/v1/documents/$DOC_ID:chunk-preview" \
    "6. 使用不同参数预览分块；只预览，不覆盖正式 chunks" \
    -H "Content-Type: application/json" \
    --data-binary '{"strategy":"fixed","size":300,"overlap":50,"sentenceAligned":true,"limit":5}'
call 200 GET "/api/v1/documents/$DOC_ID/chunks" \
    "7. 查询当前正式 chunks（含 hash、token 数与条款锚点）"
call 200 GET "/api/v1/documents/$DOC_ID/chunk-config" \
    "8. 查询正式分块使用的参数和配置版本"
call 200 GET "/api/v1/documents/$DOC_ID/clauses?tenant_id=$TENANT_ID&page=1&page_size=20" \
    "9. 同租户查询条款清单，可用于导出和溯源"
call 403 GET "/api/v1/documents/$DOC_ID/clauses?tenant_id=tenant-b" \
    "10. 换租户访问同一条款清单，应被 403 拒绝"
call 404 GET "/api/v1/documents/never-parsed/parse-result" \
    "11. 查询不存在的解析结果，应返回 404"

# ───────────────────────────────
# 二、metadata.router：标准元数据、人工修正与审计
# ───────────────────────────────
call 200 GET "/api/v1/documents/$DOC_ID/metadata" \
    "12. 查询自动抽取的标准元数据"
call 200 PUT "/api/v1/documents/$DOC_ID/metadata" \
    "13. 人工修正元数据；X-User-Id 会写入审计记录" \
    -H "Content-Type: application/json" \
    -H "X-User-Id: curl-demo-user" \
    --data-binary '{"standardNo":"GB/T 22239-2019","standardName":"信息安全技术 网络安全等级保护基本要求","issuer":"国家市场监督管理总局","domain":"网络安全","securityLevel":"public","confidence":0.99}'
call 200 GET "/api/v1/documents/$DOC_ID/metadata/audit" \
    "14. 查询自动标注和人工修正的审计轨迹"
call 200 GET "/api/v1/metadata:query?standardNo=GB%2FT%2022239-2019&limit=10" \
    "15. 按完整标准号精确过滤元数据；还可使用 issuer/effectiveFrom/effectiveTo/keyword/domain"

# ───────────────────────────────
# 三、embedding.router：chunks 批量向量化
# ───────────────────────────────
call 202 POST "/api/v1/index/embedding-jobs" \
    "16. 为指定文档的 chunks 创建向量化任务；响应 data.id 是任务 ID" \
    -H "Content-Type: application/json" \
    --data-binary "{\"kbId\":\"$KB_ID\",\"documentIds\":[\"$DOC_ID\"]}"

JOB_ID="$("$PYTHON" - "$BODY_FILE" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(payload.get("data", {}).get("id", ""))
PY
)"
if [[ -z "$JOB_ID" ]]; then
    echo "无法从创建任务响应中读取 data.id，停止后续测试。" >&2
    exit 1
fi
echo "后续任务接口使用 JOB_ID=$JOB_ID"

call 200 GET "/api/v1/index/embedding-jobs/$JOB_ID" \
    "17. 查询任务进度、成功/失败数及失败 chunk 定位"
call 200 GET "/api/v1/index/embedding-jobs" \
    "18. 查询向量化任务列表"

# 演示文档很小，任务往往已完成，因此暂停/恢复/重试返回 409 也是正确行为。
call 200,409 POST "/api/v1/index/embedding-jobs/$JOB_ID:pause" \
    "19. 暂停运行中的任务；已完成时返回 409"
call 200,409 POST "/api/v1/index/embedding-jobs/$JOB_ID:resume" \
    "20. 恢复已暂停任务；当前状态不允许时返回 409"
call 200,409 POST "/api/v1/index/embedding-jobs/$JOB_ID:retry" \
    "21. 重试失败 chunks；没有失败项或状态不允许时返回 409"
call 200 GET "/api/v1/index/embedding-jobs/$JOB_ID/vectors:verify" \
    "22. 校验向量模型、维度及异常向量清单"
call 404 POST "/api/v1/index/embedding-jobs" \
    "23. 显式指定不存在且没有 chunks 的文档，应返回 404" \
    -H "Content-Type: application/json" \
    --data-binary "{\"kbId\":\"$KB_ID\",\"documentIds\":[\"no-such-document\"]}"

# ───────────────────────────────
# 四、summary.router：标准简介生成与追溯
# ───────────────────────────────
call 200 POST "/api/v1/documents/$DOC_ID/summaries" \
    "24. 从条款和元数据生成标准简介；regenerate=true 强制生成新版本" \
    -H "Content-Type: application/json" \
    -H "X-Tenant-Id: $TENANT_ID" \
    --data-binary '{"regenerate":true}'
call 200 GET "/api/v1/documents/$DOC_ID/summaries/latest" \
    "25. 查询最新标准简介" \
    -H "X-Tenant-Id: $TENANT_ID"
call 404 GET "/api/v1/documents/never-summarized/summaries/latest" \
    "26. 查询不存在文档的简介，应返回 404" \
    -H "X-Tenant-Id: $TENANT_ID"

printf '\n============================================================\n'
printf '测试结束：共 %d 个请求，失败 %d 个。\n' "$TOTAL" "$FAILED"
if ((FAILED > 0)); then
    exit 1
fi
echo "全部功能点通过。"
