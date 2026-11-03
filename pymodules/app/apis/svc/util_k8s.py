import traceback

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from loguru import logger
import yaml

# 创建deployment
def k8s_create_deployment(name_space: str, dep_name: str, imageId: str,
                      cpu: float,  # CPU limit, cores
                      memory: int,  # Memory limit, Mb
                      entrypoint: str, #启动程序
                      args: list = None,   # 启动参数
                      PVCs: list = None,   # 挂载卷list, 格式: [[pvc_name, mount_path],...]
                      ENVs: dict = None,   # 设置环境变量, 格式: {name: value, name1:value1}
                      wd: str = None,     # 工作目录
                      fsGroup: int = None,  # 用户id, 非root启动容器
                      gpu: int = None, ### add by cong,  分配GPU 显存数， 单位： GB
                      extra_mounts: list = None, # 额外的直接需要挂载的mount路径， item格式：vol_name,mount_path, 只读挂载
                      shmsize: int = -1, # 是否设置shm大小,  -1为不设置， 单位 MB
                      cpu_request_times: int = 50,  # k8s配置的CPU超分配倍数， 68环境是50倍， 75是20倍（应该是跟namespace捆绑的）
                     ):
    v1 = client.AppsV1Api()

    # Configure Pod template container
    mounts = []
    vols = []
    if PVCs is not None:
        for i, (pvc_name, mount_path) in enumerate(PVCs):
            vols.append(client.V1Volume(name=f"volume{i}", persistent_volume_claim=
                        client.V1PersistentVolumeClaimVolumeSource(claim_name=pvc_name)))
            mounts.append(client.V1VolumeMount(mount_path=mount_path, name=f"volume{i}"))
    if shmsize > 0:
        if vols is None:
            vols = []
            mounts = []
        vol = client.V1Volume(name=f"cache-volume", empty_dir=client.V1EmptyDirVolumeSource(medium="Memory", size_limit=f"{shmsize}Mi"))
        vols.append(vol)
        mounts.append(client.V1VolumeMount(mount_path="/dev/shm", name=f"cache-volume"))

    if extra_mounts is not None:
        for i, (vol, mpath) in enumerate(extra_mounts):
            vols.append(client.V1Volume(name=f"vol-{i}", persistent_volume_claim=
                        client.V1PersistentVolumeClaimVolumeSource(claim_name=vol)))
            mounts.append(client.V1VolumeMount(mount_path=mpath, name=f"vol-{i}", read_only=True))

    # for test only
    # vols.append(client.V1Volume(name="local-dir", host_path=client.V1HostPathVolumeSource(path="/k8s/data", type="Directory")) )
    # mounts.append(client.V1VolumeMount(mount_path='/data1', name=f"local-dir") )

    if ENVs is not None:
        env = [{"name":k, "value":v} for k, v in ENVs.items()]
    else:
        env = None
    mem = f"{memory}Mi"
    #cpu_req = f"{cpu * 1000 / _conf.get('cpu_request_times', 50)}m"  # 在68环境是 50倍(1000/50=20)超分, 75环境则是 20倍超分

    if cpu_request_times > 0:
        cpu_req = f"{cpu * 1000 / cpu_request_times}m"  # 在68环境是 50倍(1000/50=20)超分, 75环境则是 20倍超分
        cpu = f"{cpu:.1f}"

        if gpu is None:
            resources={'limits': {'memory': mem, 'cpu': cpu},
                       'requests':{'memory':mem, 'cpu': cpu_req}}
        else:
            # resources={'limits': {'memory': mem, 'cpu': cpu, "aliyun.com/gpu-mem": gpu},
            #            'requests':{'memory': mem, 'cpu': cpu_req}}
            resources = {'limits': {'memory': mem, 'cpu': cpu,
                                    "nvidia.com/gpu": 1, # 指定的是使用n个物理GPU卡；
                                    "nvidia.com/gpumem": gpu*1024, # Each vGPU contains 3000m device memory （Optional,Integer）
                                    "nvidia.com/gpucores": 50, # Each vGPU uses 30% of the entire GPU （Optional,Integer)
                                    },
                         'requests': {'memory': mem, 'cpu': cpu_req}}
    else:
        if gpu is None:
            resources={'limits': {'memory': mem, 'cpu': cpu} }
        else:
            resources = {'limits': {'memory': mem, 'cpu': cpu, "aliyun.com/gpu-mem": gpu}}
    container = client.V1Container(
        name = dep_name,
        image = imageId,
        image_pull_policy="IfNotPresent",
#        image_pull_policy="Always",
        command=[entrypoint],
        env=env,
        args=args,
        resources=resources,
        volume_mounts=mounts,
        working_dir=wd,
    )
    sec = None if fsGroup is None else client.V1PodSecurityContext(fs_group=fsGroup, run_as_user=fsGroup)
    spec = client.V1PodSpec(containers=[container], volumes=vols, security_context=sec)

    # for develop env
    # if gpu is None:
    #     spec=client.V1PodSpec(containers=[container], volumes=vols, security_context=sec)
    # else:
    #     spec=client.V1PodSpec(containers=[container], volumes=vols, node_name="k8s-slave-gpu", security_context=sec)

    # for zjdx
    # if gpu is None:
    #     spec=client.V1PodSpec(containers=[container], volumes=vols, security_context=sec)
    # else:
    #    host = random.choice(['jqyx-sjwj01', 'jqyx-sjwj02'])
    #    env.append({'name':'NVIDIA_VISIBLE_DEVICES', 'value': 'all'})
    #    spec=client.V1PodSpec(containers=[container], volumes=vols, node_name=host,security_context=sec)

    # Create and configurate a spec section
    template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(labels={"app": dep_name}),
        spec=spec)
    # Create the specification of deployment
    spec = client.V1DeploymentSpec(
        replicas=1,
        template=template,
        selector={'matchLabels': {'app': dep_name}})
    # Instantiate the deployment object
    deployment = client.V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=client.V1ObjectMeta(name=dep_name),
        spec=spec)
    # Create deployement
    try:
        api_response = v1.create_namespaced_deployment(
            body=deployment,
            namespace=name_space)
 #       log.info("create deployment %s, return %s", dep_name, api_response)
        return True
    except Exception as x:
        # 将创建错误信息写入数据库中
        # db.devc_update_errormsg(dep_name,"create deployment %s failed %s" % (dep_name, x))
        logger.error("create deployment {} failed {}", dep_name, x)
    return False



