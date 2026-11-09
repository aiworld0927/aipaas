# -*- coding: utf-8 -*-
"""
Kubernetes 操作工具类。

基于官方 kubernetes 客户端（client-python）封装常用资源操作。
底层为 HTTP 连接池复用机制（非长连接），通过 ApiClient 管理连接，
长期运行的服务应复用同一实例并显式 close()。

用法:
    with K8sClient() as k8s:
        for pod in k8s.list_pods("default"):
            print(pod.metadata.name)
"""
import datetime
import time
from typing import Dict, List, Optional, Any

from kubernetes import client, config, stream
from kubernetes.client.rest import ApiException

from aipaas_common.core.log import logger

kubernetes_path = "D:/coding/gitee/aipaas/pymodules/conf/admin.conf"


class K8sClient:
    """Kubernetes 操作工具类，封装常用资源操作。"""

    def __init__(self,kubeconfig_path: Optional[str] = None, context: Optional[str] = None, pool_size: Optional[int] = None,):
        """
        初始化客户端。

        :param kubeconfig_path: kubeconfig 文件路径；为 None 时使用默认路径
                                或集群内配置（ServiceAccount）
        :param context: 指定 kubeconfig 中的 context
        :param pool_size: 连接池最大连接数；为 None 时使用客户端默认值
                          （CPU 核心数 × 5）。高并发场景建议显式调大
        """
        self._load_config(kubeconfig_path, context)

        # 共享同一个 ApiClient，避免各 API 类各自创建独立连接池导致泄漏
        configuration = client.Configuration.get_default_copy()
        if pool_size:
            configuration.connection_pool_maxsize = pool_size
        self.api_client = client.ApiClient(configuration)

        self.core_v1 = client.CoreV1Api(self.api_client)
        self.apps_v1 = client.AppsV1Api(self.api_client)
        self.batch_v1 = client.BatchV1Api(self.api_client)



    def _load_config(self, kubeconfig_path: Optional[str], context: Optional[str]):
        """加载认证配置：优先尝试集群内配置，失败则回退到 kubeconfig。"""
        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster config")
        except config.ConfigException:
            if kubeconfig_path:
                config.load_kube_config(config_file=kubeconfig_path, context=context)
            else:
                config.load_kube_config(context=context)
            logger.info("Loaded kubeconfig")

    def close(self):
        """关闭 ApiClient，释放底层连接池。长期运行的服务应在退出时调用。"""
        self.api_client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ==================== Pod 操作 ====================

    def list_pods(
             namespace: str = "default", label_selector: Optional[str] = None
    ) -> List[client.V1Pod]:
        """
        列出指定命名空间下的 Pod。

        :param namespace: 命名空间
        :param label_selector: 标签选择器，如 "app=nginx,tier=web"
        :return: V1Pod 列表
        """
        return self.core_v1.list_namespaced_pod(
            namespace, label_selector=label_selector
        ).items

    def get_pod( name: str, namespace: str = "default") -> client.V1Pod:
        """获取单个 Pod 详情。"""
        return self.core_v1.read_namespaced_pod(name, namespace)

    def delete_pod( name: str, namespace: str = "default", grace_period: int = 30):
        """
        删除 Pod。

        :param grace_period: 优雅终止等待秒数
        """
        return self.core_v1.delete_namespaced_pod(
            name, namespace, grace_period_seconds=grace_period
        )

    def get_pod_logs(
            
            name: str,
            namespace: str = "default",
            container: Optional[str] = None,
            tail_lines: int = 100,
            previous: bool = False,
    ) -> str:
        """
        获取 Pod 日志。

        :param container: 多容器 Pod 中指定容器名；None 表示默认容器
        :param tail_lines: 返回最后 N 行
        :param previous: 是否获取上一个已终止容器的日志
        """
        return self.core_v1.read_namespaced_pod_log(
            name=name,
            namespace=namespace,
            container=container,
            tail_lines=tail_lines,
            previous=previous,
        )

    def exec_in_pod(
            
            name: str,
            command: List[str],
            namespace: str = "default",
            container: Optional[str] = None,
    ) -> str:
        """
        在 Pod 内执行命令并返回标准输出（等价于 kubectl exec）。

        注意：依赖目标容器内存在对应可执行文件（如 sh、ls），
        distroless 镜像可能无法执行任何命令。
        """
        return stream.stream(
            self.core_v1.connect_get_namespaced_pod_exec,
            name,
            namespace,
            container=container,
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )

    # ==================== Deployment 操作 ====================

    def list_deployments( namespace: str = "default") -> List[client.V1Deployment]:
        """列出命名空间下所有 Deployment。"""
        return self.apps_v1.list_namespaced_deployment(namespace).items

    def get_deployment( name: str, namespace: str = "default") -> client.V1Deployment:
        """获取 Deployment 详情。"""
        return self.apps_v1.read_namespaced_deployment(name, namespace)

    def scale_deployment( name: str, replicas: int, namespace: str = "default"):
        """扩缩容 Deployment 副本数。"""
        body = {"spec": {"replicas": replicas}}
        return self.apps_v1.patch_namespaced_deployment_scale(name, namespace, body)

    def restart_deployment( name: str, namespace: str = "default"):
        """
        触发 Deployment 滚动重启（等价于 kubectl rollout restart）。
        通过 patch 注入 restartedAt 注解，使 Pod 模板变化从而触发滚动更新。
        """
        now = datetime.datetime.utcnow().isoformat("T") + "Z"
        body = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {"kubectl.kubernetes.io/restartedAt": now}
                    }
                }
            }
        }
        return self.apps_v1.patch_namespaced_deployment(name, namespace, body)

    def update_deployment_image(
             name: str, container_name: str, image: str, namespace: str = "default"
    ):
        """更新 Deployment 中指定容器的镜像，触发滚动更新。"""
        body = {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": container_name, "image": image}]
                    }
                }
            }
        }
        return self.apps_v1.patch_namespaced_deployment(name, namespace, body)

    def create_deployment_from_dict( body: Dict[str, Any], namespace: str = "default"):
        """通过 dict（如 YAML 解析结果）创建 Deployment。"""
        return self.apps_v1.create_namespaced_deployment(namespace, body)

    def delete_deployment( name: str, namespace: str = "default"):
        """删除 Deployment。"""
        return self.apps_v1.delete_namespaced_deployment(name, namespace)

    # ==================== Service 操作 ====================

    def list_services( namespace: str = "default") -> List[client.V1Service]:
        """列出命名空间下所有 Service。"""
        return self.core_v1.list_namespaced_service(namespace).items

    def get_service( name: str, namespace: str = "default") -> client.V1Service:
        """获取 Service 详情。"""
        return self.core_v1.read_namespaced_service(name, namespace)

    def create_service( body: Dict[str, Any], namespace: str = "default"):
        """通过 dict 创建 Service。"""
        return self.core_v1.create_namespaced_service(namespace, body)

    def delete_service( name: str, namespace: str = "default"):
        """删除 Service。"""
        return self.core_v1.delete_namespaced_service(name, namespace)

    # ==================== ConfigMap / Secret 操作 ====================

    def list_configmaps( namespace: str = "default") -> List[client.V1ConfigMap]:
        """列出命名空间下所有 ConfigMap。"""
        return self.core_v1.list_namespaced_config_map(namespace).items

    def get_configmap( name: str, namespace: str = "default") -> client.V1ConfigMap:
        """获取 ConfigMap 详情。"""
        return self.core_v1.read_namespaced_config_map(name, namespace)

    def create_configmap( body: Dict[str, Any], namespace: str = "default"):
        """通过 dict 创建 ConfigMap。"""
        return self.core_v1.create_namespaced_config_map(namespace, body)

    def delete_configmap( name: str, namespace: str = "default"):
        """删除 ConfigMap。"""
        return self.core_v1.delete_namespaced_config_map(name, namespace)

    def list_secrets( namespace: str = "default") -> List[client.V1Secret]:
        """
        列出命名空间下所有 Secret。
        注意：返回对象的 data 字段为 base64 编码值，需自行解码。
        """
        return self.core_v1.list_namespaced_secret(namespace).items

    def get_secret( name: str, namespace: str = "default") -> client.V1Secret:
        """获取 Secret 详情（data 为 base64 编码）。"""
        return self.core_v1.read_namespaced_secret(name, namespace)

    # ==================== StatefulSet / DaemonSet 操作 ====================

    def list_statefulsets( namespace: str = "default") -> List[client.V1StatefulSet]:
        """列出命名空间下所有 StatefulSet。"""
        return self.apps_v1.list_namespaced_stateful_set(namespace).items

    def scale_statefulset( name: str, replicas: int, namespace: str = "default"):
        """扩缩容 StatefulSet 副本数。"""
        body = {"spec": {"replicas": replicas}}
        return self.apps_v1.patch_namespaced_stateful_set_scale(name, namespace, body)

    def delete_statefulset( name: str, namespace: str = "default"):
        """删除 StatefulSet。"""
        return self.apps_v1.delete_namespaced_stateful_set(name, namespace)

    def list_daemonsets( namespace: str = "default") -> List[client.V1DaemonSet]:
        """列出命名空间下所有 DaemonSet。"""
        return self.apps_v1.list_namespaced_daemon_set(namespace).items

    def delete_daemonset( name: str, namespace: str = "default"):
        """删除 DaemonSet。"""
        return self.apps_v1.delete_namespaced_daemon_set(name, namespace)

    # ==================== Job 操作 ====================

    def create_job( body: Dict[str, Any], namespace: str = "default"):
        """通过 dict 创建 Job。"""
        return self.batch_v1.create_namespaced_job(namespace, body)

    def list_jobs(
             namespace: str = "default", label_selector: Optional[str] = None
    ) -> List[client.V1Job]:
        """列出命名空间下所有 Job，可按标签过滤。"""
        return self.batch_v1.list_namespaced_job(
            namespace, label_selector=label_selector
        ).items

    def get_job( name: str, namespace: str = "default"):
        """获取 Job 详情（含 spec 和 status）。"""
        return self.batch_v1.read_namespaced_job(name, namespace)

    def get_job_status( name: str, namespace: str = "default"):
        """
        仅获取 Job 的 status 子资源，适合轮询场景，开销小于 read_namespaced_job。
        """
        return self.batch_v1.read_namespaced_job_status(name, namespace)

    def delete_job(
             name: str, namespace: str = "default", propagation_policy: str = "Background"
    ):
        """
        删除 Job。

        :param propagation_policy: Background（先删 Job 再异步清理 Pod）
                                   / Foreground（先删 Pod 再删 Job）
                                   / Orphan（保留 Pod，移除归属）
        """
        body = client.V1DeleteOptions(propagation_policy=propagation_policy)
        return self.batch_v1.delete_namespaced_job(name, namespace, body)

    def wait_job_complete(
            
            name: str,
            namespace: str = "default",
            poll_interval: int = 5,
            timeout: int = 600,
    ) -> str:
        """
        轮询等待 Job 完成。

        :return: "Complete" / "Failed" / "Timeout"
        """
        elapsed = 0
        while elapsed < timeout:
            status = self.batch_v1.read_namespaced_job_status(name, namespace).status
            if status.succeeded and status.succeeded > 0:
                return "Complete"
            if status.failed and status.failed > 0:
                return "Failed"
            time.sleep(poll_interval)
            elapsed += poll_interval
        return "Timeout"

    # ==================== CronJob 操作 ====================

    def create_cronjob( body: Dict[str, Any], namespace: str = "default"):
        """通过 dict 创建 CronJob。"""
        return self.batch_v1.create_namespaced_cron_job(namespace, body)

    def list_cronjobs( namespace: str = "default") -> List[client.V1CronJob]:
        """列出命名空间下所有 CronJob。"""
        return self.batch_v1.list_namespaced_cron_job(namespace).items

    def get_cronjob( name: str, namespace: str = "default"):
        """获取 CronJob 详情。"""
        return self.batch_v1.read_namespaced_cron_job(name, namespace)

    def patch_cronjob( name: str, body: Dict[str, Any], namespace: str = "default"):
        """
        部分更新 CronJob，常用于修改 schedule 或 suspend 字段。
        """
        return self.batch_v1.patch_namespaced_cron_job(name, namespace, body)

    def delete_cronjob( name: str, namespace: str = "default"):
        """删除 CronJob。"""
        return self.batch_v1.delete_namespaced_cron_job(name, namespace)

    def trigger_job_from_cronjob(
             cronjob_name: str, job_name: str, namespace: str = "default"
    ):
        """
        从已有 CronJob 手动触发一次 Job（等价于
        kubectl create job --from=cronjob/xxx）。

        通过复制 job_template.spec 创建新 Job，并设置 ownerReferences
        指向父 CronJob，保持归属关系便于追溯与清理。
        """
        cronjob = self.batch_v1.read_namespaced_cron_job(cronjob_name, namespace)
        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(
                name=job_name,
                annotations={"cronjob.kubernetes.io/instantiate": "manual"},
                owner_references=[
                    client.V1OwnerReference(
                        api_version=cronjob.api_version,
                        kind=cronjob.kind,
                        name=cronjob_name,
                        uid=cronjob.metadata.uid,
                    )
                ],
            ),
            spec=cronjob.spec.job_template.spec,
        )
        return self.batch_v1.create_namespaced_job(namespace, job)

    # ==================== 节点操作 ====================

    def list_nodes(self) -> List[client.V1Node]:
        """列出集群所有节点。"""
        return self.core_v1.list_node().items

    def label_node( name: str, labels: Dict[str, str]):
        """给节点打标签（合并式 patch，不覆盖已有标签）。"""
        body = {"metadata": {"labels": labels}}
        return self.core_v1.patch_node(name, body)

    def cordon_node( name: str, unschedulable: bool = True):
        """
        标记节点是否可调度（cordon/uncordon）。

        :param unschedulable: True 表示 cordon（禁止新 Pod 调度）
        """
        body = {"spec": {"unschedulable": unschedulable}}
        return self.core_v1.patch_node(name, body)

    def drain_node( name: str):
        """
        简化版节点驱逐：对节点上非 DaemonSet 管理的 Pod 调用 Eviction API。

        注意：未处理 emptyDir、本地 PV、PDB 等完整 drain 语义，
        生产环境建议使用 kubectl drain。
        """
        pods = self.core_v1.list_pod_for_all_namespaces(
            field_selector=f"spec.nodeName={name}"
        ).items
        for pod in pods:
            owner_refs = pod.metadata.owner_references or []
            if any(ref.kind == "DaemonSet" for ref in owner_refs):
                continue
            eviction = client.V1Eviction(
                metadata=client.V1ObjectMeta(
                    name=pod.metadata.name, namespace=pod.metadata.namespace
                )
            )
            try:
                self.core_v1.create_namespaced_pod_eviction(
                    pod.metadata.name, pod.metadata.namespace, eviction
                )
            except ApiException as e:
                logger.warning(
                    f"Failed to evict {pod.metadata.namespace}/{pod.metadata.name}: {e}"
                )

    # ==================== PVC 操作 ====================

    def list_pvcs(
             namespace: str = "default"
    ) -> List[client.V1PersistentVolumeClaim]:
        """列出命名空间下所有 PVC。"""
        return self.core_v1.list_namespaced_persistent_volume_claim(namespace).items

    def get_pvc(
             name: str, namespace: str = "default"
    ) -> client.V1PersistentVolumeClaim:
        """获取 PVC 详情。"""
        return self.core_v1.read_namespaced_persistent_volume_claim(name, namespace)

    # ==================== Namespace / Event 操作 ====================

    def list_namespaces(self) -> List[str]:
        """列出所有命名空间名称。"""
        return [ns.metadata.name for ns in self.core_v1.list_namespace().items]

    def create_namespace( name: str):
        """创建命名空间。"""
        body = client.V1Namespace(metadata=client.V1ObjectMeta(name=name))
        return self.core_v1.create_namespace(body)

    def delete_namespace( name: str):
        """删除命名空间（级联删除其下所有资源，谨慎使用）。"""
        return self.core_v1.delete_namespace(name)

    def get_events(
             namespace: str = "default", field_selector: Optional[str] = None
    ) -> List[client.CoreV1Event]:
        """
        获取命名空间事件，常用于排障。

        :param field_selector: 如 "involvedObject.name=my-pod"
        """
        return self.core_v1.list_namespaced_event(
            namespace, field_selector=field_selector
        ).items


