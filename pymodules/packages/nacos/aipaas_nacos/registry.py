"""Nacos 服务注册接入层（参考官方 Python SDK 使用手册的 v2 异步 API）。

职责：
- 按 ``common.nacos`` 配置创建命名服务客户端；
- 进程启动时把当前 FastAPI 实例注册为 Nacos 临时实例，退出时注销；
- 注册失败只记 warning，不阻断业务进程启动（注册中心不可用不应让服务起不来）。

实例信息：
- ``ip``：默认取本机默认出口网卡地址（容器 / 裸机通用），可用
  ``common.nacos.register_ip`` 覆盖；
- ``port``：取 ``settings.module.port``，与 uvicorn 实际监听端口同源；
- ``service_name``：由调用方传入部署单元名（如 ``svc-ingest`` / ``svc-query``）；
- ``metadata``：自动收集 FastAPI 路由写入实例元数据（见 ``collect_interface_metadata``），
  调用方可从 Nacos 直接发现服务暴露的接口清单。
"""
from __future__ import annotations

import asyncio
import socket
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Iterable, Iterator

from fastapi import FastAPI
from fastapi.routing import APIRoute
from v2.nacos import (
    ClientConfigBuilder,
    DeregisterInstanceParam,
    GRPCConfig,
    NacosNamingService,
    RegisterInstanceParam,
)

from aipaas_common.conf.settings import get_settings
from aipaas_common.core.log import logger
from aipaas_common.utils.path_util import PROJECT_ROOT

# FastAPI 内置文档路由，不属于业务接口，不写入 Nacos 元数据
_EXCLUDED_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


@dataclass
class NacosRegistration:
    """一次成功注册的上下文，注销时复用同一组 service/ip/port/cluster。"""

    naming: NacosNamingService
    service_name: str
    group_name: str
    ip: str
    port: int
    cluster_name: str


def resolve_instance_ip() -> str:
    """解析注册到 Nacos 的实例 IP：配置优先，否则取默认出口网卡地址。"""
    nacos = get_settings().common.nacos
    configured = nacos.register_ip if nacos else None
    if configured:
        return configured
    try:
        # UDP connect 不会真正发包，只用于拿到默认路由网卡的本地地址
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())


def _build_client_config():
    """把 common.nacos 配置转换为 SDK 的 ClientConfig。"""
    nacos = get_settings().common.nacos
    log_dir = PROJECT_ROOT / "logs" / "nacos"
    builder = (
        ClientConfigBuilder()
        .server_address(nacos.server_address)
        .namespace_id(nacos.namespace)
        .log_level(nacos.log_level)
        .log_dir(str(log_dir))
        .cache_dir(str(log_dir / "cache"))
        .grpc_config(GRPCConfig(grpc_timeout=nacos.grpc_timeout_ms))
    )
    # 未配置账号时不传空凭据，避免服务端开启鉴权前被误判为登录失败
    if nacos.username_str:
        builder.username(nacos.username_str)
    if nacos.password_str:
        builder.password(nacos.password_str)
    return builder.build()


async def create_naming_service() -> NacosNamingService:
    """按公共配置创建命名服务客户端（调用方负责 shutdown）。"""
    return await NacosNamingService.create_naming_service(_build_client_config())


async def _server_reachable(server_address: str) -> bool:
    """TCP 预检：注册中心不可达时直接跳过，避免 SDK 长时间重试拖慢启动。"""
    for address in server_address.split(","):
        host, _, port = address.strip().rpartition(":")
        if not host or not port.isdigit():
            continue
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, int(port)), timeout=1.0
            )
        except (OSError, ValueError, asyncio.TimeoutError):
            continue
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return True
    return False