def k8s_create_pvc(name_space:str, pvc_name:str, size_in_mb:int, storage_class:str="glusterfs-sc"):
    v1 = client.CoreV1Api()
    pvc = client.V1PersistentVolumeClaim(api_version="v1", kind="PersistentVolumeClaim",
                                         metadata=client.V1ObjectMeta(name=pvc_name,labels={"pvc": pvc_name}),
                                         )
    pvc.spec = client.V1PersistentVolumeClaimSpec(storage_class_name=storage_class,
                                                  access_modes=["ReadWriteMany", ],
                                                  volume_mode="Filesystem",
                                                  resources=client.V1ResourceRequirements(requests={"storage": f"{size_in_mb}Mi"}))
    try:
        resp = v1.create_namespaced_persistent_volume_claim(namespace=name_space, body=pvc)
    except ApiException as x:
        # db.devc_update_errormsg(pvc_name,"create pvc failed, reason: %s, body: %s" % (x.reason, x.body))
        logger.error("create pvc failed, reason: {}}, body: {}", x.reason, x.body)
        return False
    except Exception as x:
        logger.error("create pvc failed: {}, {}", x, traceback.format_exc())
        return False
    if resp.status.phase == 'Pending':
        return True
    else:
        logger.warning("create pvc failed11, return {}", resp)
        return False


