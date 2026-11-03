import os
import re
import traceback

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from loguru import logger
import yaml
import json
from kubernetes.stream import stream


class K8S:
    def __init__(self, kubeconfig, namespace=None):
        self.kubeconfig = kubeconfig
        if not namespace:
            with open(self.kubeconfig) as fi:
                j = yaml.load(fi, Loader=yaml.FullLoader)
            self.namespace = j['contexts'][0]['context']['namespace']
        else:
            self.namespace = namespace

        api = config.new_client_from_config(kubeconfig)
        self.corev1 = client.CoreV1Api(api)
        self.appv1 = client.AppsV1Api(api)
        self.api_instance = client.AutoscalingV1Api(api)

    def create_pvc(self, pvc_name: str, size_in_mb: int, storage_class_name="glusterfs-sc"):
        logger.info("create pvc name={} size={}Mi", pvc_name, size_in_mb)
        pvc = client.V1PersistentVolumeClaim(api_version="v1", kind="PersistentVolumeClaim",
                                             metadata=client.V1ObjectMeta(name=pvc_name, labels={"pvc": pvc_name}),
                                             )
        pvc.spec = client.V1PersistentVolumeClaimSpec(storage_class_name=storage_class_name,
                                                      access_modes=["ReadWriteMany", ],
                                                      volume_mode="Filesystem",
                                                      resources=client.V1ResourceRequirements(
                                                          requests={"storage": f"{size_in_mb}Mi"}))
        try:
            resp = self.corev1.create_namespaced_persistent_volume_claim(namespace=self.namespace, body=pvc)
        except ApiException as x:
            # db.devc_update_errormsg(pvc_name,"create pvc failed, reason: %s, body: %s" % (x.reason, x.body))
            logger.error("create pvc failed, reason: {}, body: {}", x.reason, x.body)
            return False
        if resp.status.phase == 'Pending':
            return True
        else:
            logger.warning("create pvc failed11, return {}", resp)
            return False

    # 创建service
    def create_service(self, svc_name: str,
                       Ports: list,
                       # 暴露端口, 格式: [ [name, protocol, port, node_port], ...], node_port为None时表明外部端口自动分配
                       selector: dict,  # 如何选择服务的目标, 如: {"app":"something"}
                       ):
        service = client.V1Service(api_version="v1", kind="Service")
        service.metadata = client.V1ObjectMeta(name=svc_name)
        spec = client.V1ServiceSpec()
        spec.type = "NodePort"
        spec.selector = selector
        spec.ports = [client.V1ServicePort(name=name, protocol=protocol, port=port, node_port=node_port)
                      for name, protocol, port, node_port in Ports]
        service.spec = spec
        try:
            resp = self.corev1.create_namespaced_service(namespace=self.namespace, body=service)
            #        log.info("create service return %s", resp)
            return True
        except Exception as x:
            # db.devc_update_errormsg(svc_name,"create service failed: %s" % x)
            logger.error("create service failed: {}", x)
            return False

    def create_horizontal_pod_autoscaling(self,
                                          hpa_name: str,
                                          max_replicas: int,
                                          min_replicas: int,
                                          target_cpu_utilization_percentage: int,
                                          object_kind: str,
                                          object_name: str):
        """
        创建水平扩缩容
        :param hpa_name:name of horizontal pod autoscaling
        :param max_replicas: 扩容的最大数量
        :param min_replicas: 缩容的最小数量
        :param target_cpu_utilization_percentage: cpu的阈值
        :param object_kind: 自动扩缩容对象的k8s资源种类
        :param object_name: 自动扩缩容对象的名字
        :return:
        """

        scale_target_ref = client.V1CrossVersionObjectReference(api_version='apps/v1',
                                                                kind=object_kind,
                                                                name=object_name)
        spec = client.V1HorizontalPodAutoscalerSpec(max_replicas=max_replicas,
                                                    min_replicas=min_replicas,
                                                    scale_target_ref=scale_target_ref,
                                                    target_cpu_utilization_percentage=target_cpu_utilization_percentage)
        body = client.V1HorizontalPodAutoscaler(api_version='autoscaling/v1',
                                                kind='HorizontalPodAutoscaler',
                                                metadata=client.V1ObjectMeta(name=hpa_name),
                                                spec=spec)
        try:
            api_response = self.api_instance.create_namespaced_horizontal_pod_autoscaler(namespace=self.namespace,
                                                                                         body=body)
            logger.info("create horizontal_pod_autoscaler {}, return {}", hpa_name, str(api_response))
            return True
        except Exception as x:
            # 将创建错误信息写入数据库中
            # db.devc_update_errormsg(dep_name,"create deployment %s failed %s" % (hpa_name, x))
            logger.error("create deployment {} failed {}", hpa_name, x)
        return False

    def delete_all(self, dep_name=None, svc_name=None, pvc_name=None):
        if dep_name == '' or svc_name == '' or pvc_name == '':
            logger.warning("fatal operations: dep[{}] svc[{}] pvc[{}] {}",
                           dep_name, svc_name, pvc_name, traceback.format_exc())
            return
        if dep_name is not None:
            dep_name = dep_name.strip()
            hpa_name = dep_name + '-hpa'
            try:
                if dep_name != "":
                    resp = self.appv1.delete_namespaced_deployment(name=dep_name, namespace=self.namespace,
                                                                   body=client.V1DeleteOptions(
                                                                       propagation_policy='Foreground',
                                                                       grace_period_seconds=5))
                    logger.info("delete deployment {} return {}", dep_name, resp)

                    resp_hpa = self.api_instance.delete_namespaced_horizontal_pod_autoscaler(name=hpa_name,
                                                                                             namespace=self.namespace)
                    logger.info("delete hpa {} return {}", hpa_name, resp_hpa)
            except Exception as x:
                logger.error("delete deployment {} or hpa {} failed, but Continue... {}", dep_name, hpa_name, x)
        if svc_name is not None:
            try:
                svc_name = svc_name.strip()
                if svc_name != "":
                    resp = self.corev1.delete_namespaced_service(name=svc_name, namespace=self.namespace,
                                                                 body=client.V1DeleteOptions())
                    logger.info("delete service {} return {}", svc_name, resp)
            except Exception as x:
                logger.error("delete service {} failed, but Continue... {} {}", svc_name, x, traceback.format_exc())
        if pvc_name is not None:
            try:
                pvc_name = pvc_name.strip()
                if pvc_name != "":
                    resp = self.corev1.delete_namespaced_persistent_volume_claim(name=pvc_name,
                                                                                 namespace=self.namespace,
                                                                                 body=client.V1DeleteOptions())
                    logger.info("delete pvc {} return {}", pvc_name, resp)
            except Exception as x:
                logger.error("delete pvc {} failed, but Continue...{}", pvc_name, x)
        # 查询pvc，并删除pvc对应的svc (这个服务是用于glusterfs在线扩容的)
        # if pvc_name is not None:
        #     try:
        #         ret = self.corev1.list_namespaced_persistent_volume_claim(namespace=self.namespace, label_selector=f"pvc={pvc_name}")
        #         pvc_info = ret.items[0]
        #         vname = pvc_info.spec.volume_name
        #         svc_gd_name = 'glusterfs-dynamic-' + vname[4:]
        #         ret = self.corev1.delete_namespaced_service(namespace=self.namespace, field_selector="metadata.name=%s" % svc_gd_name)
        #     except Exception as x:
        #         logger.error("delete svc %s failed, but Continue...%s" % (svc_name, x))

    def create_deployment(self, dep_name: str, image: str,
                          cpu: float,  # CPU limit, cores
                          memory: int,  # Memory limit, Mb
                          entrypoint: str,  # 启动程序
                          args: list = None,  # 启动参数
                          PVCs: list = None,  # 挂载卷list, 格式: [[pvc_name, mount_path],...]
                          ENVs: dict = None,  # 设置环境变量, 格式: {name: value, name1:value1}
                          wd: str = None,  # 工作目录
                          fsGroup: int = None,  # 用户id, 非root启动容器
                          gpu: int = -1,  ### add by cong,  分配GPU 显存数， 单位： GB
                          extra_mounts: list = None,  # 额外的直接需要挂载的mount路径， item格式：vol_name,mount_path,if_readonly
                          shmsize: int = -1,  # 是否设置shm大小
                          cpu_request_times: int = 50,  # k8s配置的CPU超分配倍数， 68环境是50倍， 75是20倍（应该是跟namespace捆绑的）
                          node_name: str = None,  # 绑定到指定的node节点主机上
                          gpu_res_spec: dict = None,  # 如果要求分配 GPU， 那么这里传入规格的内容
                          privileged: bool = False,  # 是否为特权容器, 加入特权是为了使用fuse, 但4pd的vgpu插件不能使用此模式
                          inst_count: int = 1,
                          ):
        # Configure Pod template container
        if PVCs is not None:
            mounts = []
            vols = []
            for i, (pvc_name, mount_path) in enumerate(PVCs):
                vols.append(client.V1Volume(name=f"volume{i}", persistent_volume_claim=
                client.V1PersistentVolumeClaimVolumeSource(claim_name=pvc_name)))
                mounts.append(client.V1VolumeMount(mount_path=mount_path, name=f"volume{i}"))
        else:
            vols = None
            mounts = None
        if shmsize > 0:
            if vols is None:
                vols = []
                mounts = []
            vol = client.V1Volume(name=f"cache-volume",
                                  empty_dir=client.V1EmptyDirVolumeSource(medium="Memory", size_limit=f"{shmsize}Mi"))
            vols.append(vol)
            mounts.append(client.V1VolumeMount(mount_path="/dev/shm", name=f"cache-volume"))

        if extra_mounts is not None:
            if mounts is None:
                mounts = []
                vols = []
            for i, (vol, mpath, is_readonly) in enumerate(extra_mounts):
                vols.append(client.V1Volume(name=f"vol-{i}", persistent_volume_claim=
                client.V1PersistentVolumeClaimVolumeSource(claim_name=vol)))
                mounts.append(client.V1VolumeMount(mount_path=mpath, name=f"vol-{i}", read_only=is_readonly))

        if ENVs is not None:
            # env = [{"name": k, "value": v} for k, v in ENVs.items()]
            env = [client.V1EnvVar(name=k, value=v) for k, v in ENVs.items()]
        else:
            env = []
        mem = f"{memory}Mi"
        # cpu_req = f"{cpu * 1000 / _conf.get('cpu_request_times', 50)}m"  # 在68环境是 50倍(1000/50=20)超分, 75环境则是 20倍超分
        try:
            cpu_request_times = int(cpu_request_times)
        except:
            logger.warning("cpu_request_times [{}] parse int failed, use -1 instead", repr(cpu_request_times))
            cpu_request_times = -1
        if cpu_request_times > 0:
            cpu_req = f"{cpu * 1000 / cpu_request_times}m"  # 在68环境是 50倍(1000/50=20)超分, 75环境则是 20倍超分
            cpu = f"{cpu:.1f}"

            resources = {'limits': {'memory': mem, 'cpu': cpu},
                         'requests': {'memory': mem, 'cpu': cpu_req}}
            # else:
            # resources = {'limits': {'memory': mem, 'cpu': cpu, "aliyun.com/gpu-mem": gpu},
            #              'requests': {'memory': mem, 'cpu': cpu_req, "aliyun.com/gpu-mem": gpu}}
        else:
            cpu = f"{cpu:.1f}"
            resources = {'limits': {'memory': mem, 'cpu': cpu}}
            # resources = {'limits': {'memory': mem, 'cpu': cpu, "aliyun.com/gpu-mem": gpu}}
        command = entrypoint if type(entrypoint) == list else [entrypoint]
        # 分配为 privileged 容器， 以便使用 fuse-zip,  (2022-5-11 导致4pd的vgpu插件工作异常, 需要先去掉)
        security_context = client.V1SecurityContext(allow_privilege_escalation=True, privileged=True,
                                                    capabilities=client.V1Capabilities(
                                                        add=['SYS_ADMIN'])) if privileged else None
        container = client.V1Container(
            name=dep_name,
            image=image,
            image_pull_policy="IfNotPresent",  # "Always","IfNotPresent"
            command=command,
            env=env,
            args=args,
            resources=resources,
            volume_mounts=mounts,
            working_dir=wd,
            security_context=security_context,
        )
        sec = None if fsGroup is None else client.V1PodSecurityContext(fs_group=fsGroup, run_as_user=fsGroup)

        # 添加host
        host_aliases, host_conf = None, None
        if os.path.exists('/app/hosts.txt'):
            with open('/app/hosts.txt', 'r') as f:
                host_conf = f.read()
        if host_conf:
            host_aliases = []
            hostAliases_list = re.split('\r|\n', host_conf)
            for row in hostAliases_list:
                hosts = row.strip().split(' ')
                hosts = [host.strip() for host in hosts if host.strip()]
                if len(hosts) > 1:
                    host_aliase = client.V1HostAlias(ip=hosts[0], hostnames=hosts[1:])
                    host_aliases.append(host_aliase)
        affinity = None
        # # 设置亲和度，不使用1+n问题节点
        # terms = client.models.V1NodeSelectorTerm(
        #     match_expressions=[
        #         {'key': 'kubernetes.io/hostname',
        #          'operator': 'In',
        #          'values': ["k8s-slave-1", "k8s-slave-2", "k8s-slave-4"]}
        #     ]
        # )
        # node_selector = client.models.V1NodeSelector(node_selector_terms=[terms])
        # node_affinity = client.models.V1NodeAffinity(
        #     required_during_scheduling_ignored_during_execution=node_selector
        # )
        # affinity = client.models.V1Affinity(node_affinity=node_affinity)
        pod_spec = client.V1PodSpec(containers=[container], volumes=vols,
                                    security_context=sec, affinity=affinity, node_name=node_name,
                                    host_aliases=host_aliases)
        try:
            self._gpu_res(gpu, gpu_res_spec, pod_spec, dep_name)
        except Exception as x:
            logger.error("set gpu_res failed, but continue!, {}, {}", x, traceback.format_exc())

        # Create and configurate a spec section
        template = client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(labels={"app": dep_name}),
            spec=pod_spec)
        # Create the specification of deployment
        dep_spec = client.V1DeploymentSpec(
            replicas=inst_count,
            template=template,
            selector={'matchLabels': {'app': dep_name}})
        # Instantiate the deployment object
        deployment = client.V1Deployment(
            api_version="apps/v1",
            kind="Deployment",
            metadata=client.V1ObjectMeta(name=dep_name),
            spec=dep_spec)
        # Create deployement
        try:
            api_response = self.appv1.create_namespaced_deployment(
                body=deployment,
                namespace=self.namespace)
            logger.info("create deployment {}, return {}", dep_name, str(api_response)[:20])
            return True
        except Exception as x:
            # 将创建错误信息写入数据库中
            # db.devc_update_errormsg(dep_name,"create deployment %s failed %s" % (dep_name, x))
            logger.error("create deployment {} failed {}", dep_name, x)
        return False

    def create_deployment_new(self, dep_name: str, image: str,
                          cpu: float,  # CPU limit, cores
                          memory: int,  # Memory limit, Mb
                          entrypoint: str = None,  # 启动程序
                          args: list = None,  # 启动参数
                          PVCs: list = None,  # 挂载卷list, 格式: [[pvc_name, mount_path],...]
                          ENVs: dict = None,  # 设置环境变量, 格式: {name: value, name1:value1}
                          wd: str = None,  # 工作目录
                          fsGroup: int = None,  # 用户id, 非root启动容器
                          resType: int = -1,  # 1:gpu, 2:npu
                          resInfo: dict = None,  # gpu或者npu详情
                          # gpu: dict = None,  ### add by cong,  分配GPU 显存数， 单位： GB
                          extra_mounts: list = None,  # 额外的直接需要挂载的mount路径， item格式：vol_name,mount_path,if_readonly
                          shmsize: int = -1,  # 是否设置shm大小
                          # cpu_request_times: int = 50,  # k8s配置的CPU超分配倍数， 68环境是50倍， 75是20倍（应该是跟namespace捆绑的）
                          node_name: str = None,  # 绑定到指定的node节点主机上
                          # gpu_res_spec: dict = None,  # 如果要求分配 GPU， 那么这里传入规格的内容
                          privileged: bool = False,  # 是否为特权容器, 加入特权是为了使用fuse, 但4pd的vgpu插件不能使用此模式
                          inst_count: int = 1,
                          affinity: list = None
                          ):
        # Configure Pod template container
        if PVCs is not None:
            mounts = []
            vols = []
            for i, (pvc_name, mount_path) in enumerate(PVCs):
                vols.append(client.V1Volume(name=f"volume{i}", persistent_volume_claim=
                client.V1PersistentVolumeClaimVolumeSource(claim_name=pvc_name)))
                mounts.append(client.V1VolumeMount(mount_path=mount_path, name=f"volume{i}"))
        else:
            vols = None
            mounts = None
        if shmsize > 0:
            if vols is None:
                vols = []
                mounts = []
            vol = client.V1Volume(name=f"cache-volume",
                                  empty_dir=client.V1EmptyDirVolumeSource(medium="Memory", size_limit=f"{shmsize}Mi"))
            vols.append(vol)
            mounts.append(client.V1VolumeMount(mount_path="/dev/shm", name=f"cache-volume"))

        if extra_mounts is not None:
            if mounts is None:
                mounts = []
                vols = []
            for i, (vol, mpath, is_readonly) in enumerate(extra_mounts):
                vols.append(client.V1Volume(name=f"vol-{i}", persistent_volume_claim=
                client.V1PersistentVolumeClaimVolumeSource(claim_name=vol)))
                mounts.append(client.V1VolumeMount(mount_path=mpath, name=f"vol-{i}", read_only=is_readonly))

        if ENVs is not None:
            # env = [{"name": k, "value": v} for k, v in ENVs.items()]
            env = [client.V1EnvVar(name=k, value=v) for k, v in ENVs.items()]
        else:
            env = []
        mem = f"{memory}Mi"

        cpu = f"{cpu:.1f}"
        resources = {'limits': {'memory': mem, 'cpu': cpu},
                     'requests': {'memory': mem, 'cpu': cpu}}
        # 处理资源
        if resType == 1:
            # gpu
            if resInfo['gpuType'] == 1:
                # aliyun
                # "gpu": 1, "gpumem": 3000, "gpucores": 30
                logger.info(f'gpu {resInfo["gpu"]}')
                logger.info(f'gpumem {resInfo["gpumem"]}')
                gpuMem = (resInfo['gpu'] * resInfo['gpumem'])/1024
                logger.info(f'gpuMem {gpuMem}')
                resources['limits'].update({"aliyun.com/gpu-mem": gpuMem})
                resources['requests'].update({"aliyun.com/gpu-mem": gpuMem})

            elif resInfo['gpuType'] == 2:
                # 第四范式，gpumem默认单位是mb
                resources['limits'].update({
                    "nvidia.com/gpu": resInfo['gpu'],
                    "nvidia.com/gpumem": resInfo['gpumem'],
                    "nvidia.com/gpucores": resInfo['gpuCores']})
                resources['requests'].update({
                    "nvidia.com/gpu": resInfo['gpu'],
                    "nvidia.com/gpumem": resInfo['gpumem'],
                    "nvidia.com/gpucores": resInfo['gpuCores']})

                resInfo['gpu_res'] = {'node_selector': {"gpu-type": resInfo['cardType'].lower()}}

            elif resInfo['gpuType'] == 3:
                # 驱动
                resInfo['gpu_res'] = {
                    "node_selector": {"gpu": "on"},
                    "env": {"NVIDIA_VISIBLE_DEVICES": "all"},
                    "tolerations": "gpu"
                }

        elif resType == 2:
            # npu
            npu = resInfo['npu']
            if 'cardSpec' in resInfo.keys():
                if resInfo['cardSpec']:
                    key = f"{resInfo['cardType']}-{resInfo['cardSpec']}"
                else:
                    key = resInfo['cardType']
            else:
                key = resInfo['cardType']
            resources['limits'].update({f"huawei.com/{key}": npu})
            resources['requests'].update({f"huawei.com/{key}": npu})

        if entrypoint:
            command = entrypoint if type(entrypoint) == list else [entrypoint]
        else:
            command = []
        # 分配为 privileged 容器， 以便使用 fuse-zip,  (2022-5-11 导致4pd的vgpu插件工作异常, 需要先去掉)
        security_context = client.V1SecurityContext(allow_privilege_escalation=True, privileged=True,
                                                    capabilities=client.V1Capabilities(
                                                        add=['SYS_ADMIN'])) if privileged else None
        container = client.V1Container(
            name=dep_name,
            image=image,
            image_pull_policy="IfNotPresent",  # "Always","IfNotPresent"
            command=command,
            env=env,
            args=args,
            resources=resources,
            volume_mounts=mounts,
            working_dir=wd,
            security_context=security_context,
        )
        sec = None if fsGroup is None else client.V1PodSecurityContext(fs_group=fsGroup, run_as_user=fsGroup)

        # 添加host
        host_aliases, host_conf = None, None
        if os.path.exists('/app/hosts.txt'):
            with open('/app/hosts.txt', 'r') as f:
                host_conf = f.read()
        if host_conf:
            host_aliases = []
            hostAliases_list = re.split('\r|\n', host_conf)
            for row in hostAliases_list:
                hosts = row.strip().split(' ')
                hosts = [host.strip() for host in hosts if host.strip()]
                if len(hosts) > 1:
                    host_aliase = client.V1HostAlias(ip=hosts[0], hostnames=hosts[1:])
                    host_aliases.append(host_aliase)
        pod_spec = client.V1PodSpec(containers=[container], volumes=vols,
                                    security_context=sec, node_name=node_name,
                                    host_aliases=host_aliases)
        # 额外的配置
        if resInfo:
            if 'gpu_res' in resInfo.keys():
                try:
                    self._gpu_res_new(resInfo['gpu_res'], pod_spec, dep_name)
                except Exception as x:
                    logger.error("set gpu_res failed, but continue!, {}, {}", x, traceback.format_exc())

        # 指定主机
        if affinity:
            self.specify_host(pod_spec, affinity)

        # Create and configurate a spec section
        template = client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(labels={"app": dep_name}),
            spec=pod_spec)
        # Create the specification of deployment
        dep_spec = client.V1DeploymentSpec(
            replicas=inst_count,
            template=template,
            selector={'matchLabels': {'app': dep_name}})
        # Instantiate the deployment object
        deployment = client.V1Deployment(
            api_version="apps/v1",
            kind="Deployment",
            metadata=client.V1ObjectMeta(name=dep_name),
            spec=dep_spec)
        # Create deployement
        try:
            api_response = self.appv1.create_namespaced_deployment(
                body=deployment,
                namespace=self.namespace)
            logger.info("create deployment {}, return {}", dep_name, str(api_response)[:20])
            return True
        except Exception as x:
            # 将创建错误信息写入数据库中
            # db.devc_update_errormsg(dep_name,"create deployment %s failed %s" % (dep_name, x))
            logger.error("create deployment {} failed {}", dep_name, x)
        return False

    def specify_host(self, pod_spec, affinity):
        node_selector_requirement = client.V1NodeSelectorRequirement(
            key="kubernetes.io/hostname",
            operator="In",
            values=affinity
        )

        # 创建一个V1NodeSelector对象，并将V1NodeSelectorRequirement对象添加到其中
        node_selector = client.V1NodeSelector(
            node_selector_terms=[
                client.V1NodeSelectorTerm(
                    match_expressions=[node_selector_requirement]
                )
            ]
        )

        # 创建一个V1Affinity对象，并将V1NodeSelector对象添加到其中
        pod_spec.affinity = client.V1Affinity(
            node_affinity=client.V1NodeAffinity(
                required_during_scheduling_ignored_during_execution=node_selector
            )
        )
        # pod_spec.affinity = [client.V1Affinity(required_during_scheduling_ignored_during_execution=)]

    def _gpu_res_new(self, res_spec, pod_spec: client.V1PodSpec, container_name):
        '''向资源中设置gpu资源描述, 通过json格式在表 c_res_gpu.res_spe 中进行描述

        入参:
        @res_spe  对应字段 c_res_gpu.res_spe 的值, 格式为json, 对应格式说明参考下面
        @env 待加入 podspec 中的env对象,  资源规则中如果有 env 配置, 则加入到里面
        @resources, 待加入 podspec 的 resources 对象, 资源规格所需的值加到里面

        - limits  内容直接追加到现有的 resources.limits 中
        - requests  可选, 有内容则追加到 resources.requests 中
        - env  可选,  内容为 key:value模式, 其取值有3类
            - str 类型,  直接将值存到 env中
            - ref 类型
                - field 从部署环境中取
                - configmap  从configmap中取值, 其值为configmap中的key
                - secret 从secret中获取值(暂不实现)
            - ref_val 根据上面 ref 的类型， 具体的值

                '''
        # wuqc 2022-4-26 说环境中的gpu节点都加上污点 gpu=gpu, 要分配gpu容器都加上这个污点容忍
        # pod_spec.tolerations = [
        #     client.V1Toleration(key="gpu", operator="Exists", effect="NoSchedule")]

        # pod_spec.tolerations = [
        #     client.V1Toleration(key="gpu", operator="Equal", effect="NoSchedule", value="gpu")]
        container: client.V1Container = pod_spec.containers[0]
        env = container.env
        logger.debug("set gpu_res of {} with {}", res_spec, container_name)

        if type(res_spec) == str:
            res_spec = json.loads(res_spec)  # zhenghj_mci 配置到表中的json串, 传入时是整个字符串, 需要再转换一次

        if 'env' in res_spec:
            for k, v in res_spec['env'].items():
                if type(v) == str:
                    env.append(client.V1EnvVar(name=k, value=v))
                else:
                    if v['ref'] == 'field':
                        env.append(client.V1EnvVar(
                            name=k,
                            value_from=client.V1EnvVarSource(
                                field_ref=client.V1ObjectFieldSelector(field_path=v['ref_val'])
                            )))
                    elif v['ref'] == 'configmap':
                        vv = v['ref_val']
                        _name, _key = vv.split(':', maxsplit=1)
                        env.append(client.V1EnvVar(
                            name=k,
                            value_from=client.V1EnvVarSource(
                                field_ref=client.V1ConfigMapKeySelector(key=_key, name=_name)
                            )))

        if 'node_selector' in res_spec:
            pod_spec.node_selector = res_spec['node_selector']

        if 'tolerations' in res_spec:
            logger.info(f"tolerations ************, {res_spec['tolerations']}")
            kk = res_spec['tolerations']
            # https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/
            if type(kk) == list:
                pod_spec.tolerations = [client.V1Toleration(key=k, operator="Equal", effect="NoSchedule", value=k) for k in kk]
                # pod_spec.tolerations = [client.V1Toleration(key=k, operator="Exists", effect="NoSchedule") for k in kk]
            elif type(kk) == str:
                pod_spec.tolerations = [client.V1Toleration(key=kk, operator="Equal", effect="NoSchedule", value=kk)]
                # pod_spec.tolerations = [client.V1Toleration(key=kk, operator="Exists", effect="NoSchedule")]
        if "scheduler" in res_spec:
            pod_spec.scheduler_name = res_spec["scheduler"]  # orion-scheduler
        if "affinity" in res_spec:
            pass
            client.V1Affinity()

    def _gpu_res(self, gpu_id, res_spec, pod_spec: client.V1PodSpec, container_name):
        '''向资源中设置gpu资源描述, 通过json格式在表 c_res_gpu.res_spe 中进行描述

        入参:
        @gpu_id  对应字段 c_res_gpu.gpu_id
        @res_spe  对应字段 c_res_gpu.res_spe 的值, 格式为json, 对应格式说明参考下面
        @env 待加入 podspec 中的env对象,  资源规则中如果有 env 配置, 则加入到里面
        @resources, 待加入 podspec 的 resources 对象, 资源规格所需的值加到里面

        - limits  内容直接追加到现有的 resources.limits 中
        - requests  可选, 有内容则追加到 resources.requests 中
        - env  可选,  内容为 key:value模式, 其取值有3类
            - str 类型,  直接将值存到 env中
            - ref 类型
                - field 从部署环境中取
                - configmap  从configmap中取值, 其值为configmap中的key
                - secret 从secret中获取值(暂不实现)
            - ref_val 根据上面 ref 的类型， 具体的值

                '''
        # if gpu_id is None or gpu_id == '':
        #     return
        # if type(gpu_id) == int and gpu_id <= 0:
        #     return

        # wuqc 2022-4-26 说环境中的gpu节点都加上污点 gpu=gpu, 要分配gpu容器都加上这个污点容忍
        # pod_spec.tolerations = [
        #     client.V1Toleration(key="gpu", operator="Exists", effect="NoSchedule")]
        pod_spec.tolerations = [
            client.V1Toleration(key="gpu", operator="Equal", effect="NoSchedule", value="gpu")]
        container: client.V1Container = pod_spec.containers[0]
        resources = container.resources
        env = container.env
        logger.debug("set gpu_res {} of {} with {}", gpu_id, res_spec, container_name)
        if type(gpu_id) == int and gpu_id > -1:
            logger.debug("compitable mode, aliyun.com/gpu-mem: {}", gpu_id)
            resources['limits']["aliyun.com/gpu-mem"] = gpu_id
            resources['requests']["aliyun.com/gpu-mem"] = gpu_id
            return
        if type(res_spec) == str:
            res_spec = json.loads(res_spec)  # zhenghj_mci 配置到表中的json串, 传入时是整个字符串, 需要再转换一次
        # for field in ('limits', 'requests'):
        #     if field in res_spec:
        #         l = resources.get(field, {})
        #         for k, v in res_spec[field].items():
        #             l[k] = v
        #         resources[field] = l
        if 'env' in res_spec:
            for k, v in res_spec['env'].items():
                if type(v) == str:
                    env.append(client.V1EnvVar(name=k, value=v))
                else:
                    if v['ref'] == 'field':
                        env.append(client.V1EnvVar(
                            name=k,
                            value_from=client.V1EnvVarSource(
                                field_ref=client.V1ObjectFieldSelector(field_path=v['ref_val'])
                            )))
                    elif v['ref'] == 'configmap':
                        vv = v['ref_val']
                        _name, _key = vv.split(':', maxsplit=1)
                        env.append(client.V1EnvVar(
                            name=k,
                            value_from=client.V1EnvVarSource(
                                field_ref=client.V1ConfigMapKeySelector(key=_key, name=_name)
                            )))
        if 'node_selector' in res_spec:
            pod_spec.node_selector = res_spec['node_selector']
        if 'tolerations' in res_spec:
            logger.info(f"tolerations ************, {res_spec['tolerations']}")
            kk = res_spec['tolerations']
            # https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/
            if type(kk) == list:
                pod_spec.tolerations = [client.V1Toleration(key=k, operator="Equal", effect="NoSchedule", value=k) for k in kk]
                # pod_spec.tolerations = [client.V1Toleration(key=k, operator="Exists", effect="NoSchedule") for k in kk]
            elif type(kk) == str:
                pod_spec.tolerations = [client.V1Toleration(key=kk, operator="Equal", effect="NoSchedule", value=kk)]
                # pod_spec.tolerations = [client.V1Toleration(key=kk, operator="Exists", effect="NoSchedule")]
        if "scheduler" in res_spec:
            pod_spec.scheduler_name = res_spec["scheduler"]  # orion-scheduler
        if "affinity" in res_spec:
            pass
            client.V1Affinity()

    def query_pod(self, deployment, get_envs=False):
        '''查询pod的状态和地址端口等
        返回: pod_name, node_host, ready, total, ports=[[target_port, protocol, node_port],...]'''
        ret = self.corev1.list_namespaced_pod(namespace=self.namespace, label_selector=f"app={deployment}")
        envs = {}
        if len(ret.items) < 1:
            pod_name, node_host, ready, total, ports = None, None, None, None, None
            #  检查 deployment 是否存在， 如果不存在， 则返回 ready='99'
            ret = self.appv1.list_namespaced_deployment(namespace=self.namespace,
                                                        field_selector=f"metadata.name={deployment}")
            if len(ret.items) < 1:
                ready = 99
            else:
                total = ret.items[0].spec.replicas
                if total < 1:
                    ready = 0
                else:
                    ready = total - ret.items[0].status.unavailable_replicas
        else:
            p = ret.items[0]
            pod_name = p.metadata.name
            st = p.status
            if st.container_statuses is None:
                if get_envs:
                    return None, None, None, None, None, None
                return None, None, None, None, None
            readyL = []
            for i in st.container_statuses:
                if i is None:
                    continue
                if i.ready:
                    readyL.append(0)
            ready = len(readyL)
            total = len(st.container_statuses)
            node_host = st.host_ip
            print("ready:", repr(ready), type(ready))
            if ready > 0 and get_envs:
                ct = p.spec.containers[0]
                for e in ct.env:
                    envs[e.name] = e.value

        ret = self.corev1.list_namespaced_service(namespace=self.namespace,
                                                  field_selector=f"metadata.name={deployment}")
        if len(ret.items) < 1:
            ports = None
        else:
            item = ret.items[0]
            ports = [[p.target_port, p.protocol, p.node_port] for p in item.spec.ports]
        if get_envs:
            return pod_name, node_host, ready, total, ports, envs
        return pod_name, node_host, ready, total, ports

    def list_event(self, name):
        self.corev1.read_namespaced_event()

    def query_pvc(self, name):
        '''查询PVC的情况, 返回名称和大小:
        phase, capacity, access_mode
        '''
        ret = self.corev1.list_namespaced_persistent_volume_claim(namespace=self.namespace,
                                                                  field_selector=f"metadata.name={name}")
        if len(ret.items) < 1:
            return None, None, "[note found]"
        else:
            ''''status': {'access_modes': ['ReadWriteMany'],
            'capacity': {'storage': '16Gi'},
            'conditions': None,
            'phase': 'Bound'}}'''
            st = ret.items[0].status
            capacity = None
            access_modes = None
            if st.capacity:
                if 'storage' in st.capacity:
                    capacity = st.capacity['storage']
            if st.access_modes:
                access_modes = st.access_modes[0]
            return st.phase, capacity, access_modes

    def query_pvc_obj(self, name):
        '''查询PVC的情况, 返回obj:
        phase, capacity, access_mode
        '''
        ret = self.corev1.read_namespaced_persistent_volume_claim(name=name, namespace=self.namespace)
        # if len(ret.items) < 1:
        #     return "[not found]"
        # else:
        #     ''''status': {'access_modes': ['ReadWriteMany'],
        #     'capacity': {'storage': '16Gi'},
        #     'conditions': None,
        #     'phase': 'Bound'}}'''
        #     # st = ret.items[0].status
        return ret

    def list_deploy(self):
        res = self.appv1.list_namespaced_deployment(self.namespace)
        logger.info("found deploy {}", len(res.items))
        return [(r.metadata.name, r.status.ready_replicas) for r in res.items]

    def get_deploy(self, deployment):
        """ 获取"""
        ret = self.appv1.list_namespaced_deployment(namespace=self.namespace,
                                                    field_selector=f"metadata.name={deployment}")
        ready = 0
        total = 0
        if not len(ret.items) < 1:
            total = ret.items[0].spec.replicas
            if total < 1:
                ready = 0
            else:
                if ret.items[0].status.unavailable_replicas is None:
                    ready = total - 0
                else:
                    ready = total - ret.items[0].status.unavailable_replicas
        return ready, total

    def k8s_query_pod(self, dep_name):
        '''根据deployment的名称, 查询pod名称

        返回: pod-name, [(inner_port,node_addr),..]
        '''
        v1 = self.corev1
        if len(dep_name.split('-')) >= 3:
            dep_name = '-'.join(dep_name.split('-')[:-2])  # 认为是pod_name

        ret = v1.list_namespaced_pod(namespace=self.namespace, label_selector=f"app={dep_name}")
        if len(ret.items) < 1:
            return None, None
        else:
            p = ret.items[0]
            pod_name = p.metadata.name
            st = p.status
            if st is None:
                return None, None
            node_host = st.host_ip
            return pod_name, node_host

    def query_svc(self, svc_name):
        v1 = self.corev1
        ret = v1.list_namespaced_service(namespace=self.namespace, field_selector=f"metadata.name={svc_name}")
        if len(ret.items) < 1:
            ports = None
        else:
            item = ret.items[0]
            p: client.models.v1_service_port.V1ServicePort = None
            ports = [(p.target_port, p.node_port, p.protocol) for p in item.spec.ports]
        return ports

    def del_svc(self, svc_name):
        try:
            resp = self.corev1.delete_namespaced_service(name=svc_name, namespace=self.namespace,
                                                         body=client.V1DeleteOptions())
            return True
        except Exception as x:
            return False

    def create_svc(self, svc_name: str,
                   Ports: list,
                   # 暴露端口, 格式: [ [name, protocol, port, node_port], ...], node_port为None时表明外部端口自 动分配
                   selector: dict,  # 如何选择服务的目标, 如: {"app":"something"}
                   ):
        v1 = self.corev1
        service = client.V1Service(api_version="v1", kind="Service")
        service.metadata = client.V1ObjectMeta(name=svc_name)
        spec = client.V1ServiceSpec()
        spec.type = "NodePort"
        spec.selector = selector
        spec.ports = [client.V1ServicePort(name=name, protocol=protocol, port=port, node_port=node_port)
                      for name, protocol, port, node_port in Ports]
        service.spec = spec
        try:
            resp = v1.create_namespaced_service(namespace=self.namespace, body=service)
            #        log.info("create service return %s", resp)
            return True
        except Exception as x:
            print(traceback.format_exc(), x)
            print(self.namespace, svc_name, Ports, selector)
            return False

    def query_deployment(self, deployment_name):
        '''
        根据pod name 查询对应deployment详细信息
        '''
        apps_v1 = self.appv1
        read_deployment = apps_v1.read_namespaced_deployment(name=deployment_name, namespace=self.namespace)
        return read_deployment

    def patc_pvc(self, name, storage, k8s_storage_class):
        logger.info("...entry _patc_pvc...")
        # try:
        #     storage_original[:-2]
        # except Exception as x:
        #     log.error(x)
        # corev1 = self.corev1
        # r = corev1.patch_namespaced_persistent_volume_claim(pvc_name, self.namespace, persistent_volume_claim)
        v1 = self.corev1

        pvc = client.V1PersistentVolumeClaim(api_version="v1", kind="PersistentVolumeClaim",
                                             metadata=client.V1ObjectMeta(name=name, labels={"pvc": name}),
                                             )

        pvc.spec = client.V1PersistentVolumeClaimSpec(storage_class_name=k8s_storage_class,
                                                      access_modes=["ReadWriteMany", ],
                                                      volume_mode="Filesystem",
                                                      resources=client.V1ResourceRequirements(
                                                          requests={"storage": f"{storage}Mi"}))
        try:
            resp = v1.patch_namespaced_persistent_volume_claim(namespace=self.namespace, body=pvc, name=name)
        except ApiException as x:
            msg = "patch pvc failed, reason: %s, body: %s" % (x.reason, x.body)
            return False, x
        return True, ''


    def patch_namespaced_deployment(self, deployment_name, deployment):

        '''
        修改deployment  cpu、mem资源（局部更新yaml）
        '''
        apps_v1 = self.appv1
        ret = apps_v1.patch_namespaced_deployment(name=deployment_name, namespace=self.namespace, body=deployment)

    def read_namespaced_persistent_volume_claim(self, pvc_name):

        '''
        根据pvc名称查询详细信息
        '''
        corev1 = self.corev1
        ret = corev1.read_namespaced_persistent_volume_claim(pvc_name, self.namespace)
        return ret

    def patch_namespaced_persistent_volume_claim(self, pvc_name, persistent_volume_claim):
        '''
        扩容pvc资源
        '''
        corev1 = self.corev1
        r = corev1.patch_namespaced_persistent_volume_claim(pvc_name, self.namespace, persistent_volume_claim)

    def get_scale(self, deploy):
        ret = self.appv1.read_namespaced_deployment_scale(deploy, self.namespace)
        if ret.spec.replicas is None:
            return 0
        return ret.spec.replicas

    def set_scale(self, deploy, scale):
        dep = self.appv1.read_namespaced_deployment_scale(deploy, self.namespace)
        dep.spec.replicas = scale
        self.appv1.patch_namespaced_deployment(deploy, self.namespace, dep)

    def pod_exec(self, container_name, message):
        stdout = None
        pod_name, node_host = self.pod_k8s_query_pod(container_name)
        exec_command = [
            "/bin/sh",
            "-c",
            'TERM=xterm-256color; export TERM; [ -x /bin/bash ] '
            '&& ([ -x /usr/bin/script ] '
            '&& /usr/bin/script -q -c "/bin/bash" /dev/null || exec /bin/bash) '
            '|| exec /bin/sh']
        container_stream = stream(self.corev1.connect_get_namespaced_pod_exec, name=pod_name, namespace=self.namespace,
                                  container=container_name, command=exec_command, stderr=True, stdin=True, stdout=True,
                                  tty=True, _preload_content=False)
        if (message != None):
            container_stream.write_stdin(str(message, encoding="utf-8"))
            stdout = container_stream.readline_stdout(timeout=3)
        return stdout

    def namespace_resource(self):
        v1 = self.corev1

        ret = v1.list_namespaced_resource_quota(namespace=self.namespace)
        if len(ret.items) < 1:
            return None, None
        else:
            logger.info(f"ret is {ret}")
            p = ret.items[0]
            res_hard = p.status.hard
            res_used = p.status.used
            # logger.info(f"res_hard is {res_hard}")
            # logger.info(f"res_used is {res_used}")
            return res_hard, res_used
            # st = p.status
            # if st is None:
            #     return None, None
            # node_host = st.host_ip
            # return pod_name, node_host

    def update_res(self, deploy):
        dep = self.appv1.read_namespaced_deployment_scale(deploy, self.namespace)
        logger.log(f'dep {dep}')

# from svc.util.k8s import K8S
# k8s = K8S('/pyserv/.kube/y13932-crmmci-ai.kubeconfig')
# rr = k8s.query_pod('plcont-negs5x', get_envs=True)
