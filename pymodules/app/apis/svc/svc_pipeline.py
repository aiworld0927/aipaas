import shlex
import subprocess

from flask import Flask, request, Response, abort
import traceback
from loguru import logger
import json
import os
import hashlib
import time
import re
import threading
import socket

import requests
import pandas as pd
import svc.util_db as _db
import svc.util_common as _util
from svc.util.k8s import K8S
from svc.util import _rjson
from svc.util import rds
from svc.util import db

from svc.util import _gen_rand_str
from svc.util import remote, ng
from pyserv_conf import horizontal_pod_autoscaler_conf as hpa_conf
from svc.svc_container import get_kube_info, check_namespace_res, res_configuration


_base_dir = '/pyserv' if os.path.exists('/pyserv') else '/data/.app'


def registerHandler(app, conf):
    global _conf

    _conf = conf
    app.add_url_rule("/pl/enter_edit_mode", "pl-enter_edit_mode", enter_edit_mode, methods=["POST"])   # 编辑流水线（启动图形化建模容器）
    app.add_url_rule("/pl/enter_edit_mode_new", "pl-enter_edit_mode_new", enter_edit_mode_new, methods=["POST"])   # 新编辑流水线（启动图形化建模容器）

    app.add_url_rule("/pl/exit_edit_mode", "pl-exit_edit_mode", exit_edit_mode, methods=["POST"])   # 退出流水线旧（删除图形化建模容器）
    app.add_url_rule("/pl/stop_container", "pl-stop_container", stop_container, methods=["POST"])   # 停止图形化建模容器
    app.add_url_rule("/pl/restart_container", "pl-restart_container", restart_container, methods=["POST"])   # 重启图形化建模容器

    app.add_url_rule("/pl/create_container", "pl-create_container", create_container, methods=["POST"])
    app.add_url_rule("/pl/query_container", "pl-query_container", query_container, methods=["POST"])
    app.add_url_rule("/pl/delete_container", "pl-delete_container", delete_container, methods=["POST"])
    # app.add_url_rule("/pl/deploy_app", "pl-deploy_model", Model.deploy_app, methods=["POST"])
    app.add_url_rule("/pl/destroy_app", "pl-destroy_app", Model.destroy_app, methods=["POST"])
    app.add_url_rule("/pl/destroy_app_new", "pl-destroy_app_new", Model.destroy_app_new, methods=["POST"])    # 图形化建模新版部署应用删除接口

    app.add_url_rule("/pl/get_deploy_logs", "pl-deploy-logs", Model.get_deploy_logs, methods=["POST"])
    app.add_url_rule("/pl/final_deploy_app", "pl-final-deploy-app", Model.final_deploy_app, methods=["POST"])
    app.add_url_rule("/pl/final_deploy_app_new", "pl-final_deploy_app_new", Model.final_deploy_app_new, methods=["POST"])   # 图形化建模新版部署接口
    app.add_url_rule("/pl/aiops_deploy_app", "pl-aiops-deploy-app", Model.aiops_deploy_app, methods=["POST"])
    app.add_url_rule("/pl/check_run_result", "pl-check-run-result", Model.check_run_result, methods=["POST"])
    app.add_url_rule("/pl/monitor_test", "pl-monitor-test", monitor_test, methods=["get"])
    app.add_url_rule("/pl/get_app_ip", "pl-get-app-ip", Model.get_app_ip, methods=["POST"])
    app.add_url_rule("/pl/pipeline_import", "pipeline-import", Model.pipeline_import, methods=["POST"])
    # app.add_url_rule("/pl/get_namespace_resource", "get-namespace-resource", get_namespace_resource, methods=["POST"])


# def get_namespace_resource():
#     k8s = K8S(_conf['k8s_config_file'])
#     try:
#         res_hard, res_used = k8s.namespace_resource()
#         return _rjson({"res_hard": res_hard, "res_used": res_used})
#     except Exception as e:
#         logger.info(f"get namespace resource failed [{e}]")
#         _rjson(None, 402, f"get namespace resource failed [{e}]")


def enter_edit_mode(params=None, is_request=True):
    try:
        if is_request:
            jsons = request.stream.read().decode('utf-8')
            params = json.loads(jsons)['BODY']
        logger.info("...enter pl/enter_edit_mode...[{}]", params)
        user_name = params["user_name"]  # 用户名
        proj_name = params['proj_name']  # 项目名称
        expe_name = params['expe_name']  # 实验名称
        run_id = params.get('run_id', 'RUN_1')  # 运行id
        res_tmpl = params.get('res_tmpl', None)  # 资源模板id
        image = params.get("image", None)  # 基础镜像
        container_name = params.get("container_name", None)  # 容器名称， 如果传 None or "",  则服务内生成
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))

    try:
        #  先检查是否已经释放
        ext_info = db.PL.get_dev_run_ext(expe_name, run_id)
        logger.info("enter_edit_mode:  get_dev_run_ext return {}", ext_info)
        try:
            _ = json.loads(ext_info)
            logger.warning("container not release, exit")
            return _rjson({'container_name': _['container_name']}, 7701,
                          "container not released yet, try again please!")
        except:
            pass
        # rd = rds.RedisHelper(host=_conf['local_redis_addr'])
        if container_name is None or container_name == "":
            container_name = f"plcont-{_gen_rand_str(6).lower()}"
        params['__container'] = container_name
        log_id = db.PL.create_progress(container_name, user_name, "enter_edit_mode")
        if log_id is None:
            raise Exception("create dev_progress failed")
        params['__log_id'] = log_id
        params['__k8s_storage_class'] = _conf.get('k8s_storage_class', 'glusterfs-sc')
        params['run_id'] = run_id
        th = threading.Thread(target=_enter_edit_mode,
                              args=(params,))
        th.start()
        logger.info("begin creating container: {}", container_name)
        return _rjson({"log_id": log_id, "container_name": container_name})
    except Exception as x:
        logger.error("enter_edit_mode failed {} , {}", str(x), traceback.format_exc())
        return _rjson(None, 701, "enter edit mode failed:" + str(x))


def enter_edit_mode_new(params=None, is_request=True):
    try:
        if is_request:
            jsons = request.stream.read().decode('utf-8')
            params = json.loads(jsons)['BODY']
        logger.info("...enter pl/enter_edit_mode_new...[{}]", params)
        user_name = params["user_name"]  # 用户名
        proj_name = params['proj_name']  # 项目名称
        expe_name = params['expe_name']  # 实验名称
        run_id = params.get('run_id', 'RUN_1')  # 运行id
        image = params.get("image", None)  # 基础镜像
        container_name = params.get("container_name", None)  # 容器名称， 如果传 None or "",  则服务内生成
        # 资源池
        scene_type = params.get('scene_type', '02')  # scene_type  01 - 训练资源池；02 - 试用资源池；03 - 推理资源池
        calc_type = params.get('calc_type', '01')  # calc_type  01-通用算力；02-GPU算力；03-NPU算力

        cpu = params['res']["cpu"]  # 使用CPU的 cores 数量, 可以为小数
        mem = params['res']["mem"]  # 内存使用限制, 单位 MB
        # shmsize = jo['res'].get('shmsize', -1)  # 设置shmsize， 默认不处理， 则由容器平台分配64Mb； 单位 Mb
        storage = params['res']["storage"]  # 卷空间大小, 单位MB, 挂载位置固定为 /data

        gpu = params['res'].get("gpu", None)  # 分配gpu，显卡个数，每张显存大小, 算力百分比，单位(第四范式mb，阿里云mb), 默认None为不分配
        npu = params['res'].get("npu", None)  # 分配npu, 卡的个数
        gpuType = params['res'].get("gpuType", None)  # 1:阿里云，2:第四范式，3:驱动 有gpu时填写

    except Exception as x:
        create_log = f'start failed, Invalid request [{str(x)}]'
        _db.devc_update_status(container_name, "07", error_msg=create_log)
        return _rjson(None, 402, "Invalid request [%s]" % str(x))

    try:
        # 设置GPU/NPU/CPU资源标识
        if gpu:
            # GPU资源配置
            params['res']['resType'] = 1
        elif npu:
            # npu设置
            params['res']['resType'] = 2
        else:
            params['res']['resType'] = -1

        # 多集群适配，获取k8s配置文件以及namespace名称
        kube_info = get_kube_info(calc_type, scene_type)
        if not kube_info:
            create_log = 'start failed，get kubeconfig failed'
            _db.devc_update_status(container_name, "07", error_msg=create_log)
            return _rjson(None, 403, create_log)

        params['kubeconfig'] = kube_info['kubeconfig']
        params['namespace'] = kube_info['namespace']
        if 'storage_class_name' in kube_info.keys():
            if kube_info['storage_class_name']:
                params['__k8s_storage_class'] = kube_info['storage_class_name']
            else:
                params['__k8s_storage_class'] = _conf.get("k8s_storage_class", "glusterfs-sc")
        else:
            params['__k8s_storage_class'] = _conf.get("k8s_storage_class", "glusterfs-sc")

        # 判断申请的资源是否足够
        res_info = {
            'cpu': cpu,
            'mem': mem
        }
        flag, msg = check_namespace_res(params['kubeconfig'], params['namespace'], res_info)
        if not flag:
            create_log = f'start failed，{msg}'
            logger.info(create_log)
            _db.devc_update_status(container_name, "07", error_msg=create_log)
            return _rjson(None, 405, create_log)

        #  先检查是否已经释放
        ext_info = db.PL.get_dev_run_ext(expe_name, run_id)
        logger.info("enter_edit_mode:  get_dev_run_ext return {}", ext_info)
        try:
            _ = json.loads(ext_info)
            logger.warning("container not release, exit")
            create_log = f'start failed, container not release, exit]'
            _db.devc_update_status(container_name, "07", error_msg=create_log)
            return _rjson({'container_name': _['container_name']}, 7701,
                          "container not released yet, try again please!")
        except:
            pass
        # rd = rds.RedisHelper(host=_conf['local_redis_addr'])
        if container_name is None or container_name == "":
            container_name = f"plcont-{_gen_rand_str(6).lower()}"
        params['__container'] = container_name
        log_id = db.PL.create_progress(container_name, user_name, "enter_edit_mode")
        if log_id is None:
            raise Exception("create dev_progress failed")
        params['__log_id'] = log_id
        params['run_id'] = run_id
        th = threading.Thread(target=_enter_edit_mode_new,
                              args=(params,))
        th.start()
        logger.info("begin creating container: {}", container_name)
        return _rjson({"log_id": log_id, "container_name": container_name})
    except Exception as x:
        create_log = f"enter_edit_mode failed {str(x)} , {traceback.format_exc()}"
        logger.error(create_log)
        _db.devc_update_status(container_name, "07", error_msg=create_log)
        return _rjson(None, 701, "enter edit mode failed:" + str(x))


def exit_edit_mode():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter pl/exit_edit_mode...[{}]", jsons)
        params = json.loads(jsons)['BODY']
        user_name = params.get("user_name")
        proj_name = params.get('proj_name')
        expe_name = params.get('expe_name')
        run_id = params.get('run_id')
        container_name = params.get('container_name', None)
        detect = params.get('detect', True)   # 探测是否停止容器
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))

    rd = None
    try:
        rd = rds.RedisHelper(host=_conf['local_redis_addr'])
        log_id = db.PL.create_progress(container_name, user_name, "exit_edit_mode")
        if log_id is None:
            raise Exception("create dev_progress failed")
        params['__log_id'] = log_id

        # message = ['ppl.exit_edit_mode', params]
        # # rd.put_message("task", )
        # rd.put_message("task", json.dumps(message))
        th = threading.Thread(target=_exit_edit_mode,
                              args=(params, detect))
        th.start()
        return _rjson({"log_id": log_id})
    except Exception as x:
        logger.error("exit_edit_mode failed {} , {}", str(x), traceback.format_exc())
        return _rjson(None, 701, "enter edit mode failed:" + str(x))
    finally:
        if rd is not None:
            rd.close()


def create_container():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter pl/create_container...[{}]", jsons)
        jo = json.loads(jsons)['ROOT']
        user = jo['user']
        container_name = jo['container_name']
        res = jo['res']  # {"cpu":1, "mem":4096, "gpu":-1,"disk":16}
        user_ns = jo.get("user_ns", None)  # 用户空间， 用于切换不同的k8s namespace， 配置项: "user_ns":{"n1":"/a/b/1.kubeconfig",...}
        image = jo.get("image")  # 镜像全路径
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))

    fsGroup = 8989  # 是否使用root启动容器
    if user_ns is None:
        k8s = K8S(_conf['k8s_config_file'])
    else:
        k8s = K8S(_conf['user_ns'][user_ns])

    token = _util._gen_rand_str(6)
    if not k8s.create_pvc(container_name, res.get("disk"),
                          storage_class_name=_conf.get("storage_class_name", "glusterfs-sc")):
        return _rjson(None, 403, "create pvc failed!")
    extra_mounts = [['pipeline-python', '/app', True]] if os.path.exists('/pyserv') else [['app1', '/app', True]]

    # extra_mounts = [['pipeline-python', '/app', True]]
    env = {
        "PYPI_SERVER": _conf["pypi_server"],
        "_USERNAME_": user,
        "_SVC_ADDR_": _conf['__svc_addr__'],
        "NVIDIA_VISIBLE_DEVICES": "all",
        "TOKEN": token,
    }
    cpu_request_times = _conf.get("cpu_request_times", 50)
    logger.info("--cpu_request_times={}", cpu_request_times)

    if not k8s.create_deployment(container_name, image, res['cpu'], res['mem'],
                                 entrypoint='/app/anaconda3/bin/python', args=['/app/mng.py', '/data/.app/proc.json'],
                                 PVCs=[[container_name, "/data"]], ENVs=env, wd="/data",
                                 fsGroup=fsGroup, gpu=res.get('gpu', -1), extra_mounts=extra_mounts, shmsize=-1,
                                 cpu_request_times=cpu_request_times):
        k8s.delete_all(pvc_name=container_name)
        return _rjson(None, 404, "create deployment failed")

    if not k8s.create_service(container_name,
                              Ports=[["proc", "TCP", 8181, None], ["ssh", "TCP", 2022, None],
                                     ["jupyter", "TCP", 8085, None]],
                              selector={"app": container_name}):
        k8s.delete_all(pvc_name=container_name, dep_name=container_name)
        return _rjson(None, 405, "create service failed")

    return _rjson({"status": "container create in progressing", "token": token})


def stop_container():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter pl/stop_container...[{}]", jsons)
        jo = json.loads(jsons)['BODY']
        user = jo['user']
        name = jo['name']  # 容器名称
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))

    try:
        # 获取容器信息，集群信息等
        container_info = _db.devc_get_v119(name)
        logger.info(container_info)
        if container_info is None:
            return _rjson(None, 5023, f"not found container {name}")
        # 修改状态为正在关闭
        # db.devc_update_status(name, '03', user)
        # 修改deploy副本数为0
        if "kubeconfig" not in container_info.keys() or "namespace" not in container_info.keys():
            k8s = K8S(container_info.get("kubeconfig", _conf['k8s_config_file']))
        else:
            k8s = K8S(container_info["kubeconfig"], container_info["namespace"])

        k8s.set_scale(name, 0)
    except Exception as x:
        # 更新数据库表dev_container，字段CONTAINER_STATUS，值05:，即关闭失败
        logger.error("stop container failed, Reason: %s" % (x))
        # 删除失败，更新数据库
        _db.devc_update_status(name, '05', op_id=user)
        return _rjson({}, rcode=405, rmsg="stop container failed")

    # 更新数据库表dev_container，字段CONTAINER_STATUS，值04。即已关闭
    _db.devc_update_status(name, '04', op_id=user)
    # 更新dev_run 表的状态
    ret = db.PL.update_dev_run(container_info['expe_name'], container_info['run_id'], 'CK-OFF', '', None)  # 更新运行状态
    logger.info("{} {} to CK-OFF return {}", container_info['expe_name'], container_info['run_id'], ret)
    return _rjson({})