# 创建service
def k8s_create_service(name_space, svc_name: str,
                       Ports: list,  # 暴露端口, 格式: [ [name, protocol, port, node_port], ...], node_port为None时表明外部端口自动分配
                       selector: dict, # 如何选择服务的目标, 如: {"app":"something"}
                       ):
    v1 = client.CoreV1Api()
    service = client.V1Service(api_version="v1", kind="Service")
    service.metadata = client.V1ObjectMeta(name=svc_name)
    spec = client.V1ServiceSpec()
    spec.type = "NodePort"
    spec.selector = selector
    spec.ports = [client.V1ServicePort(name=name, protocol=protocol, port=port, node_port=node_port)
                    for name, protocol, port, node_port in Ports]
    service.spec = spec
    try:
        resp = v1.create_namespaced_service(namespace=name_space, body=service)
#        log.info("create service return %s", resp)
        return True
    except Exception as x:
        # db.devc_update_errormsg(svc_name,"create service failed: %s" % x)
        logger.error("create service failed: {}", x)
        return False

def k8s_query_pod(name_space, dep_name, svc_name):
    '''查询pod的状态和地址端口等
    返回: pod_name, node_host, ready, total, ports=[[target_port, protocol, node_port],...]'''
    v1 = client.CoreV1Api()
    appv1 = client.AppsV1Api()

    ret = v1.list_namespaced_pod(namespace=name_space, label_selector=f"app={dep_name}")
    if len(ret.items) < 1:
        pod_name, node_host, ready, total,ports = None, None, None, None,None
        # todo 检查 deployment 是否存在， 如果不存在， 则返回 ready='99'
        ret = appv1.list_namespaced_deployment(namespace=name_space, field_selector=f"metadata.name={dep_name}")
        if len(ret.items) < 1:
            ready = 99
    else:
        p = ret.items[0]
        pod_name = p.metadata.name
        st = p.status
        if st.container_statuses is None:
            return None, None, None, None,None
        readyL = []
        for i in st.container_statuses:
            if i is None:
                continue
            if i.ready:
                readyL.append(0)
        ready = len(readyL)
        total = len(st.container_statuses)
        node_host = st.host_ip

    ret = v1.list_namespaced_service(namespace=name_space, field_selector=f"metadata.name={svc_name}")
    if len(ret.items) < 1:
        ports = None
    else:
        item = ret.items[0]
        ports = [[p.target_port, p.protocol, p.node_port] for p in item.spec.ports]
    return pod_name, node_host, ready, total, ports

def k8s_delete_all(name_space, dep_name=None, svc_name=None, pvc_name=None):
    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    if dep_name is not None:
        try:
            resp = apps_v1.delete_namespaced_deployment(name=dep_name, namespace=name_space,
                                                        body=client.V1DeleteOptions(propagation_policy='Foreground',
                                                                                    grace_period_seconds=5))
            logger.info("delete deployment {} return {}", dep_name, resp)
        except Exception as x:
            logger.error("delete deployment {} failed, but Continue... {}",dep_name, x)
    if svc_name is not None:
        try:
            resp = v1.delete_namespaced_service(name=svc_name, namespace=name_space, body=client.V1DeleteOptions())
            logger.info("delete service {}} return {}", svc_name, resp)
        except Exception as x:
            logger.error("delete service %s failed, but Continue... %s" % (svc_name, x))
    if pvc_name is not None:
        try:
            resp = v1.delete_namespaced_persistent_volume_claim(name=pvc_name,
                                                                namespace=name_space, body=client.V1DeleteOptions())
            logger.info("delete pvc {} return {}", pvc_name, resp)
        except Exception as x:
            logger.error("delete pvc %s failed, but Continue...%s" % (pvc_name, x))
   # 查询pvc，并删除pvc对应的svc
    if pvc_name is not None:
        try:
            ret = v1.list_namespaced_persistent_volume_claim(namespace=name_space, label_selector=f"pvc={pvc_name}")
            pvc_info = ret.items[0]
            vname = pvc_info.spec.volume_name
            svc_gd_name = 'glusterfs-dynamic-' + vname[4:]
            ret = v1.list_namespaced_service(namespace=name_space, field_selector="metadata.name=%s" % svc_gd_name)
        except Exception as x:
            logger.error("delete svc %s failed, but Continue...%s"% (svc_name, x) )