if __name__ == '__main__':
    # with open(kubernetes_path) as fi:
    #     j = yaml.load(fi, Loader=yaml.FullLoader)
    # print(type(j))
    # kubernetes_client = K8sClient(kubernetes_path)
    # print(kubernetes_client.list_namespaces())
    # print(kubernetes_client.list_pods(namespace="kube-system")[0])
    # print(type(kubernetes_client.list_pods(namespace="kube-system")[0]))
    # print(kubernetes_client.list_nodes())
    with K8sClient(kubeconfig_path=kubernetes_path,pool_size=50) as k8s:
        # 查询 Pod 与日志
        for pod in k8s.list_pods("default", label_selector="app=nginx"):
            print(pod.metadata.name, pod.status.phase)
        for node in k8s.list_nodes():
            print(node.metadata.name, node.status.node_info.kubelet_version)

        # print(k8s.get_pod_logs("my-pod", tail_lines=50))

        # 更新镜像并触发重启
        k8s.update_deployment_image("my-app", "app", "registry/app:v2")
        k8s.create_deployment_from_dict({})

        # 从 CronJob 手动触发一次 Job 并等待
        # k8s.trigger_job_from_cronjob("nightly", "nightly-manual-001")
        # print(k8s.wait_job_complete("nightly-manual-001"))

        # 节点维护
        # k8s.cordon_node("node-1", True)
        # k8s.drain_node("node-1")