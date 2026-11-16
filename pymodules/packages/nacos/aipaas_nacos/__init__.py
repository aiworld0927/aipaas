"""Nacos 公共对接层：供 ingest-worker / query-worker 共用的服务注册能力。"""
from aipaas_nacos.registry import (
    NacosRegistration,
    collect_interface_metadata,
    create_naming_service,
    deregister_current_service,
    nacos_lifespan,
    register_current_service,
    resolve_instance_ip,
)

__all__ = [
    "NacosRegistration",
    "collect_interface_metadata",
    "create_naming_service",
    "deregister_current_service",
    "nacos_lifespan",
    "register_current_service",
    "resolve_instance_ip",
]
