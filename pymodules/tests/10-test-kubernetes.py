# -*- coding: utf-8 -*-
import time
import urllib.request

from clients.kubernetes_client_manager import K8sClient

NS = "default"
APP = "nginx-demo"


def deploy_nginx(k8s: K8sClient):
    """创建 Nginx Deployment（2 副本）"""
    body = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": APP, "labels": {"app": APP}},
        "spec": {
            "replicas": 2,
            "selector": {"matchLabels": {"app": APP}},
            "template": {
                "metadata": {"labels": {"app": APP}},
                "spec": {
                    "containers": [
                        {
                            "name": "nginx",
                            "image": "nginx:1.25",
                            "ports": [{"containerPort": 80}],
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {"cpu": "500m", "memory": "256Mi"},
                            },
                        }
                    ]
                },
            },
        },
    }
    k8s.create_deployment_from_dict(body, namespace=NS)
    print(f"[创建] Deployment {APP}")


def expose_service(k8s: K8sClient):
    """创建 NodePort Service 暴露 Nginx"""
    body = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": APP},
        "spec": {
            "type": "NodePort",
            "selector": {"app": APP},
            "ports": [{"port": 80, "targetPort": 80, "protocol": "TCP"}],
        },
    }
    svc = k8s.create_service(body, namespace=NS)
    print(f"[Service] {svc.metadata.name} type={svc.spec.type}")


def wait_ready(k8s: K8sClient, timeout: int = 120) -> bool:
    """轮询等待 Deployment 就绪（ready == desired）"""
    elapsed = 0
    while elapsed < timeout:
        dep = k8s.get_deployment(APP, NS)
        desired = dep.spec.replicas or 0
        ready = dep.status.ready_replicas or 0
        print(f"[等待] desired={desired} ready={ready}")
        if desired > 0 and ready == desired:
            return True
        time.sleep(5)
        elapsed += 5
    return False


def show_status(k8s: K8sClient):
    """查看 Pod 与 Service 状态"""
    print("[Pod 列表]")
    for pod in k8s.list_pods(NS, label_selector=f"app={APP}"):
        print(f"  {pod.metadata.name}  phase={pod.status.phase}  "
              f"ip={pod.status.pod_ip}  node={pod.spec.node_name}")

    print("[Service]")
    svc = k8s.get_service(APP, NS)
    for p in svc.spec.ports:
        print(f"  port={p.port} targetPort={p.target_port} nodePort={p.node_port}")


def read_logs(k8s: K8sClient):
    """读取第一个 Pod 的最近日志"""
    pods = k8s.list_pods(NS, label_selector=f"app={APP}")
    if not pods:
        print("[日志] 无可用 Pod")
        return
    logs = k8s.get_pod_logs(pods[0].metadata.name, namespace=NS, tail_lines=20)
    print(f"[日志] {pods[0].metadata.name} 最近 20 行:\n{logs}")


def exec_nginx(k8s: K8sClient):
    """在 Pod 内执行命令：查看 Nginx 版本与进程"""
    pods = k8s.list_pods(NS, label_selector=f"app={APP}")
    if not pods:
        return
    pod_name = pods[0].metadata.name
    version = k8s.exec_in_pod(pod_name, ["nginx", "-v"], namespace=NS)
    print(f"[exec] nginx -v -> {version.strip()}")
    procs = k8s.exec_in_pod(pod_name, ["sh", "-c", "ps -ef | grep nginx"], namespace=NS)
    print(f"[exec] ps ->\n{procs}")


def update_index_via_configmap(k8s: K8sClient):
    """通过 ConfigMap 挂载自定义 index.html，并触发滚动更新"""
    cm_body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": f"{APP}-html"},
        "data": {"index.html": "<h1>Hello from K8sClient + Nginx</h1>\n"},
    }
    k8s.create_configmap(cm_body, namespace=NS)

    # patch Deployment，挂载 ConfigMap 到 /usr/share/nginx/html
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "nginx",
                            "volumeMounts": [
                                {"name": "html", "mountPath": "/usr/share/nginx/html"}
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "html", "configMap": {"name": f"{APP}-html"}}
                    ],
                }
            }
        }
    }
    k8s.apps_v1.patch_namespaced_deployment(APP, NS, patch)
    print("[配置] ConfigMap 挂载已应用，等待滚动更新")


def scale_and_restart(k8s: K8sClient):
    """扩缩容 + 滚动重启"""
    k8s.scale_deployment(APP, replicas=3, namespace=NS)
    print("[扩缩容] replicas -> 3")
    k8s.restart_deployment(APP, namespace=NS)
    print("[重启] rollout restart 已触发")


def access_via_service(k8s: K8sClient):
    """通过 NodePort 访问 Nginx（仅适用于本地可直连节点 IP 的环境）"""
    svc = k8s.get_service(APP, NS)
    node_port = svc.spec.ports[0].node_port
    nodes = k8s.list_nodes()
    if not nodes:
        print("[访问] 无节点信息，跳过")
        return
    # 取节点 ExternalIP，没有则用 InternalIP
    addr = None
    for a in nodes[0].status.addresses:
        if a.type == "ExternalIP":
            addr = a.address
            break
        if a.type == "InternalIP":
            addr = a.address
    if not addr:
        print("[访问] 未取到节点 IP，跳过")
        return
    url = f"http://{addr}:{node_port}/"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            print(f"[访问] {url} -> {resp.status}")
            print(resp.read().decode("utf-8", "ignore")[:200])
    except Exception as e:
        print(f"[访问] {url} 失败: {e}")


def cleanup(k8s: K8sClient):
    """清理所有本次创建的资源"""
    for deleter, name in [
        (k8s.delete_service, APP),
        (k8s.delete_deployment, APP),
        (k8s.delete_configmap, f"{APP}-html"),
    ]:
        try:
            deleter(name, namespace=NS)
            print(f"[清理] {name} 已删除")
        except Exception as e:
            print(f"[清理] {name} 删除失败: {e}")


def main():
    kubernetes_path = "D:/coding/gitee/aipaas/pymodules/conf/admin.conf"
    with K8sClient(kubernetes_path,pool_size=20) as k8s:
        deploy_nginx(k8s)
        expose_service(k8s)
        if not wait_ready(k8s):
            print("[警告] 等待就绪超时，继续演示")
        show_status(k8s)
        read_logs(k8s)
        exec_nginx(k8s)
        update_index_via_configmap(k8s)
        time.sleep(10)          # 等滚动更新初步收敛
        scale_and_restart(k8s)
        time.sleep(10)
        show_status(k8s)
        access_via_service(k8s)
        cleanup(k8s)


def test_k8s():
    from kubernetes import client, config, stream
    kubernetes_path = "D:/coding/gitee/aipaas/pymodules/conf/admin.conf"
    config.load_kube_config(kubernetes_path)
    ns = client.CoreV1Api().list_namespace().items
    print(ns)

if __name__ == "__main__":
    # main()
    test_k8s()