def _iter_route_views(routes: Iterable[Any]) -> Iterator[Any]:
    """展开应用路由树，产出带 path/methods/summary/name 的路由视图。

    FastAPI 新版本的 ``include_router`` 是惰性的：``app.routes`` 中存放的是
    ``_IncludedRouter``，真正路由要调用 ``effective_candidates()`` 才展开。
    这里统一展开，避免按 APIRouter 组织代码后接口元数据丢失。
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        expand = getattr(route, "effective_candidates", None)
        if callable(expand):
            yield from _iter_route_views(expand())
            continue
        # 已展开的路由上下文（非 APIRoute，但具备同样的关键属性）
        if hasattr(route, "path") and hasattr(route, "methods"):
            yield route


def collect_interface_metadata(app: FastAPI) -> dict[str, str]:
    """收集应用对外 HTTP 接口，用于写入 Nacos 实例 metadata。

    Nacos 以“服务实例”为注册粒度，接口信息通过实例 metadata 暴露：
    - ``interface_count``：接口数量
    - ``openapi_path``：OpenAPI 文档地址，调用方可拉取完整接口定义
    - ``interface.<函数名>``：形如 ``POST /upload | 文件上传接口``，summary 取自路由声明

    声明了 ``include_in_schema=False`` 的内部接口不会被注册。
    """
    routes: list[tuple[str, str, str, str]] = []
    for route in _iter_route_views(app.routes):
        if route.path in _EXCLUDED_PATHS:
            continue
        if not route.include_in_schema:
            continue
        methods = sorted(route.methods - {"HEAD", "OPTIONS"})
        if not methods:
            continue
        routes.append((route.path, ",".join(methods), route.summary or "", route.name or ""))
    routes.sort(key=lambda item: item[0])

    metadata: dict[str, str] = {
        "interface_count": str(len(routes)),
        "openapi_path": app.openapi_url or "",
    }
    for path, methods, summary, name in routes:
        route_key = name or path.strip("/").replace("/", ".").replace("{", "").replace("}", "")
        key = f"interface.{route_key}"
        value = f"{methods} {path}"
        if summary:
            value = f"{value} | {summary}"
        # 不同 router 可能复用同名函数，追加序号保证 metadata key 唯一
        unique_key, index = key, 2
        while unique_key in metadata:
            unique_key, index = f"{key}.{index}", index + 1
        metadata[unique_key] = value
    return metadata


async def register_current_service(
    service_name: str,
    app: FastAPI | None = None,
) -> NacosRegistration | None:
    """把当前进程注册为 Nacos 临时实例。

    未启用、注册中心不可达或注册失败均返回 None，不抛异常，保证业务进程可继续启动。
    """
    settings = get_settings()
    nacos = settings.common.nacos
    if nacos is None or not nacos.enabled:
        logger.info("Nacos 注册未启用（common.nacos 缺失或 enabled=false），跳过 service={}", service_name)
        return None

    if not await _server_reachable(nacos.server_address):
        logger.warning(
            "Nacos 服务端不可达，跳过服务注册 | service={} address={}",
            service_name,
            nacos.server_address,
        )
        return None

    ip = resolve_instance_ip()
    port = settings.module.port
    instance_metadata = {"protocol": "http", "health_path": "/health"}
    if app is not None:
        instance_metadata.update(collect_interface_metadata(app))

    naming: NacosNamingService | None = None
    try:
        naming = await create_naming_service()
        await naming.register_instance(
            RegisterInstanceParam(
                service_name=service_name,
                group_name=nacos.group_name,
                ip=ip,
                port=port,
                weight=nacos.weight,
                cluster_name=nacos.cluster_name,
                metadata=instance_metadata,
                enabled=True,
                healthy=True,
                ephemeral=True,
            )
        )
    except asyncio.CancelledError:
        # 退出时注册尚未完成：关闭已创建的客户端，实例本身未注册无需注销
        if naming is not None:
            await _shutdown_quietly(naming)
        raise
    except Exception as exc:
        logger.warning(
            "Nacos 服务注册失败，进程继续启动 | service={} address={} error={}",
            service_name,
            nacos.server_address,
            exc,
        )
        if naming is not None:
            await _shutdown_quietly(naming)
        return None

    logger.info(
        "Nacos 服务注册成功 | service={} ip={} port={} group={} namespace={} address={} interfaces={}",
        service_name,
        ip,
        port,
        nacos.group_name,
        nacos.namespace,
        nacos.server_address,
        instance_metadata.get("interface_count", "0"),
    )
    return NacosRegistration(
        naming=naming,
        service_name=service_name,
        group_name=nacos.group_name,
        ip=ip,
        port=port,
        cluster_name=nacos.cluster_name,
    )


async def deregister_current_service(registration: NacosRegistration | None) -> None:
    """注销实例并关闭客户端；未注册成功时直接返回。"""
    if registration is None:
        return
    try:
        await registration.naming.deregister_instance(
            DeregisterInstanceParam(
                service_name=registration.service_name,
                group_name=registration.group_name,
                ip=registration.ip,
                port=registration.port,
                cluster_name=registration.cluster_name,
                ephemeral=True,
            )
        )
        logger.info(
            "Nacos 服务已注销 | service={} ip={} port={}",
            registration.service_name,
            registration.ip,
            registration.port,
        )
    except Exception as exc:
        logger.warning(
            "Nacos 服务注销失败 | service={} error={}",
            registration.service_name,
            exc,
        )
    finally:
        await _shutdown_quietly(registration.naming)


async def _shutdown_quietly(naming: NacosNamingService) -> None:
    """关闭命名服务客户端，异常只记日志（退出流程不中断）。"""
    try:
        await naming.shutdown()
    except Exception as exc:
        logger.warning("Nacos 客户端关闭失败: {}", exc)


async def _collect_registration(
    task: asyncio.Task[NacosRegistration | None],
    service_name: str,
) -> NacosRegistration | None:
    """退出时收敛后台注册任务：未完成则取消，已完成则取结果。"""
    if not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return None
    try:
        return task.result()
    except Exception as exc:
        logger.warning("Nacos 服务注册任务异常 | service={} error={}", service_name, exc)
        return None


def nacos_lifespan(
    service_name: str,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """FastAPI lifespan 工厂：启动注册、退出注销。

    注册放到后台任务执行：注册中心不可达时 SDK 会长时间重试，不能因此阻塞
    服务启动与健康检查；退出时未完成的注册任务会先取消，再注销已注册实例。

    用法::

        app = FastAPI(lifespan=nacos_lifespan("svc-ingest"))
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        registration_task = asyncio.create_task(
            register_current_service(service_name, app)
        )
        try:
            yield
        finally:
            registration = await _collect_registration(registration_task, service_name)
            await deregister_current_service(registration)

    return lifespan
