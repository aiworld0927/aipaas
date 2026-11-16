#!/usr/bin/env python
"""Nacos 客户端测试脚本：读取注册信息，并按注册的接口清单发起调用。

依赖 `packages/nacos` 同款的 Nacos Python SDK（v2 异步 API），所有命令都是：
1. 连接 Nacos，按服务名拉取实例；
2. 从实例 metadata 中解析 `interface.*` 接口清单；
3. 用注册的 `ip:port` + 接口路径发起真实 HTTP 调用。

用法（在 pymodules/ 目录执行）：

    # 查看 Nacos 中注册的服务、实例与接口清单
    uv run python tests/15-test-nacos-client.py list

    # 取 svc-ingest 的接口清单 → 调用 POST /upload → 轮询 GET /status/{task_id}
    uv run python tests/15-test-nacos-client.py ingest --file ./doc/demo.md --wait 30

    # 取 svc-query 的接口清单 → 调用 POST /query（非流式，直接拿答案）
    uv run python tests/15-test-nacos-client.py query --query "金融标准知识库支持哪些业务？"

    # 流式：POST /query 拿 session_id，再订阅 GET /stream/{session_id}
    uv run python tests/15-test-nacos-client.py query --query "..." --stream

连接参数均可用环境变量覆盖（默认值适配本机 docker-compose 部署）：
    NACOS_SERVER_ADDR=127.0.0.1:8848  NACOS_NAMESPACE=public
    NACOS_GROUP=DEFAULT_GROUP         NACOS_USERNAME=nacos  NACOS_PASSWORD=nacos
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
from v2.nacos import (
    ClientConfigBuilder,
    GRPCConfig,
    ListInstanceParam,
    ListServiceParam,
    NacosNamingService,
)

DEFAULT_ADDRESS = os.getenv("NACOS_SERVER_ADDR", "127.0.0.1:8848")
DEFAULT_NAMESPACE = os.getenv("NACOS_NAMESPACE", "public")
DEFAULT_GROUP = os.getenv("NACOS_GROUP", "DEFAULT_GROUP")
DEFAULT_USERNAME = os.getenv("NACOS_USERNAME", "nacos")
DEFAULT_PASSWORD = os.getenv("NACOS_PASSWORD", "nacos")


async def create_naming(args: argparse.Namespace) -> NacosNamingService:
    """创建命名服务客户端（与 aipaas_nacos.registry 的构建方式一致）。"""
    log_dir = Path(tempfile.gettempdir()) / "aipaas-nacos-client"
    builder = (
        ClientConfigBuilder()
        .server_address(args.nacos_address)
        .namespace_id(args.namespace)
        .log_level("WARNING")
        .log_dir(str(log_dir))
        .cache_dir(str(log_dir / "cache"))
        .grpc_config(GRPCConfig(grpc_timeout=5000))
    )
    if args.username:
        builder.username(args.username)
    if args.password:
        builder.password(args.password)
    return await NacosNamingService.create_naming_service(builder.build())


async def list_services(naming: NacosNamingService, args: argparse.Namespace) -> list[str]:
    result = await naming.list_services(
        ListServiceParam(
            namespace_id=args.namespace,
            group_name=args.group,
            page_no=1,
            page_size=100,
        )
    )
    return list(result.services)


async def list_instances(
    naming: NacosNamingService, service_name: str, group: str
) -> list:
    return await naming.list_instances(
        ListInstanceParam(
            service_name=service_name,
            group_name=group,
            subscribe=False,
            healthy_only=True,
        )
    )


def interface_map(instance) -> dict[str, str]:
    """把实例 metadata 中的 interface.* 还原成 {处理函数名: "METHOD path | summary"}。"""
    return {
        key.removeprefix("interface."): value
        for key, value in (instance.metadata or {}).items()
        if key.startswith("interface.")
    }


def find_path(interfaces: dict[str, str], keyword: str, method: str) -> str | None:
    """按方法 + 路径关键字，从注册的接口清单里找出接口路径。"""
    for value in interfaces.values():
        route = value.split(" | ")[0]
        methods, _, path = route.partition(" ")
        if method in methods.split(",") and keyword in path:
            return path
    return None


def print_interfaces(service_name: str, interfaces: dict[str, str]) -> None:
    print(f"\n[{service_name}] 从 Nacos metadata 解析到 {len(interfaces)} 个接口：")
    for name in sorted(interfaces):
        print(f"  - {name:24s} {interfaces[name]}")


async def pick_instance(naming: NacosNamingService, service_name: str, group: str):
    """取第一个健康实例；注册的 ip 为不可解析地址时给出明确提示。"""
    instances = await list_instances(naming, service_name, group)
    if not instances:
        raise SystemExit(
            f"服务 {service_name} 在 Nacos 中没有健康实例，请确认服务已启动且 common.nacos.enabled=true"
        )
    print(f"\n[{service_name}] Nacos 注册实例 {len(instances)} 个：")
    for item in instances:
        print(f"  - {item.ip}:{item.port} healthy={item.healthy} cluster={item.clusterName}")
    instance = instances[0]
    if not instance.ip.replace(".", "").isdigit():
        print(
            f"  ! 注册地址 {instance.ip!r} 不是 IP，宿主机可能无法直连；"
            "可设置 NACOS_REGISTER_IP 为可达地址后重启服务"
        )
    return instance


def resolve_base_url(instance) -> str:
    return f"http://{instance.ip}:{instance.port}"


async def cmd_list(naming: NacosNamingService, args: argparse.Namespace) -> int:
    """列出所有服务、实例及接口清单。"""
    services = await list_services(naming, args)
    print(f"Nacos {args.nacos_address} namespace={args.namespace} group={args.group}")
    print(f"已注册服务 {len(services)} 个：{services or '(空)'}")

    for service_name in services:
        instances = await list_instances(naming, service_name, args.group)
        print(f"\n== {service_name}（{len(instances)} 个健康实例）")
        if not instances:
            continue
        print_interfaces(service_name, interface_map(instances[0]))
        print("  实例 metadata：")
        print(json.dumps(instances[0].metadata, ensure_ascii=False, indent=2))
    return 0


def ensure_demo_file(path: str | None) -> Path:
    """未指定文件时生成一个最小 Markdown 示例，避免上传流程依赖真实文档。"""
    if path:
        file_path = Path(path).expanduser().resolve()
        if not file_path.is_file():
            raise SystemExit(f"文件不存在：{file_path}")
        return file_path
    demo = Path(tempfile.gettempdir()) / f"nacos-demo-{uuid.uuid4().hex[:8]}.md"
    demo.write_text(
        "# Nacos 注册调用示例\n\n"
        "本文档由 tests/15-test-nacos-client.py 自动生成，用于验证 /upload 接口。\n",
        encoding="utf-8",
    )
    print(f"未指定 --file，已生成示例文件：{demo}")
    return demo


async def cmd_ingest(naming: NacosNamingService, args: argparse.Namespace) -> int:
    """获取 svc-ingest 注册的接口清单，调用 /upload 并轮询 /status/{task_id}。"""
    instance = await pick_instance(naming, args.service, args.group)
    interfaces = interface_map(instance)
    print_interfaces(args.service, interfaces)

    upload_path = find_path(interfaces, "/upload", "POST") or "/upload"
    status_path = find_path(interfaces, "/status/", "GET") or "/status/{task_id}"
    base_url = resolve_base_url(instance)
    file_path = ensure_demo_file(args.file)

    print(f"\n调用 POST {base_url}{upload_path}（文件：{file_path.name}）")
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        response = await client.post(
            f"{base_url}{upload_path}",
            files={
                "files": (
                    file_path.name,
                    file_path.read_bytes(),
                    "text/markdown" if file_path.suffix == ".md" else "application/octet-stream",
                )
            },
        )
        print(f"HTTP {response.status_code} {response.text[:500]}")
        if response.status_code != 200:
            return 1
        task_ids = response.json().get("task_ids", [])
        if not task_ids:
            print("响应中没有 task_ids，无法继续轮询")
            return 1

        deadline = time.monotonic() + args.wait
        for task_id in task_ids:
            print(f"\n轮询 GET {base_url}{status_path.replace('{task_id}', task_id)}")
            while True:
                status_response = await client.get(
                    f"{base_url}{status_path.replace('{task_id}', task_id)}"
                )
                print(f"  {status_response.text[:400]}")
                state = status_response.json().get("status")
                if state in ("completed", "failed") or time.monotonic() >= deadline:
                    break
                if args.wait <= 0:
                    break
                await asyncio.sleep(args.interval)

    print("\n提示：/upload 已返回即代表接口调用成功，导入全流程在服务后台继续执行。")
    return 0


async def read_sse(base_url: str, stream_path: str, session_id: str, wait: int) -> None:
    """订阅 /stream/{session_id}，打印事件直到 final/error 或超时。"""
    url = f"{base_url}{stream_path.replace('{session_id}', session_id)}"
    print(f"\n订阅 SSE {url}")
    deadline = time.monotonic() + wait
    timeout = httpx.Timeout(connect=10.0, read=float(wait), write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    print(f"SSE HTTP {response.status_code}")
                    return
                event = ""
                async for line in response.aiter_lines():
                    if time.monotonic() >= deadline:
                        print("（SSE 等待超时，停止订阅）")
                        return
                    if line.startswith("event: "):
                        event = line.removeprefix("event: ")
                    elif line.startswith("data: "):
                        print(f"  [{event}] {line.removeprefix('data: ')[:300]}")
                        if event in ("final", "error"):
                            return
        except httpx.ReadTimeout:
            print("（SSE 读取超时，停止订阅）")


async def cmd_query(naming: NacosNamingService, args: argparse.Namespace) -> int:
    """获取 svc-query 注册的接口清单，调用 /query（可切换流式）。"""
    instance = await pick_instance(naming, args.service, args.group)
    interfaces = interface_map(instance)
    print_interfaces(args.service, interfaces)

    query_path = find_path(interfaces, "/query", "POST") or "/query"
    stream_path = find_path(interfaces, "/stream/", "GET") or "/stream/{session_id}"
    base_url = resolve_base_url(instance)

    print(f"\n调用 POST {base_url}{query_path}（is_stream={args.stream}）")
    async with httpx.AsyncClient(timeout=httpx.Timeout(float(args.timeout))) as client:
        response = await client.post(
            f"{base_url}{query_path}",
            json={"query": args.query, "is_stream": args.stream},
        )
        print(f"HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            print(response.text[:1000])
            return 1
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:2000])
        if response.status_code != 200:
            return 1

        if args.stream:
            session_id = payload.get("session_id")
            if session_id:
                await read_sse(base_url, stream_path, session_id, args.wait)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nacos 注册信息查询与接口调用示例")
    parser.add_argument("--nacos-address", default=DEFAULT_ADDRESS, help="Nacos 地址，如 127.0.0.1:8848")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE, help="命名空间 ID")
    parser.add_argument("--group", default=DEFAULT_GROUP, help="服务分组")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="Nacos 用户名")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Nacos 密码")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出服务、实例与接口清单")

    ingest = sub.add_parser("ingest", help="调用 svc-ingest 的 /upload")
    ingest.add_argument("--service", default="svc-ingest", help="Nacos 服务名")
    ingest.add_argument("--file", help="上传文件，缺省自动生成示例 md")
    ingest.add_argument("--wait", type=int, default=30, help="状态轮询最长秒数")
    ingest.add_argument("--interval", type=float, default=2.0, help="轮询间隔秒数")

    query = sub.add_parser("query", help="调用 svc-query 的 /query")
    query.add_argument("--service", default="svc-query", help="Nacos 服务名")
    query.add_argument("--query", default="金融标准知识库支持哪些业务？", help="查询内容")
    query.add_argument("--stream", action="store_true", help="流式：再订阅 /stream/{session_id}")
    query.add_argument("--timeout", type=float, default=180.0, help="HTTP 超时秒数")
    query.add_argument("--wait", type=int, default=120, help="SSE 订阅最长秒数")
    return parser


async def main_async(args: argparse.Namespace) -> int:
    naming = await create_naming(args)
    try:
        if args.command == "list":
            return await cmd_list(naming, args)
        if args.command == "ingest":
            return await cmd_ingest(naming, args)
        return await cmd_query(naming, args)
    finally:
        await naming.shutdown()


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())


"""
cd pymodules

# 1) 查看注册信息（服务 / 实例 / 接口清单 / metadata）
uv run python tests/15-test-nacos-client.py --nacos-address 127.0.0.1:8848 list

# 2) svc-ingest：调 POST /upload，再轮询 GET /status/{task_id}
uv run python tests/15-test-nacos-client.py --nacos-address 127.0.0.1:8848 ingest --file ./doc/xxx.pdf --wait 30

# 3) svc-query：调 POST /query（非流式拿 answer）
uv run python tests/15-test-nacos-client.py --nacos-address 127.0.0.1:8848 query --query "金融标准知识库支持哪些业务？"

# 4) svc-query：流式，再订阅 /stream/{session_id}
uv run python tests/15-test-nacos-client.py --nacos-address 127.0.0.1:8848 query --query "..." --stream
"""