def restart_container():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter TaskCon_restart...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        user = jo['user']
        name = jo['name']  # 容器名称
    except Exception as x:
        # 更新数据库表dev_container，字段CONTAINER_STATUS，值06:即启动失败
        logger.error("Invalid request [%s]" % str(x))
        # 重启失败，更新数据库
        _db.devc_update_status(name, '06', op_id=user)
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # 根据容器名称查询容器信息
    container_info = _db.devc_get_v119(name)
    logger.info(container_info)
    if container_info is None:
        # 更新数据库表dev_container，字段CONTAINER_STATUS，值06:即启动失败
        logger.error(f"not found container {name}")
        # 重启失败，更新数据库
        _db.devc_update_status(name, '06', op_id=user)
        return _rjson(None, 5023, f"not found container {name}")

    try:
        # 修改状态为正在启动
        # db.devc_update_status(name, '01', user)
        # 修改deploy副本数为1
        if "kubeconfig" not in container_info.keys() or "namespace" not in container_info.keys():
            kubeconfig = container_info.get("kubeconfig", _conf['k8s_config_file'])
            k8s = K8S(kubeconfig)
        else:
            kubeconfig = container_info["kubeconfig"]
            k8s = K8S(kubeconfig, container_info["namespace"])
        # kubeconfig = container_info.get('kubeconfig', _conf['k8s_config_file'])
        # k8s = K8S(kubeconfig)
        k8s.set_scale(name, 1)
    except Exception as x:
        # 更新数据库表dev_container，字段CONTAINER_STATUS，值06:即启动失败
        logger.error("restart container failed, Reason: %s" % (x))
        # 重启失败，更新数据库
        _db.devc_update_status(name, '06', op_id=user)
        return _rjson({}, rcode=405, rmsg="restart container failed")

    # 启动进程，探索jupyter容器创建及jupyter进程启动进度
    # dev_info = {"depName": name, "pvcName": name, "serviceName": name, "kubeconfig": kubeconfig, "user": user}
    container_info['user_name'] = user
    log_id = db.PL.create_progress(container_info['depName'], user, "enter_edit_mode")
    if log_id is None:
        raise Exception("create dev_progress failed")
    container_info['__log_id'] = log_id
    container_info['__container'] = container_info['depName']
    container_info['__k8s'] = k8s
    # 判断参数
    if "token" not in container_info.keys() or "uri" not in container_info.keys() or "proj_name" not in container_info.keys():
        # 查dev_run表获取
        # 获取实验名称
        run_id = "RUN_1"
        exp_name = db.PL.get_experiment(name)
        ext_info = db.PL.get_dev_run_ext(exp_name, run_id)  # 先取出 ext_info
        j = None
        try:
            j = json.loads(ext_info)
        except:
            logger.error("can not parse json of ext_info {} {}, [{}], use query_pod instead", exp_name, run_id,
                         ext_info)
        try:
            container_info['token'] = j['token']
            container_info['uri'] = j['uri']
            container_info['proj_name'] = j['proj_name']
        except Exception as e:
            logger.error("get ext_info failed")
            return _rjson({}, rcode=405, rmsg="restart container failed")


    is_restart = True
    th = threading.Thread(target=_container_check_thread_new, args=(container_info, is_restart))
    th.start()

    logger.info("container info: {}", container_info)
    return _rjson({})


def _stop_container(container_name, k8s):
    begin_time = time.time()
    while time.time() - begin_time < 240:
        time.sleep(5)
        # 服务删除后端口不能访问了,  但容器可能还在, 不能通过端口检查来判断容器是否 down 掉
        try:
            # 检查 pod 是否存在
            pod_name, node_host, ready, total, ports = k8s.query_pod(container_name)
            if pod_name is not None:
                continue
            else:
                _db.devc_update_status(container_name, "04")
                break
        except:
            _db.devc_update_status(container_name, "06", error_msg="lack of resources")


def query_container():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter pl/create_container...[{}]", jsons)
        jo = json.loads(jsons)['ROOT']
        container_name = jo['container_name']
        user_ns = jo.get("user_ns", None)  # 用户空间， 用于切换不同的k8s namespace， 配置项: "user_ns":{"n1":"/a/b/1.kubeconfig",...}
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))

    k8s = K8S(_conf['k8s_config_file'] if user_ns is None else _conf['user_ns'][user_ns])

    pod_name, node_host, ready, total, ports = k8s.query_pod(container_name)
    return _rjson({"pod_name": pod_name, "node_host": node_host, "ready": ready, "total": total, "ports": ports})


def delete_container():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter pl/create_container...[{}]", jsons)
        jo = json.loads(jsons)['ROOT']
        container_name = jo['container_name']
        user_ns = jo.get("user_ns", None)  # 用户空间， 用于切换不同的k8s namespace， 配置项: "user_ns":{"n1":"/a/b/1.kubeconfig",...}
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    k8s = K8S(_conf['k8s_config_file'] if user_ns is None else _conf['user_ns'][user_ns])
    try:
        k8s.delete_all(dep_name=container_name, svc_name=container_name, pvc_name=container_name)
    except Exception as x:
        logger.error("delete container failed {}", x)
        return _rjson(None, 401, "delete container failed %s" % x)
    return _rjson({"status": "done"})


_res_templates = {
    "_2c4g16g": {"cpu": 2, "mem": 4096, "disk": 16 * 1024},
    "_4c8g32g": {"cpu": 4, "mem": 8192, "disk": 32 * 1024},
    "_8c16g32g": {"cpu": 8, "mem": 16384, "disk": 32 * 1024},
    "_4c8g32ggpu": {"cpu": 4, "mem": 8192, "disk": 32 * 1023, "gpu": 8},
    "_def": {"cpu": 2, "mem": 2048, "disk": 16 * 1024}
}


def failed_func(log_id, deal_log, note, errcode: int = -1):
    if errcode > 0:
        errcode = 0 - errcode
    logger.error("failed {}: {}", log_id, deal_log)
    db.PL.update_progress(log_id, -1, deal_log, f"{errcode} - {note}")


def prog_func(log_id, percent, deal_log):
    logger.info("update progress {} {} {}", log_id, percent, deal_log)
    db.PL.update_progress(log_id, percent, deal_log, '-')


def _enter_edit_mode_new(params):
    '流水线进入编辑状态'

    logger.info("--- _enter_edit_mode_new  {}", params)
    # 创建容器, 并等待完成
    user_name = params.get("user_name")
    proj_name = params.get('proj_name')
    expe_name = params.get('expe_name')
    run_id = params.get('run_id')
    image = params.get("image", None)

    log_id = params.get("__log_id")  # 这个是由调用此函数的服务产生的, 不是客户端提供, 以 __ 为前缀做区分
    logger.info("using log_id {}", log_id)

    try:
        db.PL.update_dev_run_status(expe_name, run_id, 'BJ-OPE', '分配容器中')
        fsGroup = 8989  # 是否使用root启动容器
        # 初始化k8s
        if 'namespace' in params.keys():
            k8s = K8S(params['kubeconfig'], params['namespace'])
        else:
            k8s = K8S(params['kubeconfig'])

        params['__k8s'] = k8s
        container_name = params.get('__container', None)
        if container_name is None:
            container_name = f"plcont-{_gen_rand_str(6).lower()}"  # k8s name must be lower case

        # 获取容器启动命令
        if image is None:
            # image = _conf['pipeline_base_image']
            image = "pipeline_01"
        _base = _conf.get(image, None)
        if not _base:
            logger.warning("directly use image {}", image)
            _base = {"image": image,
                     "cmd": ["/opt/pygm/bin/python", "/app/mng.py", "/app/plcont.json"]}
            # return failed_func(log_id, "can not found image conf", "contact admin please", 709)
        # 资源参数处理
        resInfo = res_configuration(params['res']['resType'], params['res'])

        params['__res'] = resInfo
        params['__image'] = _base.get('image', '[not found]')

        token = _gen_rand_str(6)
        params['token'] = token
        params['uri'] = uri = f"/jupyter-{int(time.time() * 1000)}"
        # 用户卷处理: 先查询用户卷是否存在, 不存在则创建, 否则就直接挂载
        pvc_name = f"uservol-{hashlib.sha1(user_name.encode('utf8')).hexdigest()}"
        phase, capacity, access_mode = k8s.query_pvc(pvc_name)
        if phase is not None:
            logger.info("pvc {} already exists, capacity: {}", pvc_name, capacity)
        else:
            logger.info("pvc {} not exists, create it with 16Gi")
            if not k8s.create_pvc(pvc_name, 16 * 1024,  # res.get("disk"),
                                  storage_class_name=params['__k8s_storage_class']):
                create_log = "create pvc failed!"
                logger.error(create_log)
                _db.devc_update_status(container_name, "07", error_msg=create_log)
                return failed_func(log_id, "create pvc failed!", "contact admin please", 701)
            prog_func(log_id, 0.1, "创建数据卷中...")
        params['pvc_name'] = pvc_name

        extra_mounts = [['pipeline-python', '/app', False]] if os.path.exists('/pyserv') else [['app1', '/app', False]]

        # extra_mounts = [['pipeline-python', '/app', True]] if os.path.exists('/pyserv') else [['app1', '/app', True]]
        # extra_mounts = [['app1', '/app', True]]
        # extra_mounts = [['pipeline-python', '/app', True]]
        try:
            svc_addr = _conf['__svc_addr__']
            pypi = _conf["pypi_server"]
        except Exception as x:
            logger.error("failed {}, {}", x, traceback.format_exc())
            r = requests.get("http://localhost:3181/config")
            x = r.json()
            pypi = x['pypi_server']
            svc_addr = x['manager_svc']
        pypi_host = re.search(r'http://([\d\.]+):[\d]+', pypi).group(1)
        env = {
            "PYPI_SERVER": pypi, "PYPI_HOST": pypi_host,
            "PL_HOME": f"/data/xx/{expe_name}_{run_id}",
            "_USERNAME_": user_name,
            "_SVC_ADDR_": svc_addr,
            # "NVIDIA_VISIBLE_DEVICES": "all",
            "TOKEN": token,
            "BASE_URI": uri,
        }
        # cpu_request_times = _conf.get("cpu_request_times", 50)
        # logger.info("--cpu_request_times={}", cpu_request_times)
        if not k8s.create_deployment_new(container_name, _base['image'], params['res']['cpu'], params['res']['mem'], entrypoint=_base['cmd'],
                                         args=None, PVCs=[[pvc_name, "/data"]], ENVs=env, wd="/data",
                                         fsGroup=fsGroup, resType=params['res']['resType'], resInfo=resInfo,
                                         extra_mounts=extra_mounts, shmsize=-1, ):
            create_log = "create deployment failed!"
            logger.error(create_log)
            _db.devc_update_status(container_name, "07", error_msg=create_log)
            return failed_func(log_id, "create deployment failed!", "contact admin please", 702)
        prog_func(log_id, 0.2, "创建容器中...")

        if not k8s.create_service(container_name,
                                  Ports=[["restful", "TCP", 8181, None], ["ssh", "TCP", 2022, None],
                                         ["jupyter", "TCP", 8085, None]],
                                  selector={"app": container_name}):
            k8s.delete_all(dep_name=container_name)
            create_log = "create service failed!"
            logger.error(create_log)
            _db.devc_update_status(container_name, "07", error_msg=create_log)
            return failed_func(log_id, "create service failed!", "contact admin please", 703)
        prog_func(log_id, 0.3, "等待容器启动中...")
    except Exception as x:
        logger.error("exception: {}, {}", x, traceback.format_exc())
        try:
            # db.PL.update_dev_run_status(expe_name, run_id, 'BJ-OFF', '分配容器失败')
            db.PL.update_dev_run_status(expe_name, run_id, 'CK-OFF', '分配容器失败')
            create_log = "create container failed!"
            logger.error(create_log)
            _db.devc_update_status(container_name, "07", error_msg=create_log)
        except:
            pass
        return

    logger.info("start check...")
    _container_check_thread_new(params)


def _enter_edit_mode(params):
    '流水线进入编辑状态'

    logger.info("--- _enter_edit_mode  {}", params)
    # 创建容器, 并等待完成
    user_name = params.get("user_name")
    proj_name = params.get('proj_name')
    expe_name = params.get('expe_name')
    run_id = params.get('run_id')
    res_tmpl = params.get('res_tmpl', None)
    image = params.get("image", None)

    log_id = params.get("__log_id")  # 这个是由调用此函数的服务产生的, 不是客户端提供, 以 __ 为前缀做区分
    logger.info("using log_id {}", log_id)

    try:
        db.PL.update_dev_run_status(expe_name, run_id, 'BJ-OPE', '分配容器中')

        # _conf = worker.conf

        fsGroup = 8989  # 是否使用root启动容器
        if proj_name is None or proj_name == '' or 'user_ns' not in _conf:
            k8s = K8S(_conf['k8s_config_file'])  # 使用公共 namespace
        else:
            k8s = K8S(_conf['user_ns'][proj_name])

        params['__k8s'] = k8s.kubeconfig
        container_name = params.get('__container', None)
        if container_name is None:
            container_name = f"plcont-{_gen_rand_str(6).lower()}"  # k8s name must be lower case
        if image is None:
            # image = _conf['pipeline_base_image']
            image = "pipeline_01"
        _base = _conf.get(image, None)
        if not _base:
            logger.warning("directly use image {}", image)
            _base = {"image": image,
                     "cmd": ["/opt/pygm/bin/python", "/app/mng.py", "/app/plcont.json"]}
            # return failed_func(log_id, "can not found image conf", "contact admin please", 709)

        if res_tmpl is None or res_tmpl not in _res_templates:
            logger.warning("invalid res_tmpl {},  using default template!")
            res_tmpl = "_def"
        res = _res_templates.get(res_tmpl)
        logger.info("--res: {} - {}", res_tmpl, res)
        params['__res'] = res
        params['__image'] = _base.get('image', '[not found]')

        token = _gen_rand_str(6)
        params['__token'] = token
        params['__uri'] = uri = f"/jupyter-{int(time.time() * 1000)}"
        # 用户卷处理: 先查询用户卷是否存在, 不存在则创建, 否则就直接挂载
        pvc_name = f"uservol-{hashlib.sha1(user_name.encode('utf8')).hexdigest()}"
        phase, capacity, access_mode = k8s.query_pvc(pvc_name)
        if phase is not None:
            logger.info("pvc {} already exists, capacity: {}", pvc_name, capacity)
        else:
            logger.info("pvc {} not exists, create it with 16Gi")
            if not k8s.create_pvc(pvc_name, 16 * 1024,  # res.get("disk"),
                                  storage_class_name=params['__k8s_storage_class']):
                return failed_func(log_id, "create pvc failed!", "contact admin please", 701)
            prog_func(log_id, 0.1, "创建数据卷中...")

        extra_mounts = [['pipeline-python', '/app', True]] if os.path.exists('/pyserv') else [['app1', '/app', True]]
        # extra_mounts = [['app1', '/app', True]]
        # extra_mounts = [['pipeline-python', '/app', True]]
        try:
            svc_addr = _conf['__svc_addr__']
            pypi = _conf["pypi_server"]
        except Exception as x:
            logger.error("failed {}, {}", x, traceback.format_exc())
            r = requests.get("http://localhost:3181/config")
            x = r.json()
            pypi = x['pypi_server']
            svc_addr = x['manager_svc']
        pypi_host = re.search(r'http://([\d\.]+):[\d]+', pypi).group(1)
        env = {
            "PYPI_SERVER": pypi, "PYPI_HOST": pypi_host,
            "PL_HOME": f"/data/xx/{expe_name}_{run_id}",
            "_USERNAME_": user_name,
            "_SVC_ADDR_": svc_addr,
            "NVIDIA_VISIBLE_DEVICES": "all",
            "TOKEN": token,
            "BASE_URI": uri,
        }
        cpu_request_times = _conf.get("cpu_request_times", 50)
        logger.info("--cpu_request_times={}", cpu_request_times)

        if not k8s.create_deployment(container_name, _base['image'], res['cpu'], res['mem'],
                                     entrypoint=_base['cmd'], args=None,
                                     PVCs=[[pvc_name, "/data"]], ENVs=env, wd="/data",
                                     fsGroup=fsGroup, gpu=res.get('gpu', -1), extra_mounts=extra_mounts, shmsize=-1,
                                     cpu_request_times=cpu_request_times):
            # k8s.delete_all(pvc_name=container_name)  # user volume, don't delete
            return failed_func(log_id, "create deployment failed!", "contact admin please", 702)
        prog_func(log_id, 0.2, "创建容器中...")

        if not k8s.create_service(container_name,
                                  Ports=[["restful", "TCP", 8181, None], ["ssh", "TCP", 2022, None],
                                         ["jupyter", "TCP", 8085, None]],
                                  selector={"app": container_name}):
            k8s.delete_all(dep_name=container_name)
            return failed_func(log_id, "create service failed!", "contact admin please", 703)
        prog_func(log_id, 0.3, "等待容器启动中...")
    except Exception as x:
        logger.error("exception: {}, {}", x, traceback.format_exc())
        try:
            # db.PL.update_dev_run_status(expe_name, run_id, 'BJ-OFF', '分配容器失败')
            db.PL.update_dev_run_status(expe_name, run_id, 'CK-OFF', '分配容器失败')
        except:
            pass

    logger.info("start check...")
    _container_check_thread(params)


