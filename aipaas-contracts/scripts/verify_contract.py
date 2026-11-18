#!/usr/bin/env python3
"""P0 契约校验脚本（零第三方依赖之外仅需 PyYAML）。

校验项：
1. OpenAPI 版本与契约元信息（3.1.0 / frozen / 44 项）
2. YAML 重复键（严格模式）
3. `$ref` 引用完整性
4. 统一规范：BasePath /api/v1、统一信封、分页参数、幂等键、Bearer 安全方案
5. 写接口必须声明 x-permission 与 x-audit
6. P0 功能编号覆盖（与功能清单 44 项逐一比对）

用法：
    python3 aipaas-contracts/scripts/verify_contract.py
    python3 aipaas-contracts/scripts/verify_contract.py --file path/to/openapi.yaml
"""

from __future__ import annotations

import argparse
import re
import sys

import yaml

# 《企业级RAG系统功能清单》P0 共 44 项
P0_FEATURES = {
    1, 10, 14, 18, 19, 20, 21, 36, 37, 44, 49, 57, 63, 70, 88, 98, 103, 106,
    108, 110, 113, 114, 130, 134, 139, 144, 153, 154, 156, 157, 159, 166, 173,
    175, 177, 193, 242, 246, 252, 402, 404, 405, 406, 407,
}

METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


class StrictLoader(yaml.SafeLoader):
    """检测重复键的 SafeLoader。"""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise ValueError(f"重复键: {key!r} (line {key_node.start_mark.line + 1})")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def iter_refs(node, path="$"):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                yield path, v
            else:
                yield from iter_refs(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from iter_refs(v, f"{path}[{i}]")


def resolve_ref(doc, ref: str):
    if not ref.startswith("#/"):
        return False
    cur = doc
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--file",
        default="aipaas-contracts/openapi/aipaas-openapi-v1.yaml",
        help="OpenAPI 文件路径",
    )
    args = ap.parse_args()

    errors: list[str] = []
    warnings: list[str] = []

    with open(args.file, encoding="utf-8") as f:
        try:
            doc = yaml.load(f, Loader=StrictLoader)  # noqa: S506 - StrictLoader 继承 SafeLoader
        except yaml.YAMLError as e:
            print(f"[FAIL] YAML 解析失败: {e}")
            return 1
        except ValueError as e:
            print(f"[FAIL] {e}")
            return 1

    # 1. 元信息
    if doc.get("openapi") != "3.1.0":
        errors.append(f"openapi 版本应为 3.1.0，实际 {doc.get('openapi')}")
    info = doc.get("info", {})
    if info.get("x-contract-status") != "frozen":
        warnings.append("info.x-contract-status 非 frozen")
    if info.get("x-p0-feature-count") != len(P0_FEATURES):
        errors.append("info.x-p0-feature-count 与 44 不符")
    policy = info.get("x-compatibility-policy") or {}
    if policy.get("breaking-change-notice-versions", 0) < 2:
        errors.append("兼容策略要求破坏性变更提前 ≥2 个次版本告警")

    # 2. $ref 完整性
    bad_refs = [(p, r) for p, r in iter_refs(doc) if not resolve_ref(doc, r)]
    for p, r in bad_refs:
        errors.append(f"坏引用 {r} @ {p}")

    # 3. 统一规范
    servers = doc.get("servers") or []
    if not servers or not all("/api/v1" in s.get("url", "") for s in servers):
        errors.append("servers 必须全部包含 BasePath /api/v1")
    if not any("bearerAuth" in (s or {}) for s in (doc.get("security") or [])):
        errors.append("全局 security 必须声明 bearerAuth")
    schemes = (doc.get("components") or {}).get("securitySchemes") or {}
    if "bearerAuth" not in schemes:
        errors.append("components.securitySchemes 缺少 bearerAuth")
    params = (doc.get("components") or {}).get("parameters") or {}
    for required in ("Page", "PageSize", "IdempotencyKey"):
        if required not in params:
            errors.append(f"components.parameters 缺少 {required}")
    schemas = (doc.get("components") or {}).get("schemas") or {}
    envelope = schemas.get("ApiEnvelope") or {}
    env_props = set((envelope.get("properties") or {}).keys())
    if not {"code", "message", "data", "traceId"} <= env_props:
        errors.append("ApiEnvelope 必须包含 code/message/data/traceId")

    # 4/5. 逐 operation 校验
    infra_paths = {"/healthz", "/readyz"}
    covered: set[int] = set()
    op_count = 0
    write_count = 0
    for path, item in (doc.get("paths") or {}).items():
        for method, op in item.items():
            if method not in METHODS:
                continue
            op_count += 1
            feats = op.get("x-p0-feature")
            if feats is None and path not in infra_paths:
                errors.append(f"{method.upper()} {path} 缺少 x-p0-feature")
            elif feats:
                for f in feats:
                    m = re.fullmatch(r"#?(\d+)", str(f).strip())
                    if m:
                        covered.add(int(m.group(1)))
                    else:
                        errors.append(f"{method.upper()} {path} 非法功能编号 {f!r}")
            if method in ("post", "put", "patch", "delete"):
                write_count += 1
                if op.get("x-permission") is None:
                    errors.append(f"{method.upper()} {path} 缺少 x-permission")
                audit = op.get("x-audit")
                if not isinstance(audit, dict) or "required" not in audit:
                    errors.append(f"{method.upper()} {path} 缺少 x-audit")
                elif audit.get("required") is False and not audit.get("reason"):
                    errors.append(f"{method.upper()} {path} x-audit.required=false 需说明 reason")

    missing = sorted(P0_FEATURES - covered)
    if missing:
        errors.append(f"P0 功能未被任何接口覆盖: {missing}")

    print(f"文件: {args.file}")
    print(f"契约版本: {info.get('version')}  状态: {info.get('x-contract-status')}")
    print(f"path: {len(doc.get('paths') or {})}  operation: {op_count}  写操作: {write_count}")
    print(f"schema: {len(schemas)}  $ref: {sum(1 for _ in iter_refs(doc))}  坏引用: {len(bad_refs)}")
    print(f"P0 功能覆盖: {len(covered & P0_FEATURES)}/{len(P0_FEATURES)}")
    for w in warnings:
        print(f"[WARN] {w}")
    for e in errors:
        print(f"[FAIL] {e}")
    print("结果:", "PASS" if not errors else f"FAIL ({len(errors)} 项)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