def _container_check_thread(params):
    '创建容器后检查可用性'
    log_id = params['__log_id']
    container_name = params['__container']
    k8s = K8S(params['__k8s'])

    def _ck_continue(percent, note):
        prog_func(log_id, percent, note)

    begin_time = time.time()
    create_success = False
    while time.time() - begin_time < 240:
        time.sleep(5)
        pod_name, node_host, ready, total, ports = k8s.query_pod(container_name)
        if pod_name is None:
            _ck_continue(0.35, "容器创建挂起中...")
            continue
        if node_host is None:
            _ck_continue(0.40, "容器节点主机分配中...")
            continue
        if ready < total:
            _ck_continue(0.42, "容器启动中...")
            continue
        try:
            # 检查 2022 端口是否可用
            node_port = [i[2] for i in ports if i[0] == 2022][0]
            cc = socket.create_connection((node_host, node_port), 1.0)
            cc.close()
            prog_func(log_id, 0.5, "容器创建成功, 启动容器内端口服务...")
        except Exception as x:
            _ck_continue(0.45, f"容器 {container_name} 内应用状态检查中(控制端口 {node_host}:{node_port} 探测) {x}...")
            continue
        create_success = True
        break
    if not create_success:
        logger.error("create container timeout!")
        failed_func(log_id, "create container failed!", "contact admin please", 703)
        _enter_edit_mode_failed(params)
        return

    cont_addr = f"{node_host}:{[i[2] for i in ports if i[0] == 2022][0]}"
    params['__cont_addr'] = cont_addr  # 保存容器控制端口地址
    params['__service_addr'] = f"http://{node_host}:{[i[2] for i in ports if i[0] == 8181][0]}"
    params['__jupyter_addr'] = f"http://{node_host}:{[i[2] for i in ports if i[0] == 8085][0]}"

    logger.info("container: {},  cont_addr: {}", container_name, cont_addr)

    # todo 需要把镜像信息保存在任务配置文件中
    # 恢复运行数据
    try:
        with remote.PLTask(_conf, cont_addr, params['expe_name'], params['run_id'], params['user_name']) as task:
            ret = task.prepare_env(container_name, params)
            if params.get('tmp_dir'):
                ret = task.tmp_duplicate(params)
            logger.info("prepare_env {} return {}", container_name, ret)
    except Exception as x:
        ret = False
        logger.error("prepare_env failed, {}, {}", x, traceback.format_exc())

    if not ret:
        logger.error("prepare_env failed, clear...")
        _enter_edit_mode_failed(params)
        failed_func(log_id, "容器内环境准备失败", "contact admin please", 709)
        return 'failed'

    prog_func(log_id, 0.8, "等待容器服务启动中...")

    service_addr = params['__service_addr']
    flag = False
    while time.time() - begin_time < 240:
        time.sleep(3)
        try:
            r = requests.get(f"{service_addr}/pl/ping")
            logger.info("/pl/ping return {}:  {}", r.status_code, r.text)
            if r.status_code == 200:
                logger.info("save ext_info to dev_run")
                ext_info = json.dumps(dict(container_name=container_name,
                                           ctl_addr=params['__cont_addr'],
                                           jupyter_addr=params['__jupyter_addr'],
                                           service_addr=service_addr,
                                           user_name=params['user_name'],
                                           proj_name=params['proj_name'],
                                           uri=params['__uri'],
                                           token=params['__token']))
                expe_name = params.get('expe_name')
                run_id = params.get('run_id')
                db.PL.update_dev_run(expe_name, run_id, 'BJ-REA', ext_info, container_name)  # 更新运行状态， ready状态
                db.PL.update_progress(log_id, 1.0,
                                      json.dumps({"container_name": container_name, "service_addr": service_addr}),
                                      f"plcont start success")
                # 调用ng注册, 将 uri 注册到 manager 的ng 端口
                uri, serv = params['__uri'], params['__jupyter_addr']
                logger.warning("--nginx register {}: {} -> {}", container_name, uri, serv)
                # uri = "/jupyter-1648182772802"
                # serv = "http://172.18.238.28:37703"
                # from svc.util import ng
                x = ng.NG_PX(prefix=f'{_base_dir}/ng_pyserv')
                host, port = serv[serv.find('//') + 2:].split(':')
                x.add_ng_loc(container_name, uri, host, port, conf_change_now=False)

                # 添加容器服务端口注册,  以便能让js通过ng直接调用到容器服务
                uri_svc = f"/pl-{uri.split('-')[-1]}"
                serv = params['__service_addr']
                host, port = serv[serv.find('//') + 2:].split(':')
                x.add_ng_loc(f"{container_name}_pl", uri_svc, host, port, conf_change_now=False)

                # nginx配置文件修改
                x.reload_ng()
                logger.info("enter edit mode done {} !", container_name)

                flag = True
                return
        except Exception as x:
            logger.warning("check {} failed, retrying... {}, {}", container_name, str(x), traceback.format_exc())

    if not flag:
        logger.error("wait container {} start failed, clear...", container_name)
        _enter_edit_mode_failed(params)
        failed_func(log_id, "容器内环境准备失败", "contact admin please", 709)


def _container_check_thread_new(params, is_restart=False):
    '创建容器后检查可用性'
    log_id = params['__log_id']
    container_name = params['__container']
    k8s = params['__k8s']

    def _ck_continue(percent, note):
        prog_func(log_id, percent, note)

    begin_time = time.time()
    create_success = False
    while time.time() - begin_time < 240:
        time.sleep(5)
        pod_name, node_host, ready, total, ports = k8s.query_pod(container_name)
        if pod_name is None:
            _ck_continue(0.35, "容器创建挂起中...")
            continue
        if node_host is None:
            _ck_continue(0.40, "容器节点主机分配中...")
            continue
        if ready < total:
            _ck_continue(0.42, "容器启动中...")
            continue
        try:
            # 检查 2022 端口是否可用
            node_port = [i[2] for i in ports if i[0] == 2022][0]
            cc = socket.create_connection((node_host, node_port), 1.0)
            cc.close()
            prog_func(log_id, 0.5, "容器创建成功, 启动容器内端口服务...")
        except Exception as x:
            _ck_continue(0.45, f"容器 {container_name} 内应用状态检查中(控制端口 {node_host}:{node_port} 探测) {x}...")
            continue
        create_success = True
        break
    if not create_success:
        failed_func(log_id, "create container failed!", "contact admin please", 703)
        _enter_edit_mode_failed(params, is_new=True)
        create_log = "create container timeout!"
        logger.error(create_log)
        if not is_restart:
            _db.devc_update_status(container_name, "07", error_msg=create_log)
        else:
            _db.devc_update_status(container_name, "06", error_msg=create_log)

        return

    cont_addr = f"{node_host}:{[i[2] for i in ports if i[0] == 2022][0]}"
    params['__cont_addr'] = cont_addr  # 保存容器控制端口地址
    params['__service_addr'] = f"http://{node_host}:{[i[2] for i in ports if i[0] == 8181][0]}"
    params['__jupyter_addr'] = f"http://{node_host}:{[i[2] for i in ports if i[0] == 8085][0]}"

    logger.info("container: {},  cont_addr: {}", container_name, cont_addr)

    # 恢复运行数据
    if not is_restart:
        # todo 需要把镜像信息保存在任务配置文件中
        try:
            with remote.PLTask(_conf, cont_addr, params['expe_name'], params['run_id'], params['user_name']) as task:
                ret = task.prepare_env(container_name, params)
                if params.get('tmp_dir'):
                    ret = task.tmp_duplicate(params)
                logger.info("prepare_env {} return {}", container_name, ret)
        except Exception as x:
            ret = False
            logger.error("prepare_env failed, {}, {}", x, traceback.format_exc())

        if not ret:
            logger.error("prepare_env failed, clear...")
            _enter_edit_mode_failed(params, is_new=True)
            failed_func(log_id, "容器内环境准备失败", "contact admin please", 709)
            # 修改容器状态
            create_log = "prepare_env failed, clear..."
            logger.error(create_log)
            _db.devc_update_status(container_name, "07", error_msg=create_log)
            return 'failed'

    prog_func(log_id, 0.8, "等待容器服务启动中...")

    service_addr = params['__service_addr']
    flag = False
    while time.time() - begin_time < 240:
        time.sleep(3)
        try:
            r = requests.get(f"{service_addr}/pl/ping")
            logger.info("/pl/ping return {}:  {}", r.status_code, r.text)
            if r.status_code == 200:
                logger.info("save ext_info to dev_run")
                expe_name = params.get('expe_name')
                run_id = params.get('run_id')

                # 处理参数
                ext_info = json.dumps(dict(container_name=container_name,
                                           ctl_addr=params['__cont_addr'],
                                           jupyter_addr=params['__jupyter_addr'],
                                           service_addr=service_addr,
                                           user_name=params['user_name'],
                                           proj_name=params['proj_name'],
                                           uri=params['uri'],
                                           token=params['token']))
                ret = db.PL.update_dev_run(expe_name, run_id, 'BJ-REA', ext_info, container_name)  # 更新运行状态， ready状态
                logger.info("{} {} to BJ-REA return {}", expe_name, run_id, ret)
                db.PL.update_progress(log_id, 1.0,
                                      json.dumps({"container_name": container_name, "service_addr": service_addr}),
                                      f"plcont start success")

                # 调用ng注册, 将 uri 注册到 manager 的ng 端口
                # 8085是notebook服务
                uri, serv = params['uri'], params['__jupyter_addr']
                logger.warning("--nginx register {}: {} -> {}", container_name, uri, serv)
                # uri = "/jupyter-1648182772802"
                # serv = "http://172.18.238.28:37703"
                # from svc.util import ng
                x = ng.NG_PX(prefix=f'{_base_dir}/ng_pyserv')
                host, port = serv[serv.find('//') + 2:].split(':')
                x.add_ng_loc(container_name, uri, host, port, conf_change_now=False)

                # 添加容器服务端口注册,  以便能让js通过ng直接调用到容器服务
                # 8181是图形化建模平台相关代码服务
                uri_svc = f"/pl-{uri.split('-')[-1]}"
                serv = params['__service_addr']
                host, port = serv[serv.find('//') + 2:].split(':')
                x.add_ng_loc(f"{container_name}_pl", uri_svc, host, port, conf_change_now=False)

                # nginx配置文件修改
                x.reload_ng()
                logger.info("enter edit mode done {} !", container_name)

                if not is_restart:
                    pvcName = params['pvc_name']
                else:
                    ext_info = {}
                    # ext_info信息不更新
                    db.PL.update_dev_run(expe_name, run_id, 'BJ-REA', ext_info, container_name)
                    db.PL.update_progress(log_id, 1.0,
                                          json.dumps({"container_name": container_name, "service_addr": service_addr}),
                                          f"plcont start success")

                    pvcName = params['pvcName']

                # 更新dev_container表
                dev_info = {"depName": container_name, "pvcName": pvcName, "serviceName": container_name,
                            "node_host": node_host, "ports": ports, "pod_name": pod_name, "total": total, "kubeconfig":params['kubeconfig'],
                            "namespace": params['namespace'], "service_addr": params['__service_addr'], "jupyter_addr": params['__jupyter_addr'],
                            "expe_name": params['expe_name'], "run_id": params['run_id'], "uri": params['uri'], "token": params['token'],
                            "proj_name": params['proj_name']}
                ret = _db.devc_update_v119(container_name, dev_info, "02", 1.0)
                logger.info("schedule:1.0 {} container_status=02 return {}", container_name, ret)
                flag = True
                return
        except Exception as x:
            logger.warning("check {} failed, retrying... {}, {}", container_name, str(x), traceback.format_exc())

    if not flag:
        create_log = f"wait container {container_name} start failed, clear..."
        logger.error(create_log)
        if not is_restart:
            # 检查失败，删除容器
            _enter_edit_mode_failed(params, is_new=True)
            failed_func(log_id, "容器内环境准备失败", "contact admin please", 709)
            # 修改容器状态
            logger.error(create_log)
            _db.devc_update_status(container_name, "07", error_msg=create_log)
        else:
            _db.devc_update_status(container_name, "06", error_msg=create_log)


def _enter_edit_mode_failed(params, is_new=False):
    '创建环境失败,  进行清理工作: 删除容器,  删除表记录'
    log_id = params['__log_id']
    container_name = params['__container']
    if is_new:
        k8s = params['__k8s']
    else:
        k8s = K8S(params['__k8s'])

    k8s.delete_all(dep_name=container_name, svc_name=container_name)
    # todo 删除 dev_container 记录

    expe_name = params.get('expe_name')
    run_id = params.get('run_id')
    db.PL.update_dev_run_status(expe_name, run_id, 'CK-OFF', '容器分配失败-检查状态超时，可能资源不足')


def _exit_edit_mode(params, detect=True):
    '退出流水线编辑状态, 备份数据并释放容器'
    try:
        # 创建容器, 并等待完成
        user_name = params.get("user_name")
        proj_name = params.get('proj_name')
        exp_name = params.get('expe_name')
        run_id = params.get('run_id')
        container_name = params.get('container_name')

        ext_info = db.PL.get_dev_run_ext(exp_name, run_id)  # 先取出 ext_info
        j = None
        try:
            j = json.loads(ext_info)
        except:
            logger.error("can not parse json of ext_info {} {}, [{}], use query_pod instead", exp_name, run_id,
                         ext_info)
            # todo 表中没有数据, 则通过 k8s 查询获取访问地址
            # return "done"

        container_info = _db.devc_get_v119(container_name)
        if container_info is None:
            return _rjson(None, 5023, f"not found container {container_name}")
        if "kubeconfig" not in container_info.keys() or "namespace" not in container_info.keys():
            kubeconfig = container_info.get("kubeconfig", _conf['k8s_config_file'])
            k8s = K8S(kubeconfig)
        else:
            kubeconfig = container_info["kubeconfig"]
            k8s = K8S(kubeconfig, container_info["namespace"])
        # if proj_name is None or proj_name == '' or 'user_ns' not in _conf:
        #     k8s = K8S(_conf['k8s_config_file'])  # 使用公共 namespace
        # else:
        #     k8s = K8S(_conf['user_ns'][proj_name])
        params['__k8s'] = k8s
        if j is None:
            rr = k8s.query_pod(container_name, get_envs=True)
            logger.info("query_pod of {} return {}", container_name, rr)
            pod_name, node_host, ready, total, ports, envs = rr
            uri = envs.get('BASE_URI', None) if envs else None
        else:
            uri = j.get('uri', '111')
            # params['__ctl_addr'] = j['ctl_addr']

        if uri is None:
            logger.error("can not found uri of {}", container_name)
        params['__uri'] = uri
        params['__cont_name'] = container_name
        db.PL.update_dev_run(exp_name, run_id, 'BJ-CLO', ext_info, None)  # 更新运行状态

        log_id = params.get("__log_id")  # 这个是由调用此函数的服务产生的, 不是客户端提供, 以 __ 为前缀做区分
        logger.info("using log_id {}", log_id)

        prog_func(log_id, 0.2, '备份数据中...')
        # 备份数据
        pod_name, node_host, ready, total, ports = k8s.query_pod(container_name)
        if pod_name is None or node_host is None or ready < total:
            logger.warning("container {} not ready, ignore backup folder ", container_name)
            prog_func(log_id, 0.4, '容器不可用, 放弃备份数据(认为未产生新数据)')
        else:
            cont_addr = f"{node_host}:{[i[2] for i in ports if i[0] == 2022][0]}"
            params['__cont_addr'] = cont_addr
            with remote.PLTask(_conf, cont_addr, exp_name, run_id, user_name) as task:
                ret = task.backup_env()
            if not ret:
                logger.error("backup folder of {}: {}:{}-{} failed", container_name, user_name, exp_name, run_id)
                return 'backup failed, give up delete container'

        prog_func(log_id, 0.5, '删除容器中...')

        # 释放容器
        k8s.delete_all(dep_name=container_name, svc_name=container_name)
        port_svc = f"{container_name}--ports"
        ports = k8s.query_svc(port_svc)  # 查询到现有的端口, 如果有则先删除
        if ports is not None:
            k8s.del_svc(port_svc)

        # todo 等待容器退出,  容器中的ng端口是否可用
        # dt = DelayTask("ppl.exit_check", params, delay_sec=5, timeout_secs=120,
        #                timeout_task_name="ppl.exit_force_update")
        # worker.add_delayed_task(dt)

        def _ck_continue(percent, note):
            prog_func(log_id, percent, note)

        if detect:
            begin_time = time.time()
            while time.time() - begin_time < 240:
                time.sleep(5)
                # 服务删除后端口不能访问了,  但容器可能还在, 不能通过端口检查来判断容器是否 down 掉
                try:
                    # 检查 pod 是否存在
                    pod_name, node_host, ready, total, ports = k8s.query_pod(container_name)
                    if pod_name is not None:
                        _ck_continue(0.45, f"退出容器内应用状态检查中pod是否存在, still running...")
                        continue
                    else:
                        break
                except:
                    prog_func(log_id, 0.5, "容器退出成功, 更新表状态...")

        exp_name = params.get('expe_name')
        run_id = params.get('run_id')
        # 更新dev_run 表的状态
        ret = db.PL.update_dev_run(exp_name, run_id, 'CK-OFF', '', None)  # 更新运行状态
        logger.info("{} {} to CK-OFF return {}", exp_name, run_id, ret)

        # 删除ng注册 (要先查询到 dev_run 的 uri 注册信息)
        uri = params['__uri']
        if uri:
            logger.warning("---unregister nginx {} -> {}", container_name, uri)
            x = ng.NG_PX(prefix=f'{_base_dir}/ng_pyserv')
            x.rmv_ng_loc(f"{container_name}_pl")
            x.rmv_ng_loc(container_name)
        else:
            logger.warning("not found URI of {}, ignore nginx unregister", container_name)
        prog_func(log_id, 1.0, f'容器删除完成 -- {container_name}')
        logger.info("done!!!")
    except Exception as x:
        logger.error("exit_edit_mode  {} failed {}, {}", container_name, x, traceback.format_exc())


def monitor_test():
    msg = "Access the service successfully"
    return _rjson({}, 200, msg)


class Model:
    @staticmethod
    def deploy_app():
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter pl/deploy_app...[{}]", jsons)
            params = json.loads(jsons)['BODY']
            user_name = params['user_name']
            app_name = params['app_name']  # 应用名称
            pl_container = params['pl_container']  # 流水线容器名称
            expe_name = params['expe_name']  # 实验id
            node_id = params['node_id']  # 部署节点id
            params.get('inst_count', 1)  # 运行实例数
            app_type = params.get('type', 'web')  # 服务类型
            run_id = params['run_id']  # 运行id
            proj_name = params['proj_name']
            res = params['res']  # 资源描述
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))
        k8s = K8S(_conf['k8s_config_file'])

        deploy_name = f"app-{_gen_rand_str(6)}".lower()
        params['__container'] = deploy_name
        # 删除该用户下同名app的日志（部署失败残留的日志）
        db.MD.del_deploy_logs(app_name, user_name)

        # 部署日志模板
        logs_params = {
            "percent": "0.1",
            "user_name": user_name,
            "app_name": app_name,
            "deploy_name": deploy_name,
            "app_type": app_type,
            "app_status": -1,  # 状态部署中
            "note": "部署信息检查中",
        }

        # 部署信息检查
        obj, rcode, msg = Model._check_deploy(logs_params, k8s, pl_container, params, True)
        if rcode != 200:
            return _rjson(obj, rcode, msg)
        # 调用部署方法
        body, code, msg = Model._deploy_app(logs_params, params)

        return _rjson(body, code, msg)

    @staticmethod
    def _deploy_app(logs_params, params, version_id=None):
        # 入参判断
        try:
            user_name = params['user_name']
            user_id = params.get('user_id')
            proj_name = params['proj_name']
            app_name = params['app_name']  # 应用名称
            pl_container = params['pl_container']  # 流水线容器名称
            expe_name = params['expe_name']  # 实验id
            run_id = params['run_id']  # 运行id
            node_id = params['node_id']  # 部署节点id
            ins_id = params.get('ins_id')  # 实例id
            inst_count = params.get('inst_count', 1)  # 运行实例数
            res = params['res']  # 资源描述
            app_type = params.get('type', 'web')  # 服务类型
            deploy_name = params['__container']  # app对应deploy名称
        except Exception as e:
            return None, 402, "Invalid request [%s]" % str(e)

        # 部署方法
        k8s = K8S(_conf['k8s_config_file'])

        try:
            pod_name, node_host, ready, total, ports = k8s.query_pod(pl_container)

            # 处理方法:  调用8181服务,  (要执行模型选择)
            ssh_port = [i[2] for i in ports if i[0] == 2022][0]
            # 获取容器内配置 $PL_HOME/.conf.json
            logs_params['note'] = "镜像信息获取中"
            logs_params['percent'] = "0.3"
            Model.write_logs(logs_params)
            tmp_file = "/tmp/_conf1"
            cmd = f"/data/.app/bin/fkme scp -i /data/.app/id_rsa_tr -p {ssh_port} _base_@{node_host}:/data/xx/{expe_name}_{run_id}/.conf.json {tmp_file}"
            ret = subprocess.check_output(shlex.split(cmd))
            logger.info("get conf.json {} return {}", cmd, ret)
            with open(tmp_file, "r", encoding='utf8') as fi:
                ct_conf = json.load(fi)
            os.remove(tmp_file)
            image = ct_conf['image']

            # image = "boatswain.registry.si-tech.com.cn:30100/y13932-crmmci/pipelinebase:1.5"  # test-only
            logger.info("start app deploy {} with image: {}", deploy_name, image)
            logs_params['note'] = "镜像信息获取成功"
            logs_params['percent'] = "0.35"
            Model.write_logs(logs_params)

            # todo 检查资源可用性(也可能在java服务中检查)

            # 启动容器(基础镜像从容器中获取),
            # if inst_count > 1:
            #     logger.error("multiple instances not implement yet")
            #     return _rjson(None, 405, "multiple instances not implement yet")
            logs_params['note'] = "文件服务器信息获取中"
            logs_params['percent'] = "0.4"
            Model.write_logs(logs_params)

            fsGroup = 8989  # 是否使用root启动容器
            if proj_name is None or proj_name == '' or 'user_ns' not in _conf:
                k8s = K8S(_conf['k8s_config_file'])  # 使用公共 namespace
            else:
                k8s = K8S(_conf['user_ns'][proj_name])

            params['__k8s'] = k8s.kubeconfig

            logger.info("--res: {}", res)
            params['__res'] = res
            params['__image'] = image
            # params['__deploy_name'] = deploy_name
            params['__container'] = deploy_name  # 新container名称
            fileserver_url = _conf['fileserver_url']
            ng_url = _conf['ng_url']
            # 包存储路径: /data/.models/{username}/SVC_{expe_name}_{run_id}_{node_id}
            logs_params['note'] = "文件服务器信息获取成功"
            logs_params['percent'] = "0.45"
            Model.write_logs(logs_params)

            # request_url = f"{fileserver_url}" if os.path.exists('/pyserv') else f"{ng_url}"
            request_url = fileserver_url
            if app_type == 'stream':
                pkg = f"{request_url}/models/{user_id if user_id else user_name}/{ins_id + '/' if ins_id else ''}SVC_{expe_name}_{run_id}_{node_id}/svc_stream.tgz"
            elif app_type == 'web' or app_type == "train":
                pkg = f"{request_url}/models/{user_id if user_id else user_name}/{ins_id + '/' if ins_id else ''}SVC_{expe_name}_{run_id}_{node_id}/svc.tgz"
            elif app_type == 'schedule':
                pkg = f"{request_url}/models/{user_id if user_id else user_name}/{ins_id + '/' if ins_id else ''}SVC_{expe_name}_{run_id}_{node_id}/schedule.tgz"
            else:
                logger.error(f"app类型错误：没有此类型的服务[{app_type}]")
                logs_params['note'] = f"app类型错误：没有此类型的服务[{app_type}]"
                logs_params['percent'] = "0.45"
                logs_params['app_status'] = 0  # 部署失败
                Model.write_logs(logs_params)
                return None, 703, f"app类型错误：没有此类型的服务[{app_type}]"

            env = {
                "_USERNAME_": user_name,
                "NVIDIA_VISIBLE_DEVICES": "all",
                "_STARTUP": f"{pkg}^/data/service",
            }
            cpu_request_times = _conf.get("cpu_request_times", 50)
            logger.info("--cpu_request_times={}", cpu_request_times)

            logs_params['note'] = "容器创建中"
            logs_params['percent'] = "0.5"
            Model.write_logs(logs_params)
            if not k8s.create_deployment(deploy_name, image, float(res['cpu']), int(res['mem']),
                                         entrypoint=["/bin/tini", "--"],
                                         args=["/bin/sshserv", "serve", "-c", "/data/.ss", "-f",
                                               f"{request_url}/libs/f/sshserv_plsvc.json"],
                                         PVCs=None, ENVs=env, wd="/data",
                                         fsGroup=fsGroup, gpu=res.get('gpu', -1),
                                         shmsize=-1,
                                         cpu_request_times=cpu_request_times,
                                         inst_count=inst_count
                                         ):
                logger.error("create deploy failed!")
                logs_params['note'] = "容器创建失败"
                logs_params['percent'] = "0.55"
                logs_params['app_status'] = 0  # 部署失败
                Model.write_logs(logs_params)
                return None, 702, "create deployment failed!"
            logs_params['note'] = "容器创建成功"
            logs_params['percent'] = "0.55"
            Model.write_logs(logs_params)

            # node_name = "k8s-slave-4",  # test-only
            logs_params['note'] = "svc创建中"
            logs_params['percent'] = "0.6"
            Model.write_logs(logs_params)

            if not k8s.create_service(deploy_name,
                                      Ports=[["restful", "TCP", 8181, None],
                                             ["ssh", "TCP", 2022, None],
                                             ["jupyter", "TCP", 8085, None]],
                                      selector={"app": deploy_name}):
                logger.error("create service failed")
                # 删除容器、服务
                k8s.delete_all(dep_name=deploy_name)
                logs_params['note'] = "svc创建失败"
                logs_params['percent'] = "0.65"
                logs_params['app_status'] = 0  # 部署失败
                Model.write_logs(logs_params)
                return None, 703, "create service failed!"

            logs_params['note'] = "svc创建成功"
            logs_params['percent'] = "0.65"
            Model.write_logs(logs_params)

            if app_type == 'schedule':
                pass
            else:
                # 创建水平扩缩容
                if not k8s.create_horizontal_pod_autoscaling(hpa_name=deploy_name + '-hpa',
                                                             max_replicas=hpa_conf.get('max_replicas', 1),
                                                             min_replicas=hpa_conf.get('min_replicas', 1),
                                                             target_cpu_utilization_percentage=90,
                                                             object_kind='Deployment',
                                                             object_name=deploy_name):
                    logger.error("create HorizontalPodAutoscaler failed")
                    return _rjson(None, 704, "create HorizontalPodAutoscaler failed")

            # 应用信息记录中
            logs_params['note'] = "应用信息记录中"
            logs_params['percent'] = "0.7"
            Model.write_logs(logs_params)
            if version_id:
                # 获取model相关信息
                model_path = f"/data/.models/pl/publish/{expe_name}/{version_id}"
                _model_info = db.MD.get_model_info(model_path, expe_name)
                logger.info(f'_model_info {_model_info}  model_path {model_path} expe_name {expe_name}')
                if not db.MD.new_app_info(deploy_name, app_name, 'desc', proj_name, user_name, expe_name, node_id,
                                          _model_info['model_id'], _model_info['model_name'], _model_info['version_id'],
                                          _model_info['version_desc']):
                    logger.error("dboper: new_app_info failed")
                    # 删除容器、服务
                    k8s.delete_all(dep_name=deploy_name, svc_name=deploy_name)
                    # 记录部署日志
                    logs_params['note'] = "应用信息记录失败"
                    logs_params['percent'] = "0.75"
                    logs_params['app_status'] = 0  # 部署失败
                    Model.write_logs(logs_params)
                    return None, 705, "create dev_application record failed!"
            else:
                if not db.MD.new_app_info(deploy_name, app_name, 'desc', proj_name, user_name, expe_name, node_id):
                    logger.error("dboper: new_app_info failed")
                    # 删除容器、服务
                    k8s.delete_all(dep_name=deploy_name, svc_name=deploy_name)
                    # 记录部署日志
                    logs_params['note'] = "应用信息记录失败"
                    logs_params['percent'] = "0.75"
                    logs_params['app_status'] = 0  # 部署失败
                    Model.write_logs(logs_params)
                    return None, 705, "create dev_application record failed!"

            # 应用信息记录中
            logs_params['note'] = "应用信息记录成功"
            logs_params['percent'] = "0.75"
            Model.write_logs(logs_params)

            # 等待容器启动完成(在线程中等待)
            log_id = f"{int(time.time())}-{_gen_rand_str(4)}"
            params['__log_id'] = log_id
            # params['__new_container'] = deploy_name

            logger.info("start check...")

            th = threading.Thread(target=Model._container_check, args=(params, logs_params), daemon=True)
            th.start()

            return {"deploy_name": deploy_name, "status": "starting..."}, 200, "succeed"

        except Exception as x:
            logger.error(f"deploy model failed: {x}, {traceback.format_exc()}")
            k8s.delete_all(dep_name=deploy_name, svc_name=deploy_name)
            logs_params['note'] = f"应用部署失败：{x}"
            logs_params['app_status'] = 0  # 部署失败
            Model.write_logs(logs_params)
            return None, 500, "deploy model failed: %s" % x

    @staticmethod
    def _deploy_app_new(logs_params, params, version_id=None):
        # 入参判断
        try:
            user_name = params['user_name']
            user_id = params.get('user_id')
            proj_name = params['proj_name']
            app_name = params['app_name']  # 应用名称
            pl_container = params['pl_container']  # 流水线容器名称
            expe_name = params['expe_name']  # 实验id
            run_id = params['run_id']  # 运行id
            node_id = params['node_id']  # 部署节点id
            ins_id = params.get('ins_id')  # 实例id
            inst_count = params.get('inst_count', 1)  # 运行实例数
            # res = params['res']  # 资源描述
            app_type = params.get('type', 'web')  # 服务类型
            deploy_name = params['__container']  # app对应deploy名称
        except Exception as e:
            # 更新部署状态为03，部署失败
            dep_log = f'deploy failed，Invalid request [{str(e)}]'
            _db.update_jupyter_deploy_info('', '', '03', app_name.lower(), dep_log)
            return

        # 部署方法
        k8s = params['k8s_info']['k8s']

        # 打包检查
        log_obj = logs_params
        pk_params = params.get('pk_params', {})
        kf_k8s = params['kf_k8s']
        obj, rcode, msg = Model._check_packed(log_obj, kf_k8s, pl_container, app_type, node_id,
                                              pk_params, version_id,
                                              expe_name)
        if rcode != 200:
            # 更新部署状态为03，部署失败
            dep_log = f'deploy failed，{msg}'
            _db.update_jupyter_deploy_info('', '', '03', app_name.lower(), dep_log)
            return

        try:
            pod_name, node_host, ready, total, ports = kf_k8s.query_pod(pl_container)
            if ready < total:
                dep_log = f"Please check the status of the development container"
                logger.info(dep_log)
                logs_params['note'] = f"容器状态检查失败：请检查开发容器{pl_container}状态"
                logs_params['percent'] = "0.29"
                Model.write_logs(logs_params)

                # 更新部署状态为03，部署失败
                _db.update_jupyter_deploy_info('', '', '03', app_name.lower(), dep_log)
                return

            # 处理方法:  调用8181服务,  (要执行模型选择)
            ssh_port = [i[2] for i in ports if i[0] == 2022][0]
            # 获取容器内配置 $PL_HOME/.conf.json
            logs_params['note'] = "镜像信息获取中"
            logs_params['percent'] = "0.3"
            Model.write_logs(logs_params)
            tmp_file = "/tmp/_conf1"
            cmd = f"/data/.app/bin/fkme scp -i /data/.app/id_rsa_tr -p {ssh_port} _base_@{node_host}:/data/xx/{expe_name}_{run_id}/.conf.json {tmp_file}"
            ret = subprocess.check_output(shlex.split(cmd))
            logger.info("get conf.json {} return {}", cmd, ret)
            with open(tmp_file, "r", encoding='utf8') as fi:
                ct_conf = json.load(fi)
            os.remove(tmp_file)
            image = ct_conf['image']

            # image = "boatswain.registry.si-tech.com.cn:30100/y13932-crmmci/pipelinebase:1.5"  # test-only
            logger.info("start app deploy {} with image: {}", deploy_name, image)
            logs_params['note'] = "镜像信息获取成功"
            logs_params['percent'] = "0.35"
            Model.write_logs(logs_params)

            # todo 检查资源可用性(也可能在java服务中检查)

            # 启动容器(基础镜像从容器中获取),
            # if inst_count > 1:
            #     logger.error("multiple instances not implement yet")
            #     return _rjson(None, 405, "multiple instances not implement yet")
            logs_params['note'] = "文件服务器信息获取中"
            logs_params['percent'] = "0.4"
            Model.write_logs(logs_params)

            fsGroup = 8989  # 是否使用root启动容器

            logger.info("--res: {}", params['resInfo'])
            params['__res'] = params['resInfo']
            params['__image'] = image
            # params['__deploy_name'] = deploy_name
            # params['__container'] = deploy_name  # 新container名称
            fileserver_url = _conf['fileserver_url']
            ng_url = _conf['ng_url']
            # 包存储路径: /data/.models/{username}/SVC_{expe_name}_{run_id}_{node_id}
            logs_params['note'] = "文件服务器信息获取成功"
            logs_params['percent'] = "0.45"
            Model.write_logs(logs_params)

            # request_url = f"{fileserver_url}" if os.path.exists('/pyserv') else f"{ng_url}"
            request_url = fileserver_url
            if app_type == 'stream':
                pkg = f"{request_url}/models/{user_id if user_id else user_name}/{ins_id + '/' if ins_id else ''}SVC_{expe_name}_{run_id}_{node_id}/svc_stream.tgz"
            elif app_type == 'web' or app_type == "train":
                pkg = f"{request_url}/models/{user_id if user_id else user_name}/{ins_id + '/' if ins_id else ''}SVC_{expe_name}_{run_id}_{node_id}/svc.tgz"
            elif app_type == 'schedule':
                pkg = f"{request_url}/models/{user_id if user_id else user_name}/{ins_id + '/' if ins_id else ''}SVC_{expe_name}_{run_id}_{node_id}/schedule.tgz"
            else:
                dep_log = f"app类型错误：没有此类型的服务[{app_type}]"
                logger.error(dep_log)
                logs_params['note'] = f"app类型错误：没有此类型的服务[{app_type}]"
                logs_params['percent'] = "0.45"
                logs_params['app_status'] = 0  # 部署失败
                Model.write_logs(logs_params)

                # 更新部署状态为03，部署失败
                _db.update_jupyter_deploy_info('', '', '03', app_name.lower(), dep_log)
                return

            env = {
                "_USERNAME_": user_name,
                # "NVIDIA_VISIBLE_DEVICES": "all",
                "_STARTUP": f"{pkg}^/data/service",
            }

            logs_params['note'] = "容器创建中"
            logs_params['percent'] = "0.5"
            Model.write_logs(logs_params)

            if not k8s.create_deployment_new(deploy_name, image, float(params['res']['cpu']), int(params['res']['mem']),
                                             entrypoint=["/bin/tini", "--"],args=["/bin/sshserv","serve", "-c","/data/.ss","-f",f"{request_url}/libs/f/sshserv_plsvc.json"],
                                             PVCs=None, ENVs=env, wd="/data",
                                             fsGroup=fsGroup, resType=params['resType'], resInfo=params['resInfo'],
                                             shmsize=-1, inst_count=inst_count):
                dep_log = "create deploy failed!"
                logger.error(dep_log)
                logs_params['note'] = "容器创建失败"
                logs_params['percent'] = "0.55"
                logs_params['app_status'] = 0  # 部署失败
                Model.write_logs(logs_params)

                # 更新部署状态为03，部署失败
                _db.update_jupyter_deploy_info('', '', '03', app_name.lower(), dep_log)
                return

            # if not k8s.create_deployment(deploy_name, image, float(res['cpu']), int(res['mem']),
            #                              entrypoint=["/bin/tini", "--"],
            #                              args=["/bin/sshserv", "serve", "-c", "/data/.ss", "-f",
            #                                    f"{request_url}/libs/f/sshserv_plsvc.json"],
            #                              PVCs=None, ENVs=env, wd="/data",
            #                              fsGroup=fsGroup, gpu=res.get('gpu', -1),
            #                              shmsize=-1,
            #                              cpu_request_times=cpu_request_times,
            #                              inst_count=inst_count
            #                              ):

            logs_params['note'] = "容器创建成功"
            logs_params['percent'] = "0.55"
            Model.write_logs(logs_params)

            logs_params['note'] = "svc创建中"
            logs_params['percent'] = "0.6"
            Model.write_logs(logs_params)

            if not k8s.create_service(deploy_name,
                                      Ports=[["restful", "TCP", 8181, None],
                                             ["ssh", "TCP", 2022, None],
                                             ["jupyter", "TCP", 8085, None]],
                                      selector={"app": deploy_name}):
                dep_log = "create service failed"
                logger.error(dep_log)
                # 删除容器、服务
                k8s.delete_all(dep_name=deploy_name)
                logs_params['note'] = "svc创建失败"
                logs_params['percent'] = "0.65"
                logs_params['app_status'] = 0  # 部署失败
                Model.write_logs(logs_params)

                # 更新部署状态为03，部署失败
                _db.update_jupyter_deploy_info('', '', '03', app_name.lower(), dep_log)
                return

            logs_params['note'] = "svc创建成功"
            logs_params['percent'] = "0.65"
            Model.write_logs(logs_params)

            # if app_type == 'schedule':
            #     pass
            # else:
            #     # 创建水平扩缩容
            #     if not k8s.create_horizontal_pod_autoscaling(hpa_name=deploy_name + '-hpa',
            #                                                  max_replicas=hpa_conf.get('max_replicas', 1),
            #                                                  min_replicas=hpa_conf.get('min_replicas', 1),
            #                                                  target_cpu_utilization_percentage=90,
            #                                                  object_kind='Deployment',
            #                                                  object_name=deploy_name):
            #         logger.error("create HorizontalPodAutoscaler failed")
            #         return _rjson(None, 704, "create HorizontalPodAutoscaler failed")

            # 应用信息记录中
            logs_params['note'] = "应用信息记录中"
            logs_params['percent'] = "0.7"
            Model.write_logs(logs_params)
            if version_id:
                # 获取model相关信息
                model_path = f"/data/.models/pl/publish/{expe_name}/{version_id}"
                _model_info = db.MD.get_model_info(model_path, expe_name)
                logger.info(f'_model_info {_model_info}  model_path {model_path} expe_name {expe_name}')
                if not db.MD.new_app_info(deploy_name, app_name, 'desc', proj_name, user_name, expe_name, node_id,
                                          _model_info['model_id'], _model_info['model_name'], _model_info['version_id'],
                                          _model_info['version_desc']):
                    logger.error("dboper: new_app_info failed")
                    # 删除容器、服务
                    k8s.delete_all(dep_name=deploy_name, svc_name=deploy_name)
                    # 记录部署日志
                    logs_params['note'] = "应用信息记录失败"
                    logs_params['percent'] = "0.75"
                    logs_params['app_status'] = 0  # 部署失败
                    Model.write_logs(logs_params)

                    # 更新部署状态为03，部署失败
                    _db.update_jupyter_deploy_info('', '', '03', app_name.lower(), "create dev_application record failed!")
                    return
            else:
                app_ids = db.MD.get_app_info(user_name, app_name, is_new=True)
                logger.info(f"app_ids {app_ids}")
                if app_ids is None:
                    if not db.MD.new_app_info(deploy_name, app_name, 'desc', proj_name, user_name, expe_name, node_id):
                        logger.error("dboper: new_app_info failed")
                        # 删除容器、服务
                        k8s.delete_all(dep_name=deploy_name, svc_name=deploy_name)
                        # 记录部署日志
                        logs_params['note'] = "应用信息记录失败"
                        logs_params['percent'] = "0.75"
                        logs_params['app_status'] = 0  # 部署失败
                        Model.write_logs(logs_params)

                        # 更新部署状态为03，部署失败
                        _db.update_jupyter_deploy_info('', '', '03', app_name.lower(), "create dev_application record failed!")
                        return

            # 应用信息记录中
            logs_params['note'] = "应用信息记录成功"
            logs_params['percent'] = "0.75"
            Model.write_logs(logs_params)

            # 等待容器启动完成(在线程中等待)
            log_id = f"{int(time.time())}-{_gen_rand_str(4)}"
            params['__log_id'] = log_id
            # params['__new_container'] = deploy_name

            logger.info("start check...")

            th = threading.Thread(target=Model._container_check_new, args=(params, logs_params), daemon=True)
            th.start()

            return

        except Exception as x:
            logger.error(f"deploy model failed: {x}, {traceback.format_exc()}")
            k8s.delete_all(dep_name=deploy_name, svc_name=deploy_name)
            logs_params['note'] = f"应用部署失败：{x}"
            logs_params['app_status'] = 0  # 部署失败
            Model.write_logs(logs_params)

            # 更新部署状态为03，部署失败
            _db.update_jupyter_deploy_info('', '', '03', app_name.lower(), "deploy model failed: %s" % x)
            return

    @staticmethod
    def _container_check_thread(params):
        logger.info('创建容器后检查服务可用性')
        # 处理部署日志
        logs_path = "/pyserv/logs"
        if not os.path.exists(logs_path):
            os.makedirs(logs_path, exist_ok=True)
        logger.info(f"logs_path is {logs_path}")

        logs_file = "/pyserv/logs/deploy_service.log"
        if os.path.exists(logs_file):
            os.remove(logs_file)
        logger.info(f"logs_file is {logs_file}")

        log_id = params['__log_id']
        container_name = params['__container']
        # container_name = params['__container']
        k8s = K8S(params['__k8s'])

        def _ck_continue(percent, note):
            prog_func(log_id, percent, note)

        begin_time = time.time()
        create_success = False
        while time.time() - begin_time < 240:
            time.sleep(5)
            pod_name, node_host, ready, total, ports = k8s.query_pod(container_name)
            if pod_name is None:
                _ck_continue(0.35, "容器创建挂起中...")
                Model.write_logs("容器创建挂起中...", "0.35")
                continue
            if node_host is None:
                _ck_continue(0.40, "容器创建挂起中...")
                Model.write_logs("容器创建挂起中...", "0.40")
                continue
            if ready < total:
                _ck_continue(0.42, "容器启动中...")
                Model.write_logs("容器启动中...", "0.42")
                continue
            try:
                # 检查 8181 端口是否可用
                node_port = [i[2] for i in ports if i[0] == 8181][0]
                cc = socket.create_connection((node_host, node_port), 1.0)
                prog_func(log_id, 0.5, "容器创建成功")
                logger.info(f'cc is {cc}')

                Model.write_logs("应用状态检查中...", "0.42")
                logger.info(f'Connect {node_host}:{node_port} succeed')
                cc.close()
                prog_func(log_id, 0.5, "容器创建成功, 启动容器内端口服务...")
            except Exception as x:
                _ck_continue(0.45, f"容器 {container_name} 内应用状态检查中(控制端口 {node_host}:{node_port} 探测) {x}...")
                continue
            create_success = True
            break
        if not create_success:
            logger.error("create container timeout!")
            failed_func(log_id, "create container failed!", "contact admin please", 703)
            _enter_edit_mode_failed(params)
            return

    @staticmethod
    def _container_check(params, logs_params):
        """ 创建容器后检查服务可用性 """
        # '创建容器后检查可用性,  不单要检查8181端口的可用性, 也要检查探测服务'
        # 创建部署日志
        logger.info("enter [start check...]")
        logs_params['note'] = "容器状态查询中"
        logs_params['percent'] = "0.8"
        Model.write_logs(logs_params)
        try:
            log_id = params['__log_id']
            container_name = params['__container']
            app_name = params['app_name']
            app_type = params['type']
            user_name = params['user_name']
            k8s = K8S(params['__k8s'])
        except Exception as e:
            logger.error("Invalid request [%s]" % str(e))
            return

        def _ck_continue(percent, note):
            prog_func(log_id, percent, note)

        # 检查容器是否启动成功
        begin_time = time.time()
        deploy_status = False
        while time.time() - begin_time < 60:  # 60秒
            time.sleep(5)
            try:
                ready, total = k8s.get_deploy(container_name)
            except Exception as e:
                logger.error(f"get deploy failed [{e}]")

            logger.info(f"ready is {ready}, total is {total}")

            if total == 0:
                # deploy还没创建成功
                continue
            elif total == ready:
                # 容器创建成功
                deploy_status = True
                break
            else:
                # 容器启动中
                deploy_status = False
                continue

        logger.info("check over")

        if not deploy_status:
            logger.error("create container timeout!")
            # 日志记录表
            logs_params['note'] = "容器启动失败：create container timeout！"
            logs_params['percent'] = "0.85"
            logs_params['app_status'] = 0  # 部署失败
            Model.write_logs(logs_params)
            # 部署应用表
            failed_func(log_id, "create container failed!", "contact admin please", 703)
            # 失败清理容器
            k8s.delete_all(dep_name=container_name, svc_name=container_name)
            # 修改app记录
            db.MD.update_app_info(container_name, user_name, '99', valid='N', op_note='destroying')
            # _enter_edit_mode_failed(params)
            return
        # # 记录成功以及失败实例个数
        logs_params['note'] = f"容器状态查询完成，成功{ready}，失败{total - ready}"
        logs_params['percent'] = "0.85"
        Model.write_logs(logs_params)

        # 检查服务是否部署成功
        begin_time = time.time()
        service_status = False
        logs_params['note'] = "服务可用性检查中"
        logs_params['percent'] = "0.9"
        Model.write_logs(logs_params)

        # stream服务检查
        if app_type == 'stream':
            while time.time() - begin_time < 60:
                time.sleep(5)
                try:
                    # 检查 8181 端口是否可用
                    # 获取访问ip
                    pod_name, node_host, pod_ready, pod_total, ports = k8s.query_pod(container_name)
                    # node_host = '172.18.238.28'  # test
                    # svc_port = k8s.query_svc(container_name)
                    node_port = ''
                    for i in ports:
                        if i[0] == 8181:
                            node_port = i[2]  # 外部访问端口
                            break
                    if not node_port:
                        logs_params['note'] = "获取服务访问端口失败"
                        logs_params['percent'] = "0.92"
                        logs_params['app_status'] = 0  # 部署失败
                        Model.write_logs(logs_params)
                        # 失败清理容器
                        k8s.delete_all(dep_name=container_name, svc_name=container_name)
                        # 修改app记录
                        db.MD.update_app_info(container_name, user_name, '99', valid='N', op_note='destroying')
                        return
                    # node_port = svc_port[0][1]  # 外部访问端口
                    logger.info(f"node_host is {node_host}, node_port is {node_port}")

                    url = f"http://{node_host}:{node_port}/"
                    logger.info(f"url -- {url}")

                    r = requests.get(url)
                    logger.info(f"r.status_code is {r.status_code}, r.text is {r.text}")
                    if r.text == 'True':
                        # 记录app访问ip以及port
                        db.MD.add_app_ip(container_name, node_host, node_port)
                        # 日志记录
                        prog_func(log_id, 1.0, "容器内端口服务启动正常")
                        logs_params['note'] = "服务可用性检查通过"
                        logs_params['percent'] = "0.95"
                        Model.write_logs(logs_params)
                        service_status = True
                        break
                    else:
                        raise Exception()
                except Exception as x:
                    logger.error(f"容器内应用状态检查中(控制端口 {node_host}:{node_port} 探测) {x}...")
                    _ck_continue(0.8, f"容器内应用状态检查中(控制端口 {node_host}:{node_port} 探测) {x}...")
                    continue
        elif app_type == 'web' or app_type == 'train' or app_type == 'schedule':
            while time.time() - begin_time < 60:
                time.sleep(5)
                try:
                    # 检查 8181 端口是否可用
                    pod_name, node_host, pod_ready, pod_total, ports = k8s.query_pod(container_name)
                    logger.info(f"query_pod {pod_name, node_host, pod_ready, pod_total, ports}")
                    node_port = ''
                    for i in ports:
                        if (app_type == 'web' or app_type == 'train') and i[0] == 8181:
                            node_port = i[2]  # 外部访问端口
                            break
                        if app_type == 'schedule' and i[0] == 2022:
                            node_port = i[2]  # 外部访问端口
                            break
                    if not node_port:
                        logs_params['note'] = "获取服务访问端口失败"
                        logs_params['percent'] = "0.92"
                        logs_params['app_status'] = 0  # 部署失败
                        Model.write_logs(logs_params)
                        # 失败清理容器
                        k8s.delete_all(dep_name=container_name, svc_name=container_name)
                        # 修改app记录
                        db.MD.update_app_info(container_name, user_name, '99', valid='N', op_note='destroying')
                        return
                    # node_port = port[0][1]  # 外部访问端口
                    logger.info(f"node_host is {node_host}, node_port is {node_port}")
                    cc = socket.create_connection((node_host, node_port), 1.0)
                    logger.info(f'cc is {cc}')
                    # 记录app访问ip以及port
                    db.MD.add_app_ip(container_name, node_host, node_port)
                    # 日志记录
                    # 日志记录
                    _ck_continue(1.0, "容器内端口服务启动正常")
                    logs_params['note'] = "服务可用性检查通过"
                    logs_params['percent'] = "0.95"
                    Model.write_logs(logs_params)
                    service_status = True
                    break
                except Exception as x:
                    logger.error(f"容器内应用状态检查中(控制端口 {node_host}:{node_port} 探测) {x}...")
                    _ck_continue(0.8, f"容器内应用状态检查中(控制端口 {node_host}:{node_port} 探测) {x}...")
                    continue

        if not service_status:
            logger.error("服务检查超时!")
            # 日志记录表
            logs_params['note'] = "服务可用性检查失败：检查超时！"
            logs_params['percent'] = "0.95"
            # logs_params['app_status'] = 0  # 部署失败
            Model.write_logs(logs_params)

            logs_params['note'] = f"应用{app_name}部署失败！要求实例数为{total} 成功{ready}个实例"
            logs_params['percent'] = "1"
            logs_params['app_status'] = 0  # 部署成功
            Model.write_logs(logs_params)

            # 部署应用表
            failed_func(log_id, "check service timeout!", "contact admin please", 703)
            # 失败清理容器
            k8s.delete_all(dep_name=container_name, svc_name=container_name)
            # 修改app记录
            db.MD.update_app_info(container_name, user_name, '99', valid='N', op_note='destroying')
            # _enter_edit_mode_failed(params)
            return

        logs_params['note'] = f"应用{app_name}部署成功！要求实例数为{total} 成功{ready}个实例"
        logs_params['percent'] = "1"
        logs_params['app_status'] = 1  # 部署成功
        Model.write_logs(logs_params)
        db.MD.update_app_info(container_name, user_name, '02')
        return

    @staticmethod
    def _container_check_new(params, logs_params):
        """ 创建容器后检查服务可用性 """
        # '创建容器后检查可用性,  不单要检查8181端口的可用性, 也要检查探测服务'
        # 创建部署日志
        logger.info("enter [start check...]")
        logs_params['note'] = "容器状态查询中"
        logs_params['percent'] = "0.8"
        Model.write_logs(logs_params)
        try:
            log_id = params['__log_id']
            container_name = params['__container']
            app_name = params['app_name']
            app_type = params['app_type']
            user_name = params['user_name']
            k8s = params['k8s_info']['k8s']
        except Exception as e:
            # 更新部署状态为03，部署失败
            dep_log = "Invalid request [%s]" % str(e)
            logger.error(dep_log)
            _db.update_jupyter_deploy_info('', '', '03', app_name.lower(), dep_log)
            return

        def _ck_continue(percent, note):
            prog_func(log_id, percent, note)

        # 检查容器是否启动成功
        begin_time = time.time()
        deploy_status = False
        while time.time() - begin_time < 60:  # 60秒
            time.sleep(5)
            try:
                ready, total = k8s.get_deploy(container_name)
            except Exception as e:
                logger.error(f"get deploy failed [{e}]")

            logger.info(f"ready is {ready}, total is {total}")

            if total == 0:
                # deploy还没创建成功
                continue
            elif total == ready:
                # 容器创建成功
                deploy_status = True
                break
            else:
                # 容器启动中
                deploy_status = False
                continue

        logger.info("check over")

        if not deploy_status:
            dep_log = "create container timeout!"
            logger.error(dep_log)
            # 日志记录表
            logs_params['note'] = "容器启动失败：create container timeout！"
            logs_params['percent'] = "0.85"
            logs_params['app_status'] = 0  # 部署失败
            Model.write_logs(logs_params)
            # 部署应用表
            failed_func(log_id, "create container failed!", "contact admin please", 703)
            # 失败清理容器
            k8s.delete_all(dep_name=container_name, svc_name=container_name)
            # 修改app记录
            db.MD.update_app_info(container_name, user_name, '99', valid='N', op_note='destroying')
            # _enter_edit_mode_failed(params)
            # 更新部署状态为03，部署失败
            _db.update_jupyter_deploy_info('', '', '03', app_name.lower(), dep_log)
            return
        # # 记录成功以及失败实例个数
        logs_params['note'] = f"容器状态查询完成，成功{ready}，失败{total - ready}"
        logs_params['percent'] = "0.85"
        Model.write_logs(logs_params)

        # 检查服务是否部署成功
        begin_time = time.time()
        service_status = False
        logs_params['note'] = "服务可用性检查中"
        logs_params['percent'] = "0.9"
        Model.write_logs(logs_params)

        # stream服务检查
        if app_type == 'stream':
            while time.time() - begin_time < 60:
                time.sleep(5)
                try:
                    # 检查 8181 端口是否可用
                    # 获取访问ip
                    pod_name, node_host, pod_ready, pod_total, ports = k8s.query_pod(container_name)
                    # node_host = '172.18.238.28'  # test
                    # svc_port = k8s.query_svc(container_name)
                    node_port = ''
                    for i in ports:
                        if i[0] == 8181:
                            node_port = i[2]  # 外部访问端口
                            break
                    if not node_port:
                        logs_params['note'] = "获取服务访问端口失败"
                        logs_params['percent'] = "0.92"
                        logs_params['app_status'] = 0  # 部署失败
                        Model.write_logs(logs_params)
                        # 失败清理容器
                        k8s.delete_all(dep_name=container_name, svc_name=container_name)
                        # 修改app记录
                        db.MD.update_app_info(container_name, user_name, '99', valid='N', op_note='destroying')
                        return
                    # node_port = svc_port[0][1]  # 外部访问端口
                    logger.info(f"node_host is {node_host}, node_port is {node_port}")

                    url = f"http://{node_host}:{node_port}/"
                    logger.info(f"url -- {url}")

                    r = requests.get(url)
                    logger.info(f"r.status_code is {r.status_code}, r.text is {r.text}")
                    if r.text == 'True':
                        # 记录app访问ip以及port
                        db.MD.add_app_ip(container_name, node_host, node_port)
                        # 日志记录
                        prog_func(log_id, 1.0, "容器内端口服务启动正常")
                        logs_params['note'] = "服务可用性检查通过"
                        logs_params['percent'] = "0.95"
                        Model.write_logs(logs_params)
                        service_status = True
                        break
                    else:
                        raise Exception()
                except Exception as x:
                    logger.error(f"容器内应用状态检查中(控制端口 {node_host}:{node_port} 探测) {x}...")
                    _ck_continue(0.8, f"容器内应用状态检查中(控制端口 {node_host}:{node_port} 探测) {x}...")
                    continue
        elif app_type == 'web' or app_type == 'train' or app_type == 'schedule':
            while time.time() - begin_time < 60:
                time.sleep(5)
                try:
                    # 检查 8181 端口是否可用
                    pod_name, node_host, pod_ready, pod_total, ports = k8s.query_pod(container_name)
                    logger.info(f"query_pod {pod_name, node_host, pod_ready, pod_total, ports}")
                    node_port = ''
                    for i in ports:
                        if (app_type == 'web' or app_type == 'train') and i[0] == 8181:
                            node_port = i[2]  # 外部访问端口
                            break
                        if app_type == 'schedule' and i[0] == 2022:
                            node_port = i[2]  # 外部访问端口
                            break
                    if not node_port:
                        logs_params['note'] = "获取服务访问端口失败"
                        logs_params['percent'] = "0.92"
                        logs_params['app_status'] = 0  # 部署失败
                        Model.write_logs(logs_params)
                        # 失败清理容器
                        k8s.delete_all(dep_name=container_name, svc_name=container_name)
                        # 修改app记录
                        db.MD.update_app_info(container_name, user_name, '99', valid='N', op_note='destroying')
                        # 更新部署状态为03，部署失败
                        dep_log = "Failed to obtain service access port"
                        _db.update_jupyter_deploy_info('', '', '03', app_name.lower(), dep_log)
                        return
                    # node_port = port[0][1]  # 外部访问端口
                    logger.info(f"node_host is {node_host}, node_port is {node_port}")
                    # cc = socket.create_connection((node_host, node_port), 1.0)
                    # logger.info(f'cc is {cc}')

                    url = f"http://{node_host}:{node_port}"
                    logger.info(f"url -- {url}")

                    r = requests.post(url, json=params['svc']["requestParam"])
                    logger.info(f"r is {r}")
                    logger.info(f"r.status_code is {r.status_code}")

                    if int(r.status_code) == 200:
                        # 记录app访问ip以及port
                        db.MD.add_app_ip(container_name, node_host, node_port)

                        # 日志记录
                        # 日志记录
                        _ck_continue(1.0, "容器内端口服务启动正常")
                        logs_params['note'] = "服务可用性检查通过"
                        logs_params['percent'] = "0.95"
                        Model.write_logs(logs_params)
                        service_status = True
                        break
                except Exception as x:
                    logger.error(f"容器内应用状态检查中(控制端口 {node_host}:{node_port} 探测) {x}...")
                    _ck_continue(0.8, f"容器内应用状态检查中(控制端口 {node_host}:{node_port} 探测) {x}...")
                    continue

        if not service_status:
            logger.error("服务检查超时!")
            # 日志记录表
            logs_params['note'] = "服务可用性检查失败：检查超时！"
            logs_params['percent'] = "0.95"
            # logs_params['app_status'] = 0  # 部署失败
            Model.write_logs(logs_params)

            logs_params['note'] = f"应用{app_name}部署失败！要求实例数为{total} 成功{ready}个实例"
            logs_params['percent'] = "1"
            logs_params['app_status'] = 0  # 部署成功
            Model.write_logs(logs_params)

            # 部署应用表
            failed_func(log_id, "check service timeout!", "contact admin please", 703)
            # 失败清理容器
            k8s.delete_all(dep_name=container_name, svc_name=container_name)
            # 修改app记录
            db.MD.update_app_info(container_name, user_name, '99', valid='N', op_note='destroying')
            # 更新部署状态为03，部署失败
            dep_log = "check service timeout!"
            _db.update_jupyter_deploy_info('', '', '03', app_name.lower(), dep_log)
            # _enter_edit_mode_failed(params)
            return

        logs_params['note'] = f"应用{app_name}部署成功！要求实例数为{total} 成功{ready}个实例"
        logs_params['percent'] = "1"
        logs_params['app_status'] = 1  # 部署成功
        Model.write_logs(logs_params)
        db.MD.update_app_info(container_name, user_name, '02')
        dep_log = {
            "kubeconfig": params['k8s_info']['kubeconfig'],
            "namespace": params['k8s_info']['namespace'],
            "storage_class_name": params['k8s_info']['storage_class_name'],
        }
        # 更新部署状态为02，部署成功
        _db.update_jupyter_deploy_info(url, node_port, '02', container_name, json.dumps(dep_log))
        return

    @staticmethod
    def destroy_app():
        """
        {
            'BODY': {
                'user_name':xx,
                'app_name':xx
            }
        }
        """
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter pl/destroy_app...[{}]", jsons)
            params = json.loads(jsons)['BODY']
            user_name = params['user_name']
            app_name = params['app_name']
            # expe_name = params.get('expe_name')  # 实验id
            # run_id = params.get('run_id')  # 运行id
            # node_id = params.get('node_id')  # 部署节点id
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))

        code, msg = Model._destroy_app(params)
        if code != 200:
            return _rjson(None, code, msg)

        else:
            return _rjson({"status": "destroying..."})

    @staticmethod
    def destroy_app_new():
        """
        {
            'BODY': {
                'user_name':xx,
                'app_name':xx
            }
        }
        """
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter pl/destroy_app...[{}]", jsons)
            params = json.loads(jsons)['BODY']
            user_name = params['user_name']
            deploy_name = params['deploy_name']
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))

        code, msg = Model._destroy_app_new(params)
        if code != 200:
            return _rjson(None, code, msg)

        else:
            return _rjson({"status": "destroying..."})

    @staticmethod
    def _destroy_app(params):
        msg = ''
        try:
            user_name = params['user_name']
            app_name = params['app_name']
        except Exception as x:
            msg = "Invalid request [%s]" % str(x)
            return 402, msg

        try:
            app_ids = db.MD.get_app_info(user_name, app_name)
            if app_ids is None:
                msg = "app of {} not exists", app_name
                logger.error(msg)
                return 801, msg

            k8s = K8S(_conf['k8s_config_file'])
            for app_id in app_ids:
                app_id = app_id[0]
                logger.info(f"app_id:{app_id}")
                deploy_name = app_id
                k8s.delete_all(dep_name=deploy_name, svc_name=deploy_name)

                db.MD.update_app_info(app_id, user_name, '99', valid='N', op_note='destroying')  # 正在销毁
                # 删除日志
                db.MD.del_deploy_logs(app_name, user_name)
                # db.MD.update_deploy_logs(app_name, user_name, valid=0)  # 部署日志信息状态更新为--失效

            # todo 开启线程检查销毁过程
            msg = "destroying..."
            return 200, msg
        except Exception as x:
            msg = f"destroy app failed: {x}"
            logger.error(f"destroy app failed: {x}, {traceback.format_exc()}")
            return 500, msg

    @staticmethod
    def _destroy_app_new(params):
        msg = ''
        try:
            user_name = params['user_name']
            deploy_name = params['deploy_name']
        except Exception as x:
            msg = "Invalid request [%s]" % str(x)
            return 402, msg

        try:
            dep_info_str = _db.get_deploy_info(deploy_name)
            logger.info(f"dep_info_str {dep_info_str}")

            # 部署集群命名空间--名称以及配置文件获取
            if dep_info_str is None:
                dep_info = {}
                # return _rjson(None, 5023, f"not found container {deploy_name}")
            else:
                dep_info = json.loads(dep_info_str)

            if "kubeconfig" not in dep_info.keys() or "namespace" not in dep_info.keys():
                kubeconfig = dep_info.get("kubeconfig", _conf['k8s_config_file'])
                k8s = K8S(kubeconfig)
            else:
                kubeconfig = dep_info["kubeconfig"]
                k8s = K8S(kubeconfig, dep_info["namespace"])

            try:
                k8s.delete_all(dep_name=deploy_name, svc_name=deploy_name)
            except Exception as x:
                logger.error("delete container failed, Reason: %s" % (x))
                # todo:删除失败，更新数据库
                return _rjson({}, rcode=405, rmsg="delete container failed")
            # todo:删除成功，更新数据库
            # todo:删除部署日志

            msg = "destroying..."
            return 200, msg
        except Exception as x:
            msg = f"destroy app failed: {x}"
            logger.error(f"destroy app failed: {x}, {traceback.format_exc()}")
            return 500, msg

    @staticmethod
    def write_logs(params):
        try:
            percent = params['percent']  # 部署进度
            user_name = params['user_name']  # 用户id
            app_name = params['app_name']  # app名称
            deploy_name = params['deploy_name']
            app_type = params['app_type']  # app类型
            app_status = params['app_status']  # app部署状态
            note = params['note']  # 日志
            # log_id = params.get('log_id')  # 日志id
            # is_end = params.get('is_end')  # 是否部署结束
        except Exception as x:
            logger.error("write deploy logs failed: Invalid request [%s]" % str(x))
            return

        logger.info(f"update progress {percent} {note}")

        if not db.MD.new_deploy_logs(user_name, app_name, deploy_name, app_type, app_status, note, percent):
            logger.error("dboper: new_deploy_logs failed")
            return
            # k8s.delete_all(dep_name=deploy_name, svc_name=deploy_name)
        return

    @staticmethod
    def get_deploy_logs():
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter pl/get_deploy_logs...[{}]", jsons)
            params = json.loads(jsons)['BODY']
            user_name = params['user_name']
            app_name = params['app_name']
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))

        try:
            logs_obj = db.MD.get_deploy_logs(user_name, app_name)
            if logs_obj is None:
                logger.error(f"get app {app_name} logs failed")
                return _rjson(None, 705, f"get app {app_name} logs failed")

            # logger.info(f"logs_obj -- {logs_obj}")
            # logs_train_list = []
            # logs_detect_list = []
            # logs_schedule_list = []
            #
            # for log in logs_obj:
            #     if log[3].split('-')[-1] == 'train':
            #         logs_train_list.append(log)
            #     elif log[3].split('-')[-1] == 'detect':
            #         logs_detect_list.append(log)
            #     elif log[3].split('-')[-1] == 'schedule':
            #         logs_schedule_list.append(log)
            #     else:
            #         logger.info(f"get app {app_name} logs failed [app_name is invalid]")
            #         return _rjson(None, 705, f"get app {app_name} logs failed [app_name is invalid]")
            #
            # return_data = {
            #     "logs": {
            #         'train': logs_train_list,
            #         'detect': logs_detect_list,
            #         'schedule': logs_schedule_list
            #     },
            #     "status": "succeed"
            # }

            log_df = pd.DataFrame(logs_obj, columns=['note', 'percent', 'app_status', 'app_name', 'create_time'])
            # 时间字段转字符串
            log_df['create_time'] = log_df['create_time'].apply(lambda x: x.strftime('%Y-%m-%d %H:%M:%S'))

            log_df_gp = log_df.groupby('app_name')
            return_data = {
                "logs": {app_name: df.to_dict('records') for app_name, df in log_df_gp},
                "status": "succeed"
            }

            return _rjson(return_data)
        except Exception as x:
            logger.error("get_deploy_logs failed {}", traceback.format_exc())
            return _rjson(None, 701, "get deploy logs:" + str(x))

    @staticmethod
    def _check_deploy(logs_params, k8s, pl_container, params, is_request):
        # 部署信息检查
        try:
            logs_params['note'] = "部署信息检查中"
            Model.write_logs(logs_params)
            # 检查app名称（同用户不可有重复的app_name）
            app_id = db.MD.check_app_info(logs_params['user_name'], logs_params['app_name'])
            if app_id is not None:
                # app_id 重复，删除已有app
                logger.info(
                    f"the app_name {logs_params['app_name']} is repeated")
                logger.info(f"Delete the last deployed app {logs_params['app_name']}")

                code, msg = Model._destroy_app(params)
                # 写日志
                Model.write_logs(logs_params)
                logs_params[
                    'note'] = f"应用名称{logs_params['app_name']}存在：Delete the last deployed app {logs_params['app_name']}"
                logs_params['percent'] = "0.11"
                Model.write_logs(logs_params)

                if code != 200:
                    logs_params['note'] = f"Delete the last deployed app {logs_params['app_name']} failed"
                    logs_params['percent'] = "0.12"
                    Model.write_logs(logs_params)
                    logs_params['app_status'] = 0  # 部署失败
                    return None, code, msg
                logs_params['note'] = f"Delete the last deployed app {logs_params['app_name']} succeed"
                logs_params['percent'] = "0.12"
                Model.write_logs(logs_params)

            # if not version_id:
            if pl_container.strip() == "":
                logger.error("invalid parameter of pl_container: [{}]", pl_container)
                logs_params['note'] = f"流水线容器可用性检查不通过：invalid parameter of pl_container: [{pl_container}]"
                logs_params['percent'] = "0.14"
                logs_params['app_status'] = 0  # 部署失败
                Model.write_logs(logs_params)
                return _rjson(None, 403, "Invalid request invalid pl_container")

            # 流水线编辑状态检查逻辑，是请求时立即返回，不是请求循环60s查询流水线状态
            pod_name, node_host, ready, total, ports = k8s.query_pod(pl_container)
            if (pod_name is None or ready < 1) and is_request:
                logger.error("pipeline must in edit mode")
                logs_params['note'] = f"流水线容器可用性检查不通过：pipeline must in edit mode"
                logs_params['percent'] = "0.16"
                logs_params['app_status'] = 0  # 部署失败
                Model.write_logs(logs_params)
                return None, 409, "pipeline must in edit mode"
            else:
                # 循环等待流水线容器启动成功
                begin_time = time.time()
                pipeline_status = False
                while time.time() - begin_time < 60:
                    pod_name, node_host, ready, total, ports = k8s.query_pod(pl_container)
                    if pod_name is None or ready < 1:
                        logger.error("pipeline must in edit mode, retry again")
                        time.sleep(5)
                    else:
                        pipeline_status = True
                        break
            if not pipeline_status:
                logs_params['note'] = f"流水线容器可用性检查不通过：pipeline must in edit mode"
                logs_params['percent'] = "0.16"
                logs_params['app_status'] = 0  # 部署失败
                Model.write_logs(logs_params)
                return None, 409, "pipeline must in edit mode"

            logs_params['note'] = "部署信息检查通过"
            logs_params['percent'] = "0.18"
            Model.write_logs(logs_params)
            return None, 200, None
        except Exception as x:
            logger.error(f"_check_deploy failed:{traceback.format_exc()}")
            logs_params['note'] = f"部署信息检查失败：{x}"
            logs_params['percent'] = "0.18"
            logs_params['app_status'] = 0  # 部署失败
            Model.write_logs(logs_params)
            return None, 405, f"部署信息检查失败：{x}"

    @staticmethod
    def _check_res(logs_params, k8s_info, params):
        # 判断部署申请的资源是否足够
        try:
            logs_params['percent'] = "0.08"
            logs_params['note'] = "部署资源检查中"
            Model.write_logs(logs_params)
            # 判断申请的资源是否足够
            res_info = {
                'cpu': params['res']['cpu'],
                'mem': params['res']['mem']
            }
            flag, msg = check_namespace_res(k8s_info['kubeconfig'], k8s_info['namespace'], res_info)
            if not flag:
                logger.info(msg)
                # _db.devc_update_status(pl_container, "07", error_msg=msg)
                return None, 405, msg
            return None, 200, None
        except Exception as x:
            logger.error(f"_check_deploy failed:{traceback.format_exc()}")
            logs_params['note'] = f"部署资源检查失败：{x}"
            logs_params['percent'] = "0.18"
            logs_params['app_status'] = 0  # 部署失败
            Model.write_logs(logs_params)
            return None, 405, f"部署资源检查失败：{x}"

    @staticmethod
    def _get_deploy_info(logs_params, params):
        # 部署集群命名空间--名称以及配置文件获取
        try:
            if logs_params:
                Model.write_logs(logs_params)
            # 获取k8s配置文件以及namespace名称
            k8s_info = {}
            if params['calc_type'] and params['scene_type']:
                kube_info = get_kube_info(params['calc_type'], params['scene_type'])
                if not kube_info:
                    create_log = 'start failed，get kubeconfig failed'
                    # _db.devc_update_status(pl_container, "07", error_msg=create_log)
                    return None, 403, create_log

                k8s_info['kubeconfig'] = kube_info['kubeconfig']
                k8s_info['namespace'] = kube_info['namespace']
                k8s_info['storage_class_name'] = kube_info['storage_class_name']

                k8s_info['k8s'] = K8S(k8s_info['kubeconfig'], k8s_info['namespace'])
            else:
                k8s_info['k8s'] = K8S(_conf['k8s_config_file'])
            return k8s_info, 200, None
        except Exception as x:
            logger.error(f"_get_deploy_info failed:{traceback.format_exc()}")
            if logs_params:
                logs_params['note'] = f"部署文件获取失败：{x}"
                logs_params['percent'] = "0.18"
                logs_params['app_status'] = 0  # 部署失败
                Model.write_logs(logs_params)
            return None, 405, f"部署文件获取失败：{x}"

    @staticmethod
    def _res_deal(logs_params, data):
        # 资源入参处理
        try:
            logs_params['percent'] = "0.1"
            logs_params['note'] = "资源入参处理"
            Model.write_logs(logs_params)
            # 获取k8s配置文件以及namespace名称
            # 资源处理
            # calc_type  01-通用算力；02-GPU算力；03-NPU算力
            res = {}
            if data['gpu']:
                res['resType'] = 1
            elif data['npu']:
                res['resType'] = 2
            else:
                res['resType'] = -1

            # 资源参数处理
            resInfo = res_configuration(res['resType'], data)
            res['resInfo'] = resInfo

            return res, 200, None
        except Exception as x:
            logger.error(f"_res_deal failed:{traceback.format_exc()}")
            logs_params['note'] = f"资源类型处理失败：{x}"
            logs_params['percent'] = "0.18"
            logs_params['app_status'] = 0  # 部署失败
            Model.write_logs(logs_params)
            return None, 405, f"资源类型处理失败：{x}"

    @staticmethod
    def _check_packed(logs_params, k8s, pl_container, app_type, node_id, pk_params, version_id=None, expe_name=None):
        try:
            # 调用容器中打包程序, 验证模型服务相关文件内容, 打包上传到文件服务器
            if not version_id:
                logs_params['note'] = f"{pl_container}打包中"
                logs_params['percent'] = "0.2"
                Model.write_logs(logs_params)
            else:
                logs_params['note'] = f"流水线模型版本：{version_id}打包中"
                logs_params['percent'] = "0.2"
                Model.write_logs(logs_params)
            # 获取访问ip，请求打包接口
            pod_name, node_host, pod_ready, pod_total, ports = k8s.query_pod(pl_container)
            node_port = ''
            for i in ports:
                if i[0] == 8181:
                    node_port = i[2]  # 外部访问端口
                    break
            if not node_port:
                logger.error(f"打包失败：获取流水线服务访问端口失败")
                logs_params['note'] = "打包失败：获取流水线服务访问端口失败"
                logs_params['percent'] = "0.21"
                logs_params['app_status'] = 0  # 部署失败
                Model.write_logs(logs_params)
                return None, 406, "打包失败：获取流水线服务访问端口失败"

            if app_type == "stream":
                url = f"http://{node_host}:{node_port}/pl/stream/packed_file?node_id={node_id}&&version_id={version_id}&&expe_name={expe_name}"
            elif app_type == "web":
                url = f"http://{node_host}:{node_port}/pl/packed_file?node_id={node_id}&&version_id={version_id}&&expe_name={expe_name}"
            elif app_type == "train":
                url = f"http://{node_host}:{node_port}/pl/packed_nodes_file?node_id={node_id}&&version_id={version_id}&&expe_name={expe_name}"
            elif app_type == "schedule" and pk_params:
                schedule_request = pk_params['schedule_request']
                url = f"http://{node_host}:{node_port}/pl/packed_schedule_file?node_id={node_id}&&version_id={version_id}&&expe_name={expe_name}"
                begin_time = time.time()
                info = None
                while time.time() - begin_time < 360:
                    time.sleep(5)
                    info = db.MD.get_app_ip_port(schedule_request[0]['op_id'], schedule_request[0]['app_name'])
                    logger.info(f"app_info:{info}")
                    if info is not None and info[0] != '' and info[1] != '':
                        break
                if info is not None:
                    for pk_param in schedule_request:
                        pk_param['timing_params']['assembly_line_ip'] = info[0]
                        pk_param['timing_params']['assembly_line_port'] = info[1]
                else:
                    logger.error(f"打包失败：定时调度容器获取对应应用ip、port失败")
                    logs_params['note'] = "打包失败：定时调度容器获取对应应用ip、port失败"
                    logs_params['percent'] = "0.22"
                    logs_params['app_status'] = 0  # 部署失败
                    Model.write_logs(logs_params)
                    return None, 406, "打包失败：定时调度容器获取对应应用ip、port失败"
            else:
                return None, 406, f"没有此类型应用"

            logger.info(f"请求打包接口url -- {url}")

            r = requests.post(url, json=pk_params)
            logger.info(f"Request packaging interface results, r.status_code is {r.status_code}, r.text is {r.text}")
            if r.status_code == 200:
                if json.loads(r.text)['ROOT']['RETURN_CODE'] != 0:
                    logger.error(traceback.format_exc())
                    logs_params['note'] = f"打包失败：{json.loads(r.text)['ROOT']['RETURN_MSG']}"
                    logs_params['percent'] = "0.27"
                    logs_params['app_status'] = 0  # 部署失败
                    Model.write_logs(logs_params)
                    return None, 406, f"打包失败：{json.loads(r.text)['ROOT']['RETURN_MSG']}"
                else:
                    logs_params['note'] = "打包成功"
                    logs_params['percent'] = "0.29"
                    Model.write_logs(logs_params)
                    return None, 200, None
            else:
                logger.info(f"打包失败：请求打包接口失败")
                logs_params['note'] = "打包失败：请求打包接口失败"
                logs_params['percent'] = "0.24"
                logs_params['app_status'] = 0  # 部署失败
                Model.write_logs(logs_params)
                return None, 406, "打包失败：请求打包接口失败"
        except Exception as x:
            logger.error(traceback.format_exc())
            logs_params['note'] = f"打包失败"
            logs_params['percent'] = "0.27"
            logs_params['app_status'] = 0  # 部署失败
            Model.write_logs(logs_params)
            return None, 406, f"打包失败：{x}"

    @staticmethod
    def check_run_result():
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter pl/check_run_result...[{}]", jsons)
            params = json.loads(jsons)['BODY']
            expe_name = params['expe_name']  # 实验id
            run_id = params['run_id']  # 运行id
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))

        try:
            ret = db.MD.get_run_status(expe_name, run_id)
            logger.info(f"ret is {ret}")

            # if obj is None:
            #     return _rjson(None, 500, "No results were found")
            # # logger.info(f"get run status error [{e}]")
            #
            # run_status = obj[0][0]
            # logger.info(f"run_status is {run_status}")
            # logger.info(f"obj[0] is {obj[0]}")
            #
            # op_note = obj[0][1]
            # logger.info(f"op_note is {op_note}")
            #
            # # 判断是否成功
            # if run_status == "BJ-REA" and op_note == "success":
            #     return {"expe_name": expe_name, "status": run_status, "flag": 1}, 200, "succeed"
            # else:
            #     return {"expe_name": expe_name, "status": run_status, "flag": 0}, 200, "succeed"

        except Exception as e:
            logger.error(f"get run status error [{e}]")
            return _rjson(None, 500, f"get run status error [{e}]")

    @staticmethod
    def aiops_deploy_app():
        """
        aiops侧页面对接，请求部署接口
        1、编辑流水线
        2、解析部署报文
            2.1 流水线节点拆分
            2.2 流水线调度参数整理
        3、拼接部署参数
        4、遍历发起部署
        """
        jsons = request.stream.read().decode('utf-8')
        params_list = json.loads(jsons)['BODY']
        logger.info(f"...enter pl/aiops_deploy_app...[{params_list}]")
        try:
            for params in params_list:

                try:
                    out_data = params.get("outData", {})
                    doc = out_data["BODY"]  # 执行路径
                    taskInfo = doc["taskInfo"]  # 组件详情
                    user_name = taskInfo["user"]  # 用户名
                    proj_name = taskInfo['proj_name']  # 项目名称
                    expe_name = taskInfo['expe_name']  # 实验名称
                    run_id = taskInfo.get('runId', 'RUN_1')  # 运行id
                    wfId = taskInfo['wfId']  # 运行id
                    app_name = params['appName']  # app_name
                    resource = params['resParams']  # 资源参数
                    global_params = params.get('globalParams', {})  # 全局变量
                    # if global_params:
                    labId = global_params.get("labId", "")
                    expId = global_params.get("expId", "")
                    insId = global_params.get("insId", "")
                    ppId = global_params.get("ppId", "")
                    schedule_params = params.get('scheduleParams', [])  # 调度参数
                    tenant_id = taskInfo.get("tenant_id")
                    version_id = taskInfo.get("version_id")
                except Exception as e:
                    logger.error(f"Invalid request [{e}]")
                    break
                if global_params:
                    global_params = {
                        "labId": {
                            "name": "labId",
                            "value": labId
                        },
                        "expId": {
                            "name": "expId",
                            "value": expId
                        },
                        "insId": {
                            "name": "insId",
                            "value": insId
                        },
                        "ppId": {
                            "name": "ppId",
                            "value": ppId
                        }
                    }
                # 0、根据user_name查询user_id
                if not tenant_id:
                    user_id = db.MD.get_user_id(user_name, 'ST1628643025432709710001')
                else:
                    # user_id = db.MD.get_user_id(user_name, tenant_id)
                    user_id = user_name

                if user_id is None:
                    logger.error(f"app:{app_name} has no USER_ID!")
                    break
                app_list = []
                # 1、流水线进入编辑状态
                # if not version_id:
                edit_params = {
                    "user_name": user_id,
                    "proj_name": proj_name,
                    "expe_name": expe_name,
                    "run_id": run_id,
                }
                r = enter_edit_mode(edit_params, is_request=False)
                logger.info(f"enter edit mode response:{r.json} ")
                if r.status_code == 200:
                    if r.json['ROOT']['RETURN_CODE'] == 0 or r.json['ROOT']['RETURN_CODE'] == 7701:
                        container_name = r.json['ROOT']['BODY']['container_name']
                        logger.info(f"expe_name:{expe_name}, enter edit mode succeed.")
                        time.sleep(10)
                    else:
                        logger.error(f"expe_name:{expe_name},enter edit mode failed:{r.json['ROOT']['RETURN_MSG']}")
                        break
                else:
                    logger.error(f"expe_name:{expe_name},enter edit mode failed")
                    break

                # 2、执行参数拆分
                try:
                    # 确定是否是实验
                    if labId:
                        is_lab = True
                    else:
                        is_lab = False

                    # 拆分执行路径，训练节点、推理节点
                    reason_params, train_params, reason_node_id, reason_doc, reason_type = None, None, None, {}, None
                    for node in doc['nodes'][:]:
                        comp_id = node['comp_id'].lower().split('.')[-1]
                        if 'web' in comp_id or 'stream' in comp_id:
                            reason_node_id = node['id']
                            reason_doc = {
                                'nodes': [node]
                            }
                            doc['nodes'].remove(node)
                            if 'web' in comp_id:
                                reason_type = 'web'
                            if 'stream' in comp_id:
                                reason_type = 'stream'

                    # 确认是否有推理节点
                    if reason_node_id:
                        try:
                            res = resource['reason']['res']
                            inst_count = int(resource['reason']['insCount'])
                        except:
                            res = resource['busi']['res']
                            inst_count = int(resource['busi']['insCount'])

                        app_tpye = 'reason' if resource.get('reason') else 'busi'

                        reason_params = {
                            'user_name': user_name,
                            'user_id': user_id,
                            'app_name': f"{app_name}-{app_tpye}",
                            'expe_name': expe_name,
                            'pl_container': container_name,
                            'node_id': reason_node_id,
                            'type': reason_type,
                            'is_temp': is_lab,
                            'ins_id': insId if insId else labId,
                            'res': res,
                            'run_id': run_id,
                            'proj_name': proj_name,
                            'inst_count': inst_count,
                            'pk_params': {
                                'doc': reason_doc,
                                'global_params': global_params,
                            }
                        }
                        app_list.append(reason_params)

                    # 判断是否有训练节点
                    if doc['nodes']:
                        try:
                            res = resource['train']['res']
                            inst_count = int(resource['train']['insCount'])
                        except:
                            res = resource['busi']['res']
                            inst_count = int(resource['busi']['insCount'])

                        app_tpye = 'train' if resource.get('train') else 'busi'

                        train_params = {
                            'user_name': user_name,
                            'user_id': user_id,
                            'app_name': f"{app_name}-{app_tpye}",
                            'expe_name': expe_name,
                            'pl_container': container_name,
                            'node_id': 'train',
                            'type': 'train',
                            'is_temp': is_lab,
                            'ins_id': insId if insId else labId,
                            'res': res,
                            'run_id': run_id,
                            'proj_name': proj_name,
                            'inst_count': inst_count,
                            'pk_params': {
                                'doc': doc,
                                'global_params': global_params,
                            }
                        }
                        app_list.append(train_params)

                    # 确认训练节点、推理节点调度参数
                    train_schedule_request = [
                        {
                            "app_name": train_params['app_name'],
                            "op_id": user_name,
                            "schedule_type": "crontab" if train_schedule['cron'] != "" else "once",
                            "crontab": train_schedule['cron'],
                            "timing_params": {
                                "is_lab": is_lab,
                                "kpiIds": train_schedule.get("kpiIds"),
                                "labId": labId if is_lab else "",
                                "insId": insId if not is_lab else "",
                                "mysql_conf": train_schedule['database'],
                                "batch_size": None
                            }
                        }
                        for train_schedule in schedule_params.get('trainSchedulerList', []) if doc and train_params
                    ]

                    reason_schedule_request = [
                        {
                            "app_name": reason_params['app_name'],
                            "op_id": user_name,
                            "schedule_type": "crontab" if detect_schedule['cron'] != "" else "once",
                            "crontab": detect_schedule['cron'],
                            "timing_params": {
                                "is_lab": is_lab,
                                "kpiIds": detect_schedule.get("kpiIds"),
                                "labId": labId if is_lab else "",
                                "insId": insId if not is_lab else "",
                                "mysql_conf": detect_schedule['database'],
                                "batch_size": None
                            }
                        }
                        for detect_schedule in schedule_params.get('reasonSchedulerList', []) if
                        reason_node_id and reason_params
                    ]

                    # 确认调度节点，推理节点部署参数
                    if reason_params and reason_schedule_request:
                        detect_schedule_params = {
                            'user_name': user_name,
                            'user_id': user_id,
                            'app_name': f"{reason_params['app_name']}-schedule",
                            'expe_name': expe_name,
                            'pl_container': container_name,
                            'node_id': 'detectSchedule',
                            'type': 'schedule',
                            'is_temp': is_lab,
                            'ins_id': insId if insId else labId,
                            'res': {"cpu": 0.5, "mem": 500},
                            'run_id': run_id,
                            'proj_name': proj_name,
                            'inst_count': 1,
                            'pk_params': {
                                'schedule_request': reason_schedule_request,
                                'global_params': global_params,
                            }
                        }
                        app_list.append(detect_schedule_params)

                    if train_params and train_schedule_request:
                        train_schedule_params = {
                            'user_name': user_name,
                            'user_id': user_id,
                            'app_name': f"{train_params['app_name']}-schedule",
                            'expe_name': expe_name,
                            'pl_container': container_name,
                            'node_id': 'trainSchedule',
                            'type': 'schedule',
                            'is_temp': is_lab,
                            'ins_id': insId if insId else labId,
                            'res': {"cpu": 0.5, "mem": 500},
                            'run_id': run_id,
                            'proj_name': proj_name,
                            'inst_count': 1,
                            'pk_params': {
                                'schedule_request': train_schedule_request,
                                'global_params': global_params,
                            }
                        }
                        app_list.append(train_schedule_params)

                    deploy_app = []
                    for app in app_list:
                        if app:
                            deploy_app.append(app['app_name'])
                            th = threading.Thread(target=Model.final_deploy_app, args=(app, False, version_id),
                                                  daemon=True)
                            th.start()
                            time.sleep(1)
                    logger.info(f"app:{app_name} deploy starting, app_list:{deploy_app}")

                except Exception as e:
                    logs_params = {
                        "percent": "0",
                        "user_name": user_name,
                        "app_name": app_name,
                        "deploy_name": '',
                        "app_type": '',
                        "app_status": 0,  # 状态部署中
                        "note": f"部署信息检查失败{e}"}
                    Model.write_logs(logs_params)
                    logger.error(f"deploy app:{app_name} failed,{traceback.format_exc()}")
                    break
            logger.info("app status starting...")
            return _rjson("app status starting...", 200, "succeed")
        except Exception as e:
            logger.error(f"deploy app failed, {traceback.format_exc()}")
            return _rjson(None, 701, "deploy app failed:" + str(e))

    @staticmethod
    def final_deploy_app(params=None, is_request=True, version_id=None):
        try:
            if is_request:
                jsons = request.stream.read().decode('utf-8')
                params = json.loads(jsons)['BODY']
            logger.info("...enter pl/deploy_app...[{}]", params)
            user_name = params['user_name']
            app_name = params['app_name']  # 应用名称
            expe_name = params['expe_name']
            pl_container = params['pl_container']  # 流水线容器名称
            node_id = params.get('node_id', '')  # 部署节点id
            app_type = params.get('type', 'web')  # 服务类型
            is_temp = params.get('is_temp', False)  # 是否是临时容器
            res = params['res']  # 资源描述
            run_id = params['run_id']  # 运行id
            proj_name = params['proj_name']  # 项目名称
            inst_count = params.get('inst_count', 1)  # 运行实例数
            pk_params = params.get('pk_params')

        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))

        k8s = K8S(_conf['k8s_config_file'])
        # 判断是否是临时容器
        if is_temp:
            deploy_name = f"lab-{_gen_rand_str(6)}".lower()
        else:
            deploy_name = f"app-{_gen_rand_str(6)}".lower()
        params['__container'] = deploy_name

        # 删除该用户下同名app的日志（部署失败残留的日志）
        db.MD.del_deploy_logs(app_name, user_name)

        # 部署日志模板
        logs_params = {
            "percent": "0.1",
            "user_name": user_name,
            "app_name": app_name,
            "deploy_name": deploy_name,
            "app_type": app_type,
            "app_status": -1,  # 状态部署中
            "note": "部署信息检查中",
            # "log_id": log_id
        }
        # 部署信息检查
        obj, rcode, msg = Model._check_deploy(logs_params, k8s, pl_container, params, is_request)
        if rcode != 200:
            return _rjson(obj, rcode, msg)

        # 打包检查
        obj, rcode, msg = Model._check_packed(logs_params, k8s, pl_container, app_type, node_id, pk_params, version_id,
                                              expe_name)
        if rcode != 200:
            return _rjson(obj, rcode, msg)

        # 部署
        obj, rcode, msg = Model._deploy_app(logs_params, params, version_id)

        # 部署定时任务容器
        # obj, rcode, msg = Model.deploy_scheduler_app()
        return _rjson(obj, rcode, msg)

    @staticmethod
    def final_deploy_app_new(params=None, is_request=True, version_id=None):
        try:
            if is_request:
                jsons = request.stream.read().decode('utf-8')
                params = json.loads(jsons)['BODY']
            logger.info("...enter pl/deploy_app_new...[{}]", params)
            user_name = params['user_name']
            app_name = params['app_name']  # 应用名称
            expe_name = params['expe_name']
            pl_container = params['pl_container']  # 流水线容器名称
            node_id = params.get('node_id', '')  # 部署节点id
            app_type = params.get('type', 'web')  # 服务类型

            scene_type = params.get('scene_type', '02')  # scene_type  01 - 训练资源池；02 - 试用资源池；03 - 推理资源池
            calc_type = params.get('calc_type', '01')  # calc_type  01-通用算力；02-GPU算力；03-NPU算力
            res = params['res']  # 资源描述
            cpu = params['res']["cpu"]  # 使用CPU的 cores 数量, 可以为小数
            mem = params['res']["mem"]

            res = {}
            res['gpu'] = params['res'].get("gpu", None)  # 分配gpu，显卡个数，每张显存大小, 算力百分比，单位(第四范式mb，阿里云gb), 默认None为不分配
            res['npu'] = params['res'].get("npu", None)  # 分配npu, 卡的个数
            res['gpuType'] = params['res'].get("gpuType", None)  # 1:阿里云，2:第四范式，3:驱动 有gpu时填写

            run_id = params['run_id']  # 运行id
            proj_name = params['proj_name']  # 项目名称
            inst_count = params.get('inst_count', 1)  # 运行实例数

            params['svc']['requestParam']  # 部署服务的请求入参

            pk_params = params.get('pk_params', {})

        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))

        # 更新部署状态为01，部署中
        _db.update_jupyter_deploy_info('', '', '01', app_name.lower())

        # 容器名称生成
        # deploy_name = f"app-{_gen_rand_str(6)}".lower()
        params['__container'] = app_name
        # 删除该用户下同名app的日志（部署失败残留的日志）
        db.MD.del_deploy_logs(app_name, user_name, is_new=True)

        # 部署日志模板
        logs_params = {
            "percent": "0.05",
            "user_name": user_name,
            "app_name": app_name,
            "deploy_name": app_name,
            # "deploy_name": deploy_name,
            "app_type": app_type,
            "app_status": -1,  # 状态部署中
            "note": "部署文件获取中",
            # "log_id": log_id
        }

        # 部署集群命名空间--名称以及配置文件获取
        obj, rcode, msg = Model._get_deploy_info(logs_params, params)
        if rcode != 200:
            return _rjson(obj, rcode, msg)
        params['k8s_info'] = obj

        # 资源检查
        obj, rcode, msg = Model._check_res(logs_params, params['k8s_info'], params)
        if rcode != 200:
            return _rjson(obj, rcode, msg)

        # 资源处理
        obj, rcode, msg = Model._res_deal(logs_params, res)
        if rcode != 200:
            return _rjson(obj, rcode, msg)
        params['resInfo'] = obj['resInfo']
        params['resType'] = obj['resType']

        # 部署信息检查
        # 检查开发容器的状态
        # 获取开发容器信息，集群信息等
        kf_container_info = _db.devc_get_v119(pl_container)
        logger.info(kf_container_info)
        if kf_container_info is None:
            kf_container_info = {}
            # return _rjson(None, 5023, f"not found container {pl_container}")
        if "kubeconfig" not in kf_container_info.keys() or "namespace" not in kf_container_info.keys():
            kf_k8s = K8S(kf_container_info.get("kubeconfig", _conf['k8s_config_file']))
        else:
            kf_k8s = K8S(kf_container_info["kubeconfig"], kf_container_info["namespace"])
        obj, rcode, msg = Model._check_deploy(logs_params, kf_k8s, pl_container, params, is_request)
        if rcode != 200:
            return _rjson(obj, rcode, msg)

        params['kf_k8s'] = kf_k8s
        # 部署
        th = threading.Thread(target=Model._deploy_app_new, args=(logs_params, params, version_id))
        th.start()

        # 部署定时任务容器
        # obj, rcode, msg = Model.deploy_scheduler_app()
        if rcode == 200:
            rcode = 0
        return _rjson(obj, rcode, msg)

    @staticmethod
    def get_app_ip():
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter pl/get_app_ip...[{}]", jsons)
            params = json.loads(jsons)['BODY']
            user_name = params['user_name']
            app_name = params['app_name']
            pipeline_id = params['pipeline_id']
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))

        apps_obj = db.MD.get_apps(user_name, app_name,pipeline_id)
        if apps_obj is None:
            logger.error(f"get app {app_name} logs failed")
            return _rjson(None, 705, f"get app {app_name} ip and port failed")

        logger.info(f"apps_obj -- {apps_obj}")
        app_data = []
        for _obj in apps_obj:
            app_data.append(
                {
                    "app_ip": _obj[0],
                    "app_port": _obj[1],
                    "app_name": _obj[2],
                    "pod_name": _obj[3],
                    "url": f"http://{_obj[0]}:{_obj[1]}/execute",
                    "exp_name": _obj[4]
                }
            )
        return _rjson(app_data, 200)

    @staticmethod
    def pipeline_import():
        '''
            流水线模型导入【暂不使用】

            从模型生成流水线

            入参:
            @new_expe_name_text    实验名称(文本)
            @new_expe_desc  实验描述
            @proj     所属项目
            @user    用户名
        '''
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter pl/pipeline_import...[{}]", jsons)
            params = json.loads(jsons)['BODY']
            new_expe_name_text = params['new_expe_name_text']
            new_expe_desc = params.get("new_expe_desc")
            proj = params.get("proj")
            user = params.get("user")
            model_path = params.get("model_path")
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))

        # 生成新的实验，生成新导入的数据
        try:
            # 生成新的实验，生成新导入的数据
            fileserver_url = _conf['fileserver_url']
            url = f"{fileserver_url}/pipeline_import_record"
            logger.info(f"request import data url -- {url}")

            data = json.dumps({"BODY": dict(
                new_expe_name_text=new_expe_name_text,
                new_expe_desc=new_expe_desc,
                proj=proj,
                user=user,
                model_path=model_path,
            )})
            r = requests.post(url, data=data)
            logger.info(f"import data result, r.status_code is {r.status_code}, r.text is {r.text}")
            if r.status_code == 200:
                if json.loads(r.text)['ROOT']['RETURN_CODE'] != 0:
                    logger.error(f"import data failed: {json.loads(r.text)['ROOT']['RETURN_MSG']}")
                    return None, 406, f"import data failed：{json.loads(r.text)['ROOT']['RETURN_MSG']}"
                else:
                    expe_name = json.loads(r.text)['ROOT']['BODY']['expe_name']
                    run_id = json.loads(r.text)['ROOT']['BODY']['run_id']

            else:
                logger.error(f"import data failed")
                return None, 500, f"import data failed"

        except Exception as x:
            logger.error("import data failed {}, {}", x, traceback.format_exc())
            return _rjson(None, 701, f"import data failed {x}")

        # 导入第三方包以及流水线相关文件
        # 1 流水线进入编辑状态
        # if not version_id:
        edit_params = {
            "user_name": user,
            "proj_name": proj,
            "expe_name": expe_name,
            "run_id": run_id,
        }
        r = enter_edit_mode(edit_params, is_request=False)
        logger.info(f"enter edit mode response:{r.json} ")
        if r.status_code == 200:
            if r.json['ROOT']['RETURN_CODE'] == 0 or r.json['ROOT']['RETURN_CODE'] == 7701:
                container_name = r.json['ROOT']['BODY']['container_name']
                logger.info(f"expe_name:{expe_name}, enter edit mode succeed.")
                time.sleep(10)
            else:
                logger.error(f"expe_name:{expe_name},enter edit mode failed:{r.json['ROOT']['RETURN_MSG']}")
                return _rjson(None, 701, f"import failed [enter edit mode failed:{r.json['ROOT']['RETURN_MSG']}]")
        else:
            logger.error(f"expe_name:{expe_name},enter edit mode failed")
            return _rjson(None, 701, f"import failed [enter edit mode failed]")

        # 2 导入文件
        k8s = K8S(_conf['k8s_config_file'])
        pod_name, node_host, pod_ready, pod_total, ports = k8s.query_pod(container_name)
        node_port = ''
        for i in ports:
            if i[0] == 8181:
                node_port = i[2]  # 外部访问端口
                break
        if not node_port:
            logger.error(f"expe_name:{expe_name},enter edit mode failed [do not get node_port]")
            return None, 406, f"expe_name:{expe_name},enter edit mode failed [do not get node_port]"

        cmd = f"/data/.app/bin/fkme scp -i /data/.app/id_rsa_tr -p {node_port} {model_path}/run _base_@{node_host}:/data/xx/{expe_name}_{run_id}/run"
        logger.info("file-sync: {}", cmd)
        try:
            subprocess.check_output(shlex.split(cmd), shell=False)
        except Exception as x:
            logger.error("fkme failed {}", x)
            return None, 406, f"import failed [fkme failed {x}]"

        # 备份安装的扩展包
        cmd = f"/data/.app/bin/fkme scp -i /data/.app/id_rsa_tr -p {node_port} {model_path}/.local _base_@{node_host}:/data/xx/{expe_name}_{run_id}/.local"
        logger.info("fkme: {}", cmd)
        try:
            subprocess.check_output(shlex.split(cmd), shell=False)
        except Exception as x:
            logger.error("fkme failed {}", x)
            return None, 406, f"import failed [fkme failed {x}]"

        return _rjson({"status": "success", "expe_name": expe_name, "run_id": run_id})
