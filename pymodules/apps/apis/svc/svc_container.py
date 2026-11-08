# _*_ coding:utf-8_*_

# 开发容器、计算容器、自动化学习容器相关服务
import os
import logging as log
from loguru import logger
import json
import subprocess
import threading
import time
import shutil

import requests
import operator
from flask import Flask, request, Response, abort
import random
import yaml
import socket
import traceback
import svc.util_db as db
from svc.util_k8s import k8s_create_deployment, k8s_create_service, k8s_delete_all, k8s_query_pod
from svc.util.k8s import K8S

from svc.util import db as _db

from kubernetes import client, config
from kubernetes.client.rest import ApiException
import string
from random import choice, randint

import urllib.parse as urlparse
import shlex
import tarfile
import paramiko
import re

from flask import url_for

import sys
import importlib

importlib.reload(sys)
_pjoin = os.path.join

from svc.util import ng

_base_dir = '/pyserv' if os.path.exists('/pyserv') else '/data/.app'


def registerHandler(app, conf):
    global k8s_name_space
    global k8s_docker_repository
    global jupyter_start_sha1
    global jupyter_start_package
    global task_start_sha1
    global task_start_package
    global _conf
    global base_path, sftpgo_path, fileserv_path, base_home
    global data_store_changed, sftpgo_users

    k8s_name_space = conf['k8s_name_space']
    k8s_docker_repository = conf['k8s_docker_repository']

    jupyter_start_package = conf['jupyter_start_package']
    jupyter_start_sha1 = conf['jupyter_start_sha1']
    task_start_sha1 = conf['task_start_sha1']
    task_start_package = conf['task_start_package']

    base_home = conf['base_home']
    base_path = conf.get("fileserv_path", "/pyserv/fileserv")
    fileserv_path = base_path
    sftpgo_path = conf.get('sftpgo_path', None)
    if sftpgo_path is not None:
        if not sftpgo_path.startswith('/'):  # 相对路径处理
            sftpgo_path = f"{conf['base_home']}/{sftpgo_path}"

        data_store_changed = conf['data_store_changed']  # 数据改变时调用的方法, 该方法把数据保存到文件中
        sftpgo_users = conf['data_store_get']('sftpgo_users')

    app.add_url_rule("/TaskCon_create", "TaskCon_create", TaskCon_create, methods=["POST"])
    app.add_url_rule("/TaskCon_remove", "TaskCon_remove", TaskCon_remove, methods=["POST"])
    app.add_url_rule("/TaskCon_stop", "TaskCon_stop_old", TaskCon_stop_old, methods=["POST"])
    app.add_url_rule("/TaskCon_stop_new", "TaskCon_stop", TaskCon_stop, methods=["POST"])
    app.add_url_rule("/TaskCon_restart", "TaskCon_restart_old", TaskCon_restart_old, methods=["POST"])
    app.add_url_rule("/TaskCon_restart_new", "TaskCon_restart", TaskCon_restart, methods=["POST"])
    app.add_url_rule("/k8s_contanier_stat", "k8s_contanier_stat_new", k8s_contanier_stat_new, methods=["POST"])
    app.add_url_rule("/Upload_examples_file", "Upload_examples_file", Upload_examples_file, methods=["POST"])
    app.add_url_rule("/Examples_file", "Examples_file", Examples_file, methods=["POST"])
    # app.add_url_rule("/gm_start", "gm_start", gm_start, methods=["POST"])
    app.add_url_rule("/TaskAuto_start_temp", "TaskAuto_start_temp", TaskAuto_start_temp, methods=["POST"])
    app.add_url_rule("/jupyterfile2fileserver", "jupyterfile2fileserver", jupyterfile2fileserver, methods=["POST"])
    app.add_url_rule("/DevContainerToImageV119", "DevContainerToImageV119", DevContainerToImageV119, methods=["POST"])
    app.add_url_rule("/GenImageFromLocalI", "GenImageFromLocalI", GenImageFromLocalI, methods=["POST"])
    app.add_url_rule("/ModelPubImage_k8s_v119", "ModelPubImage_k8s_v119", ModelPubImage_k8s_v119, methods=["POST"])
    app.add_url_rule("/application_deployment", "application_deployment", application_deployment, methods=["POST"])
    app.add_url_rule("/ModelFileSFTPV119", "ModelFileSFTPV119", ModelFileSFTPV119, methods=["POST"])
    app.add_url_rule("/api_qry_v119", "api_qry_v119", api_qry_v119, methods=["POST"])
    app.add_url_rule("/application_stop", "application_stop", application_stop, methods=["POST"])
    app.add_url_rule("/application_start", "application_start", application_start, methods=["POST"])
    app.add_url_rule("/list_host", "list_host", list_host, methods=["POST"])
    app.add_url_rule("/create_pvc_app", "create_pvc_app", create_pvc_app, methods=["POST"])
    app.add_url_rule("/list_pvc_app", "list_pvc_app", list_pvc_app, methods=["POST"])
    app.add_url_rule("/del_pvc_app", "del_pvc_app", del_pvc_app, methods=["POST"])
    app.add_url_rule("/list_baseImage_app", "list_baseImage_app", list_baseImage_app, methods=["POST"])
    app.add_url_rule("/del_genImage", "del_genImage", del_genImage, methods=["POST"])
    app.add_url_rule("/FileservAddrQry", "FileservAddrQry", FileservAddrQry, methods=["POST"])
    app.add_url_rule("/app_container_stat", "app_container_stat", app_container_stat, methods=["POST"])  # 探测部署容器的ip和端口
    # app.add_url_rule("/create_fileserver", "create_fileserver",create_fileserver, methods=["POST"])
    app.add_url_rule("/Jupyter_expansion", "Jupyter_expansion", Jupyter_expansion, methods=["POST"])
    app.add_url_rule("/app_stretch", "app_stretch", app_stretch, methods=["POST"])  # 应用伸缩，即伸缩pod数量
    app.add_url_rule("/app_stretch_qry", "app_stretch_qry", app_stretch_qry, methods=["POST"])  # 应用伸缩，查询实际伸缩pod数量
    app.add_url_rule("/image_copy", "image_copy", image_copy, methods=["POST"])  # 镜像复制操作
    app.add_url_rule("/GetDatasetsToJupyter", "GetDatasetsToJupyter", GetDatasetsToJupyter,
                     methods=["POST"])  # 数据集管理界面, 直接发起向指定容器同步数据集
    app.add_url_rule("/automl_model_register", "automl_model_register", automl_model_register,
                     methods=["POST"])  # 模型文件的按版本管理功能实现--模型注册
    app.add_url_rule("/inquire_model_by_user", "inquire_model_by_user", inquire_model_by_user,
                     methods=["POST"])  # 模型文件的按版本管理功能实现--模型条件查询--按用户查询查询模型
    app.add_url_rule("/inquire_model_by_maxTime", "inquire_model_by_maxTime", inquire_model_by_maxTime,
                     methods=["POST"])  # 模型文件的按版本管理功能实现--模型条件查询--按用户查询查询模型
    app.add_url_rule("/inquire_model_by_metrics", "inquire_model_by_metrics", inquire_model_by_metrics,
                     methods=["POST"])  # 模型文件的按版本管理功能实现--模型条件查询--按指标最值查询模型
    app.add_url_rule("/get_configparam_by_modeltype", "get_configparam_by_modeltype", get_configparam_by_modeltype,
                     methods=["POST"])  # 根据模型类型获取模板参数
    # app.add_url_rule("/start_task", "start_task",start_task, methods=["POST"]) # nni训练任务启动
    app.add_url_rule("/stop_task", "stop_task", stop_task, methods=["POST"])  # nni训练任务停止
    app.add_url_rule("/check_status", "check_status", check_status, methods=["POST"])  # nni训练任务状态查询
    app.add_url_rule("/get_trials", "get_trials", get_trials, methods=["POST"])  # 获取nni训练信息
    # app.add_url_rule("/model_export", "model_export",model_export, methods=["POST"]) # 模型保存,与/model_deploy模型部署服务合并
    app.add_url_rule("/model_deploy", "model_deploy", model_deploy, methods=["POST"])  # 模型部署
    app.add_url_rule("/get_trials_log", "get_trials_log", get_trials_log, methods=["POST"])  # 查询trial日志
    app.add_url_rule("/show_tb", "show_tb", show_tb, methods=["POST"])  # start tensorboard to view the train scalar
    app.add_url_rule("/pod_exec", "pod_exec", pod_exec, methods=["POST"])
    app.add_url_rule("/experi_data_collect", "experi_data_collect", experi_data_collect, methods=["POST"])
    app.add_url_rule("/deploy_app", "deploy_app", deploy_app, methods=["POST"])   # 编码式建模服务部署
    app.add_url_rule("/get_namespace_gpu", "get_namespace_gpu", get_namespace_gpu, methods=["POST"])   # 命名空间GPU算力资源获取
    app.add_url_rule("/instance/container/stop", "stop_instance_container", stop_instance_container, methods=["POST"])  # 删除部署实例容器


    def has_no_empty_params(rule):
        defaults = rule.defaults if rule.defaults is not None else ()
        arguments = rule.arguments if rule.arguments is not None else ()
        return len(defaults) >= len(arguments)

    from werkzeug.routing import Rule
    def site_map():
        links = []
        rule: Rule = None
        for rule in app.url_map.iter_rules():
            # Filter out rules we can't navigate to in a browser
            # and rules that require parameters
            # if "GET" in rule.methods and has_no_empty_params(rule):
            try:
                url = url_for(rule.endpoint, **(rule.defaults or {}))
                links.append((url, rule.endpoint))
            except:
                pass
        return "\n".join([f"{a} == {b}" for a, b in links])

    app.add_url_rule('/site-map', 'site-map', site_map, methods=['GET'])

    _conf = conf

    config.load_kube_config(_conf["k8s_config_file"])

    # db.pool_init(_conf["db_conn_param"])
    db._init_(_conf)

    _dump_conf(conf)
    # 启动线程监控容器信息，host_ip,内外端口等
    th = threading.Thread(target=query_container_info)
    th.start()
    # 启动线程，更新node状态
    # th = threading.Thread(target=update_node_status)
    # th.start()
    # 启动单独线程,执行nni自动部署
    # th = threading.Thread(target=train_task_mon,args=(_conf, db))
    # th.start()
    random.seed(time.time())


def _dump_conf(conf):
    # 查询训练容器的k8s的相关信息
    scene_type = '01'
    k8s_info_list = db.K8sInfo.get_k8s_train_info(scene_type)
    logger.info(f"k8s_info is {k8s_info_list}")
    if k8s_info_list:
        for k8s_info in k8s_info_list:
            namespace, kubeconfig_name, path = k8s_info
            if path[-1] == '/':
                kubeconfig_path = path + kubeconfig_name
            else:
                kubeconfig_path = path + '/' + kubeconfig_name
            data = {'namespace': namespace, 'kubeconfig_name': kubeconfig_name, 'kubeconfig': kubeconfig_path}
            w_env_json(conf, data)
    else:
        logger.error(f"get kubeinfo failed")


def w_env_json(conf, data):
    _env_json_path = f"/data/.app/.{data['kubeconfig_name']}_{data['namespace']}_env.json"
    with open(_env_json_path, 'w', encoding='utf8') as fo:
        json.dump(dict(
            fileserver_url=conf['fileserver_url'],
            fileserver_sftp=conf['FS_ADDR'],
            manager_svc=conf['__svc_addr__'],
            pypi_server=conf['pypi_server'],
            jupyter_start_package=conf['jupyter_start_package'],
            db_conn_str=conf["db_conn_str"],
            local_ip=conf['local_ip'],
            k8s_name_space=data['namespace'],
            k8s_config_file=data['kubeconfig'],
            k8s_cpu_requests_time=conf.get('cpu_request_times', -1),
            k8s_storage_class=conf.get("k8s_storage_class", None),
        ), fo)


def _upload_conf_to_container(cont_host, cont_port, env_path=None):
    '''向jupyter容器中上传 环境配置文件'''
    if not env_path:
        env_path = '/data/.app/.env.json'
    if not os.path.exists(env_path):
        logger.error("env_path {env_path} not exist")
    cmd = shlex.split(
        f"/data/.app/bin/fkme scp -i /data/.app/id_rsa_tr -p {cont_port} {env_path}  _base_@{cont_host}:/data/.app/.env.json")
    try:
        out = subprocess.check_output(cmd, shell=False)
        logger.info("upload .env.json done!")
    except Exception as x:
        logger.error("upload .env.json failed! {} {}", x, traceback.format_exc())


# def loop_func(func):
#     timer = Timer(10,func)
#     timer.start()
#     start = time.time()
#     end = start
#     while end - start<=20:
#         continue
#     timer.cancer()


# 启动自动化学习
def TaskAuto_start_temp():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter TaskAuto_start_temp...[%s]" % jsons)
        jo = json.loads(jsons)['ROOT']
        target = jo.get('target', "172.18.238.17:34220")  # 目标执行容器, 对应的前台参数为 CONTAINER_NAME
        expe_type = jo.get("expe_type", "03")  # 实验类型 01-开发容容器任务 03-自动化学习， 这里固定为03
        expe_name = jo['expe_name']  # 实验名称， 用户输入， 没有中文
        run_id = jo['run_id']  # 执行id
        task_type = jo.get("task_type", "structured-data-classifier")  # structured-data-classifier text-classifier
        trials = jo.get("trials", 4)
        epochs = jo.get("epochs", 10)  # 训练迭代数， 默认值10
        dataset = jo.get("dataset", None)
        extra_params = jo.get("extra_params", None)
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    wd = "/data"
    # TODO cmd先从配置里面取, 后续需要更新
    cmd_conf = _conf["automl"][task_type]["cmd"]
    cmd = cmd_conf + " --trials " + str(trials) + " --epochs " + str(epochs)
    if extra_params is not None:
        for k, v in extra_params.items():
            cmd = cmd + " --" + k + " " + str(v)
    node_id = _gen_rand_str(3)
    rdata = {"BODY": {"wfId": expe_name, "procId": "aaa",
                      "taskId": run_id, "nodeId": node_id,
                      "CMD": cmd, "CWD": wd}}
    # TODO 当前需要上传到计算容器内的数据集，从配置中取automl，后续需要更新
    files = _conf["automl"][task_type]["files"]
    # TODO remote_path值暂时置为"/data", 后续需要更新
    remote_path = "/data"
    if files is not None:
        for local_path in files:
            if not upload_path(f"http://{target}", local_path, remote_path):
                raise Exception(f"upload_path {local_path} to {remote_path} failed")
    if dataset is not None:
        for local_path in dataset:
            if not upload_path(f"http://{target}", local_path, remote_path):
                raise Exception(f"upload_path {local_path} to {remote_path} failed")
    log.info(rdata)
    url = f"http://{target}/wf_start"
    r = requests.post(url, json=rdata)
    if r.status_code != 200:
        raise Exception(f"start process failed, http-code: {r.status_code}")
        return _rjson({}, rcode=406, rmsg=f"start process failed, http-code: {r.status_code}")
    return _rjson({})


def update_node_status():
    while True:
        run_node_status = db.devc_get_all_node_status()
        for node_info in run_node_status:
            run_id = node_info[0]  # 对应task_id
            expe_name = node_info[1]  # wf_id
            node_name = node_info[2]  # node_id
            container_name = node_info[3]
            container_info = db.devc_get_v119(container_name)
            if container_info is None:
                continue
            node_host = container_info.get("node_host", None)
            ports = container_info.get("ports", None)
            if node_host is None or ports is None:
                continue
            for port in ports:
                if port[0] == 8181:
                    p = port[2]
            url = f"http://{node_host}:{p}/wf_check"
            rdata = {"taskId": run_id, "nodeId": node_name}
            r = requests.post(url, json=rdata)
            # log.info(url, r.status_code, r.text)
            if r.status_code != 200:
                raise Exception(f"start process failed, http-code: {r.status_code}")
                continue
            procs = json.loads(r.text)
            status = procs.get("status", None)
            if status == "running":
                db.devc_update_node_status("01", run_id, expe_name, node_name)
            elif status == "exited":
                error_msg = procs.get("stderr", None)
                db.devc_update_node_status("04", run_id, expe_name, node_name, error_msg)
            else:
                db.devc_update_node_status("02", run_id, expe_name, node_name)
        time.sleep(300)


def query_container_info():
    while True:
        time.sleep(10)
        try:
            containerInfoList = db.devc_get_all_container_info()
            if containerInfoList is None:
                continue
            # logger.info("query_container_info len={}", len(containerInfoList))
            for (containerInfo, contaniername) in containerInfoList:
                node_host = containerInfo.get("node_host", None)
                if node_host:
                    continue
                dep_name = containerInfo.get("depName", None)
                if containerInfo.get("create_type", None) == 'manually':
                    continue  # 手工创建容器, 不处理
                pod_name, node_host, ready, total, ports = k8s_query_pod(k8s_name_space, dep_name,
                                                                         containerInfo.get("serviceName", None))
                if node_host is not None and ports is not None:
                    containerInfo["node_host"] = node_host
                    containerInfo["ports"] = ports
                    containerInfo["pod_name"] = pod_name
                    containerInfo["total"] = total
                    db.devc_update_v119(contaniername, containerInfo, None, None)
                # elif ready == 99:  # 先不做处理,  连接不同的k8s 环境可能产生冲突, 另外就是不同的ns 也可能出现毛病
                #     r = db.devc_update_v119(contaniername, containerInfo, '99', None)
                #     logger.warning("--container check: {}'s deployment not found, update status to 99, return {}",
                #                    contaniername, r)

        except Exception as x:
            log.error("query container info failed %s" % str(x))


def _gen_rand_str(nc=6):
    if nc == 0:
        min_char, max_char = 24, 32
    else:
        min_char, max_char = nc, nc
    allchar = string.ascii_letters + string.digits  # string.punctua/tion +
    token = "".join(choice(allchar) for x in range(randint(min_char, max_char)))
    return token


def _rjson(obj, rcode=0, rmsg="success"):
    ret = {"ROOT": {"HEADER": {}, "RETURN_CODE": rcode, "RETURN_MSG": rmsg, "BODY": obj}}
    r = Response(json.dumps(ret), mimetype="application/json; charset=utf-8")
    return r


# 上传本地文件或目录到计算容器
def _upload_file(url, local_file, remote_file):
    log.info("...enter _upload_file...")
    files = {'myfile': open(local_file, 'rb')}
    values = {'path': remote_file}
    r = requests.post(url, files=files, data=values)
    if r.status_code != 200:
        return False
    return True


def upload_path(base_url, local_path, remote_path):
    log.info("...enter_upload_path...")
    if not os.path.exists(local_path):
        return False
    url = f"{base_url}/wf_putfile"
    if os.path.isfile(local_path):
        remote_file = f"{remote_path}/{os.path.basename(local_path)}"
        log.info(f"-------------remote_file {remote_file}")
        return _upload_file(url, local_path, remote_file)
    elif os.path.isdir(local_path):
        _ll = len(os.path.dirname(local_path))
        if local_path.find('/') >= 0:
            _ll += 1
        # _rbase = f"{remote_path}/{os.path.basename}"
        for root, dirs, files in os.walk(local_path):
            for f in files:
                fpath = os.path.join(root, f)
                remote_file = f"{remote_path}/{fpath[_ll:]}"
                _r = _upload_file(url, fpath, remote_file)
                if _r != True:
                    return False
    return True


# 启动自动化学习
def TaskAuto_start(target, expe_name, run_id, task_type, trials, epochs, dataset, extra_params, dst_podname):
    log.info("...enter TaskAuto_start...")
    # TODO 当前需要上传到计算容器内的数据集，从配置中取automl，后续需要更新
    # TODO remote_path值暂时置为"/data", 后续需要更新
    remote_path = "/data"
    src_podname = _conf["fileserve_pod"]
    log.info(f"src_podname={src_podname}")
    log.info(f"dst_podname={dst_podname}")
    if dataset is not None:
        for local_path in dataset:
            local_path_base = os.path.dirname(local_path)
            src_dir = os.path.dirname(local_path_base)
            src_name = os.path.basename(local_path_base)
            log.info(src_dir)
            log.info(src_name)
            # src_dir="/data/.datasets/congpq"
            # src_name="train"
            src_cmd = f"{_conf['kubectl']} --kubeconfig {_conf['k8s_config_file']} exec {src_podname} -- tar cmf - -C {src_dir} {src_name}"
            dst_cmd = f"{_conf['kubectl']} --kubeconfig {_conf['k8s_config_file']} exec {dst_podname} -i -- tar xmf - -C {remote_path} --no-same-owner"
            log.info(f"{src_cmd} | {dst_cmd}")
            proc = subprocess.Popen(f"{src_cmd} | {dst_cmd}", shell=True)
            try:
                rcode = proc.wait()
                log.info(f"Upload Success {local_path} to {remote_path}")
                # print(out.decode("utf8"))
            except Exception as x:
                log.info("Upload Failed [%s]" % str(x))

    # 上传代码文件
    files = _conf["automl"][task_type]["files"]
    if files is not None:
        for local_path in files:
            if not upload_path(f"http://{target}", local_path, remote_path):
                raise Exception(f"upload_path {local_path} to {remote_path} failed")
    wd = "/data"
    # TODO cmd先从配置里面取, 后续需要更新
    file_path = os.path.join(remote_path, src_name)
    cmd_conf = _conf["automl"][task_type]["cmd"]
    cmd = cmd_conf + " --trials " + str(trials) + " --epochs " + str(epochs) + " --file_path " + str(file_path)
    log.info(cmd)
    if extra_params is not None:
        for k, v in extra_params.items():
            cmd = cmd + " --" + k + " " + str(v)
    node_id = _gen_rand_str(3)
    rdata = {"BODY": {"wfId": expe_name, "procId": "aaa",
                      "taskId": run_id, "nodeId": node_id,
                      "CMD": cmd, "CWD": wd}}
    url = f"http://{target}/wf_start"
    r = requests.post(url, json=rdata)
    if r.status_code != 200:
        raise Exception(f"start process failed, http-code: {r.status_code}")
        return _rjson({}, rcode=406, rmsg=f"start process failed, http-code: {r.status_code}")
    return _rjson({})


# 检查容器是否启动成功，即端口是否暴漏出来、host_id是否存在,(用于检测自动化学习容器是否启动成功，若启动成功则进一步启动自动化学习实验)
def k8s_contanier_detect(containerName):
    log.info(f"...enter k8s_contanier_detect...{containerName}")
    container_info = db.devc_get_v119(containerName)
    if container_info is None:
        return None, None, None
    node_host = container_info.get("node_host", None)
    ports = container_info.get("ports", "")
    pod_name = container_info.get("pod_name", None)
    p = None
    for port in ports:
        if port[0] == 8181:
            p = port[2]
    return node_host, p, pod_name


# 探索jupyter容器是否启动，jupyter进程是否启动，若都启动返回url,否则返回""
def k8s_contanier_stat():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter k8s_contanier_stat2...[%s]" % jsons)
        jo = json.loads(jsons)['ROOT']
        name = jo['name']  # 容器名称
        start_mode = jo.get('start_mode', None)  # 编码式建模打开模式  notebook(tree)和lab(lab)和vscode
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    container_info = db.devc_get_v119(name)
    if container_info is None:
        return _rjson(None, 5018, f"not found contanier {name}")
    dep_name = container_info.get("depName", None)
    svc_name = container_info.get("serviceName", None)
    ctype = container_info.get("ctype", "01")
    env = container_info.get("env", {})
    token = env.get("_TOKEN_", "")

    logger.info("query k8s container...")
    '''查询pod的状态和地址端口等
    返回: pod_name, node_host, ready, total, ports=[[target_port, protocol, node_port],...]'''
    _env_json_path = ''
    if "kubeconfig" not in container_info.keys() or "namespace" not in container_info.keys():
        k8s = K8S(container_info.get("kubeconfig", _conf['k8s_config_file']))
        _env_json_path = '/data/.app/.env.json'
    else:
        k8s = K8S(container_info["kubeconfig"], container_info["namespace"])
        kubeconfig_name = os.path.basename(container_info["kubeconfig"])
        _env_json_path = f"/data/.app/.{kubeconfig_name}_{container_info['namespace']}_env.json"

    try:
        # 增加判断容器是否限制
        deployment_by_name = k8s.query_deployment(name)
        replicas_num = deployment_by_name.spec.replicas
        if 0 == replicas_num or replicas_num is None:
            # 容器已停止需进行容器启动
            deployment_by_name.spec.replicas = 1
            k8s.patch_namespaced_deployment(name, deployment_by_name)
            # 修改数据库表中容器状态为进行中
            db.devc_update_status(name, "02")
            return _rjson({"url": ""})
        # ports=[[target_port, protocol, node_port],...]
        pod_name, node_host, ready, total, ports = k8s.query_pod(dep_name)
        if pod_name is None:
            logger.info("容器创建挂起中 {}...", dep_name)
            return _rjson({"url": ""})
        if node_host is None:
            logger.info("容器节点主机分配中 {}...", dep_name)
            return _rjson({"url": ""})
        if ready < total:
            logger.info("容器启动中 {}...", dep_name)
            return _rjson({"url": ""})

        # 配置 ctype_opts 中ports的第一个端口号为 服务端口
        opts = _conf.get("ctype_opts", {}).get(ctype, None)
        service_port = opts.get("ports", [8181, ])[0]

        p = None
        if ports is not None:
            for port in ports:
                if port[0] == service_port:
                    p = port[2]
        base_uri = env.get("BASE_URI", "[not found]")
        logger.info("---k8s_contanier_stat BASE_URI: {}", env.get("BASE_URI", "[not found]"))
        try:
            c = socket.create_connection((node_host, p), 1.0)
            c.close()
        except Exception as x:
            log.error(x)
            return _rjson({"url": ""}, rmsg="Port check failed [%s]" % str(x))

        # 上传环境配置到容器中
        _node_host = node_host
        _node_sshport = [i[2] for i in ports if i[0] == 2022][0]
        _upload_conf_to_container(_node_host, _node_sshport, _env_json_path)

        # 将端口注册到 ng 上,  并在删除容器的时候, 删除注册 (添加规则, 注册的时候保存注册时间, 超过1天的自动删除, 无需额外处理)
        try:
            x = ng.NG_PX(prefix=f'{_base_dir}/ng_pyserv')
            x.add_ng_loc(base_uri[1:], base_uri, node_host, p)
            # node_host, p = "134.96.176.16", "9089"  # for zjdx, frontend forward
            # node_host, p = "134.96.176.15", "3191"  # for zjdx, manager forward
            logger.warning("register nginx port of {} to {}:{}", base_uri, node_host, p)
        except Exception as x:
            logger.error("ng_px failed {} {}", x, traceback.format_exc())
            # logger.error(None, 507, f"ng register failed {x}")

        # 检查配置和客户端ip, 以便区分公网访问和内网访问, 返回不同的地址,  配置方式: "jupyter_visit_roles":[{"match":"", "target":["ip","port"]}]
        orig_addr = f"http://{node_host}:{p}{base_uri}"
        _cc = "jupyter_visit_roles"
        if "ip_client" in jo and _cc in _conf:
            ip_client = jo.get("ip_client")
            logger.warning("return jupyter visit url base on client_ip {}, matchs: {}", ip_client, _conf[_cc])
            for v in _conf[_cc]:
                match, nmatch, target = v.get('match', None), v.get('nmatch', None), v['target']
                if match and re.match(match, ip_client):
                    node_host, p = target
                    logger.warning("visit url matched [{}] then to {}", match, target)
                    break
                elif nmatch and not re.match(nmatch, ip_client):
                    node_host, p = target
                    logger.warning("visit url nmatched [{}] then to {}", nmatch, target)
                    break

        if service_port == 8085:  # 这里以 8085 端口号作为判断是否启动jupyter的条件
            if start_mode in ["tree", "lab"]:
                ret_url = f"http://{node_host}:{p}{base_uri}/{start_mode}?token={token}"
            else:
                ret_url = f"http://{node_host}:{p}{base_uri}/lab?token={token}"
        else:
            ret_url = f"http://{node_host}:{p}{base_uri}"

        logger.info("ret_url: {}", ret_url)
        return _rjson({"url": ret_url, "orig_addr": orig_addr})
    except Exception as x:
        logger.error("failed {}, {}", x, traceback.format_exc())
        return _rjson({"url": ""}, rmsg="--- check failed [%s]" % str(x))


def k8s_contanier_stat_new():
    """
        判断容器状态以及jupyter服务/vscode服务状态
    """
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter k8s_contanier_stat_new...[%s]" % jsons)
        jo = json.loads(jsons)['ROOT']
        name = jo['name']  # 容器名称
        start_mode = jo.get('start_mode', None)  # 编码式建模打开模式  notebook(tree)和lab(lab)和vscode
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    container_info = db.devc_get_v119(name)
    if container_info is None:
        return _rjson(None, 5018, f"not found contanier {name}")
    dep_name = container_info.get("depName", None)
    svc_name = container_info.get("serviceName", None)
    ctype = container_info.get("ctype", "01")
    env = container_info.get("env", {})
    token = env.get("_TOKEN_", "")

    logger.info("query k8s container...")

    # 获取集群信息以及环境信息
    _env_json_path = ''
    if "kubeconfig" not in container_info.keys() or "namespace" not in container_info.keys():
        k8s = K8S(container_info.get("kubeconfig", _conf['k8s_config_file']))
        _env_json_path = '/data/.app/.env.json'
    else:
        k8s = K8S(container_info["kubeconfig"], container_info["namespace"])
        kubeconfig_name = os.path.basename(container_info["kubeconfig"])
        _env_json_path = f"/data/.app/.{kubeconfig_name}_{container_info['namespace']}_env.json"

    try:
        # 增加判断容器是否限制
        deployment_by_name = k8s.query_deployment(name)
        replicas_num = deployment_by_name.spec.replicas
        # 判断容器是否运行中状态
        if 0 == replicas_num or replicas_num is None:
            # 容器已停止需进行容器启动
            deployment_by_name.spec.replicas = 1
            k8s.patch_namespaced_deployment(name, deployment_by_name)
            # 修改数据库表中容器状态为进行中
            db.devc_update_status(name, "02")
            return _rjson({"url": ""})
        # 获取容器信息
        pod_name, node_host, ready, total, ports = k8s.query_pod(dep_name)
        if pod_name is None:
            logger.info("容器创建挂起中 {}...", dep_name)
            return _rjson({"url": ""})
        if node_host is None:
            logger.info("容器节点主机分配中 {}...", dep_name)
            return _rjson({"url": ""})
        if ready < total:
            logger.info("容器启动中 {}...", dep_name)
            return _rjson({"url": ""})

        # 配置 ctype_opts 中ports的第一个端口号为 服务端口
        opts = _conf.get("ctype_opts", {}).get(ctype, None)
        if start_mode in ["vscode"]:
            service_port = opts.get("ports", [8086, ])[1]
        else:
            # 配置 ctype_opts 中ports的第一个端口号为 服务端口
            opts = _conf.get("ctype_opts", {}).get(ctype, None)
            service_port = opts.get("ports", [8085, ])[0]

        p = None
        if ports is not None:
            for port in ports:
                if port[0] == service_port:
                    p = port[2]
        base_uri = env.get("BASE_URI", "[not found]")
        logger.info("---k8s_contanier_stat BASE_URI: {}", env.get("BASE_URI", "[not found]"))
        # 判断编码工具服务是否正常
        try:
            c = socket.create_connection((node_host, p), 1.0)
            c.close()
        except Exception as x:
            log.error(x)
            return _rjson({"url": ""}, rmsg="Port check failed [%s]" % str(x))

        # 上传环境配置到容器中
        _node_host = node_host
        _node_sshport = [i[2] for i in ports if i[0] == 2022][0]
        _upload_conf_to_container(_node_host, _node_sshport, _env_json_path)

        # 将端口注册到 ng 上,  并在删除容器的时候, 删除注册 (添加规则, 注册的时候保存注册时间, 超过1天的自动删除, 无需额外处理)
        try:
            x = ng.NG_PX(prefix=f'{_base_dir}/ng_pyserv')
            if service_port == 8086:
                x.add_ng_loc(f"vscode-{base_uri[1:]}", f"/vscode/{base_uri[1:]}", node_host, p)
            else:
                x.add_ng_loc(base_uri[1:], base_uri, node_host, p)

            logger.warning("register nginx port of {} to {}:{}", base_uri, node_host, p)
        except Exception as x:
            logger.error("ng_px failed {} {}", x, traceback.format_exc())
            # logger.error(None, 507, f"ng register failed {x}")

        # 检查配置和客户端ip, 以便区分公网访问和内网访问, 返回不同的地址,  配置方式: "jupyter_visit_roles":[{"match":"", "target":["ip","port"]}]
        orig_addr = f"http://{node_host}:{p}{base_uri}"
        _cc = "jupyter_visit_roles"
        if "ip_client" in jo and _cc in _conf:
            ip_client = jo.get("ip_client")
            logger.warning("return jupyter visit url base on client_ip {}, matchs: {}", ip_client, _conf[_cc])
            for v in _conf[_cc]:
                match, nmatch, target = v.get('match', None), v.get('nmatch', None), v['target']
                if match and re.match(match, ip_client):
                    node_host, p = target
                    logger.warning("visit url matched [{}] then to {}", match, target)
                    break
                elif nmatch and not re.match(nmatch, ip_client):
                    node_host, p = target
                    logger.warning("visit url nmatched [{}] then to {}", nmatch, target)
                    break

        # 获取编码工具服务地址
        if service_port == 8085:  # 这里以 8085 端口号作为判断是否启动jupyter的条件
            if start_mode in ["tree", "lab"]:
                ret_url = f"http://{node_host}:{p}{base_uri}/{start_mode}?token={token}"
            else:
                ret_url = f"http://{node_host}:{p}{base_uri}/lab?token={token}"
        elif service_port == 8086:
            if start_mode in ["vscode"]:
                ret_url = f"http://{node_host}:{p}"
        else:
            ret_url = f"http://{node_host}:{p}{base_uri}"

        logger.info("ret_url: {}", ret_url)
        return _rjson({"url": ret_url, "orig_addr": orig_addr})
    except Exception as x:
        logger.error("failed {}, {}", x, traceback.format_exc())
        return _rjson({"url": ""}, rmsg="--- check failed [%s]" % str(x))


# 20230620 新版编码式建模扩容没有使用该方法
def _patc_pvc(name_space, name, storage):
    """
    扩容pvc，只能增大storage
    """
    logger.info("...entry _patc_pvc...")
    # try:  
    #     storage_original[:-2]
    # except Exception as x:
    #     log.error(x)
    # corev1 = self.corev1
    # r = corev1.patch_namespaced_persistent_volume_claim(pvc_name, self.namespace, persistent_volume_claim)

    v1 = client.CoreV1Api()
    pvc = client.V1PersistentVolumeClaim(api_version="v1", kind="PersistentVolumeClaim",
                                         metadata=client.V1ObjectMeta(name=name, labels={"pvc": name}),
                                         )

    pvc.spec = client.V1PersistentVolumeClaimSpec(storage_class_name=_conf.get("k8s_storage_class", "glusterfs-sc"),
                                                  access_modes=["ReadWriteMany", ],
                                                  volume_mode="Filesystem",
                                                  resources=client.V1ResourceRequirements(
                                                      requests={"storage": f"{storage}Mi"}))
    try:
        resp = v1.patch_namespaced_persistent_volume_claim(namespace=name_space, body=pvc, name=name)
    except ApiException as x:
        db.devc_update_errormsg(name, "patch pvc failed, reason: %s, body: %s" % (x.reason, x.body))
        log.error("patch pvc failed, reason: %s, body: %s" % (x.reason, x.body))
        return False, x
    return True, ''


def jupyter_percent(user, name, ctype, image, cpu, mem, storage, env, token, is_Jupyter_expansion=False):
    log.info("...enter jupyter_percent ...")
    if is_Jupyter_expansion:
        try:
            # 扩容前首先将需要扩容的容器，进度重置为0
            dev_info = {}
            db.devc_update_v119(name, dev_info, "01", 0.0)
        except Exception as x:
            log.error(x)
    start = time.time()
    end = start
    #    jupyter_schedule={"Pending":0.4,"ContainerCreating":0.6,"Running":0.8}
    v1 = client.CoreV1Api()
    cmd_get_dep = f"{_conf['kubectl']} --kubeconfig {_conf['k8s_config_file']} get deployment|grep {name}"
    # 该字段是用于判断资源是否不足
    is_lack_resources = False
    is_first_Pending = True
    is_first_ContainerCreating = True
    is_first_Running = True
    while end - start < 120:
        time.sleep(2.0)
        end = time.time()
        # 查询deployment->解析READY
        # try:
        #     out = subprocess.check_output(cmd_get_dep,shell=True)
        # except Exception as ex:
        #     log.error(ex)
        # str_out = out.decode(encoding="utf-8")
        # if str_out is not None and str_out!= "":
        #     str_out_sp = str_out.split("   ")
        #     log.info(f"str_out_sp: {str_out_sp}")
        #     str_out_sp_sp=str_out_sp[1].split("/")
        #     log.info(f"str_out_sp_sp: {str_out_sp_sp}")
        #     if str_out_sp_sp[0]==str_out_sp_sp[1]:
        #         log.info("schedule: 0.2")
        #         # 此阶段说明，查询deployment->READY,说明启动的pod数量是对的，下一步就是查询pod的启动状态
        #         db.devc_jupyter_percent(name,"0.2")
        #         is_lack_resources=False
        #     else:
        #         continue
        # else:
        #     continue
        r1 = v1.list_namespaced_pod(namespace=k8s_name_space, label_selector=f"app={name}")
        if len(r1.items) < 1:
            logger.info("no pod found!!!")
        else:
            item = r1.items[-1]
            pod_name = item.metadata.name
            cmd_get_pod = f"{_conf['kubectl']} --kubeconfig {_conf['k8s_config_file']} get pod|grep {pod_name}"
            try:
                out = subprocess.check_output(cmd_get_pod, shell=True)
                str_out = out.decode(encoding="utf-8")
            except Exception as ex:
                log.error(f"ex: {ex}")
                str_out = ""
            # str_out = out.decode(encoding="utf-8")
            if str_out is not None and str_out != "":
                log.info(f"str_out: {str_out}")
                str_out_sp = str_out.split(" ")
                log.info(f"str_out_sp: {str_out_sp}")
                if "Pending" in str_out_sp:
                    pod_status = "Pending"
                    is_lack_resources = True
                    log.info(f"jupyter_pod_status: {pod_status}  0.4")
                    # 保障Pending、ContainerCreating状态时，只更新一次数据库
                    if is_first_Pending:
                        db.devc_jupyter_percent(name, 0.4)
                        is_first_Pending = False
                    continue
                elif "ContainerCreating" in str_out_sp:
                    pod_status = "ContainerCreating"
                    log.info(f"jupyter_pod_status: {pod_status}  0.6")
                    if is_first_ContainerCreating:
                        db.devc_jupyter_percent(name, 0.6)
                        is_first_ContainerCreating = False
                    continue
                elif "Running" in str_out_sp:
                    pod_status = "Running"
                    log.info(f"jupyter_pod_status: {pod_status}  0.8")
                    if is_first_Running:
                        db.devc_jupyter_percent(name, 0.8)
                        is_lack_resources = False
                        is_first_Running = False
                    break
                else:
                    log.info(f"{pod_name} start failed")
                    is_lack_resources = True
                    db.devc_jupyter_percent(name, "0.0")
                    break
    if is_lack_resources:
        log.info("lack of resources")
        db.devc_update_status(name, "06", error_msg="lack of resources")


# 检测自动化学习容器是否启动成功，若成功则启动自动化学习实验
def gm_start_process(name, autoParams):
    log.info(f"...enter gm_start_process...{name}")
    start = time.time()
    end = start
    expe_name = autoParams.get("expe_name", "")
    run_id = autoParams.get("run_id", "")
    task_type = autoParams.get("task_type", "")
    dataset = autoParams.get("dataset", "")
    auto_key = autoParams.get("auto_key", {})
    trials = auto_key.get("trials", "")
    epochs = auto_key.get("epochs", "")
    extra_params = autoParams.get("extra_params", None)
    while end - start <= 240:
        node_host, p, pod_name = k8s_contanier_detect(name)
        log.info(f"-----node_host:{node_host},-----port:{p}")
        if node_host is not None and p is not None and pod_name is not None:
            target = str(node_host) + ":" + str(p)
            # time.sleep(80)
            # TaskAuto_start(target,expe_name,run_id,task_type,trials,epochs,dataset,extra_params)
            # break
            cmd = f"curl http://{target}/wf_check"
            log.info("cmd", cmd)
            p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if len(p.stdout.readlines()) > 0:
                TaskAuto_start(target, expe_name, run_id, task_type, trials, epochs, dataset, extra_params, pod_name)
                break
        time.sleep(5)
        end = time.time()


# 删除计算容器,删除容器, 删除包括卷 服务 deployment在内的k8s资源
# 97: 正在销毁, 98: 销毁失败, 99: 已销毁
def TaskCon_remove():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter TaskCon_remove...[{}]", jsons)
        jo = json.loads(jsons)['ROOT']
        user = jo['user']
        name = jo['name']  # 容器名称
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))

    try:
        # 根据容器名称查询容器信息
        container_info = db.devc_get_v119(name)
        if container_info is None:
            return _rjson(None, 5023, f"not found container {name}")
        logger.info(container_info)
        depName = container_info.get("depName", None)
        pvcName = container_info.get("pvcName", None)
        serviceName = container_info.get("serviceName", None)
        if container_info.get("create_type", None) == 'manually':
            return _rjson(None, 7017, f"手工创建容器， 不允许删除")

        if "kubeconfig" not in container_info.keys() or "namespace" not in container_info.keys():
            kubeconfig = container_info.get("kubeconfig", _conf['k8s_config_file'])
            k8s = K8S(kubeconfig)
        else:
            kubeconfig = container_info["kubeconfig"]
            k8s = K8S(kubeconfig, container_info["namespace"])
        # kubeconfig = container_info.get('kubeconfig', _conf['k8s_config_file'])

        db.devc_update_status(name, '97')
        try:
            # k8s = K8S(kubeconfig)
            k8s.delete_all(dep_name=depName, pvc_name=pvcName, svc_name=serviceName)
            # 更新数据库表dev_container，字段CONTAINER_STATUS，值97，即正在销毁
        except Exception as x:
            # 更新数据库表dev_container，字段CONTAINER_STATUS，值98:，即销毁失败
            logger.error("delete container failed, Reason: %s" % (x))
            # 删除失败，更新数据库
            db.devc_update_status(name, '98')
            return _rjson({}, rcode=405, rmsg="delete container failed")
        # 更新数据库表dev_container，字段CONTAINER_STATUS，值99。即已销毁
        db.devc_update_status(name, '99')
        # 根据容器名称获取CONTAINER_INFO
        # container_info=db.devc_get_v119(name)
        # 更新数据库表dev_container，删除container_info中ports和node_host

        if "node_host" in container_info.keys():
            del container_info["node_host"]
        if "ports" in container_info.keys():
            del container_info["ports"]
        db.devc_update_containerinfo_v119(name, container_info)

        # #update c_res_gpu 状态为 02
        # gpu_id = container_info.get('gpu', None)
        # if type(gpu_id) == str:
        #     _ret = _db.JU.update_gpu_res(gpu_id, "02")
        #     logger.warning("update c_res_gpu status to 02 of {} {}, return {}",
        #                    depName, gpu_id, _ret)

        # update c_res_gpu 状态为 03
        # gpu_id = container_info.get('gpu', None)
        # if type(gpu_id) == str:
        #     _ret = _db.JU.update_gpu_res(gpu_id, "03")
        #     logger.warning("update c_res_gpu status to 02 of {} {}, return {}",
        #                    depName, gpu_id, _ret)

        return _rjson({})
    except Exception as x:
        logger.error("container_remove failed {}", traceback.format_exc())
        return _rjson(None, 509, "remove failed")


# 新增计算容器,启动用于模型训练或者任务调度的容器
# 容器类型  01: Jupyter容器, 02: 计算容器, 03: 自动化学习容器
# 容器状态  01: 正在启动, 02: 运行中, 03: 正在关闭, 04: 已关闭, 05: 关闭失败, 06: 启动失败, 07: 第一次启动失败, 97: 正在销毁, 98: 销毁失败, 99: 已销毁
def TaskCon_create():
    extra_args = {}
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter TaskCon_create...[{}]", jsons)
        jo = json.loads(jsons)['ROOT']
        user = jo['user']
        name = jo['name']  # 容器名称
        ctype = jo['ctype']  # 取值: task--计算容器 jupyter--开发容器
        image = jo['image']
        cpu = jo['res']["cpu"]  # 使用CPU的 cores 数量, 可以为小数
        mem = jo['res']["mem"]  # 内存使用限制, 单位 MB
        shmsize = jo['res'].get('shmsize', -1)  # 设置shmsize， 默认不处理， 则由容器平台分配64Mb； 单位 Mb
        storage = jo['res']["storage"]  # 卷空间大小, 单位MB, 挂载位置固定为 /data
        env = jo['env']

        extra_args['gpu'] = jo['res'].get("gpu", None)  # 分配gpu，显卡个数，每张显存大小, 算力百分比，单位(第四范式mb，阿里云mb), 默认None为不分配
        extra_args['npu'] = jo['res'].get("npu", None)  # 分配npu, 卡的个数
        extra_args['gpuType'] = jo['res'].get("gpuType", None)  # 1:阿里云，2:第四范式，3:驱动 有gpu时填写

        # extra_args['gpu_res'] = jo['res'].get("gpu_res", None)  # gpu资源规格
        extra_args['ext'] = jo.get('ext', {})  # 允许提供扩展的参数
        extra_args['scene_type'] = jo.get('scene_type', '02')  # scene_type  01 - 训练资源池；02 - 试用资源池；03 - 推理资源池
        extra_args['calc_type'] = jo.get('calc_type', '01')  # calc_type  01-通用算力；02-GPU算力；03-NPU算力

        extra_args['args'] = jo.get('_args', None)  # 启动命令, 可以通过参数指定, 格式 ["/app/run.sh", ""]
        extra_args['mounts'] = jo.get("mounts",
                                      None)  # 额外挂载卷， 默认只读， 格式： [[volume-name, mount-path, readonly), ...], eg: [["jupyter-123", "/model_path", True],]
    except Exception as x:
        create_log = f'start failed, Invalid request [{str(x)}]'
        db.devc_update_status(name, "07", error_msg=create_log)
        return _rjson(None, 402, "start failed, Invalid request [%s]" % str(x))
    token = _gen_rand_str()
    env["PYPI_SERVER"] = _conf["pypi_server"]
    env["_PYPI_SERVER"] = _conf["pypi_server"]
    env["SHELL"] = "bash"
    env["_USERNAME_"] = user
    env["_WS_ADDR_"] = _conf["_WS_ADDR_"]
    env["_CONT_NAME_"] = name
    env["_CTYPE_"] = ctype
    env["_TOKEN_"] = token
    env["HOME"] = '/data'
    env["BASE_URI"] = f"/{name}"  # "/jupyter"
    env["LOGDIR"] = "/data/metric"
    # if extra_args['gpu_res'] and 'env' in extra_args['gpu_res']:
    #     tmp_items = extra_args['gpu_res']['env']
    #     for kk, vv in tmp_items.items():
    #         env[kk] = vv
    env['FSWEB'] = _conf.get('_fsweb', '')
    env["_FILESERVER_URL"] = _conf.get("fileserver_url", "")
    env["_SVC_ADDR_"] = _conf.get('__svc_addr__', '')
    logger.info("create container {} ENV: [{}]", name, env)

    # 判断是GPU还是NPU还是CPU
    if extra_args['gpu']:
        extra_args['resType'] = 1
    elif extra_args['npu']:
        extra_args['resType'] = 2
    else:
        extra_args['resType'] = -1

    extra_mounts = extra_args.get("mounts", None)
    if extra_mounts and len(extra_mounts) > 2:
        create_log = 'start failed, Only two extra mounts can be mounted'
        db.devc_update_status(name, "07", error_msg=create_log)
        _rjson(None, 402, create_log)

    # 获取k8s配置文件以及namespace名称
    kube_info = get_kube_info(extra_args['calc_type'], extra_args['scene_type'])
    if not kube_info:
        create_log = 'start failed，get kubeconfig failed'
        db.devc_update_status(name, "07", error_msg=create_log)
        return _rjson(None, 403, create_log)

    extra_args['kubeconfig'] = kube_info['kubeconfig']
    extra_args['namespace'] = kube_info['namespace']
    extra_args['storage_class_name'] = kube_info['storage_class_name']

    # 判断申请的资源是否足够
    res_info = {
        'cpu': cpu,
        'mem': mem
    }
    flag, msg = check_namespace_res(extra_args['kubeconfig'], extra_args['namespace'], res_info)
    if not flag:
        create_log = f'start failed，{msg}'
        logger.info(create_log)
        db.devc_update_status(name, "07", error_msg=create_log)
        return _rjson(None, 405, create_log)

    th = threading.Thread(target=create_container_process, args=(user, name, ctype, image, cpu, mem, storage,
                                                                 env, token, extra_args, shmsize))
    th.start()
    # if ctype == "01":
    #     # 更新jupyter启动进度 dev_container表-》STATUP_PERCENT字段
    #     th = threading.Thread(target=jupyter_percent, args=(user, name, ctype, image, cpu, mem, storage, env, token))
    #     th.start()
    return _rjson({})


def res_configuration(res_type, res_info):
    # 资源处理，方便k8s进行资源配置
    res_type = int(res_type)
    if res_type == 1:
        gpuType = int(res_info['gpuType'])
        # gpu
        if gpuType == 1 or gpuType == 2:
            # aliyun或者第四范式
            res_config = {'gpuType': gpuType, 'gpu': res_info['gpu']['gpu'],
                       'gpumem': res_info['gpu']['gpumem'], 'gpuCores': res_info['gpu']['gpuCores']}
            # 第四范式增加卡类型
            if gpuType == 2:
                res_config.update({
                    'cardType': res_info['gpu']['cardType']
                })
        elif gpuType == 3:
            # 驱动
            res_config = {'gpuType': gpuType}
    elif res_info['resType'] == 2:
        # npu
        res_config = {'npu': res_info['npu']['npu'], 'cardType': res_info['npu']['cardType'],
                   'cardSpec': res_info['npu']['cardSpec']}

    else:
        # cpu
        res_config = None

    return res_config


def create_container_process(user, name, ctype, image, cpu, mem, storage,
                             env, token, extra_args, shmsize):
    if image.find('/') < 0:  # 自定镜像需要提供完整的包括仓库地址的路径
        if 'namespace' in extra_args.keys():
            ns = extra_args['namespace']
        else:
            ns = _conf.get("k8s_image_ns", k8s_name_space).strip()
            if ns == '':
                ns = k8s_name_space
        image = f"{k8s_docker_repository}/{ns}/{image}"
    logger.info("using image: {}", image)
    logger.info("extra_args: {}", extra_args)
    try:
        uri = "jupyter-%s" % _gen_rand_str(nc=0)  # Done uri要随机生成
        depName = name.lower()  # deployment名称与容器名称相同
        pvcName = depName
        serviceName = depName
        # 初始化k8s
        if 'namespace' in extra_args.keys():
            k8s = K8S(extra_args['kubeconfig'], extra_args['namespace'])
        else:
            k8s = K8S(extra_args['kubeconfig'])

        # DONE 创建pvc
        if storage > 0:
            # if not k8s_create_pvc(k8s_name_space, pvcName, storage, storage_class=_conf.get("k8s_storage_class", "glusterfs-sc")):
            if 'storage_class_name' in extra_args.keys():
                if extra_args['storage_class_name']:
                    k8s_storage_class = extra_args['storage_class_name']
                else:
                    k8s_storage_class = _conf.get("k8s_storage_class", "glusterfs-sc")
            else:
                k8s_storage_class = _conf.get("k8s_storage_class", "glusterfs-sc")
            if not k8s.create_pvc(pvcName, storage, storage_class_name=k8s_storage_class):
                create_log = f"create pvc {pvcName} failed"
                logger.error(create_log)
                db.devc_update_status(name, "07", error_msg=create_log)  # 创建失败， 更新表状态"07" 创建失败
                return
            logger.info("create pvc done!")
            pvcs = [[pvcName, '/data']]
        else:
            pvcs = []
        #  创建deployment
        if "args" in extra_args and extra_args['args'] is not None:
            args = extra_args['args']
            fsGroup = 8989
            extra_mounts = None
            ports = [8086, 8085, 8181, 2022]
            coding_mode = 'jupyter'
        else:
            opts = _conf.get("ctype_opts", {}).get(ctype, None)
            logger.info("ctype={} opts={}", ctype, opts)
            args = opts.get('cmd', None)

            # 判断启动的工具
            coding_mode = db.get_coding_mode(name)
            logger.info(f"coding_mode is {coding_mode}")
            if not coding_mode:
                coding_mode = 'jupyter'
                logger.info(f"get coding_mode failed, use default type 'jupyter'")
            args = args[coding_mode]

            # 如果获取不到，直接走默认
            if args is None:
                logger.error("create_container_process invalid ctype [%s], use default 01")
                args = _conf['ctype_opts']['01']['cmd'][coding_mode]

            fsGroup = opts.get("user", 8989)  # 是否使用root启动容器
            extra_mounts = opts.get('ext_mounts', None)
            ports = opts.get("ports", [8086, 8085, 8181, 2022])

        ev = extra_args.get("mounts", None)
        if ev is not None:
            if extra_mounts is None:
                extra_mounts = ev
            else:
                extra_mounts.extend(ev)
        logger.info("-- extra_mounts: {}", extra_mounts)

        entry = args[0]
        args = args[1:]

        # 资源处理

        resInfo = res_configuration(extra_args['resType'], extra_args)

        if not k8s.create_deployment_new(depName, image, cpu, mem, entrypoint=entry,
                                         args=args, PVCs=pvcs, ENVs=env, wd="/data",
                                         fsGroup=fsGroup, resType=extra_args['resType'], resInfo=resInfo,
                                         extra_mounts=extra_mounts, shmsize=shmsize, ):
            create_log = f"create deployment {depName} failed, delete all"
            logger.error(create_log)
            k8s.delete_all(pvc_name=pvcName)
            db.devc_update_status(name, "07", error_msg=create_log)
            return
        logger.info("create deployment done!")
        ports = [[f"port-{p}", "TCP", p, None] for p in ports]
        logger.info("service ports {}", str(ports))
        ### 容器类型  01: Jupyter容器, 02: 计算容器, 03: 自动化学习容器, （现在（2022-4-21)只支持一种容器了
        # if ctype == "01":
        if not k8s.create_service(serviceName,
                                  Ports=ports,
                                  selector={"app": depName}):
            create_log = f"create service failed!, delete_all {depName}"
            logger.error(create_log)
            k8s.delete_all(dep_name=depName, pvc_name=pvcName)
            db.devc_update_status(name, "07", error_msg=create_log)
            return

        # 启动进程，探索jupyter容器创建及jupyter进程启动进度
        dev_info = {"depName": depName, "pvcName": pvcName, "serviceName": serviceName, "token": token, "image": image,
                    "uri": uri, "contaniername": name, "ctype": ctype, "cpu": cpu, "mem": mem, "resInfo": resInfo,
                    "storage": storage, "env": env, "shmsize": shmsize, "kubeconfig": extra_args['kubeconfig']}
        if 'namespace' in extra_args.keys():
            dev_info['namespace'] = extra_args['namespace']
        if 'storage_class_name' in extra_args.keys():
            dev_info['storage_class_name'] = extra_args['storage_class_name']

        th = threading.Thread(target=_jupyter_stat, args=(depName, serviceName, name, dev_info, coding_mode))
        th.start()
        logger.info("create service {} done! checking progress...", depName)

        logger.info("container info: {}", dev_info)
        if ctype != "01":
            con_status = "02"
            try:
                logger.info(dev_info)
                db.devc_update_v119(name, dev_info, con_status, 0.0)
            except Exception as x:
                logger.error("update database %s error failed %s" % ("dev_container", x))
        # 更新容器数据进数据库
        # 01: 正在启动, 02: 运行中，06: 启动失败
        # else:
        #     con_status = "01"
        # try:
        #     logger.info(dev_info)
        #     db.devc_update_v119(name, dev_info, con_status, 0.0)
        # except Exception as x:
        #     logger.error("update database %s error failed %s" % ("dev_container", x))
    except Exception as x:
        create_log = f"create container failed {x}, {traceback.format_exc()}"
        logger.error(create_log)
        db.devc_update_status(name, "07", error_msg=create_log)
        return
    logger.info("create jupyter container {} done!", depName)


def Jupyter_expansion():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info(f"...enter Jupyter_expansion...[{jsons}]")
        jo = json.loads(jsons)['ROOT']
        # user = jo['user']
        name = jo['name']  # 容器名称
        # ctype = jo['ctype']  # 取值: task--计算容器 jupyter--开发容器
        # image = jo['image']
        cpu = jo['res']["cpu"]  # 使用CPU的 cores 数量, 可以为小数
        mem = jo['res']["mem"]  # 内存使用限制, 单位 MB
        # storage = jo['res']["storage"]  # 卷资源
        # env = jo['env']
        # token = jo.get("token", None)
        storage = jo['res'].get("storage", None)
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # 扩容cpu、men资源
    # 根据容器名称查询容器信息
    container_info = db.devc_get_v119(name)
    if container_info is None:
        return _rjson(None, 5023, f"not found container {name}")
    logger.info(container_info)

    if "kubeconfig" not in container_info.keys() or "namespace" not in container_info.keys():
        kubeconfig = container_info.get("kubeconfig", _conf['k8s_config_file'])
        k8s = K8S(kubeconfig)
    else:
        kubeconfig = container_info["kubeconfig"]
        k8s = K8S(kubeconfig, container_info["namespace"])
    # kubeconfig = container_info.get('kubeconfig', _conf['k8s_config_file'])
    # k8s = K8S(kubeconfig)
    try:
        # cpu_request_times = _conf.get('cpu_request_times', 50)
        # cpu_request_times = 50  # cpu资源超分倍数
        deployment_by_name = k8s.query_deployment(name)
        ct = deployment_by_name.spec.template.spec.containers[0]
        mem = f"{mem}Mi"
        ct.resources.limits['cpu'] = cpu
        ct.resources.requests['cpu'] = cpu
        # ct.resources.requests['cpu'] = f"{cpu * 1000 / cpu_request_times}m"
        ct.resources.limits['memory'] = mem
        ct.resources.requests['memory'] = mem
        # 执行更新资源
        ret = k8s.patch_namespaced_deployment(name, deployment_by_name)
    except Exception as x:
        logger.error(f"Failed to modify resource [{x}]")
        return _rjson(None, 402, f"Failed to modify resource [{x}]")
    # 扩容存储卷资源
    if storage:
        # 获取存储卷类名称
        if 'storage_class_name' in container_info.keys():
            storage_class_name = container_info['storage_class_name']
        else:
            storage_class_name = _conf.get("k8s_storage_class", "glusterfs-sc")
        # flag, msg = _patc_pvc_new(k8s_name_space, name, storage)
        # flag, msg = _patc_pvc(k8s_name_space, name, storage)
        flag, msg = k8s.patc_pvc(name, storage, storage_class_name)
        if not flag:
            return _rjson(None, 402, f"Failed to modify pvc [{msg}]")
    # token=_gen_rand_str()
    # env["PYPI_SERVER"]=_conf["pypi_server"]
    # env["_USERNAME_"]=user
    # env["_WS_ADDR_"]=_conf["_WS_ADDR_"]
    # env["_CONT_NAME_"]=name
    # env["_CTYPE_"]=ctype
    # env["_TOKEN_"]=token
    # env["BASE_URI"]="/jupyter"
    # env["LOGDIR"]="/data/metric"
    # env["NVIDIA_VISIBLE_DEVICES"]="all"
    # env["_FILESERVER_URL"]=_conf["fileserver_url"]
    # # 删除deployment和svc
    # k8s_delete_all(k8s_name_space, dep_name=name,pvc_name=None,svc_name=name)
    # # 重新创建容器
    # th = threading.Thread(target=_create_fileserver_process, args=(user,name,ctype,image,cpu,mem,env,token,name,"/data",storage))
    # th.start()
    # # 更新jupyter启动进度 dev_container表-》STATUP_PERCENT字段
    # th = threading.Thread(target=jupyter_percent, args=(user,name,ctype,image,cpu,mem,storage,env,token,True))
    # th.start()
    return _rjson({})


# 暂无调用
def _create_fileserver_process(user, name, ctype, image, cpu, mem, env, token, pvcName, mountPath, storage=None):
    log.info("...enter _create_fileserver_process...")
    image = f"{k8s_docker_repository}/{k8s_name_space}/{image}"
    uri = "jupyter-%s" % _gen_rand_str(nc=0)  # Done uri要随机生成
    depName = name.lower()  # deployment名称与容器名称相同
    pvcName = name.lower()
    serviceName = name.lower()
    if storage is not None:
        # 比较扩容的storage是否大于原来storage
        try:
            v1 = client.CoreV1Api()
            r1 = v1.list_namespaced_persistent_volume_claim(namespace=k8s_name_space, label_selector=f"pvc={name}")
        except Exception as x:
            db.devc_update_status(name, "06", error_msg=str(x))
            log.error(x)
            return
        storage_original = ""
        if len(r1.items) >= 1:
            for i in range(len(r1.items)):
                item = r1.items[i]
                storage_original = item.spec.resources.requests.get("storage", "")
                # storage_origina_Mi=float(storage_original[:-2])*1000
                storage_origina_Mi = storage_original
                log.info(f"storage_original:{storage_origina_Mi}")
                log.info(f"storage_new:{storage}Mi")
        # storage_original : 1Gi
        if not _patc_pvc(k8s_name_space, name, storage):
            db.devc_update_status(name, "06")
            return
    # 探索deployment和svc是否已删除,当deployment已删除，再重建容器
    start = time.time()
    end = start
    v1 = client.CoreV1Api()
    while end - start <= 300:
        time.sleep(10)
        r1 = v1.list_namespaced_pod(namespace=k8s_name_space, label_selector=f"app={depName}")
        if len(r1.items) < 1:
            log.info("begin rebuild jupyter container")
            break
        end = time.time()
    # DONE 创建deployment
    args = ""
    if ctype == "02":
        args = [task_start_package,
                task_start_sha1]
    else:
        args = [jupyter_start_package,
                jupyter_start_sha1]
    # args = ["-c", "while true; do echo hello; sleep 1h;done"]
    fsGroup = None if _conf.get("jupyter_use_root", False) else 8989
    if not k8s_create_deployment(k8s_name_space, depName, image, cpu, mem, entrypoint="/usr/run.sh",
                                 args=args, PVCs=[[pvcName, mountPath]], ENVs=env, wd="/data", fsGroup=fsGroup,
                                 cpu_request_times=_conf.get('cpu_request_times', 50)):
        db.devc_update_status(name, "06")
        #        k8s_delete_all(k8s_name_space, pvc_name=pvcName)
        log.error("create deployment failed")
        return
    if ctype == "01":
        if not k8s_create_service(k8s_name_space, serviceName,
                                  Ports=[["proc", "TCP", 8181, None], ["ssh", "TCP", 2022, None],
                                         ["jupyter", "TCP", 8085, None]],
                                  selector={"app": depName}):
            db.devc_update_status(name, "06")
            # k8s_delete_all(k8s_name_space, dep_name=depName,pvc_name=pvcName)
            log.error("create svc failed")
            return
        else:
            dev_info = {"depName": depName, "pvcName": pvcName, "serviceName": serviceName, "token": token,
                        "image": image, "uri": uri, "contaniername": name, "ctype": ctype, "cpu": cpu, "mem": mem,
                        "env": env}
            th = threading.Thread(target=_jupyter_stat, args=(depName, serviceName, name, dev_info))
            th.start()
    else:
        if not k8s_create_service(k8s_name_space, serviceName,
                                  Ports=[["proc", "TCP", 8181, None], ["ssh", "TCP", 2022, None]],
                                  selector={"app": depName}):
            db.devc_update_status(name, "06")
            # k8s_delete_all(k8s_name_space, dep_name=depName, pvc_name=pvcName)
            log.error("create svc failed")

    if ctype != "01":
        # 更新容器数据进数据库
        # 01: 正在启动, 02: 运行中，06: 启动失败
        try:
            dev_info = {"depName": depName, "pvcName": pvcName, "serviceName": serviceName, "token": token,
                        "image": image, "uri": uri, "contaniername": name, "ctype": ctype, "cpu": cpu, "mem": mem,
                        "env": env}
            log.info(dev_info)
            db.devc_update_v119(name, dev_info, "02", 0.0)
        except Exception as x:
            log.error("update database %s error failed %s" % ("dev_container", x))


def _jupyter_stat(dep_name, svc_name, name, dev_info, coding_mode='jupyter', is_restart=False):
    logger.info("...enter _jupyter_stat...")
    start = time.time()
    end = start
    jupyter_success = False
    if 'namespace' in dev_info.keys():
        k8s = K8S(dev_info['kubeconfig'], dev_info['namespace'])
    else:
        k8s = K8S(dev_info['kubeconfig'])

    # 定时探测
    while end - start <= 360:
        if is_restart:
            sleep_t = 2
        else:
            sleep_t = 10
        time.sleep(sleep_t)
        end = time.time()
        pod_name, node_host, ready, total, ports = k8s.query_pod(dep_name)
        if pod_name is None:
            logger.info("容器创建挂起中 {}...", dep_name)
            continue
        if node_host is None:
            logger.info("容器节点主机分配中 {}...", dep_name)
            continue
        if ready < total:
            logger.info("容器启动中 {}...", dep_name)
            continue
        p = None  # jupyter服务端口
        p_8086 = None  # vscode服务端口
        if ports is not None:
            for port in ports:  # [inner_port, 'TCP', out_port]
                if port[0] == 8085:
                    p = port[2]
                if port[0] == 8086:
                    p_8086 = port[2]
        # jupyter_url = f"http://{node_host}:{p}/jupyter/lab"
        import socket
        try:
            if coding_mode == 'jupyter':
                # 测试jupyter服务
                c = socket.create_connection((node_host, p), 1.0)
                c.close()
                logger.info("container {} check success(port ready {}:{})", dep_name, node_host, p)
            else:
                # 测试vscode服务
                c = socket.create_connection((node_host, p_8086), 1.0)
                c.close()
                logger.info("container {} check success(port ready {}:{})", dep_name, node_host, p_8086)
            # log.info(dev_info)
            jupyter_success = True
            # 写表，保存container_info以及状态信息
            dev_info["node_host"] = node_host
            dev_info["ports"] = ports
            dev_info["pod_name"] = pod_name
            dev_info["total"] = total
            if not is_restart:
                ret = db.devc_update_v119(name, dev_info, "02", 1.0)
                logger.info("schedule:1.0 {} container_status=02 return {}", name, ret)
            else:
                db.devc_update_v119(name, dev_info, None, None)
                # 更新数据库表dev_container，字段CONTAINER_STATUS，值02。即运行中
                db.devc_update_status(name, '02', op_id=dev_info['user'])
            # 更新容器数据进数据库
            # 01: 正在启动, 02: 运行中，06: 启动失败
            break
        except Exception as x:
            logger.error(x)
            continue
    if not jupyter_success:
        if not is_restart:
            try:
                # 超时,  需要将资源删除
                ret = db.devc_update_v119(name, dev_info, "07", 0.0)
                create_log = f"Creating container {dep_name} timed out"
                db.devc_update_status(name, "07", error_msg=create_log)
                logger.error("jupyter container {} create failed, up devc return {}", dep_name, ret)
                k8s.delete_all(dep_name=dep_name, svc_name=dep_name, pvc_name=dep_name)
                logger.info("deleted timeout container {}", dep_name)
            except Exception as x:
                logger.error("container {} del failed {} {}", dep_name, x, traceback.format_exc())
        elif is_restart:
            create_log = f"restart container {dep_name} timed out"
            db.devc_update_status(name, "06", error_msg=create_log)


# 停止计算容器,停止容器, 删除包括 服务/ deployment, 但保留pvc
# 03: 正在关闭, 04: 已关闭, 05: 关闭失败,
# 新版没有用该方法
def TaskCon_stop_old():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter TaskCon_stop_old...[%s]" % jsons)
        jo = json.loads(jsons)['ROOT']
        user = jo['user']
        name = jo['name']  # 容器名称
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # 根据容器名称查询容器信息
    container_info = db.devc_get_v119(name)
    log.info(container_info)
    if container_info is None:
        return _rjson(None, 5023, f"not found container {name}")
    depName = container_info.get("depName", None)
    pvcName = container_info.get("pvcName", None)
    serviceName = container_info.get("serviceName", None)
    if container_info.get("create_type", None) == 'manually':
        return _rjson(None, 7017, f"手工创建容器， 不允许停止")
    try:
        db.devc_update_status(name, '03')
        k8s_delete_all(k8s_name_space, dep_name=depName, svc_name=serviceName)
        # 更新数据库表dev_container，字段CONTAINER_STATUS，值97，即正在销毁          
    except Exception as x:
        # 更新数据库表dev_container，字段CONTAINER_STATUS，值98:，即销毁失败
        log.error("delete container failed, Reason: %s" % (x))
        # 删除失败，更新数据库
        db.devc_update_status(name, '05')
        return _rjson({}, rcode=405, rmsg="delete container failed")
    # 更新数据库表dev_container，字段CONTAINER_STATUS，值99。即已销毁
    db.devc_update_status(name, '04')
    # 根据容器名称获取CONTAINER_INFO
    container_info = db.devc_get_v119(name)
    # 更新数据库表dev_container，删除container_info中ports和node_host
    if "node_host" in container_info.keys():
        del container_info["node_host"]
    if "ports" in container_info.keys():
        del container_info["ports"]
    db.devc_update_containerinfo_v119(name, container_info)
    return _rjson({})


# 停止计算容器,停止容器, 将deployment副本数修改为0
# 03: 正在关闭, 04: 已关闭, 05: 关闭失败,
def TaskCon_stop():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter TaskCon_stop...[%s]" % jsons)
        jo = json.loads(jsons)['ROOT']
        user = jo['user']
        name = jo['name']  # 容器名称
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # 根据容器名称查询容器信息
    container_info = db.devc_get_v119(name)
    logger.info(container_info)
    if container_info is None:
        return _rjson(None, 5023, f"not found container {name}")
    if container_info.get("create_type", None) == 'manually':
        return _rjson(None, 7017, f"手工创建容器， 不允许停止")

    try:
        # 修改状态为正在关闭
        # db.devc_update_status(name, '03', user)
        # 修改deploy副本数为0
        if "kubeconfig" not in container_info.keys() or "namespace" not in container_info.keys():
            k8s = K8S(container_info.get("kubeconfig", _conf['k8s_config_file']))
        else:
            k8s = K8S(container_info["kubeconfig"], container_info["namespace"])

        # kubeconfig = container_info.get('kubeconfig', _conf['k8s_config_file'])
        # k8s = K8S(kubeconfig)
        k8s.set_scale(name, 0)
    except Exception as x:
        # 更新数据库表dev_container，字段CONTAINER_STATUS，值05:，即关闭失败
        logger.error("stop container failed, Reason: %s" % (x))
        # 删除失败，更新数据库
        db.devc_update_status(name, '05', op_id=user)
        return _rjson({}, rcode=405, rmsg="stop container failed")
    # 更新数据库表dev_container，字段CONTAINER_STATUS，值04。即已关闭
    db.devc_update_status(name, '04', op_id=user)
    return _rjson({})


def GetDatasetsToJupyter():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter GetDatasetsToJupyter...[{}]", jsons)
        jo = json.loads(jsons)['ROOT']
        name = jo['name']  # 容器名称
        src_datasets = jo.get('src_datasets', None)  # 文件服务器内数据集路径
        dest_datasets = jo.get('dest_datasets', "/data")  # jupyter容器内下载数据集路径
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    container_info = db.devc_get_v119(name)
    if container_info is None:
        logger.error("devc_get_v119 return None")
        return _rjson(None, 407, "Container data missed, can not continue.")
    logger.info("devc_get_v119 return {}", container_info)
    node_host = container_info.get("node_host", None)
    ports = container_info.get("ports", None)
    if node_host is None or ports is None:
        logger.error("node_host of {} not found, can not continue.", name)
        return _rjson(None, 431, "node_host not found, can not continue.")
    port_2022 = [p_o for p_i, _, p_o in ports if p_i == 2022]
    if len(port_2022) == 0:
        logger.error("can not find outer port of 2022, {}", name)
        return _rjson(None, 439, "can not find outer port of 2022")
    port_2022 = port_2022[0]
    fname = os.path.basename(src_datasets)
    cmd = shlex.split(
        f"/data/.app/bin/fkme scp -i /data/.app/id_rsa_tr -p {port_2022} \"{src_datasets}\"  _base_@{node_host}:{dest_datasets}/{fname}")
    try:
        out = subprocess.check_output(cmd, shell=False)
    except Exception as x:
        logger.error("file transfer to jupyter failed! {} {}", x, traceback.format_exc())
        return _rjson(None, 435, "transfer failed %s" % x)
    return _rjson({"status": out.decode("utf8")})

    # pod_name = container_info.get("pod_name",None)
    # if pod_name is None:
    #     logger.warning("can not get pod_name from database, query by k8s")
    #     try:
    #         pod_name, node_host, ready, total, ports = k8s_query_pod(k8s_name_space, name, name)
    #     except Exception as x:
    #         logger.error("Pod name not found, can not continue.")
    #         return _rjson(None, 409, "Pod name not found, can not continue.")
    #
    # src_podname = _conf['fileserve_pod']
    # log.info(f"src_podname={src_podname}")
    # log.info(f"dst_podname={pod_name}")
    # # src_datasets_base = os.path.dirname(src_datasets)
    # src_dir = os.path.dirname(src_datasets)
    # src_name = os.path.basename(src_datasets)
    # src_cmd = f"{_conf['kubectl']} --kubeconfig {_conf['k8s_config_file']} exec {src_podname} -- tar cmf - -C {src_dir} {src_name}"
    # dst_cmd = f"{_conf['kubectl']} --kubeconfig {_conf['k8s_config_file']} exec {pod_name} -i -- tar xmf - -C {dest_datasets} --no-same-owner"
    # logger.info("kubectl src_cmd: {}", src_cmd)
    # logger.info("kubectl dst_cmd: {}", dst_cmd)
    # th = threading.Thread(target=_getDatasetsToJupyter, args=(src_cmd,dst_cmd,src_datasets,dest_datasets))
    # th.start()
    # return _rjson({})


def _getDatasetsToJupyter(src_cmd, dst_cmd, src_datasets, dest_datasets):
    proc = subprocess.Popen(f"{src_cmd} | {dst_cmd}", shell=True)
    try:
        rcode = proc.wait()
        if rcode == 0:
            logger.info(f"Upload success {src_datasets} to {dest_datasets}")
        else:
            logger.error(f"Upload {src_datasets} to {dest_datasets} failed, return_code: {rcode}")
        # print(out.decode("utf8"))
    except Exception as x:
        logger.error("Upload Failed [%s]" % str(x))


# 重启计算容器,重启停止的容器, 创建 服务/ deployment, 把原来的pvc挂上
# 容器状态  01: 正在启动, 02: 运行中, 03: 正在关闭, 04: 已关闭, 05: 关闭失败, 06: 启动失败, 97: 正在销毁, 98: 销毁失败, 99: 已销毁
# 新版调用该方法的url不用了
def TaskCon_restart_old():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter TaskCon_restart_old...[%s]" % jsons)
        jo = json.loads(jsons)['ROOT']
        user = jo['user']
        name = jo['name']  # 容器名称
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # 删除deployment、svc
    container_info = db.devc_get_v119(name)
    log.info(container_info)
    depName = container_info.get("depName", None)
    pvcName = container_info.get("pvcName", None)
    serviceName = container_info.get("serviceName", None)
    image = container_info.get("image", None)
    cpu = container_info.get("cpu", None)
    mem = container_info.get("mem", None)
    gpu = container_info.get("gpu", None)
    env = container_info.get("env", None)
    ctype = container_info.get("ctype", None)
    token = container_info.get("token", None)
    storage = container_info.get("storage", None)
    shmsize = container_info.get("shmsize", -1)
    contanier_status = db.devc_get_status(name)
    log.info(contanier_status)
    if container_info.get("create_type", None) == 'manually':
        return _rjson(None, 7017, f"手工创建容器， 不允许进行此操作")
    if contanier_status == "04" or contanier_status == "06":
        if ctype == "02":
            # DONE 创建deployment
            args = [task_start_package,
                    task_start_sha1]
        else:
            args = [jupyter_start_package,
                    jupyter_start_sha1]
        # args = ["-c", "while true; do echo hello; sleep 1h;done"]
        fsGroup = None if _conf.get("jupyter_use_root", False) else 8989  # 是否使用root启动容器
        if not k8s_create_deployment(k8s_name_space, depName, image, cpu, mem, entrypoint="/usr/run.sh",
                                     args=args, PVCs=[[pvcName, "/data"]], ENVs=env, wd="/data", fsGroup=fsGroup,
                                     gpu=gpu, shmsize=shmsize,
                                     cpu_request_times=_conf.get('cpu_request_times', 50)):
            # k8s_delete_all(k8s_name_space, pvc_name=pvcName)
            db.devc_update_status(name, "06")
            return _rjson(None, 5012, "create deployment failed")
        # 若创建jupyter容器，则需要另外暴漏一个端口7171
        # 容器类型  01: Jupyter容器, 02: 计算容器, 03: 自动化学习容器
        if ctype == "01" or ctype == "03":
            if not k8s_create_service(k8s_name_space, serviceName,
                                      Ports=[["proc", "TCP", 8181, None], ["ssh", "TCP", 2022, None],
                                             ["jupyter", "TCP", 8085, None]],
                                      selector={"app": depName}):
                db.devc_update_status(name, "06")
                k8s_delete_all(k8s_name_space, dep_name=depName, pvc_name=pvcName)
                return _rjson(None, 5017, "create jupyter svc failed")
        elif ctype == "02":
            if not k8s_create_service(k8s_name_space, serviceName,
                                      Ports=[["proc", "TCP", 8181, None], ["ssh", "TCP", 2022, None]],
                                      selector={"app": depName}):
                db.devc_update_status(name, "06")
                k8s_delete_all(k8s_name_space, dep_name=depName, pvc_name=pvcName)
                return _rjson(None, 5017, "create svc failed")
        try:
            dev_info = {"depName": depName, "pvcName": pvcName, "serviceName": serviceName, "token": token,
                        "image": image, "contaniername": name, "ctype": ctype, "cpu": cpu, "mem": mem,
                        "storage": storage, "env": env}
            log.info(dev_info)
            db.devc_update_v119(name, dev_info, "02", 1.0)
        except Exception as x:
            logger.error("update database %s error failed %s" % ("dev_container", x))
            return _rjson(None, 5018, "update database %s error failed %s" % ("dev_container", x))
    return _rjson({})


# 重启计算容器, 将deployment副本数修改为1
def TaskCon_restart():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter TaskCon_restart...[%s]" % jsons)
        jo = json.loads(jsons)['ROOT']
        user = jo['user']
        name = jo['name']  # 容器名称
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # 根据容器名称查询容器信息
    container_info = db.devc_get_v119(name)
    logger.info(container_info)
    if container_info is None:
        return _rjson(None, 5023, f"not found container {name}")
    if container_info.get("create_type", None) == 'manually':
        return _rjson(None, 7017, f"手工创建容器， 不允许停止")

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
        db.devc_update_status(name, '06', op_id=user)
        return _rjson({}, rcode=405, rmsg="restart container failed")

    # 启动进程，探索jupyter容器创建及jupyter进程启动进度
    # dev_info = {"depName": name, "pvcName": name, "serviceName": name, "kubeconfig": kubeconfig, "user": user}
    container_info['user'] = user
    is_restart = True
    # 获取启动模式
    coding_mode = db.get_coding_mode(name)
    logger.info(f"coding_mode is {coding_mode}")
    if not coding_mode:
        coding_mode = 'jupyter'
        logger.info(f"get coding_mode failed, use default type 'jupyter'")
    th = threading.Thread(target=_jupyter_stat, args=(name, name, name, container_info, coding_mode, is_restart))
    th.start()
    logger.info("create service {} done! checking progress...", name)

    logger.info("container info: {}", container_info)
    # 更新数据库表dev_container，字段CONTAINER_STATUS，值02。即运行中
    # db.devc_update_status(name, '02', user)
    return _rjson({})

    # # 样例文件查询服务
    # def Examples_file():
    #     try:
    #         jsons = request.stream.read().decode('utf-8')
    #         log.info("...enter Examples_file...[%s]" % jsons)
    #         jo = json.loads(jsons)['ROOT']
    #         path = jo.get('path',"")
    #     except Exception as x:
    #         return _rjson(None, 402, "Invalid request [%s]" % str(x))
    #     if path.startswith("/"):
    #         path = path[1:]
    #     path = os.path.join("/pyserv/jupyter_examples_file",path)
    #     if not os.path.exists(path):
    #         return _rjson(None, 403, f"path {path} not exists")
    #     dirs = []
    #     files = []
    #     for f in os.listdir(path):
    #         fpath = os.path.join(path, f)
    #         st = os.stat(fpath)
    #         ltime = time.localtime(int(st.st_mtime)+28800) # linux环境需要加上8小时
    #         time_str = time.strftime("%Y-%m-%d %H:%M:%S",ltime)
    #         if os.path.isdir(fpath):
    #             dirs.append({"dirname":f,"last_update_time":time_str,"size":str(round(int(st.st_size)/1024,2))+"kB"})
    #         else:
    #             files.append({"fileName":f, "last_update_time":time.strftime("%a %d %B %Y %H:%M:%S", time.localtime(st.st_mtime)), "size":str(round(int(st.st_size)/1024,2))+"kB"})
    #     return _rjson({"basepath": path, "dirs":dirs, "files":files})


# 样例文件查询服务，返回目录下的所有文件，并嵌套返回
def Examples_file():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter Examples_file...[%s]" % jsons)
        jo = json.loads(jsons)['ROOT']
        pattern = jo.get('pattern', "")
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    root = _conf["jupyter_examples_file"]
    id1 = _gen_rand_str()
    filelist = {'filePath': root, "dirPath": "jupyter_examples_file/", "type": "root", "id": id1, 'child_dirs': []}
    list_dir_pattern(root, filelist, pattern)
    return _rjson({"filelist": filelist})


# 样例文件上传至开发容器
def Upload_examples_file():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter Upload_examples_file...[%s]" % jsons)
        jo = json.loads(jsons)['ROOT']
        target = jo.get('target', "172.18.238.17:31623")
        local_path = jo.get('local_path', "/pyserv/jupyter_examples_file")
        remote_path = jo.get('remote_path', "/data")
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    if remote_path.startswith("/"):
        remote_path = remote_path[1:]
    remote_path = os.path.join("/data", remote_path)
    #  targetf=os.path.join("http://",target)
    try:
        upload_path(f"{target}", local_path, remote_path)
    except Exception as x:
        raise Exception(f"upload_path {local_path} to {remote_path} failed")
        return _rjson(None, 405, f"upload_path {local_path} to {remote_path} failed")
    return _rjson({})


# 查询指定目录下的所有文件，并以嵌套格式返回
def list_dir_pattern(basepath, filelist, pattern):
    for i in os.listdir(basepath):
        temp_dir = os.path.join(basepath, i)
        if os.path.isdir(temp_dir):
            id1 = _gen_rand_str()
            dirName = os.path.split(temp_dir)[-1]
            dirPath = dirName + "/"
            temp = {'filePath': temp_dir, "id": id1, 'dirPath': dirPath, "label": dirName, "type": "dir",
                    'child_dirs': []}
            filelist['child_dirs'].append(list_dir(temp_dir, temp, pattern))
        else:
            if i.find(pattern) >= 0:
                id1 = _gen_rand_str()
                statinfo = os.stat(temp_dir)
                filePath = os.path.dirname(temp_dir)
                dirName = os.path.split(filePath)[-1]
                dirPath = dirName + "/"
                tempfile = {'filePath': filePath, "id": id1, "label": i, 'dirPath': dirPath, "type": "file",
                            "fileName": i, "fileSizeMb": '%.3fKB' % (statinfo.st_size / 1024),
                            "updateTime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(statinfo.st_mtime + 28800))}
                filelist['child_dirs'].append(tempfile)
    return filelist


# 图形化建模启动
def gm_start():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter gm_start...[%s]" % jsons)
        jo = json.loads(jsons)['ROOT']
        user = jo['taskInfo']['user']
        wfId = jo['taskInfo']['wfId']
        runId = jo['taskInfo']['runId']
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    for node in jo["nodes"]:
        for n_inparams in node["inparams"]:
            if "from" in n_inparams.keys():
                if "value" in n_inparams.keys():
                    del n_inparams["value"]
    with open(f"/pyserv/gm/gui-model-flow-{user}-{wfId}-{runId}.json", "w") as fo:
        fo.write(json.dumps(jo))
        log.info(f"write to /pyserv/gm/gui-model-flow-{user}-{wfId}-{runId}.json")
    return _rjson({})


def move_file(orgin_path, moved_path):
    dir_files = os.listdir(orgin_path)  # 得到该文件夹下所有的文件
    for file in dir_files:
        file_orgin_path = os.path.join(orgin_path, file)  # 路径拼接成绝对路径
        file_moved_path = os.path.join(moved_path, file)
        if os.path.exists(file_moved_path):
            if os.path.isfile(file_moved_path):
                os.remove(file_moved_path)
            else:
                shutil.rmtree(file_moved_path)
        shutil.move(file_orgin_path, moved_path)


def un_tgz(filename, fname):  # filename是文件的绝对路径
    path1 = os.path.splitext(filename)
    if path1[1] == ".gz" or path1[1] == ".tgz":
        tar = tarfile.open(filename)
        des_dir = os.path.abspath(os.path.join(path1[0], ".."))
        tar.extractall(des_dir)
        tar.close()
        if path1[1] == ".gz":
            src_dir = os.path.splitext(path1[0])[0]
        else:
            src_dir = path1[0]
        try:
            # 将压缩的文件由解压缩文件中移动至code/下
            move_file(src_dir, des_dir)
        except Exception as ex:
            log.info({ex})
        # 删除解压的文件夹
        shutil.rmtree(src_dir)
    if path1[1] == ".zip":
        cwdpath = os.path.abspath(os.path.join(path1[0], ".."))
        sout = subprocess.Popen(f"unzip {fname} ", cwd=cwdpath, shell=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        stdout = sout.stdout.read().decode("utf-8", "ignore")
        stderr = sout.stderr.read().decode("utf-8", "ignore")
        log.info(f"{stdout}\n{stderr}")


# 上传文件
def upload():
    base_path = _conf["base_path"]
    #    file_dir = __path( request.args.get("path") )
    ModelId = request.form["ModelPkId"]
    NodeId = request.form["NodeId"]
    FileType = request.form["FileType"]
    DestPath = request.form["DestPath"]
    log.info(f"ModelPkId---{ModelId}")
    log.info(f"NodeId---{NodeId}")
    log.info(f"FileType---{FileType}")
    log.info(f"DestPath---{DestPath}")
    fpath = os.path.join(base_path, ModelId, NodeId, FileType)
    if DestPath != "":
        fpath = os.path.join(fpath, DestPath)
    f = request.files['myfile']  # 从表单的file字段获取文件，myfile为该表单的name值
    log.info(f"f---{f}")
    fname = f.filename
    log.info(f"fname---{fname}")
    if not os.path.exists(fpath):
        os.makedirs(fpath, exist_ok=True)  # 文件夹不存在就创建
    try:
        f.save(os.path.join(fpath, fname))
    except Exception as ex:
        log.info(f"{ex}")
    path1 = os.path.splitext(os.path.join(fpath, fname))
    log.info(f"path1---{path1}")
    # 判断是否为压缩文件，并解压
    if path1[1] == ".gz" or path1[1] == ".tgz" or path1[1] == ".zip":
        try:
            un_tgz(os.path.join(fpath, fname), fname)
            os.remove(os.path.join(fpath, fname))
        except Exception as ex:
            log.info(f"upload fail,reason:{ex}")
            return _rjson(None, 518, f"upload fail,reason:{ex}")
    basepath = os.path.join(base_path, ModelId, NodeId)
    #    filelist = {'dirname':basepath,'child_dirs':[],'files':[]}
    id1 = _gen_rand_str()
    filelist = {'root': basepath, "dirPath": f"{NodeId}/", "dirName": f"{NodeId}", "type": "root", "id": id1,
                "fileUseType": "03", 'child_dirs': []}
    code_dir = os.path.join(basepath, "code")
    data_dir = os.path.join(basepath, "data")
    if os.path.exists(code_dir):
        id_code = _gen_rand_str()
        filelist_code = {'filePath': code_dir, "dirPath": "code/", "dirName": "code", "id": id_code, "type": "root",
                         "fileUseType": "01", 'child_dirs': []}
        list_dir(code_dir, filelist_code, "01")
        filelist.get("child_dirs").append(filelist_code)
    if os.path.exists(data_dir):
        id_data = _gen_rand_str()
        filelist_data = {'filePath': data_dir, "dirPath": "data/", "dirName": "data", "id": id_data, "type": "root",
                         "fileUseType": "02", 'child_dirs': []}
        list_dir(data_dir, filelist_data, "02")
        filelist.get("child_dirs").append(filelist_data)
    # 模型管理->提供方式：提供模型文件服务
    if FileType == "" and NodeId == "" and DestPath == "":
        modelpath = os.path.join(base_path, ModelId)
        id_code = _gen_rand_str()
        filelist_code = {'filePath': modelpath, "dirPath": "code/", "dirName": "code", "id": id_code,
                         "type": "root", "fileUseType": "01", 'child_dirs': []}
        list_dir(modelpath, filelist_code, "01",
                 model_service_file_upload=True)  # 模型服务文件上传时，使用model_service_file_upload=True
        filelist.get("child_dirs").append(filelist_code)
    return _rjson({"filelist": filelist})

    # 返回目录结构,新增model_service_file_upload入参，实现模型服务文件文件上传时，只返回指定目录下的文件及文件夹


def list_dir(basepath, filelist, fileUseType, model_service_file_upload=False):
    for i in os.listdir(basepath):

        temp_dir = os.path.join(basepath, i)
        if os.path.isdir(temp_dir):
            id1 = _gen_rand_str()
            dirName = os.path.split(temp_dir)[-1]
            dirPath = dirName + "/"
            temp = {'filePath': temp_dir, "id": id1, 'dirPath': dirPath, 'dirName': dirName, "type": "dir",
                    "fileUseType": fileUseType, 'child_dirs': []}
            if model_service_file_upload:
                filelist['child_dirs'].append(temp_dir)
            else:
                filelist['child_dirs'].append(list_dir(temp_dir, temp, fileUseType))
        else:
            id1 = _gen_rand_str()
            statinfo = os.stat(temp_dir)
            filePath = os.path.dirname(temp_dir)
            dirName = os.path.split(filePath)[-1]
            dirPath = dirName + "/"
            tempfile = {'filePath': filePath, "id": id1, "label": i, 'dirPath': dirPath, 'dirName': "", "type": "file",
                        "fileUseType": fileUseType, "fileName": i, "fileSizeMb": '%.3fKB' % (statinfo.st_size / 1024),
                        "updateTime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(statinfo.st_mtime + 28800))}
            filelist['child_dirs'].append(tempfile)
    return filelist


# jupyter内文件上传至文件服务器
def jupyterfile2fileserver():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter upload_jupyter_file...[%s]" % jsons)
        jo = json.loads(jsons)['ROOT']
        src_containerName = jo['src_containerName']  # 源容器名称
        src1 = jo.get('src', [])  # 源文件路径
        dst = jo.get('dst', "")  # 目标路径
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    th = threading.Thread(target=_jupyterfile2fileserver, daemon=True, args=(src_containerName, src1, dst))
    th.start()
    return _rjson({})


def _jupyterfile2fileserver(src_containerName, src1, dst):
    # 根据容器名称获取pod名称
    container_info = db.devc_get_v119(src_containerName)
    if container_info is None:
        logger.error(f"not found contanier {src_containerName}")
        # return _rjson(None, 5018, f"not found contanier {src_containerName}")
    src_podname = container_info.get("pod_name", None)
    if src_podname is None:
        logger.error(f"not found contanier {src_containerName}")
        # return _rjson(None, 5019, f"not found contanier {src_containerName}")
    if _base_dir == "/pyserv":
        dst_podname = _conf['fileserve_pod']
        logger.info(f"dst_podname {dst_podname}")
        if not os.path.isdir(dst):
            dst = os.path.dirname(dst)
        try:
            tt = subprocess.check_output(
                f"{_conf['kubectl']} --kubeconfig {_conf['k8s_config_file']} exec {dst_podname} -i -- mkdir -p {dst}",
                shell=True)
            # print(out.decode("utf8"))
            logger.info(f"Mkdir {dst} Success")
        except Exception as x:
            logger.info(f"Mkdir {dst} fail,Reason [%s]" % str(x))

        if isinstance(src1, str):
            src1 = [src1]

        for src in src1:
            src_dir = os.path.dirname(src)
            src_name = os.path.basename(src)
            container_kf = container_info['kubeconfig']
            src_cmd = f"{_conf['kubectl']} --kubeconfig {container_kf} exec {src_podname} -- tar cmf - -C {src_dir} {src_name}"
            # dst_cmd = f"{_conf['kubectl']} --kubeconfig {_conf['k8s_config_file']} exec {dst_podname} -i -- tar xmf - -C {dst} --no-same-owner"
            dst_cmd = f"{_conf['kubectl']} --kubeconfig {_conf['k8s_config_file']} exec {dst_podname} -i -- tar xmf - -C {dst} --no-same-owner"

            try:
                logger.info(f"----pod-copy {src_cmd} {dst_cmd}")
                out = subprocess.check_output(f"{src_cmd} | {dst_cmd}", shell=True)
                logger.info(f"Upload Success {src} to {dst}")
                # print(out.decode("utf8"))
            except Exception as x:
                logger.error("Upload Failed [%s]" % str(x))
    else:
        node_host = container_info.get("node_host", None)
        ports = container_info.get("ports", None)
        if node_host is None or ports is None:
            logger.error("node_host of {} not found, can not continue.", src_podname)
            # return _rjson(None, 431, "node_host not found, can not continue.")
        port_2022 = [p_o for p_i, _, p_o in ports if p_i == 2022]
        if len(port_2022) == 0:
            logger.error("can not find outer port of 2022, {}", src_podname)
            # return _rjson(None, 439, "can not find outer port of 2022")
        port_2022 = port_2022[0]
        # cmd = shlex.split(
        #     f"/data/.app/bin/fkme scp -i /data/.app/id_rsa_tr -p {port_2022} _base_@{node_host}:{src1} \"{dst}\"")
        #
        # logger.info(f'cmd is {cmd}')
        # try:
        #     out = subprocess.check_output(cmd, shell=False)
        # except Exception as x:
        #     logger.error("file transfer from jupyter failed! {} {}", x, traceback.format_exc())
        if dst[-1] != "/":
            # 带文件名称的
            dst = os.path.dirname(dst)
        if isinstance(src1, str):
            # 源目录是字符串
            src1 = [src1]

        for src in src1:
            dst_path = os.path.join(dst, os.path.basename(src))
            logger.debug(f"fst_path is {dst_path}")
            cmd = shlex.split(
                f"/data/.app/bin/fkme scp -i /data/.app/id_rsa_tr -p {port_2022} _base_@{node_host}:{src} \"{dst_path}\"")

            logger.info(f'cmd is {cmd}')
            try:
                out = subprocess.check_output(cmd, shell=False)
            except Exception as x:
                logger.error("file transfer from jupyter failed! {} {}", x, traceback.format_exc())
                # return _rjson(None, 435, "transfer failed %s" % x)
        # return _rjson({"status": out.decode("utf8")})


def image_copy():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter image_copy...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        Dir = jo.get('Dir', None)
        OriginalImageName = jo['OriginalImageName']
        OriginalImageTag = jo['OriginalImageTag']
        NewImageName = jo['NewImageName']
        NewImageTag = jo['NewImageTag']
        ImageId = jo['ImageId']
    except Exception as x:
        # Done 更新镜像发布状态,镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(ImageId, "05")
        return _rjson(None, 402, "Invalid request [%s]" % str(x))

    th = threading.Thread(target=_pull_push_image, daemon=True, args=(
        ImageId, OriginalImageName, OriginalImageTag, NewImageName, NewImageTag, k8s_name_space))
    th.start()
    return _rjson({"return": "copying images"})


def _pull_push_image(ImageId, OriginalImageName, OriginalImageTag, NewImageName, NewImageTag, k8s_name_space):
    cmd_pull = f"docker pull {k8s_docker_repository}/{k8s_name_space}/{OriginalImageName}:{OriginalImageTag}"
    log.info("..........docker pull.......")
    log.info(cmd_pull)
    proc_pull = subprocess.Popen(cmd_pull, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    try:
        rcode = proc_pull.wait()
        log.info("cmd_pull = ", cmd_pull)
    except Exception as x:
        errinfo = f"{str(x)}"
        log.error(errinfo)
    log.info("..........docker tag.......")
    cmd_tag = f"docker tag {k8s_docker_repository}/{k8s_name_space}/{OriginalImageName}:{OriginalImageTag} {k8s_docker_repository}/{k8s_name_space}/{NewImageName}:{NewImageTag}"
    proc_tag = subprocess.Popen(cmd_tag, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    try:
        rcode = proc_tag.wait()
        log.info("cmd_tag = ", cmd_tag)
    except Exception as x:
        errinfo = f"{str(x)}"
        log.error(errinfo)
    log.info("..........docker first push.......")
    cmd_push = f"docker push {k8s_docker_repository}/{k8s_name_space}/{NewImageName}:{NewImageTag}"
    first_push = True  # 用于标识第一次push是否成功
    proc = subprocess.Popen(cmd_push, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    try:
        proc.wait()
        log.info(f"docker first push success")
        db.update_devImage_status(ImageId, "02", k8s_name_space, NewImageName, NewImageTag)
    except Exception as ex:
        first_push = False
        log.error(f"docker first push fail,Reason: {ex}")
    log.info(".................docker second push..........")
    proc = subprocess.Popen(cmd_push, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    try:
        proc.wait()
        log.info(f"docker second push success")
        # Done 更新dev_image表，镜像生成成功
        db.update_devImage_status(ImageId, "02", k8s_name_space, NewImageName, NewImageTag)
    except Exception as ex:
        # Done 更新表dev_image，镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(ImageId, "05")
        log.error(f"docker second push fail,Reason: {ex}")
    # 删除本地镜像
    cmd_del1 = f"docker rmi {k8s_docker_repository}/{k8s_name_space}/{NewImageName}:{NewImageTag}"
    cmd_del2 = f"docker rmi {k8s_docker_repository}/{k8s_name_space}/{OriginalImageName}:{OriginalImageTag}"
    proc = subprocess.Popen(cmd_del1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    try:
        proc.wait()
        log.info(f"docker rmi local images {NewImageName}:{NewImageTag} success")
    except Exception as ex:
        log.error(f"docker rmi images fail,Reason: {ex}")
    proc = subprocess.Popen(cmd_del2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    try:
        proc.wait()
        log.info(f"docker rmi local images {OriginalImageName}:{OriginalImageTag} success")
    except Exception as ex:
        log.error(f"docker rmi images fail,Reason: {ex}")


def DevContainerToImageV119():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter DevContainerToImageV119...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        Dir = jo.get('Dir', None)
        HandlerScripts = jo.get('HandlerScripts', None)
        ImageId = jo['ImageId']
        ImageName = jo['ImageName']
        ImageTag = jo['ImageTag']
        ContainerName = jo['ContainerName']
    except Exception as x:
        # Done 更新镜像发布状态,镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(ImageId, "05")
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # Done 根据containername获取podname
    container_info = db.devc_get_v119(ContainerName)
    if container_info is None:
        return _rjson({None, 403, "Not Found Container [%s]" % str(ContainerName)})
    podName = container_info.get("pod_name", None)
    BaseImage = container_info.get("image", None)
    if podName is None or BaseImage is None:
        # Done 更新表dev_image，镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(ImageId, "05")
        return _rjson({None, 403, "Not Found Container [%s]" % str(ContainerName)})
    th = threading.Thread(target=__build_image, daemon=True,
                          args=(ImageId, ImageName, ImageTag, ContainerName, podName, BaseImage, Dir, HandlerScripts))
    th.start()
    return _rjson({"return": "building images"})


def __build_image(ImageId, ImageName, ImageTag, ContainerName, podName, BaseImage, Dir, HandlerScripts):
    log.info("...enter __build_image ...")
    # 将容器内Dir文件及文件夹copy至宿主机

    basepath = os.path.join(_conf["DevconToImage_fileserv_path"],
                            "DevConToImage_File_v119")  # 路径为 /tmp/DevConToImage_File_v119
    if not os.path.exists(basepath):
        try:
            os.makedirs(basepath, exist_ok=True)
        except Exception as x:
            log.error(str(x))
    basepath_container = os.path.join(basepath, ContainerName)
    if not os.path.exists(basepath_container):
        try:
            os.makedirs(basepath_container, exist_ok=True)
        except Exception as x:
            log.error(str(x))
    for dir in Dir:
        if dir.startswith("/data"):
            dir = dir[6:]
        cmd = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {podName}:{dir} {basepath_container}/data/{dir}"  # 将容器内的文件及文件夹copy至/t{ContainerName}目录下
        log.info("cmd = ", cmd)
        proc = subprocess.Popen(cmd, shell=True)
        try:
            rcode = proc.wait()
        except Exception as x:
            log.info(f"cmd: {cmd}")
            errinfo = f"{str(x)}"
            log.error(errinfo)
    cmd1 = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {podName}:.local {basepath_container}/data/.local"
    proc = subprocess.Popen(cmd1, shell=True)
    try:
        rcode = proc.wait()
        log.info("cmd1 = ", cmd1)
    except Exception as x:
        errinfo = f"{str(x)}"
        log.error(errinfo)
        # Done 更新数据库dev_image表中05 生成失败
        return _rjson(None, rcode=507, rmsg=f"{errinfo}")
    cmd3 = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {podName}:.local {basepath_container}/root/.local"
    proc = subprocess.Popen(cmd3, shell=True)
    try:
        rcode = proc.wait()
        log.info("cmd3 = ", cmd3)
    except Exception as x:
        errinfo = f"{str(x)}"
        log.error(errinfo)
        # Done 更新数据库dev_image表中05 生成失败
        return _rjson(None, rcode=507, rmsg=f"{errinfo}")
    cmd2 = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {podName}:.library {basepath_container}/data/.library"
    proc = subprocess.Popen(cmd2, shell=True)
    try:
        rcode = proc.wait()
        log.info("cmd2 = ", cmd2)
    except Exception as x:
        errinfo = f"{str(x)}"
        log.error(errinfo)
        # Done 更新数据库dev_image表中05 生成失败
        return _rjson(None, rcode=507, rmsg=f"{errinfo}")
    cmd4 = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {podName}:.library {basepath_container}/root/.library"
    proc = subprocess.Popen(cmd4, shell=True)
    try:
        rcode = proc.wait()
        log.info("cmd4 = ", cmd4)
    except Exception as x:
        errinfo = f"{str(x)}"
        log.error(errinfo)
        # Done 更新数据库dev_image表中05 生成失败
        return _rjson(None, rcode=507, rmsg=f"{errinfo}")
    # 4、生成Dockerfile
    Dockerfile = os.path.join(basepath_container, "Dockerfile")
    log.info("--writing Dockerfile: %s" % Dockerfile)
    with open(Dockerfile, "w", encoding='utf-8') as fi:
        fi.write("FROM " + BaseImage + '\n')
        for dir in os.listdir(basepath_container):
            if dir == "Dockerfile" or dir == ".local" or dir == ".library":
                continue
            fi.write(
                f"COPY {dir} {dir}" + "\n")  # 将Dir文件copy至镜像内，Dir文件按存放在宿主机/tmp/DevConToImage_File_v119/{ContainerName}目录下
        if HandlerScripts is not None and HandlerScripts != "":
            fi.write(f"ENTRYPOINT {HandlerScripts}" + "\n")
    #            fi.write(f"ENTRYPOINT [\"{HandlerScripts}\"]" + "\n")
    log.info(f"Dockerfile------{Dockerfile}")
    log.info("..........docker build.......")
    proc = subprocess.Popen(f"docker build  -q -t {ImageName}:{ImageTag} .", cwd=basepath_container, shell=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        proc.wait()
    except Exception as ex:
        log.info(f"docker build fail,reason:---{ex}")
    stderr = proc.stderr.read().decode(encoding="utf-8")
    GenerateImageId = ""  # 获取生成的镜像id
    if stderr == "":
        log.info("...docker build ...success")
        GenerateImageId = proc.stdout.read().decode(encoding="utf-8")[7:-1]
        # Done 更新数据库MT_PREDIST_IMAGE表中PREDICT_IMAGE_STATUS=02 生成成功
        log.info("..........docker tag.......")
        subprocess.check_output(
            f"docker tag {ImageName}:{ImageTag} {k8s_docker_repository}/{k8s_name_space}/{ImageName}:{ImageTag}",
            cwd=basepath_container, shell=True)
        log.info("..........docker first push.......")
        first_push = True
        proc = subprocess.Popen(f"docker push {k8s_docker_repository}/{k8s_name_space}/{ImageName}:{ImageTag}",
                                cwd=basepath_container, shell=True)
        try:
            proc.wait()
            log.info(f"docker first push success")
            db.update_devImage_status(ImageId, "02", k8s_name_space, ImageName, ImageTag)
        except Exception as ex:
            first_push = False
            log.error(f"docker first push fail,Reason: {ex}")
        log.info("...............docker second push..........")
        proc = subprocess.Popen(f"docker push {k8s_docker_repository}/{k8s_name_space}/{ImageName}:{ImageTag}",
                                cwd=basepath_container, shell=True)
        try:
            proc.wait()
            log.info(f"docker second push success")
            # Done 更新dev_image表，镜像生成成功
            db.update_devImage_status(ImageId, "02", k8s_name_space, ImageName, ImageTag)
        except Exception as ex:
            # Done 更新表dev_image，镜像生成失败05:创建失败
            db.update_devImage_onlyStatus(ImageId, "05")
            log.error(f"docker second push fail,Reason: {ex}")
        else:
            # Done 更新dev_image表，镜像生成成功
            db.update_devImage_status(ImageId, "02", k8s_name_space, ImageName, ImageTag)
    else:
        # Done 更新dev_image表，镜像生成失败
        db.update_devImage_onlyStatus(ImageId, "05")
        log.info(f"docker build exception---{stderr}")
    cmd = f"docker images|grep {GenerateImageId[:12]}"
    out = ""
    try:
        out = subprocess.check_output(cmd, shell=True)
    except Exception as ex:
        log.error(f"ex: {ex}")
    str_out = out.decode(encoding="utf-8")
    image_size = None
    log.info(f"str_out: {str_out}")
    if str_out is not None and str_out != "":
        image_size = list(str_out.split(" "))[-1]
        log.info(f"image_size: {image_size}")
        # 更新镜像信息，
        imageInfo = json.dumps({"image_size": image_size, "imageId": GenerateImageId})
        db.update_devImage_imageInfo(ImageId, imageInfo)
    # 删除本地镜像
    cmd_del_images = f"docker rmi -f {ImageName}:{ImageTag}"
    cmd_del_images_ = f"docker rmi -f {k8s_docker_repository}/{k8s_name_space}/{ImageName}:{ImageTag}"
    try:
        subprocess.check_output(cmd_del_images, shell=True)
        log.info(f".................rmi {cmd_del_images} success..........")
    except Exception as ex:
        log.error(f"rmi {cmd_del_images} fail,Reason: {ex}")
    try:
        subprocess.check_output(cmd_del_images_, shell=True)
        log.info(f".................rmi {cmd_del_images_} success..........")
    except Exception as ex:
        log.error(f"rmi {cmd_del_images} fail,Reason: {ex}")
    # 删除Dockerfile文件
    try:
        shutil.rmtree(basepath_container)
        log.info(f"rm {basepath_container} success")
    except Exception as ex:
        log.info(f"rm {basepath_container} fail,reason:---{ex}")
    return


def GenImageFromLocalI():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter GenImageFromLocalI...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        Dir = jo.get('Dir', None)
        HandlerScripts = jo.get('HandlerScripts', None)
        ImageId = jo['ImageId']
        LIPath = jo['LIPath']
        ImageName = jo['ImageName']
        ImageTag = jo['ImageTag']
    except Exception as x:
        # Done 更新镜像发布状态,镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(ImageId, "05")
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    th = threading.Thread(target=__build_image_fromLocalImage, daemon=True, args=(ImageId, ImageName, ImageTag, LIPath))
    th.start()
    return _rjson({"return": "building images"})


# 从文件服务器中上传本地镜像
def __build_image_fromLocalImage(ImageId, ImageName, ImageTag, LIPath):
    basepath = os.path.join(_conf["DevconToImage_fileserv_path"], "LocalImage")  # 路径为 /tmp/DevConToImage_File_v119
    if not os.path.exists(basepath):
        try:
            os.makedirs(basepath, exist_ok=True)
        except Exception as x:
            log.error(str(x))
    basepath_localimage = os.path.join(basepath, ImageId)
    if not os.path.exists(basepath_localimage):
        try:
            os.makedirs(basepath_localimage, exist_ok=True)
        except Exception as x:
            log.error(str(x))
    image = os.path.basename(LIPath)
    if LIPath.startswith("/data"):
        Local_LIPath = LIPath[6:]
    else:
        Local_LIPath = LIPath
    basepath_localimage_ = os.path.join(basepath_localimage, image)
    # Done 镜像文件copy至本地 --因镜像文件先上传至主机环境,所以这块逻辑先去掉
    cmd_copy = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {_conf['fileserve_pod']}:{Local_LIPath} {basepath_localimage_}"  # 将容器内的文件及文件夹copy至/tmp/{ContainerName}目录下
    proc = subprocess.Popen(cmd_copy, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        rcode = proc.wait()
        log.info(f"{cmd_copy} success")
    except Exception as x:
        # Done 更新表dev_image，镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(ImageId, "05")
        errinfo = f"{str(x)}"
        log.error(f"cmd_copy fail: {cmd_copy} fail Reason", errinfo)
    if basepath_localimage_.endswith(".tgz") or basepath_localimage_.endswith(".tar.gz"):
        try:
            tar = tarfile.open(basepath_localimage_, "r:gz")
            tar.extractall(path=basepath_localimage)
            log.info("extractall success")
        except Exception as x:
            errinfo = f"{str(x)}"
            log.error(f"extractall {basepath_localimage_} fail: ", errinfo)
        tar.close()
    # Done docker load
    cmd_load = f"docker load -i {image}"
    sout1 = ""
    try:
        out1 = subprocess.check_output(cmd_load, cwd=basepath_localimage, stderr=subprocess.STDOUT, shell=True)
        sout1 = out1.decode(encoding="utf-8")
        log.info(f"{cmd_load} success")
    except Exception as x:
        errinfo = f"{str(x)}"
        log.error(f"cmd_load fail: {cmd_load} fail Reason", errinfo)
        # Done 更新表dev_image，镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(ImageId, "05")
    if sout1 != "" and sout1 is not None:
        image_name = sout1[14:-1]
        # Done docker tag
        cmd_tag = f"docker tag {image_name} {k8s_docker_repository}/{k8s_name_space}/{ImageName}:{ImageTag}"
        try:
            out2 = subprocess.check_output(cmd_tag, cwd=basepath_localimage, stderr=subprocess.STDOUT, shell=True)
            log.info(f"{cmd_tag} success")
        except Exception as x:
            errinfo = f"{str(x)}"
            log.error(f"cmd_tag fail: {cmd_tag} fail Reason", errinfo)
            # Done 更新表dev_image，镜像生成失败05:创建失败
            db.update_devImage_onlyStatus(ImageId, "05")
        # Done docker push
        cmd_push = f"docker push {k8s_docker_repository}/{k8s_name_space}/{ImageName}:{ImageTag}"
        proc = subprocess.Popen(cmd_push, cwd=basepath_localimage, stderr=subprocess.STDOUT, shell=True)
        try:
            proc.wait()
            log.info(f"{cmd_push} success")
        except Exception as x:
            errinfo = f"{str(x)}"
            log.error(f"cmd_push fail: {cmd_push} fail Reason", errinfo)
            # Done 更新表dev_image，镜像生成失败05:创建失败
            db.update_devImage_onlyStatus(ImageId, "05")
    # 更新镜像信息(image_size)
    cmd_grep = f"docker images|grep {ImageName}"
    str_out = ""
    try:
        out4 = subprocess.check_output(cmd_grep, shell=True)
        str_out = out4.decode(encoding="utf-8")
    except Exception as ex:
        log.error(f"ex: {ex}")
    image_size = None
    if str_out is not None and str_out != "":
        image_size = list(str_out.split(" "))[-1]
        log.info(f"image_size: {image_size}")
        # Done 更新镜像信息，
        imageInfo = json.dumps({"image_size": image_size})
        db.update_devImage_imageInfo(ImageId, imageInfo)
        # Done 删除文件服务器中上传的镜像文件
        rdata = {"BODY": {"path": LIPath}}
        url = f"{_conf['fileserver_url']}/DevContainerFilesDel"
        r = requests.post(url, data=json.dumps(rdata))
        if r.status_code != 200:
            log.error(f"start process failed, http-code: {r.status_code}")
        else:
            log.info(f"delete {LIPath} success")
        # Done 删除下载到本地的镜像文件
        try:
            shutil.rmtree(basepath_localimage)
            log.info(f"rm {basepath_localimage} success")
        except Exception as x:
            errinfo = f"{str(x)}"
            log.error(f"rm {basepath_localimage} fail Reason", errinfo)
        log.info(f"k8s_name_space={k8s_name_space}|ImageName={ImageName}|ImageTag={ImageTag}")
        # Done 删除本地镜像load进来的镜像
        cmd_del_images = f"docker rmi -f {ImageName}:{ImageTag}"
        cmd_del_images_ = f"docker rmi -f {k8s_docker_repository}/{k8s_name_space}/{ImageName}:{ImageTag}"
        try:
            subprocess.check_output(cmd_del_images, shell=True)
            log.info(f".................rmi {cmd_del_images} success..........")
        except Exception as ex:
            log.error(f"rmi {cmd_del_images} fail,Reason: {ex}")
        try:
            subprocess.check_output(cmd_del_images_, shell=True)
            log.info(f".................rmi {cmd_del_images_} success..........")
        except Exception as ex:
            log.error(f"rmi {cmd_del_images} fail,Reason: {ex}")
        db.update_devImage_status(ImageId, "02", k8s_name_space, ImageName, ImageTag)
        return


# # 模型镜像发布接口
def ModelPubImage_k8s_v119():
    # vurl = "http://133.0.212.76:8711/ModelPubImage_k8s_v119"
    # try:
    #     jsons = request.stream.read().decode('utf-8')
    #     log.info("...enter ModelPubImage_k8s_v119...[%s]" % jsons)
    #     r = requests.post(vurl, data=jsons)
    #     if r.status_code != 200:
    #         logger.error("{} failed {} {}", vurl, r.status_code, r.text)
    #         return _rjson(None, 500, "failed!")
    #     return _rjson(r.json())
    # except Exception as x:
    #     logger.error("ModelPubImage_k8s_v119 {}", x)
    #     return _rjson(None,  8711, f"outer svc {x} failed {traceback.format_exc()}")

    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter ModelPubImage_k8s_v119...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        ImageId = jo['ImageId']
        ImageName = jo['ImageName']
        ImageTag = jo['ImageTag']
        HandlerScripts = jo.get('HandlerScripts', None)
        BaseImage = jo.get('BaseImage', "mconda37:v2")
        ModelFile = jo.get('ModelFile', [])
        LocalFile = jo.get('LocalFile', [])
        JupyterFile = jo.get('JupyterFile', {})
        Packages = jo.get('Packages', [])
        Envs = jo.get('Envs', [])
        Ports = jo.get("Ports", "")
    except Exception as x:
        # DONE 更新镜像发布状态,镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(ImageId, "05")
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # 建立临时目录
    basepath = os.path.join(_conf['basepath_tmp'], "." + ImageId)
    if not os.path.exists(basepath):
        os.makedirs(basepath, exist_ok=True)
    # 生成Dockerfile
    # th = threading.Thread(target=_build_push_image_pre, daemon=True,args=(ImageId,BaseImage,HandlerScripts,ModelFile,LocalFile,JupyterFile,Packages,Envs,Ports,basepath))
    # th.start()
    # th.join()
    # 启动容器build_push_image线程,创建镜像，并push到k8s镜像仓库
    th = threading.Thread(target=_build_push_image, daemon=True, args=(
        ImageId, BaseImage, HandlerScripts, ModelFile, LocalFile, JupyterFile, Packages, Envs, Ports, ImageName,
        ImageTag,
        basepath))
    th.start()
    return _rjson({"return": "building images"})


# 将docker build和docker push操作放在线程中
def _build_push_image(imageId, BaseImage, HandlerScripts, ModelFile, LocalFile, JupyterFile, Packages, Envs, Ports,
                      imageName, imageTag, basepath):
    log.info("...enter _build_push_image...")
    entrypath = _pjoin(basepath, "entry")
    sftpgopath = _pjoin(entrypath, "sftpgo")
    ng_homepath = _pjoin(entrypath, "ng_home")
    node_exporterpath = _pjoin(entrypath, "node_exporter")
    entrypath_source = _pjoin(_conf['docker_image_path'], "entry_temp")
    try:
        shutil.copytree(_pjoin(entrypath_source, "dockerSub_modelMain"), entrypath)
        shutil.copytree(_pjoin(entrypath_source, "sftpgo"), sftpgopath)
        shutil.copytree(_pjoin(entrypath_source, "ng_home"), ng_homepath)
        shutil.copytree(_pjoin(entrypath_source, "node_exporter"), node_exporterpath)
    except Exception as ex:
        # DONE 更新镜像发布状态,镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(imageId, "05")
        return _rjson(None, 503, f"{ex}")
    pkgs_path = os.path.join(entrypath, "pkgs")
    try:
        os.makedirs(pkgs_path, exist_ok=True)
    except Exception as ex:
        # DONE 更新镜像发布状态,镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(imageId, "05")
        return _rjson(None, 504, f"{ex}")
    # copy ModelFile、LocalFile、JupyterFile至本地
    if ModelFile is not None and len(ModelFile) > 0:
        modelfile_path = os.path.join(entrypath, "modelfile")
        try:
            os.makedirs(modelfile_path, exist_ok=True)
        except Exception as ex:
            # DONE 更新镜像发布状态,镜像生成失败05:创建失败
            db.update_devImage_onlyStatus(imageId, "05")
            return _rjson(None, 504, f"{ex}")
    if LocalFile is not None and len(LocalFile) > 0:
        LocalFile_path = os.path.join(entrypath, "localfile")
        try:
            os.makedirs(LocalFile_path, exist_ok=True)
        except Exception as ex:
            # DONE 更新镜像发布状态,镜像生成失败05:创建失败
            db.update_devImage_onlyStatus(imageId, "05")
            return _rjson(None, 504, f"{ex}")
    if JupyterFile is not None and len(JupyterFile) > 0:
        jupyterfile_path = os.path.join(entrypath, "jupyterfile")
        try:
            os.makedirs(jupyterfile_path, exist_ok=True)
        except Exception as ex:
            # DONE 更新镜像发布状态,镜像生成失败05:创建失败
            db.update_devImage_onlyStatus(imageId, "05")
            return _rjson(None, 504, f"{ex}")
    if ModelFile is not None and len(ModelFile) > 0:
        for mfile in ModelFile:
            if os.path.isdir(mfile):
                cmd_copy_mfile = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {_conf['fileserve_pod']}:{mfile} {modelfile_path}"  # 将容器内的文件及文件夹copy至/tmp/modelfile目录下
            else:
                file_name = os.path.basename(mfile)
                cmd_copy_mfile = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {_conf['fileserve_pod']}:{mfile} {modelfile_path}/{file_name}"  # 将容器内的文件及文件夹copy至/tmp/modelfile目录下
            proc = subprocess.Popen(cmd_copy_mfile, shell=True)
            try:
                rcode = proc.wait()
                log.info(f"{cmd_copy_mfile} success")
            except Exception as x:
                errinfo = f"{str(x)}"
                log.error(f"cmd_copy_mfile fail: {cmd_copy_mfile} fail Reason", errinfo)
    if LocalFile is not None and len(LocalFile) > 0:
        for lfile in LocalFile:
            if os.path.isdir(lfile):
                cmd_copy_lfile = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {_conf['fileserve_pod']}:{lfile} {LocalFile_path}"  # 将容器内的文件及文件夹copy至/tmp/modelfile目录下
            else:
                file_name = os.path.basename(lfile)
                cmd_copy_lfile = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {_conf['fileserve_pod']}:{lfile} {LocalFile_path}/{file_name}"  # 将容器内的文件及文件夹copy至/tmp/modelfile目录下
            roc = subprocess.Popen(cmd_copy_lfile, shell=True)
            try:
                rcode = proc.wait()
                log.info(f"{cmd_copy_lfile} success")
            except Exception as x:
                errinfo = f"{str(x)}"
                log.error(f"cmd_copy_lfile fail: {cmd_copy_lfile} fail Reason", errinfo)
    # 根据jupyter容器名称，获取pod名称
    jcontaniername = JupyterFile.get("contaniername", "")
    container_info = db.devc_get_v119(jcontaniername)
    if container_info is not None:
        jpod = container_info.get("pod_name", None)
        for jfile in JupyterFile.get("filepath", []):
            if os.path.isdir(jfile):
                cmd_copy_jupyterfile = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {jpod}:{jfile} {jupyterfile_path}"  # 将容器内的文件及文件夹copy至/tmp/modelfile目录下
            else:
                file_name = os.path.basename(jfile)
                cmd_copy_jupyterfile = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {jpod}:{jfile} {jupyterfile_path}/{file_name}"  # 将容器内的文件及文件夹copy至/tmp/modelfile目录下
            proc = subprocess.Popen(cmd_copy_jupyterfile, shell=True)
            try:
                rcode = proc.wait()
                log.info(f"{cmd_copy_jupyterfile} success")
            except Exception as x:
                errinfo = f"{str(x)}"
                log.error(f"cmd_copy_jupyterfile fail: {cmd_copy_jupyterfile} fail Reason", errinfo)
        cmd1 = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {jpod}:.local {basepath}/data/.local"
        proc = subprocess.Popen(cmd1, shell=True)
        try:
            rcode = proc.wait()
            log.info(f"{cmd1} success")
        except Exception as x:
            errinfo = f"{str(x)}"
            log.error(f"cmd1 fail: {cmd1} fail Reason", errinfo)
        cmd2 = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {jpod}:.local {basepath}/root/.local"
        proc = subprocess.Popen(cmd2, shell=True)
        try:
            rcode = proc.wait()
            log.info(f"{cmd2} success")
        except Exception as x:
            errinfo = f"{str(x)}"
            log.error(f"cmd2 fail: {cmd2} fail Reason", errinfo)
    # 更新镜像生成进度，dev_progress表-》FINISH_PERCENT字段
    # log_id="LOG"+_gen_rand_str(11)
    # try:
    #     _insert_dev_progress(log_id,create_id,create_id,obj_id,async_task_type)
    #     log.info("_insert_database")
    # except Exception as x:
    #     log.error(x)

    jHandlerScripts = JupyterFile.get("HandlerScripts", None)
    # 将jupyter容器内/data/.local目录copy至
    if Ports is not None:
        Ports = Ports.replace(",", " ")  # EXPOSE 12 34
    Dockerfile = os.path.join(basepath, "Dockerfile")
    log.info("--writing Dockerfile: %s" % Dockerfile)
    with open(Dockerfile, "w", encoding='utf-8') as fi:
        requirement_temp_path = os.path.join(pkgs_path, "requirements_temp.txt")
        requirement_path = os.path.join(pkgs_path, "requirements.txt")
        with open(requirement_temp_path, "w+", encoding='utf-8') as fii:  # 生成requirements.txt
            for pack in Packages:
                fii.write(pack["Name"])
                if pack["Version"] is not None and pack["Version"] != "":
                    fii.write("==")
                    fii.write(pack["Version"])
                fii.write("\n")

            fii.write("flask" + "\n")
            fii.write("psutil==5.7.0" + "\n")
            fii.write("django==1.11" + "\n")
        lines_pkg = set()
        outfiile = open(requirement_path, 'w+', encoding='utf-8')
        f = open(requirement_temp_path, 'r', encoding='utf-8')
        for line in f:
            line_split = line.split("==")
            if line_split[0].strip().lower() not in lines_pkg:
                outfiile.write(line)
                lines_pkg.add(line_split[0].strip().lower())
        outfiile.close()
        f.close()
        fi.write("FROM " + BaseImage + '\n')
        fi.write("COPY entry /entry" + "\n")
        fi.write("COPY data/.local /data/.local" + "\n")
        fi.write("COPY root/.local /root/.local" + "\n")
        # 修改扩展包安装方式为pypi私服
        pypi_server = _conf['pypi_server']  # format:  http://132.91.147.152:31377
        fi.write(
            f"RUN pip install --index-url {pypi_server} --trusted-host {pypi_server[7:pypi_server.rfind(':')]} -r /entry/pkgs/requirements.txt" + '\n')
        # Done Ports、8022、8081端口均需要Expose暴漏出来
        fi.write("EXPOSE 8022 8081 " + Ports + "\n")
        # Done ENV设置HandlerScripts放到环境变量 MODEL_SERVICE
        if HandlerScripts is not None and HandlerScripts != "":
            fi.write("ENV MODEL_SERVICE=%s\n" % HandlerScripts)
        if Envs is not None and Envs:
            for env in Envs:
                env_temp = env.strip()
                if env_temp.find("=") != -1:
                    env_temp = env_temp.split("=")
                    fi.write(f"ENV {env_temp[0]}={env_temp[1]}" + "\n")
        if jHandlerScripts is None:
            fi.write("ENTRYPOINT python /entry/model_main.py")
        else:
            fi.write(f"ENTRYPOINT python /entry/model_main.py & {jHandlerScripts}")
        # fi.write("ENTRYPOINT [\"python\",\"/entry/model_main.py\"]"+"\n")
        log.info(f"Dockerfile------{Dockerfile}")

    log.info("..........docker build begin.......")
    try:
        sout = subprocess.Popen(f"docker build  -q -t {imageName}:{imageTag} .", cwd=basepath, shell=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        exit_code = sout.wait()
        stderr = sout.stderr.read()
        stdout = sout.stdout.read()
        if exit_code != 0:
            log.error("docker build failed %s", stderr)
        else:
            log.info("..........docker build success.......[%s]", stdout)
    except Exception as ex:
        log.info(f"docker build fail,reason:---{ex}")
        # DONE 更新镜像发布状态,镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(imageId, "05")
        return
    log.info("..........docker tag begin.......")
    try:
        subprocess.check_output(
            f"docker tag {imageName}:{imageTag} {k8s_docker_repository}/{k8s_name_space}/{imageName}:{imageTag}",
            cwd=basepath, shell=True)
        log.info("..........docker tag success.......")
    except Exception as ex:
        log.info(f"docker tag fail,reason:---{ex}")
        # DONE 更新镜像发布状态,镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(imageId, "05")
        return
    log.info("..........docker first push begin.......")
    try:
        proc = subprocess.Popen(f"docker push {k8s_docker_repository}/{k8s_name_space}/{imageName}:{imageTag}",
                                shell=True)
        rcode = proc.wait()
        log.info("..........docker first push success.......")
    except Exception as ex:
        log.info(f"docker first push fail,reason:---{ex}")
    log.info("...............docker second push..........")
    try:
        proc = subprocess.Popen(f"docker push {k8s_docker_repository}/{k8s_name_space}/{imageName}:{imageTag}",
                                shell=True)
        rcode = proc.wait()
        log.info("...............docker second push success..........")
    except Exception as ex:
        log.error(f"docker second push fail,Reason: {ex}")
        # DONE 更新镜像发布状态,镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(imageId, "05")
    # 镜像生成成功
    log.info(f"update table dev_image IMAGE_ID={imageId},IMAGE_STATUS=02,IMAGE_NAME={imageName},TAG={imageTag}")
    db.update_devImage_status(imageId, "02", k8s_name_space, imageName, imageTag)
    # 更新镜像信息(image_size)
    cmd = f"docker images|grep {imageName}"
    out = ""
    try:
        out = subprocess.check_output(cmd, shell=True)
    except Exception as ex:
        log.error(f"ex: {ex}")
    str_out = out.decode(encoding="utf-8")
    image_size = None
    if str_out is not None and str_out != "":
        image_size = list(str_out.split(" "))[-1]
        log.info(f"image_size: {image_size}")
        # Done 更新镜像信息，
        imageInfo = json.dumps({"image_size": image_size})
        db.update_devImage_imageInfo(imageId, imageInfo)
    # 删除临时文件 basepath
    if os.path.exists(basepath):
        try:
            shutil.rmtree(basepath)
            log.info(f"rm {basepath} success")
        except Exception as ex:
            log.info(f"rm {basepath} fail,Reason {ex}")
            return _rjson(None, 509, f"{basepath} rm fail")
    cmd_del_images = f"docker rmi -f {imageName}:{imageTag}"
    cmd_del_images_ = f"docker rmi -f {k8s_docker_repository}/{k8s_name_space}/{imageName}:{imageTag}"
    try:
        subprocess.check_output(cmd_del_images, shell=True)
        log.info(f".................rmi {cmd_del_images} success..........")
    except Exception as ex:
        log.error(f"rmi {cmd_del_images} fail,Reason: {ex}")
    try:
        subprocess.check_output(cmd_del_images_, shell=True)
        log.info(f".................rmi {cmd_del_images_} success..........")
    except Exception as ex:
        log.error(f"rmi {cmd_del_images} fail,Reason: {ex}")
    return


# 镜像部署
def application_deployment():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter application_deployment...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        # appId = jo['appId']  #暂时注销
        user = jo['user']
        containerName = jo['containerName']
        res = jo['res']
        if 'cpu' not in res:
            raise Exception('param res.cpu be needed!')
        if 'mem' not in res:
            raise Exception('param res.mem be needed!')
        deployment_type = jo['deployment_type']
        image = jo['image']
        env = jo.get('env', {})
        ports = jo.get('ports', [])
        entry = jo.get("entry", "")
        mount_name = jo.get("mount_name", "")
        mount_path = jo.get("mount_path", "")
        host = jo.get("host", "")
        # 下面的参数是为了在dev_application中新增记录,添加的参数
        appName = jo['appName']
        appDesc = jo['appDesc']
        projectName = jo['projectName']
        appParam = jo['appParam']
        createId = jo['createId']
        opId = jo['opId']
        opNote = jo['opNote']
    except Exception as x:
        # #DONE 更新镜像发布状态,镜像部署失败06:启动失败
        # db.update_devApp_status(appId,"06") 暂时注销
        return _rjson(None, 501, "receive request parameter failed %s" % x)

    # 报文中,内存mem单位为Gi,代码逻辑mem内存单位是Mi,此处做个转换Gi->Mi
    res['mem'] = res['mem'] * 1024
    # 新增表记录
    # appId,appName,appDesc,projectName,appType,appStatus(默认状态为00),imageI,appParam,createId,opId,opNote
    appId = str(time.time()).split(".")[0] + str(random.randrange(1, 5000, 4))
    imageId = str(time.time()).split(".")[0] + str(random.randrange(5001, 10000, 4))
    db.insert_app(appId, appName, appDesc, projectName, deployment_type, "01", imageId, appParam, createId, opId,
                  opNote)
    try:
        ret, msg = _check_res_limit(_conf['k8s_portal_addr'], k8s_name_space, res)
    except Exception as x:
        log.error("check res limit fail %s" % str(x))
    if not ret:
        # DONE 更新镜像发布状态,镜像部署失败06:启动失败
        db.update_devApp_status(appId, "06")
        return _rjson(None, 509, "resource exceed limit: %s" % msg)
    pod_name = f"md-{digest(user)[-6:]}-{_gen_rand_str(4)}".lower()
    # 应用类型(deployment_type)和报文信息(jo)入库
    jo['pod_name'] = pod_name
    jo['svc_name'] = pod_name  # pvc_name与deployment名称相同
    env['LD_LIBRARY_PATH'] = "/data/.library:/root/.library"
    db.update_apptype_param(appId, deployment_type, json.dumps(jo))
    log.info(appId, pod_name, image, res, entry, env, ports, ret, host, mount_name, mount_path, deployment_type)
    th = threading.Thread(target=_create_dep_svc, daemon=True, args=(
        appId, pod_name, image, res, entry, env, ports, ret, host, mount_name, mount_path, deployment_type))
    th.start()
    # 探测容器启动状态，并更新数据库
    # th = threading.Thread(target=loop_func,args=(DevContainerStat,jo,appId))
    # th.start()
    return _rjson({"appId": appId})


def app_container_stat():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter app_container_stat...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        appId = jo['appId']
    except Exception as x:
        return _rjson(None, 501, "receive request parameter failed %s" % x)
    app_params = db.get_appInfo(appId)
    dep_name = None
    svc_name = None
    for app_param in app_params:
        app_param_d = json.loads(app_param[0].decode('utf-8'))
        dep_name = app_param_d.get("pod_name", None)
        svc_name = app_param_d.get("svc_name", None)
    if dep_name is not None and svc_name is not None:
        log.info(f"dep_name:{dep_name}")
        v1 = client.CoreV1Api()
        ret = v1.list_namespaced_pod(namespace=k8s_name_space, label_selector=f"app={dep_name}")
        if len(ret.items) < 1:
            return _rjson({"ip": "", "port": []})
        else:
            p = ret.items[0]
            pod_name = p.metadata.name
            st = p.status
            if st is None:
                return _rjson({"ip": "", "port": []})
            try:
                ready = len([0 for i in st.container_statuses if i.ready])
            except Exception as x:
                return _rjson({"ip": "", "port": []})
            node_host = st.host_ip
        ret = v1.list_namespaced_service(namespace=k8s_name_space, field_selector=f"metadata.name={svc_name}")
        if len(ret.items) < 1:
            log.info("service not found")
            ports = None
            return _rjson({"ip": "", "port": []})
        else:
            item = ret.items[0]
            ports = [{p.target_port: str(p.node_port)} for p in item.spec.ports]
            return _rjson({"ip": node_host, "port": ports})
    else:
        return _rjson({"ip": "", "port": []})


def _create_dep_svc(appId, pod_name, image, res, entry, env, ports, ret, host, mount_name, mount_path, deployment_type):
    image_id = f"{_conf['k8s_docker_repository']}/{k8s_name_space}/{image}"
    log.info("start model test container with image: %s res: %s, entry: %s", image_id, res, entry)
    ret = _create_dep(pod_name, image_id, res['cpu'], host, res['mem'], mount_name, mount_path, deployment_type, entry,
                      env, fsGroup=8989)
    log.info("create_dep return %s", ret)
    if not ret:
        # DONE 更新镜像发布状态,镜像部署失败06:启动失败
        db.update_devApp_status(appId, "06")
        return _rjson(None, 507, "create deployment failed")
    ret = _create_service(pod_name, ports)
    if not ret:
        _delete_all(pod=pod_name)
        log.error("create service failed")
        # DONE 更新镜像发布状态,镜像部署失败06:启动失败
        db.update_devApp_status(appId, "06")
        return _rjson(None, 511, "create service failed")
    else:
        # DONE 更新镜像发布状态,镜像部署失败06:启动失败
        db.update_devApp_status(appId, "02")


# 创建 pod
def _create_dep(pod_name, image_id, cpu, host, memory, mount_name, mount_path, deployment_type, entry="", env={},
                fsGroup=None, gpu=None):
    if deployment_type == "02" and mount_name != "" and mount_path != "":
        vols = [client.V1Volume(name=f"volume-{mount_name}",
                                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                                    claim_name=mount_name)), ]
        mounts = [client.V1VolumeMount(mount_path=mount_path, name=f"volume-{mount_name}")]
    elif deployment_type == "02" and mount_name == "" and mount_path == "":
        vols = None
        mounts = None
    elif deployment_type == "01":
        vols = None
        mounts = None
    cpu_req = f"{cpu * 20}m"
    cpu = f"{cpu:.1f}"
    mem = f"{memory}Mi"
    if gpu is None:
        resources = {'limits': {'memory': mem, 'cpu': cpu},
                     'requests': {'memory': mem, 'cpu': cpu_req}}
    else:
        resources = {'limits': {'memory': mem, 'cpu': cpu, "aliyun.com/gpu-mem": gpu},
                     'requests': {'memory': mem, 'cpu': cpu_req, "aliyun.com/gpu-mem": gpu}}
    v1 = client.AppsV1Api()
    container = client.V1Container(
        name=pod_name,
        image=image_id,
        image_pull_policy="IfNotPresent",
        volume_mounts=mounts,
        env=[
            {'name': 'TZ', 'value': 'PRC'},  # 设定时区
        ],
        resources={'limits': {'memory': mem, 'cpu': cpu}},
    )
    sec = None if fsGroup is None else client.V1PodSecurityContext(fs_group=fsGroup, run_as_user=fsGroup)
    if entry != "":
        cmd = shlex.split(entry)
        container.command = [cmd[0], ]
        if len(cmd) > 1:
            container.args = cmd[1:]
    if type(env) == dict:
        for k, v in env.items():
            container.env.append({'name': k, 'value': v})
    template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(labels={"app": pod_name}),
        spec=client.V1PodSpec(containers=[container], node_name=host, volumes=vols, security_context=sec))

    spec = client.V1DeploymentSpec(replicas=1, template=template, selector={'matchLabels': {'app': pod_name}})

    deployment = client.V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=client.V1ObjectMeta(name=pod_name),
        spec=spec)
    try:
        api_response = v1.create_namespaced_deployment(
            namespace=k8s_name_space, body=deployment
        )
        log.info("create dep %s, return %s", pod_name, api_response)
        return True
    except Exception as x:
        log.error("create dep %s failed %s" % (pod_name, x))
    return False


# 创建service
def _create_service(pod_name, ports=[]):
    v1 = client.CoreV1Api()
    service = client.V1Service(api_version="v1", kind="Service")
    service.metadata = client.V1ObjectMeta(name=pod_name, labels={"app": pod_name})
    spec = client.V1ServiceSpec()
    spec.type = "NodePort"
    spec.selector = {"app": pod_name}
    spec.ports = []
    try:
        for p in ports:
            if type(p) == str:
                if p.find(':') > 0:
                    pp = p.split(":")  # port before : is outer port
                    t = client.V1ServicePort(name=f"port-{pp[1]}", protocol="TCP", port=int(pp[1]),
                                             target_port=int(pp[1]), node_port=int(pp[0]))
                else:
                    t = client.V1ServicePort(name=f"port-{p}", protocol="TCP", port=int(p))
            else:
                t = client.V1ServicePort(name=f"port-{p}", protocol="TCP", port=int(p))
            spec.ports.append(t)
    except Exception as x:
        log.error(f"create service failed, invalid ports {ports}")
        return False
    service.spec = spec
    try:
        resp = v1.create_namespaced_service(namespace=k8s_name_space, body=service)
        log.info("create service %s return %s", pod_name, resp)
    except Exception as x:
        log.error(f"create service {pod_name} failed {x}")
        return False
    return True


def _delete_all(pod=None, svc=None):
    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    if svc is not None:
        try:
            resp = v1.delete_namespaced_service(name=svc, namespace=k8s_name_space, body=client.V1DeleteOptions())
            log.info("delete service %s return %s", svc, resp)
        except Exception as x:
            log.error("delete service %s failed, but Continue... %s" % (pod, x))
    if pod is not None:
        try:
            resp = apps_v1.delete_namespaced_deployment(name=pod, namespace=k8s_name_space,
                                                        body=client.V1DeleteOptions())
            log.info("delete dep %s return %s", pod, resp)
        except Exception as x:
            log.error("delete dep %s failed, but Continue... %s" % (svc, x))


def _check_res_limit(k8s_portal_addr, k8s_name_space, res):
    limit_res = get_res_k8s(k8s_portal_addr, k8s_name_space)
    if type(limit_res) == str:
        log.error("check res limit failed %s" % limit_res)
        return False, limit_res
    memory = res.get("mem", 1)  # Mi
    memory_free = limit_res['mem']['mb_limit'] - limit_res['mem']['mb_used']
    if memory > memory_free:
        return False, 'memory request exceeded available %.3f Mi, request %.3f Mi' % (memory_free, memory)
    cpu = res.get("cpu", 1)
    cpu_free = limit_res['cpu']['cores_limit'] - limit_res['cpu']['cores_used']
    if cpu > cpu_free:
        return False, 'cpu request exceeded available %.3f cores' % (cpu_free,)
    volume_size_in_mb = res.get("volume_size_in_mb", 1024)
    store_free = limit_res['store']['mb_limit'] - limit_res['store']['mb_used']
    if volume_size_in_mb > store_free:
        return False, 'store request exceeded available %.3f Mi' % (store_free,)
    return True, None


def get_res_k8s(k8s_portal_addr, k8s_name_space):
    url = f"{k8s_portal_addr}/csp/register/roles/all"
    r = requests.get(url)
    if r.status_code == 200:
        robj = json.loads(r.text)
        for o in robj:
            if o['name'] == k8s_name_space:
                cluster_id = o['cluster']
    else:
        return f'find cluster_id failed {r.status_code} -- {r.text}'
    url = f"{k8s_portal_addr}/csp/register/resourcequota/{k8s_name_space}/cluster/{cluster_id}"
    r = requests.get(url)
    ret = {}
    if r.status_code == 200:
        jo = json.loads(r.text)
        # labs = ["hard", "used", "resourceType"]
        for d in jo:
            res_type, limit, used = d['resourceType'], d['hard'], d['used']
            if res_type == 'cpu':
                ret['cpu'] = {"cores_limit": float(limit), "cores_used": float(used[:-1]) / 1000}
            elif res_type == 'memory':
                ret['mem'] = {"mb_limit": __sz2mb(limit), "mb_used": __sz2mb(used)}
            elif res_type == 'requests.storage':
                ret['store'] = {"mb_limit": __sz2mb(limit), "mb_used": __sz2mb(used)}
        return ret
    else:
        return f'get res info failed {r.status_code} -- {r.text}'


def __sz2mb(x):
    if x[-2:] == 'Gi':
        return float(x[:-2]) * 1024
    elif x[-2:] == 'Mi':
        return float(x[:-2])
    elif x[-2:] == 'Ti':
        return float(x[:-2]) * 1024 * 1024
    else:
        return float(x) / 1024 / 1024


def digest(s: str):
    import hashlib
    # m = hashlib.sha256()
    # m.update(s.encode('utf-8'))
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


# 镜像部署后的运维,停止
def application_stop():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter application_stop...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        appId = jo['appId']
    except Exception as x:
        # Done 停止失败 关闭失败:05
        db.update_devApp_status(appId, "05")
        return _rjson(None, 501, "receive request parameter failed %s" % x)
    app_params = db.get_appInfo(appId)
    for app_param in app_params:
        app_param_d = json.loads(app_param[0].decode('utf-8'))
        pod_name = app_param_d.get("pod_name", None)
        svc_name = app_param_d.get("svc_name", None)
        if pod_name != "" and svc_name != "" and pod_name is not None and svc_name is not None:
            _delete_all(pod=pod_name, svc=svc_name)
    # Done 停止成功 已关闭:04
    db.update_devApp_status(appId, "04")
    return _rjson({})


# 镜像部署后的运维,启动
def application_start():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter application_start...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        appId = jo['appId']
    except Exception as x:
        # 启动失败  启动失败:06
        db.update_devApp_status(appId, "06")
        return _rjson(None, 501, "receive request parameter failed %s" % x)
    app_params = db.get_appInfo(appId)
    for app_param in app_params:
        app_param_d = json.loads(app_param[0])
        pod_name = app_param_d.get("pod_name", None)
        image = app_param_d.get("image", None)
        res = app_param_d.get("res", {})
        try:
            ret, msg = _check_res_limit(_conf['k8s_portal_addr'], k8s_name_space, res)
        except Exception as x:
            log.error("check res limit fail %s" % str(x))
        if not ret:
            # DONE 更新镜像发布状态,镜像部署失败06:启动失败
            db.update_devApp_status(appId, "06")
            return _rjson(None, 509, "resource exceed limit: %s" % msg)
        entry = app_param_d.get("entry", None)
        env = app_param_d.get("env", {})
        ports = app_param_d.get("ports", [])
        host = app_param_d.get("host", None)
        mount_name = app_param_d.get("mount_name", None)
        mount_path = app_param_d.get("mount_path", None)
        deployment_type = app_param_d.get("deployment_type", None)
        env['LD_LIBRARY_PATH'] = "/data/.library"
        db.update_apptype_param(appId, deployment_type, app_param[0])
        log.info(appId, pod_name, image, res, entry, env, ports, ret, host, mount_name, mount_path, deployment_type)
        th = threading.Thread(target=_create_dep_svc, daemon=True, args=(
            appId, pod_name, image, res, entry, env, ports, ret, host, mount_name, mount_path, deployment_type))
        th.start()
    # 启动失败  启动失败:02
    db.update_devApp_status(appId, "02")
    return _rjson({})


# 应用伸缩，即pod数量伸缩
def app_stretch():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter app_stretch...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        appId = jo['appId']
        num = jo['num']
    except Exception as x:
        return _rjson(None, 501, "receive request parameter failed %s" % x)
    app_params = db.get_appInfo(appId)
    log.info(app_params)
    for app_param in app_params:
        app_param_d = json.loads(app_param[0])
    dep_name = app_param_d.get("pod_name", None)  # 此处数据库中存储的实际是deployment
    log.info(f"deployment_name: {dep_name}")
    if dep_name is not None:
        cmd_stretch = f"{_conf['kubectl']} --kubeconfig {_conf['k8s_config_file']} scale deployment {dep_name} --replicas={num}"
        proc = subprocess.Popen(cmd_stretch, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        try:
            rcode = proc.wait()
        except Exception as x:
            errinfo = f"{str(x)}"
            log.error(f"app stretch fail Reason", errinfo)
            return _rjson(None, 502, f"{cmd_stretch} fail Reason", errinfo)
    return _rjson({})


# 应用伸缩，查询实际伸缩的数量
def app_stretch_qry():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter app_stretch_qry...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        appId = jo['appId']
    except Exception as x:
        return _rjson(None, 501, "receive request parameter failed %s" % x)
    app_params = db.get_appInfo(appId)
    log.info(app_params)
    for app_param in app_params:
        app_param_d = json.loads(app_param[0])
    dep_name = app_param_d.get("pod_name", None)  # 此处数据库中存储的实际是deployment
    baseUrl = f"http://172.21.3.104:8081"
    userPath = urlparse.urljoin(baseUrl, '/csp/register/applicationReplicas/aipaas')
    r = requests.get(userPath,
                     params={"cluster": "sitech-k8s-sc", "namespace": "y13932-crmmci", "name": "md-b4d399-n3ry"})
    if r.status_code == 500:
        return _rjson({}, rcode=r.status_code, rmsg=r.text)
    return _rjson({"status_code": r.status_code, "text": r.text})


def ModelFileSFTPV119():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter ModelFileSFTP...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        user = jo['user']
        imageId = jo['imageId']
        filePath = jo['filePath']
        password = jo['password']
    except Exception as x:
        log.error("load request body failed %s" % x)
        return _rjson(None, 7812, "load request body failed %s" % x)
    # 读取配置端口
    with open(f"{sftpgo_path}/sftpgo.json", 'r', encoding='utf8') as fi:
        cc = json.load(fi)
    sftpgo_serve_port = cc['sftpd']['bind_port']
    sftpgo_mng_port = cc['httpd']['bind_port']
    filePath = os.path.join("LocalImage", filePath)
    log.info(f"fileserv_path: {fileserv_path}")
    log.info(f"filePath: {filePath}")
    os.makedirs(filePath, exist_ok=True)
    baseUrl = f"http://127.0.0.1:{sftpgo_mng_port}"
    userPath = urlparse.urljoin(baseUrl, '/api/v1/user')
    userobj = {'id': 0, 'username': user, 'uid': 0, 'gid': 0, 'max_sessions': 0,
               'quota_size': 0, 'quota_files': 0, 'upload_bandwidth': 0, 'download_bandwidth': 0,
               'home_dir': os.path.join(fileserv_path, filePath),
               'password': password,
               'permissions': ['list', 'download', 'upload', 'delete', 'rename', 'create_dirs']}
    r = requests.post(userPath, json=userobj)
    if r.status_code == 200:
        # done save added user
        sftpgo_users[imageId] = {"user": user, "path": filePath, "optime": time.time()}
        data_store_changed()
        return _rjson({"ip": _conf['local_ip'], "port": sftpgo_serve_port})
    else:
        return _rjson(None, 501, "add sftp user failed, %s" % r.text)


# 查询sftp上传的文件列表
def api_qry_v119():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter api_qry_v119...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        imageId = jo.get('imageId', "")
    except Exception as x:
        log.error("load request body failed %s" % x)
        return _rjson(None, 7812, "load request body failed %s" % x)
    id1 = _gen_rand_str()
    path = os.path.join(base_path, "LocalImage")
    fpath = os.path.join(path, imageId)
    if not os.path.exists(fpath):
        return _rjson(None, 402, f"{fpath} not exists")
    if imageId != "" and imageId is not None:
        filelist = {'root': fpath, "dirPath": f"{imageId}/", "dirName": imageId, "type": "root", "id": id1,
                    "fileUseType": "03", 'child_dirs': []}
    else:
        filelist = {'root': fpath, "dirPath": "LocalImage/", "dirName": "LocalImage", "type": "root", "id": id1,
                    "fileUseType": "03", 'child_dirs': []}
    list_dir(fpath, filelist, "01")
    return _rjson({"filelist": filelist})

    if os.path.exists(fpath):
        id_code = _gen_rand_str()
        filelist_code = {'filePath': fpath, "dirPath": "LocalImage/", "LocalImage": "code", "id": id_code,
                         "type": "root", "fileUseType": "01", 'child_dirs': []}
        list_dir(fpath, filelist_code, "01")
        filelist.get("child_dirs").append(filelist_code)
    return _rjson({"filelist": filelist})


def list_host():
    url_k8s = "http://boatswain.si-tech.com.cn:30088"
    url = f"{url_k8s}/csp/register/nodes"
    host_info = []
    r = requests.get(url)
    if r.status_code == 200:
        jo = json.loads(r.text)
        if jo["status"] == "SUCCESS":
            for d in jo['data']:
                temp_host = {"host": str(d["name"]), "internalIP": str(d["internalIP"])}
                host_info.append(temp_host)
    return _rjson({"host_info": host_info})


def create_pvc_app():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter create_pvc...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        user = jo['user']
        pvc_name = jo['pvc_name']
        size_in_mb = jo.get('size_in_mb', 100)  # 默认是100Mi
    except Exception as x:
        log.error("load request body failed %s" % x)
        return _rjson(None, 7812, "load request body failed %s" % x)
    v1 = client.CoreV1Api()
    pvc = client.V1PersistentVolumeClaim(api_version="v1", kind="PersistentVolumeClaim",
                                         metadata=client.V1ObjectMeta(name=pvc_name,
                                                                      labels={"pvc": "app", "username": f"{user}"}),
                                         )
    pvc.spec = client.V1PersistentVolumeClaimSpec(storage_class_name=_conf.get("k8s_storage_class", "glusterfs-sc"),
                                                  access_modes=["ReadWriteMany", ],
                                                  volume_mode="Filesystem",
                                                  resources=client.V1ResourceRequirements(
                                                      requests={"storage": f"{size_in_mb}Mi"}))
    try:
        resp = v1.create_namespaced_persistent_volume_claim(namespace=k8s_name_space, body=pvc)
    except ApiException as x:
        db.devc_update_errormsg(pvc_name, "create pvc failed, reason: %s, body: %s" % (x.reason, x.body))
        log.error("create pvc failed, reason: %s, body: %s" % (x.reason, x.body))
        return _rjson(None, 7813, "create pvc failed, reason: %s, body: %s" % (x.reason, x.body))
    if resp.status.phase == 'Pending':
        return _rjson({})
    else:
        log.warning("create pvc failed11, return %s", resp)
        return _rjson(None, 7814, "create pvc failed11, return %s", resp)


def list_pvc_app():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter list_pvc_app...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        user = jo.get("user", None)
    except Exception as x:
        log.error("load request body failed %s" % x)
        return _rjson(None, 7812, "load request body failed %s" % x)
    v1 = client.CoreV1Api()
    if user is None or user == "":
        r2 = v1.list_namespaced_persistent_volume_claim(namespace="y13932-crmmci", label_selector="pvc=app")
    else:
        r2 = v1.list_namespaced_persistent_volume_claim(namespace=k8s_name_space,
                                                        label_selector=f"pvc=app,username={user}")
    if len(r2.items) < 1:
        return _rjson("no pod found!!!")
    else:
        pvc_names = []
        for i in range(len(r2.items)):
            item = r2.items[i]
            pvc_name = item.metadata.name
            pvc_names.append(pvc_name)
        return _rjson({"pvc_names": pvc_names})


# 删除数据卷
def del_pvc_app():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter create_pvc...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        pvc_name = jo["pvc_name"]
    except Exception as x:
        log.error("load request body failed %s" % x)
        return _rjson(None, 7812, "load request body failed %s" % x)
    v1 = client.CoreV1Api()
    try:
        resp = v1.delete_namespaced_persistent_volume_claim(name=pvc_name,
                                                            namespace=k8s_name_space, body=client.V1DeleteOptions())
        return _rjson({})
    except ApiException as x:
        return _rjson({f"Del {pvc_name} failed, Reason": x.reason})


def list_baseImage_app():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter list_image_app...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        imageName = jo.get("imageName", "")
    except Exception as x:
        log.error("load request body failed %s" % x)
        return _rjson(None, 7812, "load request body failed %s" % x)
    # 遍历数据库查询镜像
    image_tags = db.get_image_tag_from_devimage()
    imageTags = []
    for image_tag in image_tags:
        image = image_tag[0]  # 对应image
        tag = image_tag[1]  # 对应tag
        imageTags.append(image + ":" + tag)
    log.info("imageTags", imageTags)
    result = []
    for imageTag in imageTags:
        log.info("imageTag.find(imageName)", imageTag, imageTag.find(imageName), imageName)
        if imageTag.find(imageName) >= 0:
            result.append(imageTag)
    return _rjson({"images": result})


# 删除生成的镜像
def del_genImage():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter del_genImage...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        imageId = jo['imageId']
    except Exception as x:
        log.error("load request body failed %s" % x)
        return _rjson(None, 7812, "load request body failed %s" % x)
    # Done 删除表记录,镜像删除，修改逻辑，不再更新状态99了
    db.images_del(imageId)
    return _rjson({})


# 查询当前文件服务器的rest访问地址
def FileservAddrQry():
    v1 = client.CoreV1Api()
    node_host = None
    ret = v1.list_namespaced_pod(namespace=k8s_name_space, label_selector="app=fileserver")
    if len(ret.items) < 1:
        return _rjson({"restaddr ": "", "sftp_port": ""})
        pod_name, node_host, ready, total = None, None, None, None
    else:
        p = ret.items[0]
        pod_name = p.metadata.name
        st = p.status
        if st is None:
            return _rjson({"restaddr ": "", "sftp_port": ""})
        try:
            ready = len([0 for i in st.container_statuses if i.ready])
        except Exception as x:
            return _rjson({"restaddr ": "", "sftp_port": ""})
        total = len(st.container_statuses)
        print(f"containers ready:  {ready} / {total}")
        node_host = st.host_ip
    ret = v1.list_namespaced_service(namespace=k8s_name_space, field_selector="metadata.name=fileserver")
    if len(ret.items) < 1:
        log.info("service not found")
        ports = None
        return _rjson({"restaddr ": "", "sftp_port": ""})
    else:
        item = ret.items[0]
        ports = [[p.target_port, p.protocol, p.node_port] for p in item.spec.ports]
    p = None
    p1 = None
    if ports is not None:
        for port in ports:
            if port[0] == 8181:
                p = port[2]
            if port[0] == 2022:
                p1 = port[2]
    import socket
    try:
        c = socket.create_connection((node_host, p), 1.0)
        c.close()
        restaddr = f"http://{node_host}:{p}"
    except Exception as x:
        return _rjson({"restaddr ": "", "sftp_port": ""})
    return _rjson({"restaddr": restaddr, "sftp_port": p1})


def automl_model_register():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter automl_model_register...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        model_id = jo['model_id']
        version_id = jo['version_id']
        version_desc = jo['version_desc']
        model_path = jo.get('model_path', None)
        is_best = jo['is_best']
        is_valid = jo['is_valid']
        create_id = jo['create_id']
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    try:
        db.model_register(model_id, version_id, version_desc, model_path, is_best, is_valid, create_id)
    except Exception as x:
        return _rjson(None, 403, "Update table dev_model_version failed, [%s]" % str(x))
    return _rjson({})


def inquire_model_by_user():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter inquire_model_by_user...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        create_id = jo['create_id']
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    model_paths = []
    try:
        model_paths = db.inquire_model_by_user(create_id)
    except Exception as x:
        return _rjson(None, 403, "select model_path form table dev_model_version failed, [%s]" % str(x))
    return _rjson({"model_paths": model_paths})


def inquire_model_by_maxTime():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter inquire_model_by_maxTime...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        model_id = jo['model_id']
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    model_path = ""
    try:
        model_path = db.inquire_model_by_maxTime(model_id)
        log.info(model_path)
    except Exception as x:
        return _rjson(None, 403, "select model_path form table dev_model_version failed, [%s]" % str(x))
    return _rjson({"model_path": model_path})


def inquire_model_by_metrics():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter inquire_model_by_metrics...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        model_id = jo['model_id']
        metric = jo['metric']
        max_value = jo.get('max_value', True)  # 默认取指标
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    model_paths = []
    try:
        model_paths = db.inquire_model_by_metrics(metric, model_id)
        log.info(model_paths)
    except Exception as x:
        return _rjson(None, 403, "select model_path form table dev_model_version failed, [%s]" % str(x))
    if max_value:
        return _rjson({"model_path": model_paths[-1]})
    else:
        return _rjson({"model_path": model_paths[0]})


def get_configparam_by_modeltype():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter get_configparam_by_modeltype...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        model_type = jo['model_type']
        task_name = jo['model_code']
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # 解析配置文件，读取model_type对应的配置参数
    # 拼接预先定义封装的代码路径
    task_name_dir = os.path.join(_conf["nni_code"], task_name, "define.json")
    with open(task_name_dir, 'r', encoding='utf8') as fi:
        infos = json.load(fi)
        configparam = infos.get("input_params", [])
    return _rjson({"configparam": configparam,
                   "desc_metrics": infos.get('desc_metrics', '[not available]'),
                   "desc_hyperparams": infos.get('desc_hyperparams', '[not available]')})


class Proc:
    def __init__(self, stdin, stdout, stderr):
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr


class NNI_Task:
    def __init__(self, local_conf, cont_addr, task_id, exp_name=None, user_name=None, node_id=None, dep_path=None):
        self._conf = local_conf
        self.user_name = user_name
        self.exp_name = exp_name
        self.nt = self.__conn_cont(local_conf, cont_addr, task_id, node_id, exp_name, dep_path)

    # def __init__(self, local_conf, cont_addr, exp_name, run_id, user_name=None, dep_path=None):

    # self.params = params
    # if task_name is None:
    #     self.task_conf = None
    # else:
    #     self.task_name = task_name
    #     self.task_path = f"{self._conf['task_base']}/{task_name}"
    #     with open(f"{self.task_path}/define.json", "r", encoding="utf8") as fi:
    #         self.task_conf = json.load(fi)

    def __conn_cont(self, _conf, cont_addr, task_id, node_id, exp_name, dep_path):
        host, port = cont_addr.split(":", maxsplit=1)
        nt = NodeTask(wf_id='123', task_id=task_id, proc_id="2", node_id=node_id,
                      conninfo=dict(
                          host=host, port=int(port),
                          username='_base_', client_keys=[_conf['CONT_IDENTITY']], known_hosts=None), exp_name=exp_name,
                      user_name=self.user_name)
        nt.connect(dep_path)
        return nt

    def close(self):
        self.nt.disconnect()

    def get_dataset(self, dataset):
        '在容器中下载指定数据集到， 其中的文件存放到 $LOG_DIR_C/data 目录下'
        if dataset is None:
            log.warning("dataset is None")
            return
        # put id file
        self.nt._scp(self._conf['FS_IDENTITY'], f"{self.nt.log_dir_c}/.id_1", is_upload=True, is_dir=False)
        if dataset.startswith("/"):  # 判断database是否为数据集在文件服务器的全路径
            ds_path = dataset
        else:
            ds_path = f"/data/.datasets/{self.nt.user_name}/{dataset}"
        fs_host, fs_port = self._conf['FS_ADDR'].split(":", maxsplit=1)
        try:
            out = self.nt._exec_(
                f"chmod 600 .id_1 && ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p {fs_port} -i .id_1"
                f" {self._conf['FS_USER']}@{fs_host} ls {ds_path}")
        except Exception as x:
            log.error(x)
        '''
        out:
        no -p 39913 -i .id_1 _dd_@172.18.243.23 ls /data/.datasets/congpq/train/stsFile=/dev/null -o StrictHostKeyChecking=n
        Warning: Permanently added '[172.18.243.23]:39913' (RSA) to the list of known hosts.
        ls: cannot access '/data/.datasets/congpq/train/': No such file or directory
        '''
        # 判断文件不存在
        if out.find("cannot access") >= 0:
            return False

        # if out.find("images")<0:
        #     # ds_path下有images、labels、classes.txt
        #     ds_path = os.path.join(ds_path,out.split("  ")[-2].split("\r\r\n")[-1])# 若上层目录中无images，则取子层目录
        try:
            out = self.nt._exec_(f"chmod 600 {self.nt.log_dir_c}/.id_1 && rm -rf ./data && scp -q -r"
                                 f" -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -P {fs_port} "
                                 f"-i {self.nt.log_dir_c}/.id_1 "
                                 f"{self._conf['FS_USER']}@{fs_host}:{ds_path} ./data "
                                 f"&& rm -rf {self.nt.log_dir_c}/.id_1")
        except Exception as x:
            logger.error(x)
        out = self.nt._exec_(f"ls -l ./data")
        fl = out.splitlines()
        if len(fl) == 4 and fl[2][0] == 'd':
            dname = fl[2].split()[-1]
            self.nt._exec_(f"mv ./data/{dname}/* ./data")
        return True

    def save_data_to_file(self, data, remote_path):
        fname = os.path.basename(remote_path)
        tmp_fname = f'/tmp/{fname}'
        with open(tmp_fname, 'w', encoding='utf-8') as fo:
            fo.write(data)
        self.nt._scp(tmp_fname, remote_path, is_upload=True, is_dir=False)

    def start(self, task_name, params):
        '开启任务的进程'
        # params = self.params
        # t_conf = self.task_conf
        task_path = f"{self._conf['task_base']}/{task_name}"
        with open(f"{task_path}/define.json", "r", encoding="utf8") as fi:
            t_conf = json.load(fi)

        nt = self.nt

        # done 检查是否已经有进程 nnictl 进程在运行中,  check if the container was occupied
        out = nt._exec_("ps -ef|grep nni_node/node")
        if out.find("site-packages") > 0:
            log.error("contrainer had a trainning process already. can not continue.")
            return None

        # 1.1 check pyenv,
        if 'pyenv' in t_conf:
            nt.source_act(t_conf.get('pyenv'))
        # nni进程启动前, 训练任务的前置处理过程(check pyenv)记录到表 dev_run 的 op_note 中
        try:
            ret = db.update_bak1_to_devRunNode("check pyenv", self.nt.task_id, self.exp_name)
            log.info(
                f"update bak_1:check pyenv to table dev_run columns bak_1 where run_id={self.nt.task_id} and exp_name={self.exp_name}")
        except Exception as x:
            log.error("update bak_1 to table dev_run columns OP_NOTE error,Reason：[%s]" % str(x))

        # 2 检查训练依赖环境
        dep = t_conf.get("dependency", None)
        if dep:
            log.info("install dependencies: {}", dep)
            nt.install_dependency(dep, pypi_server=self._conf['pypi_server'])
        # nni进程启动前, 训练任务的前置处理过程(检查训练依赖环境)记录到表 dev_run 的 op_note 中
        try:
            ret = db.update_bak1_to_devRunNode("check pyenv\n检查训练依赖环境", self.nt.task_id, self.exp_name)
            log.info(
                f"update bak_1:check pyenv\n检查训练依赖环境 to table dev_run columns bak_1 where run_id={self.nt.task_id} and exp_name={self.exp_name}")
        except Exception as x:
            log.error("update bak_1 to table dev_run columns bak_1 error,Reason：[%s]" % str(x))

        # 3 上传任务目录文件
        nt._exec_("rm -rf .task")
        nt._scp(task_path, f"{nt.log_dir_c}/", is_upload=True, is_dir=True)
        nt._exec_(f"mv {task_name} .task")
        # nni进程启动前, 训练任务的前置处理过程(上传任务目录文件)记录到表 dev_run 的 op_note 中
        try:
            ret = db.update_bak1_to_devRunNode("check pyenv\n检查训练依赖环境\n上传任务目录文件", self.nt.task_id, self.exp_name)
            log.info(
                f"update bak_1:check pyenv\n检查训练依赖环境\n上传任务目录文件 to table dev_run columns bak_1 where run_id={self.nt.task_id} and exp_name={self.exp_name}")
        except Exception as x:
            log.error("update bak_1 to table dev_run columns bak_1 error,Reason：[%s]" % str(x))

        # 4 将任务参数存放到环境变量中
        for k, v in params.items():
            nt._exec_(f"export _param_{k}={v}")
        # nni进程启动前, 训练任务的前置处理过程(将任务参数存放到环境变量中)记录到表 dev_run 的 op_note 中
        try:
            ret = db.update_bak1_to_devRunNode("check pyenv\n检查训练依赖环境\n上传任务目录文件\n将任务参数存放到环境变量中", self.nt.task_id,
                                               self.exp_name)
            log.info(
                f"update bak_1:check pyenv\n检查训练依赖环境\n上传任务目录文件\n将任务参数存放到环境变量中 to table dev_run columns bak_1 where run_id={self.nt.task_id} and exp_name={self.exp_name}")
        except Exception as x:
            log.error("update bak_1 to table dev_run columns bak_1 error,Reason：[%s]" % str(x))

        # 5.0 检查配置中是否有需要下载数据集， 有则下载
        vname = t_conf.get("load_dataset", None)
        if vname:
            ds_name = params.get(vname, None)
            if ds_name:
                log.info(f"download dataset {ds_name}")
                ret = self.get_dataset(ds_name)
                if ret is False:
                    try:
                        db.update_bak1_to_devRunNode(
                            f"download dataset  {ds_name}  failed,Reason：No such file or directory", self.nt.task_id,
                            self.exp_name)
                    except Exception as x:
                        log.error(f"unpdate bak_1 to devRun failed, Reason: {x}")
                        return
                    log.info(f"download dataset {ds_name}, Reason: No such file or directory")
                    return
        # nni进程启动前, 训练任务的前置处理过程(检查配置中是否有需要下载数据集， 有则下载)记录到表 dev_run 的 op_note 中
        try:
            ret = db.update_bak1_to_devRunNode("check pyenv\n检查训练依赖环境\n上传任务目录文件\n将任务参数存放到环境变量中\n检查配置中是否有需要下载数据集， 有则下载",
                                               self.nt.task_id, self.exp_name)
            log.info(
                f"update bak_1:check pyenv\n检查训练依赖环境\n上传任务目录文件\n将任务参数存放到环境变量中\n检查配置中是否有需要下载数据集， 有则下载 to table dev_run columns bak_1 where run_id={self.nt.task_id} and exp_name={self.exp_name}")
        except Exception as x:
            log.error("update bak_1 to table dev_run columns bak_1 error,Reason：[%s]" % str(x))

        # 5.1 检查并执行预处理脚本
        if os.path.exists(f"{task_path}/preprocess.py"):
            # 切换目录是为了让 preprocess.py 与 train.py 所看到的data目录位置相同（../data）
            out = nt._exec_("cd .task && python preprocess.py && cd ..")
            if out.find('Traceback (most recent call last)') >= 0:
                try:
                    db.update_bak1_to_devRunNode(out[-200:], self.nt.task_id, self.exp_name)
                except Exception as x:
                    log.error(x)
                log.error("preprocess 处理失败!")
                return None
        # nni进程启动前, 训练任务的前置处理过程(检查并执行预处理脚本， 有则下载)记录到表 dev_run 的 op_note 中
        try:
            ret = db.update_bak1_to_devRunNode(
                "check pyenv\n检查训练依赖环境\n上传任务目录文件\n将任务参数存放到环境变量中\n检查配置中是否有需要下载数据集， 有则下载\n检查并执行预处理脚本", self.nt.task_id,
                self.exp_name)
            log.info(
                f"update bak_1:check pyenv\n检查训练依赖环境\n上传任务目录文件\n将任务参数存放到环境变量中\n检查配置中是否有需要下载数据集， 有则下载\n检查并执行预处理脚本， 有则下载 to table dev_run columns bak_1 where run_id={self.nt.task_id} and exp_name={self.exp_name}")
        except Exception as x:
            log.error("update bak_1 to table dev_run columns bak_1 error,Reason：[%s]" % str(x))

        # done 6.0 检查并改写 config.yaml
        tmp_file = f"/tmp/{time.time()}.yaml"
        with open(f"{task_path}/config.yaml", "r", encoding="utf8") as fi:
            cc = yaml.load(fi)
        cmd = cc.get('trial', {}).get('command', '')
        _modified = False
        if "max_duration" in params:  # 检查设置最长训练时间（单次）
            # cc['maxExecDuration'] = f"{cc['max_duration']}m"
            cc['maxExecDuration'] = params["max_duration"] + "m"
            _modified = True
        if cmd.find('{') >= 0 and cmd.find('}'):  # 检查并替换 trial.command 中的变量
            cmd = cmd.format(**params)
            cc['trial']['command'] = cmd
            _modified = True
        # done 调整启动进程的处理， 是否需要启动tensorboard
        if t_conf.get("start_tensorboard", True):
            nt._exec_(f"export _TB_PORT={self._conf['tensorboard_port']}")  # 把tb的端口放到环境变量中
            # 替换trial.command， 在前面添加 python runcmd.py
            cc['trial']['command'] = f"python runcmd.py {cmd}"
            # 上传 runcmd.py 到 .task 目录下
            nt._scp(f"{self._conf['task_base']}/runcmd.py", f"{nt.log_dir_c}/.task/", is_upload=True, is_dir=False)
            _modified = True

            # 上传并启动 nginx
            nt._scp(f"{self._conf['task_base']}/ng1", f"{nt.log_dir_c}/.task/", is_upload=True, is_dir=True)
            nt._exec_(f"chmod +x .task/ng1/sbin/nginx && .task/ng1/sbin/nginx -p .task/ng1")
        if _modified:
            with open(tmp_file, 'w', encoding='utf8') as fo:
                yaml.dump(cc, fo)
            nt._scp(tmp_file, f"{nt.log_dir_c}/.task/config.yaml", is_upload=True, is_dir=False)
        # nni进程启动前, 训练任务的前置处理过程(检查并改写 config.yaml)记录到表 dev_run 的 op_note 中
        try:
            ret = db.update_bak1_to_devRunNode(
                "check pyenv\n检查训练依赖环境\n上传任务目录文件\n将任务参数存放到环境变量中\n检查配置中是否有需要下载数据集， 有则下载\n检查并执行预处理脚本\n检查并改写 config.yaml",
                self.nt.task_id, self.exp_name)
            log.info(
                f"update bak_1:check pyenv\n检查训练依赖环境\n上传任务目录文件\n将任务参数存放到环境变量中\n检查配置中是否有需要下载数据集， 有则下载\n检查并执行预处理脚本\n检查并改写 config.yaml to table dev_run columns bak_1 where run_id={self.nt.task_id} and exp_name={self.exp_name}")
        except Exception as x:
            log.error("update bak_1 to table dev_run columns bak_1 error,Reason：[%s]" % str(x))

        # 6 启动训练任务
        out = nt._exec_(f"nnictl create --config {nt.log_dir_c}/.task/config.yaml --port {self._conf['nni_port']} 2>&1",
                        remove_str='\x1b\\[\\d+m')
        tag = "The experiment id is "
        p = out.find(tag)
        if p < 0:
            log.error("Experiment startup failed")
            return None
        else:
            p += len(tag)
            exp_id = out[p: out.find('\n', p)]
            # 更新实验id exp_id如表dev_run、字段WF_ID
            try:
                ret = db.update_dev_run_expId("nni-task", self.nt.task_id, self.exp_name)
                log.info(
                    f"update exp_id:nni-task to table dev_run columns WF_ID where task_id={self.nt.task_id} and exp_name={self.exp_name}")
            except Exception as x:
                log.error("update exp_id to table dev_run columns WF_ID error,Reason：[%s]" % str(x))
            # 更新nni任务启动状态, dev_run表, RUN_STATUS：01
            try:
                ret = db.devc_update_devRun_nodeStatus("01", self.nt.task_id, self.exp_name, op_note="")
                log.info(
                    f"update run_status:01 to table dev_run columns RUN_STATUS where run_id={self.nt.task_id} and exp_name={self.exp_name}")
            except Exception as x:
                log.error("update run_status to table dev_run columns RUN_STATUS error,Reason：[%s]" % str(x))
            # 更新nni任务启动状态：dev_run_node表, RUN_STATUS：01
            try:
                ret = db.update_dev_run_node_status("01", self.nt.task_id, self.exp_name, self.nt.node_id,
                                                    bak_1="check pyenv\n检查训练依赖环境\n上传任务目录文件\n将任务参数存放到环境变量中\n检查配置中是否有需要下载数据集， 有则下载\n检查并执行预处理脚本\n检查并改写 config.yaml\n启动训练任务")
                log.info(
                    f"update run_status:01 to table dev_run_node columns RUN_STATUS where run_id={self.nt.task_id} and exp_name={self.exp_name} and node_id={self.nt.node_id}")
            except Exception as x:
                log.error("update run_status to table dev_run_node columns RUN_STATUS error,Reason：[%s]" % str(x))
            log.info("---exp_id: {}", exp_id)
            return exp_id

    def check_status(self):
        out = self.nt._exec_("curl http://localhost:8787/api/v1/nni/check-status && echo", out_remove_fistline=True)
        try:
            r = json.loads(out)
            return r
        except Exception as x:
            logger.error(f"-- check_status failed: {x}")
            return None

    def get_trials(self):
        out = self.nt._exec_("curl http://localhost:8787/api/v1/nni/trial-jobs && echo", out_remove_fistline=True)
        try:
            r = json.loads(out)
            return r
        except Exception as x:
            logger.error(f"-- get trials failed: {x}")
            return None

    def get_trials_log(self, ip_address, service_port, trial_id):
        out = self.nt._exec_("curl http://{ip_address}:{service_port}/api/v1/nni/trial-log/{trial_id}/TRIAL_ERROR",
                             out_remove_fistline=True)
        try:
            r = json.loads(out)
            return r
        except Exception as x:
            log.error("-- get trials failed: {}", x)
            return None

    def stop_task(self, task_name):
        '停止任务， 如果使用了pyenv， 需要先进行切换'
        nt = self.nt
        task_path = f"{self._conf['task_base']}/{task_name}"
        with open(f"{task_path}/define.json", "r", encoding="utf8") as fi:
            t_conf = json.load(fi)
        if 'pyenv' in t_conf:
            nt.source_act(t_conf.get('pyenv'))

        if t_conf.get("start_tensorboard", False):
            # 停止nginx
            nt._exec_(f".task/ng1/sbin/nginx -p .task/ng1 -s stop")

        out = nt._exec_("nnictl stop", remove_str='\x1b\\[\\d+m')  # 去掉termcolor 标记
        log.info(out)
        p = out.find("Stop experiment success.")
        if p < 0:
            log.error("Experiment stop failed")
        else:
            log.info(out)

    def model_export(self, nni_exp_id, nni_trial_id, model_id, model_ver):
        '模型导出， 其中 nni_exp_id 可以为None， nni_trial_id 也可以为 None， 前者为None则选择当前环境中最新的exp ID， 后者为None时， 则选择最新完成的trial'
        nt = self.nt
        _exec = nt._exec_

        # 获取任务配置
        out = nt._exec_('cat .task/define.json && echo', out_remove_fistline=True)
        t_conf = json.loads(out)

        if nni_exp_id is None:
            # find the latest experiment
            out = _exec(f"cat ~/nni-experiments/.experiment && echo", out_remove_fistline=True)
            try:
                s = json.loads(out)
            except:
                log.error("can not found experiment list")
                return False
            elist = [[opts['id'], opts['startTime'], opts['status']] for opts in s.values()]
            elist = sorted(elist, key=operator.itemgetter(1))
            exp = elist[-1]
            nni_exp_id = exp[0]
            log.info("found latest exp-id: {}", nni_exp_id)

        if nni_trial_id is None:
            # find the latest trial,  But how to determine if the trial is finished successfully?
            out = _exec(f"ls -lrt ~/nni-experiments/{nni_exp_id}/trials/", out_remove_fistline=True)
            nni_trial_id = out.strip().splitlines()[-1].split()[-1]
            log.info("found latest trial-id: {}", nni_trial_id)

        # 创建目录， 准备好导出模型相关的文件后， 再 scp 到对应文件服务器上
        m_dir = f"{model_id}_{model_ver}"
        nt._exec_(f"rm -rf {m_dir} && mkdir -p {m_dir}/data")
        for f in t_conf['pred_files']:
            nt._exec_(f"cp -R .task/{f} {m_dir}/")
        nt._exec_(f"cp -r ~/nni-experiments/{nni_exp_id}/trials/{nni_trial_id}/* {m_dir}/data/")

        # 保存模型训练的参数， 这个参数之前保存在哪里？  由train.py 决定的
        # generate start_script
        ss = t_conf['pred_files'][0]
        if ss[-3:] == '.py':
            nt._exec_(f"echo 'python {ss} $@' > {m_dir}/start_script")
        else:
            nt._exec_(f"echo 'sh ./{ss} $@' > {m_dir}/start_script")

        # done 保存 pred_dependency 到目录中， 以便部署的时候能预装依赖模块。
        # 额外保存 pyenv
        t = {"dependency": t_conf['pred_dependency']}
        if "pyenv" in t_conf:
            t['pyenv'] = t_conf['pyenv']
        self.save_data_to_file(json.dumps(t), f"{nt.log_dir_c}/{m_dir}/dependency.json")

        # 上传目录到 文件服务器
        nt._scp(self._conf['FS_IDENTITY'], f"{nt.log_dir_c}/.id_1", is_upload=True, is_dir=False)
        model_path = f"/data/.models/{nt.user_name}"
        fs_host, fs_port = self._conf['FS_ADDR'].split(":", maxsplit=1)
        # mkdir of model path
        nt._exec_(
            f"chmod 600 .id_1 && ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p {fs_port} -i .id_1"
            f" {self._conf['FS_USER']}@{fs_host} rm -rf {model_path}/{m_dir}")
        nt._exec_(f"ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p {fs_port} -i .id_1"
                  f" {self._conf['FS_USER']}@{fs_host} mkdir -p {model_path}")
        out = nt._exec_(f"chmod 600 .id_1 "
                        f"&& scp -r "
                        f" -q -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -P {fs_port} "
                        f"-i {self.nt.log_dir_c}/.id_1 "
                        f" {m_dir} {self._conf['FS_USER']}@{fs_host}:{model_path}/ "
                        f" && rm -rf {self.nt.log_dir_c}/.id_1")
        print(out)
        print(nt._exec_(f"rm -rf {m_dir}"))

    def model_deploy(self, model_id, model_ver=None):
        '将指定的模型（之前导出的）部署到指定主机上，暂时启动为服务'
        # done 模型部署并启动服务
        log.info("--model_deploy %s %s", model_id, model_ver)
        nt = self.nt

        # 0 check if the nginx is deployed
        out = nt._exec_(f"ls -l /data/ng1")
        if out.find("cannot access") > 0:
            # 上传并启动 nginx
            nt._scp(f"{self._conf['task_base']}/ng1", f"/data", is_upload=True, is_dir=True)
            out = nt._exec_(f"chmod +x /data/ng1/sbin/nginx && /data/ng1/sbin/nginx -p /data/ng1")
            print(out)

        # 1 下载模型文件
        nt._scp(self._conf['FS_IDENTITY'], f"{self.nt.log_dir_c}/.id_1", is_upload=True, is_dir=False)
        fs_host, fs_port = self._conf['FS_ADDR'].split(":", maxsplit=1)
        fs_user = self._conf['FS_USER']
        if model_ver is None:  # Find the latest version
            dpath = f"/data/.models/{nt.user_name}/"
            out = nt._exec_(
                f"chmod 600 .id_1 && ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p {fs_port} -i .id_1"
                f" {self._conf['FS_USER']}@{fs_host} ls -lrt {dpath}")
            m = f"{model_id}_"
            for l in out.splitlines():
                if l.find(m) > 0:
                    data_path = l.strip().split()[-1]
        else:
            data_path = f"/data/.models/{nt.user_name}/{model_id}_{model_ver}"
        dep_path = model_id
        log.info("src_path: %s, dst_path: [%s]", data_path, dep_path)
        out = nt._exec_(f"chmod 600 .id_1 && rm -rf ./{dep_path} && scp -r"
                        f" -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -P {fs_port} "
                        f"-i {nt.log_dir_c}/.id_1 "
                        f"{fs_user}@{fs_host}:{data_path} ./{dep_path} "
                        f"&& rm -rf {nt.log_dir_c}/.id_1")

        # 2 检查安装依赖
        out = nt._exec_(f'cat {dep_path}/dependency.json && echo', out_remove_fistline=True)
        log.info("dependency.json [%s]", out)
        dep_obj = json.loads(out)
        print("---dependency:", dep_obj)
        if "pyenv" in dep_obj:
            nt.source_act(dep_obj.get('pyenv'))
        nt.install_dependency(dep_obj['dependency'], pypi_server=self._conf['pypi_server'])

        # 3 Pick a random port number, as restful service port
        random.seed(time.time())
        port_number = random.randint(12048, 13048)

        # 4 Start the service
        out = nt.run_cmd(f'cd ./{dep_path} && nohup sh start_script {port_number}')
        print(out)

        # 5 register the port to nginx
        try:
            nt._exec_(f"python /data/ng1/ng.py add {model_id} {port_number}")
            log.info(f"register the port to nginx: python /data/ng1/ng.py add {model_id} {port_number}")
        except Exception as x:
            log.error(f"register the port to nginx error: python /data/ng1/ng.py add {model_id} {port_number}")

    def model_deploy_train_failed(self, model_id, run_id, model_ver=None):
        '将指定的模型（之前导出的）部署到指定主机上，暂时启动为服务'
        # done 模型部署并启动服务
        nt = self.nt

        # 0 check if the nginx is deployed
        out = nt._exec_(f"ls -l /data/ng1")
        if out.find("cannot access") > 0:
            # 上传并启动 nginx
            nt._scp(f"{self._conf['task_base']}/ng1", f"/data", is_upload=True, is_dir=True)
            out = nt._exec_(f"chmod +x /data/ng1/sbin/nginx && /data/ng1/sbin/nginx -p /data/ng1")

        # 1 下载模型文件
        nt._scp(self._conf['FS_IDENTITY'], f"{self.nt.log_dir_c}/.id_1", is_upload=True, is_dir=False)
        fs_host, fs_port = self._conf['FS_ADDR'].split(":", maxsplit=1)
        fs_user = self._conf['FS_USER']
        if model_ver is None:  # Find the latest version
            dpath = f"/data/.models/{nt.user_name}/"
            out = nt._exec_(
                f"chmod 600 .id_1 && ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p {fs_port} -i .id_1"
                f" {self._conf['FS_USER']}@{fs_host} ls -lrt {dpath}")
            m = f"{model_id}_"
            for l in out.splitlines():
                if l.find(m) > 0:
                    data_path = l.strip().split()[-1]
        else:
            data_path = f"/data/.models/{nt.user_name}/{model_id}_{model_ver}"
        dep_path = model_id
        log.info(f"{fs_user}@{fs_host}:{data_path} ./{dep_path}")
        out = nt._exec_(f"chmod 600 .id_1 && rm -rf ./{dep_path} && scp -r"
                        f" -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -P {fs_port} "
                        f"-i {nt.log_dir_c}/.id_1 "
                        f"{fs_user}@{fs_host}:{data_path} ./{dep_path} "
                        f"&& rm -rf {nt.log_dir_c}/.id_1")
        # 2 register the error logs to nginx
        try:
            out = nt._exec_(f"python /data/ng1/ng.py add_error_log {model_id} {run_id} {model_ver}")
            log.info(
                f"register the error logs to nginx: python /data/ng1/ng.py add_error_log {model_id} {run_id} {model_ver}")
        except Exception as x:
            log.error(
                f"register the error logs to nginx error: python /data/ng1/ng.py add_error_log {model_id} {run_id} {model_ver}")

    def model_show_tensorboard(self, exp_name, run_id=None):
        'start a tensorboard process in test container, to view the train scalar data'
        # through python /data/ng1/ng.py start-tb <path to tb data>
        # auto shutdown in 5 min
        nt = self.nt

        # 0 check if the nginx is deployed
        out = nt._exec_(f"ls -l /data/ng1")
        if out.find("cannot access") > 0:
            # 上传并启动 nginx
            nt._scp(f"{self._conf['task_base']}/ng1", f"/data", is_upload=True, is_dir=True)
            out = nt._exec_(f"chmod +x /data/ng1/sbin/nginx && /data/ng1/sbin/nginx -p /data/ng1")
            print(out)
        else:
            # only update ng.py
            nt._scp(f"{self._conf['task_base']}/ng1/ng.py", f"/data/ng1/", is_upload=True, is_dir=False)

        # 1 下载模型文件
        nt._scp(self._conf['FS_IDENTITY'], f"{self.nt.log_dir_c}/.id_1", is_upload=True, is_dir=False)
        fs_host, fs_port = self._conf['FS_ADDR'].split(":", maxsplit=1)
        fs_user = self._conf['FS_USER']
        if run_id is None or run_id == '':  # Find the latest version
            dpath = f"/data/.models/{nt.user_name}/"
            out = nt._exec_(
                f"chmod 600 .id_1 && ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p {fs_port} -i .id_1"
                f" {self._conf['FS_USER']}@{fs_host} ls -lrt {dpath}")
            m = f"{exp_name}_"
            for l in out.splitlines():
                if l.find(m) > 0:
                    data_path = l.strip().split()[-1].split('/')[-1]
                    data_path = f"{dpath}{data_path}"
        else:
            data_path = f"/data/.models/{nt.user_name}/{exp_name}_{run_id}"
        dep_path = f"{exp_name}_tb"
        out = nt._exec_(f"chmod 600 .id_1 && rm -rf ./{dep_path} && scp -r"
                        f" -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -P {fs_port} "
                        f"-i {nt.log_dir_c}/.id_1 "
                        f"{fs_user}@{fs_host}:{data_path} ./{dep_path} "
                        f"&& rm -rf {nt.log_dir_c}/.id_1")
        out = nt._exec_(f"ls -l ./{dep_path}")
        if out.find("cannot access") >= 0:
            log.error("data get failed")
            return "can not find the train data"

        # 2 only switch venv
        out = "\n".join(nt._exec_(f'cat {dep_path}/dependency.json && echo').splitlines()[1:])
        dep_obj = json.loads(out)
        print("---dependency:", dep_obj)
        if "pyenv" in dep_obj:
            nt.source_act(dep_obj.get('pyenv'))

        # 3 Pick a random port number, as restful service port
        random.seed(time.time())
        port_number = random.randint(12048, 13048)

        # 4 start tensorboard and register the port to nginx
        ng_cmd = f"python /data/ng1/ng.py start_tb {exp_name}_tb {port_number} {nt.log_dir_c}/{dep_path} >/tmp/{exp_name}.log 2>&1 &"
        # logger.info("ng_cmd: {}", ng_cmd)
        out = nt._exec_(ng_cmd)
        log.info("deploy tensorboard finished!! %s", out)
        return None


class NodeTask:
    def __init__(self, wf_id, task_id, proc_id, node_id, conninfo, exp_name, user_name=None):
        self.wf_id = wf_id
        self.task_id = task_id
        self.proc_id = proc_id
        self.node_id = node_id
        self.exp_name = exp_name
        self.conninfo = conninfo
        self.local_base_dir = '.'
        self.ssh = None
        self._base_ = ''
        self._home_ = None
        self.user_name = user_name
        self._until_ = '[_PS1_]'

    def connect(self, wd=None, run_env=None):
        ssh = paramiko.SSHClient()

        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cc = self.conninfo
        host, port, username = cc['host'], cc['port'], cc['username']
        privkey = paramiko.RSAKey.from_private_key_file(cc['client_keys'][0])
        ssh.connect(host, port=int(port), username=username, pkey=privkey)
        print("connected!")
        self.ssh = ssh
        _exec = self._exec_

        # env include PATH not set, only HOME and TERM
        proc = Proc(*ssh.exec_command("bash -s"))
        self.proc = proc

        out = self._exec_('export PS1=%s\\\\n' % self._until_)
        print('---set PS1', out)

        # determine the work directory
        if wd is not None and wd != '':
            if wd[:2] == '~/':
                out = self._exec_('echo $LOGDIR')
                self.log_dir = out.strip().split('\n')[-1].strip()
                self.log_dir_c = f"{self.log_dir}/{wd[2:]}"
            else:
                self.log_dir_c = wd
        else:
            out = self._exec_('echo $LOGDIR')
            self.log_dir = out.strip().split('\n')[-1].strip()
            if self.log_dir.find('$') >= 0:
                self.log_dir = '/data'
            self.log_dir_c = f"{self.log_dir}/{self.exp_name}-{self.node_id}"
        self._exec_(f"mkdir -p {self.log_dir_c}")
        self._exec_(f"cd {self.log_dir_c}")
        log.debug("logdir_c: {}", self.log_dir_c)

        out = self._exec_(f'export HOME={self.log_dir_c} '
                          f'&& export PATH=$PATH:{self.log_dir_c}/.local/bin:/data/.local/bin'
                          )

        if self.user_name is None:
            out = self._exec_("echo $_USERNAME_")
            self.user_name = out.splitlines()[-1]
        else:
            _exec(f"export _USERNAME_={self.user_name}")

        # set task environments
        if wd is not None and wd != '':
            pass
        else:
            envs = [f"LOGDIR_C=\"{self.log_dir_c}\"", f"export PROC_ID=\"{self.proc_id}\"",
                    f"WF_ID=\"{self.wf_id}\"", f"TASK_ID=\"{self.task_id}\"",
                    f"export NODE_ID=\"{self.node_id}\"", f"_base_=\"{self.log_dir_c}\""]
            for env in envs:
                self._exec_(f"export {env}")
        if run_env is not None:
            for k, v in run_env.items():
                self._exec_(f"export {k}=\"{v}\"")

    def _exec_(self, cmd, ln_limit=-1, outs=None, remove_str=None, out_remove_fistline=False):
        proc = self.proc
        proc.stdin.write(cmd + '\n')
        print('====', cmd)
        if outs is None:
            outs = []
        if ln_limit > 0:
            for i in range(ln_limit):
                ln = proc.stdout.readline()
                outs.append(ln)
        else:
            if remove_str:
                remove_str = re.compile(remove_str)
            while True:
                ln = proc.stdout.readline()
                if remove_str:
                    # print('---: [%s]' % ln, ln.strip()==self._until_, repr(ln.strip()), repr(self._until_))
                    ln = re.sub(remove_str, '', ln)
                if ln.strip() == self._until_:
                    break
                outs.append(ln)
        if out_remove_fistline:
            return "".join(outs[1:])
        else:
            return "".join(outs)

    def source_act(self, name):
        out = self._exec_('source activate %s && export PS1=%s\\\\n' % (name, self._until_))
        return out

    def change_home(self, newhome):
        self.log_dir_c = newhome
        self._exec_(f"mkdir -p {self.log_dir_c}")
        self._exec_(f"cd {self.log_dir_c}")

        self._exec_(f'export HOME={self.log_dir_c} '
                    f'&& export PATH=$PATH:{self.log_dir_c}/.local/bin:/data/.local/bin'
                    )

    def disconnect(self):
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.ssh.close()

    def install_dependency(self, dep, pypi_server=None):
        '''
- dependency
  - package\[\]  预定义的依赖包
    - name 包的名称, 包的定义中, 包含了相关文件和安装脚本, 以及环境变量
    - target_path  包的安装路径
  - pip\[\]  python包, 每个包的格式: `<pkg-name>[==<ver>]`
  - files\[\] 依赖的文件或目录
    - path   源文件或目录
    - target_path  文件上传后的目标路径, only for sftp
        '''
        self._dep = dep
        if 'files' in dep:
            for p in dep['files']:
                self.install_file(p['path'], p['target_path'])
        if 'package' in dep:
            for p in dep['package']:
                self.install_pkg(p['name'], target_path=p['target_path'])
        if 'pip' in dep:
            self.install_pip(dep['pip'], pypi_server)

    def install_pip(self, pip_list, pypi_server):
        '''install pip packages'''
        if pypi_server is None or pypi_server == '':
            log.warning("install_pip but without pypi_server given")
            return
        r = re.match(r'http://([\d.]+):\d+', pypi_server)
        if r is None:
            log.warning("pypi_server {} not valid", pypi_server)
            return
        pypi_host = r.group(1)
        p = " ".join([f"'{i}'" for i in pip_list])

        # --user conflicted with HOME=/,  can not read stderr
        cmd = f"pip install -i {pypi_server} --trusted-host {pypi_host} -q --user {p}"
        out = self._exec_(cmd)
        print(out)
        # print("stderr", self.proc.stderr.readline() )

    def _scp(self, src, dst, is_upload=True, is_dir=True):
        c = self.conninfo
        if is_upload:
            args = shlex.split(
                f"scp -q {'-r' if is_dir else ''} -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no "
                f"-P {c['port']} -i {c['client_keys'][0]} "
                f"{src} "
                f"{c['username']}@{c['host']}:{dst}")
        else:
            args = shlex.split(
                f"scp -q {'-r' if is_dir else ''} -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no "
                f"-P {c['port']} -i {c['client_keys'][0]} "
                f"{c['username']}@{c['host']}:{src} "
                f"{dst}")
        log.debug("scp cmd: [{}]", args)
        out = subprocess.check_output(args)
        print(out)

    def install_pkg(self, name, target_path=''):
        'upload package.tar.gz and unpack it'
        print('begin to install package', name)
        print(self._exec_("pwd"))

        t0 = time.time()
        self._scp(f"{self.local_base_dir}/{name}/package.tar.gz", f"{self.log_dir_c}/{target_path}/", is_upload=True)
        print('upload time', time.time() - t0)

        envfile = f"{self.local_base_dir}/{name}/env"
        if os.path.exists(envfile):
            for ln in open(envfile):
                if ln.strip() != '':
                    self._exec_(ln.strip())

        installfile = f"{self.local_base_dir}/{name}/install.sh"
        if os.path.exists(installfile):
            self._exec_(f"_target_={self.log_dir_c}/{target_path}")
            for ln in open(installfile):
                if ln.strip() != '':
                    self._exec_(ln.strip())

        print(self._exec_("echo $PATH $LOGDIR_C"))

    def install_file(self, path, target=''):
        fpath = f"{self.local_base_dir}/{self.wf_id}/{path}"
        if not os.path.exists(fpath):
            log.error("fpath: {} not exists", fpath)
            return
        self._scp(fpath, f"{self.log_dir_c}/{target}", is_upload=True)

    def uninstall_files(self, files):
        ff = []
        for p in files:
            ff.append(f"{self.log_dir_c}/{p['target_path']}/{p['path']}")
        self._exec_(f"rm -rf {' '.join(ff)}")

    def uninstall_pkg(self, pkg):
        for p in pkg:
            name, target_path = p['name'], p['target_path']
            uninstallfile = f"{self.local_base_dir}/{name}/uninstall.sh"
            if os.path.exists(uninstallfile):
                self._exec_(f"_target_={self.log_dir_c}/{target_path}")
                for ln in open(uninstallfile):
                    if ln.strip() != '':
                        self._exec_(ln.strip())

    def uninstall_pip(self, pip_list):
        self._exec_(f"rm -rf {self.log_dir_c}/.local/lib")

    def uninstall_dependency(self):
        '''remove dependency on exit'''
        if 'files' in self._dep:
            self.uninstall_files(self._dep['files'])
        if 'package' in self._dep:
            self.uninstall_pkg(self._dep['package'])
        if 'pip' in self._dep:
            self.uninstall_pip(self._dep['pip'])

    def run_cmd(self, cmd):
        'run cmd in background'
        out = self._exec_(f"{cmd} 1>stdout 2>stderr &")
        # [1] 1078
        jobid = out.strip().split('\n')[-1].strip()
        print('--- jobid', jobid)
        #    [1]      1078
        self.job_idx, self.job_pid = [i.strip() for i in jobid.split(maxsplit=1)]
        return self.job_pid

    def check_cmd(self):
        '''
        check the background job status,
         return (status, exit-code)
          status: running not-run done failed
          exit-code: 0 or not-0
        '''
        if not hasattr(self, 'job_idx') or self.job_idx is None:
            return 'not-run', 0
        out = self._exec_("jobs")
        for ln in out.split('\n'):
            if ln.startswith(self.job_idx):
                print(ln)
                # [1]+  Running                 ./a.sh &
                # [1]+  Exit 1                  ./a.sh
                # [1]+  Done                    sleep 5
                ps = ln.split()
                if ps[1] == 'Running':
                    return 'running', 0
                elif ps[1] == 'Done':
                    return 'done', 0
                elif ps[1] == 'Exit':
                    return 'failed', int(ps[2])
        return 'not-run', 0

    def kill_cmd(self):
        if hasattr(self, 'job_pid'):
            self._exec_(f"kill -9 {self.job_pid}")


# 启动nni任务
def start_task():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter start...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        run_id = jo['run_id']  # 此参数用作task_id
        exp_name = jo['exp_name']  # 实验名称
        task_name = jo['task_name']  # 任务标识id， 预先定义封装的代码组合， 按照分类配置到数据库中
        node_id = jo['node_id']  # 此处用run_id (对应ssh连接中的 node_id）
        user_name = jo['user_name']  # 启动任务的用户名
        params = jo['params']
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
        # 选择训练容器
    try:
        ret = db.train_pick_cont(user_name, exp_name, run_id)  # 返回值ret=cont_name, ip_addr, ctl_port, svc_port
    except Exception as x:
        log.error("train pick cont error: [%s]", traceback.format_exc())
        return _rjson(None, 403, "can not pick a train container")

    th = threading.Thread(target=_start, args=(run_id, node_id, exp_name, task_name, user_name, params, ret))
    th.start()
    return _rjson({})


def show_tb():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter start show_tb...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        run_id = jo['run_id']  # 此参数用作task_id
        exp_name = jo['exp_name']  # 实验名称
        user_name = jo['user_name']  # 启动任务的用户名
    except Exception as x:
        log.error("parse input failed [%s]", traceback.format_exc())
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # choice test container
    try:
        ret = db.test_pick_cont()  # 返回值ret=cont_name, ip_addr, ctl_port, svc_port
    except Exception as x:
        log.error("train pick cont error: [%s]" % str(x))
    container_name, ip_address, control_port, service_port = ret

    log.info("connect to test_container [%s]...", container_name)
    task = NNI_Task(_conf, f"{ip_address}:{control_port}", exp_name,
                    exp_name=exp_name, node_id=run_id, user_name=user_name,
                    dep_path=f"~/{exp_name}")
    log.info("test container connected, start tensorboard")
    ret = task.model_show_tensorboard(exp_name, run_id)
    task.close()
    if ret is not None:
        return _rjson(None, 403, "start tensorboard failed [%s]" % ret)
    return _rjson({"url": f"http://{ip_address}:{service_port}/{exp_name}_tb/"})


def _start(task_id, node_id, exp_name, task_name, user_name, params, ret):
    if ret is None:
        # 更新nni任务状态，无可用容器时，更新 07:预处理阶段异常结束
        try:
            ret = db.devc_update_devRun_nodeStatus("07", task_id, exp_name, op_note="No available container!")
            log.info(
                f"update run_status:07 op_note:Not available container! to table dev_run columns RUN_STATUS where run_id={task_id} and exp_name={exp_name}")
        except Exception as x:
            log.error("update run_status to table dev_run columns RUN_STATUS error,Reason：[%s]" % str(x))
        try:
            # 更新nni任务状态，无可用容器时，更新 07:预处理阶段异常结束
            ret = db.update_dev_run_node_status("07", task_id, exp_name, node_id, bak_1="Not available container!")
            log.info(
                f"update run_status:07 to table dev_run_node columns RUN_STATUS where task_id={task_id} and exp_name={exp_name} and node_id={node_id}")
        except Exception as x:
            log.error("update run_status to table dev_run_node columns RUN_STATUS error,Reason：[%s]" % str(x))
        return _rjson("No available container!")
    else:
        cont_name, ip_addr, ctl_port, svc_port = ret
        log.info("cont_name: {}, ip: {}", cont_name, ip_addr)
    # 启动nni进程
    cont_addr = ip_addr + ":" + str(ctl_port)
    try:
        task = NNI_Task(_conf, cont_addr, task_id=task_id, exp_name=exp_name, user_name=user_name, node_id=node_id, )
        exp_id = task.start(task_name=task_name, params=params)
        task.close()
    except Exception as x:
        log.error(f"start process error,Reason: {x}")
    # 训练容器中，task启动失败情况
    if exp_id is None:
        # 停止训练容器中进程
        log.info("Experiment startup failed")
        # 修改训练容器的占用状态
        try:
            db.update_dev_container_poole_container_status("0", cont_name)
            log.info(f"update table dev_container_pool set is_use=0 where cont_name={cont_name}")
        except Exception as x:
            log.error(f"update table dev_container_pool set is_use=0 where cont_name={cont_name} error: {x}")
        # 更新nni任务启动状态：dev_run_node表, RUN_STATUS：04 -- 异常结束
        try:
            ret = db.update_dev_run_node_status("07", task_id, exp_name, node_id)
            log.info(
                f"update run_status:07 to table dev_run_node columns RUN_STATUS where run_id={task_id} and exp_name={exp_name} and node_id={node_id}")
        except Exception as x:
            log.error("update run_status to table dev_run_node columns RUN_STATUS error,Reason：[%s]" % str(x))
        # 更新nni任务启动状态, dev_run表, RUN_STATUS：04 -- 异常结束
        try:
            ret = db.devc_update_devRun_nodeStatus("07", task_id, exp_name)
            log.info(
                f"update run_status:07 to table dev_run columns RUN_STATUS where run_id={task_id} and exp_name={exp_name}")
        except Exception as x:
            log.error("update run_status to table dev_run columns RUN_STATUS error,Reason：[%s]" % str(x))
        return
    # 更新训练容器名称进表dev_run_node、字段CONTAINER_NAME
    try:
        db.update_dev_run_node_container_name(cont_name, task_id, exp_name, node_id)
        log.info(
            f"update conName:{cont_name} to table dev_run_node where task_id={task_id} and exp_name={exp_name} and node_id={node_id}")
    except Exception as x:
        log.error("update_dev_run_node_container_name [%s]" % str(x))
    try:
        db.update_conName_to_devExperiment(cont_name, exp_name)
        log.info(f"update conName:{cont_name} to table devExperiment")
    except Exception as x:
        log.error("update conName to devExperiment failed:[%s]" % str(x))


# 停止nni任务
def stop_task():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter stop_task...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        cont_name = jo['cont_name']
        exp_name = jo['exp_name']
        node_id = jo['node_id']
        user_name = jo['user_name']
        task_name = jo['task_name']
        task_id = jo['run_id']  # 此处用run_id作为task_id使用
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # 根据cont_name获取cont_addr
    try:
        ret = db.get_contAddr_from_contName(cont_name)
    except Exception as x:
        log.error("update_dev_run_node_container_name [%s]" % str(x))
    if ret is None:
        return _rjson(f"Not found container {cont_name}")
    else:
        ip_addr, ctl_port = ret
        cont_addr = ip_addr + ":" + str(ctl_port)
    try:
        task = NNI_Task(_conf, cont_addr, task_id=task_id, exp_name=exp_name, user_name=user_name, node_id=node_id, )
        task.stop_task(task_name)
        task.close()
    except Exception as x:
        log.error(f"stop process error,Reason: {x}")
    return _rjson({})


def check_status():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter check_status...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        cont_name = jo['cont_name']
        exp_name = jo['exp_name']
        node_id = jo['node_id']
        task_id = jo['run_id']  # 此处用run_id作为task_id使用
        user_name = jo['user_name']
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # 根据cont_name获取cont_addr
    try:
        ret = db.get_contAddr_from_contName(cont_name)
    except Exception as x:
        log.error("update_dev_run_node_container_name [%s]" % str(x))
    if ret is None:
        return _rjson(f"Not found container {cont_name}")
    else:
        ip_addr, ctl_port = ret
        cont_addr = ip_addr + ":" + str(ctl_port)
    task = NNI_Task(_conf, cont_addr, task_id=task_id, exp_name=exp_name, user_name=user_name, node_id=node_id)
    r = task.check_status()
    task.close()
    if r is None:
        return _rjson("no task")
    else:
        return _rjson(r)


#
def get_trials():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter get_trials...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        cont_name = jo['cont_name']
        exp_name = jo['exp_name']
        node_id = jo['node_id']
        task_id = jo['run_id']  # 此处用run_id作为task_id使用
        user_name = jo['user_name']
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # 根据cont_name获取cont_addr
    try:
        ret = db.get_contAddr_from_contName(cont_name)
    except Exception as x:
        log.error("update_dev_run_node_container_name [%s]" % str(x))
    if ret is None:
        return _rjson(f"Not found container {cont_name}")
    else:
        ip_addr, ctl_port = ret
        cont_addr = ip_addr + ":" + str(ctl_port)
    task = NNI_Task(_conf, cont_addr, task_id=task_id, exp_name=exp_name, user_name=user_name, node_id=node_id)
    r = task.get_trials()
    task.close()
    if r is not None:
        return _rjson({"trials": r})
    else:
        return _rjson(None, 300, "no task running!")


# 模型保存
def model_export():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter model_export...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        cont_name = jo['cont_name']
        exp_name = jo['exp_name']
        node_id = jo['node_id']
        user_name = jo['user_name']
        task_id = jo['run_id']  # 此处用run_id作为task_id使用,模型上传至文件服务器的{run_id}/{node_id}目录下
        exp_id = jo['exp_id']
        trial_id = jo['trial_id']
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # 根据cont_name获取cont_addr
    try:
        ret = db.get_contAddr_from_contName(cont_name)
    except Exception as x:
        log.error("update_dev_run_node_container_name [%s]" % str(x))
    if ret is None:
        return _rjson(f"Not found container {cont_name}")
    else:
        ip_addr, ctl_port = ret
        cont_addr = ip_addr + ":" + str(ctl_port)
    try:
        task = NNI_Task(_conf, cont_addr, task_id=task_id, exp_name=exp_name, user_name=user_name, node_id=node_id)
        task.model_export(exp_id, trial_id, exp_name, task_id)
        task.close()
    except Exception as x:
        log.error(f"model export error,Reason: {x}")
    return _rjson({})


# 模型部署
def model_deploy():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter model_deploy...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        cont_name = jo['cont_name']
        run_id = jo['run_id']
        node_id = jo['node_id']
        exp_name = jo['exp_name']
        user_name = jo['user_name']
        exp_id = jo.get("exp_id", None)
        trial_id = jo.get("trial_id", None)
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # 根据cont_name获取cont_addr，即获取训练容器cont_addr_train
    try:
        ret = db.get_contAddr_from_contName(cont_name)
    except Exception as x:
        log.error("update_dev_run_node_container_name [%s]" % str(x))
    if ret is None:
        return _rjson(f"Not found container {cont_name}")
    else:
        ip_addr_train, ctl_port_train = ret
        cont_addr_train = ip_addr_train + ":" + str(ctl_port_train)
    # 选择部署容器
    try:
        ret = db.deploy_pick_cont(user_name, exp_name, run_id)  # 返回值ret=cont_name, ip_addr, ctl_port, svc_port
    except Exception as x:
        log.error("deploy pick cont error: [%s]" % str(x))
    if ret is None:
        return _rjson("Not available container!")
    else:
        cont_name_deploy, ip_addr_deploy, ctl_port_deploy, svc_port_deploy = ret
        log.info("cont_name: {}, ip: {}", cont_name_deploy, ip_addr_deploy)
    # 启动线程，执行部署服务
    cont_addr_deploy = ip_addr_deploy + ":" + str(ctl_port_deploy)
    th = threading.Thread(target=_model_deploy, args=(
        cont_addr_deploy, cont_addr_train, run_id, node_id, exp_name, user_name, cont_name_deploy, exp_id, trial_id))
    th.start()
    return _rjson({})


def _model_deploy(cont_addr_deploy, cont_addr_train, run_id, node_id, exp_name, user_name, cont_name, exp_id, trial_id):
    # 模型保存
    try:
        task = NNI_Task(_conf, cont_addr_train, task_id=run_id, exp_name=exp_name, user_name=user_name, node_id=node_id)
        task.model_export(exp_id, trial_id, exp_name, run_id)
        task.close()
    except Exception as x:
        log.error(f"model export error,Reason: {x}")
        return
    # 模型部署
    try:
        task = NNI_Task(_conf, cont_addr_deploy, task_id=run_id, exp_name=exp_name, user_name=user_name,
                        node_id=node_id)
        task.model_deploy(exp_name, run_id)
        task.close()
    except Exception as x:
        log.error(f"model deploy error,Reason: {x}")
        return
    try:
        db.update_conName_to_devExperiment(cont_name, exp_name)
        log.info(f"update conName:{cont_name} to table devExperiment")
    except Exception as x:
        log.error("update conName to devExperiment failed:[%s]" % str(x))


# 获取日志
def get_trials_log():
    try:
        jsons = request.stream.read().decode('utf-8')
        log.info("...enter get_trials_log...[%s]" % jsons)
        jo = json.loads(jsons)['BODY']
        ip_address = jo['ip_address']
        service_port = jo['service_port']
        trial_id = jo['trial_id']
    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # cmd = f"curl http://{ip_address}:{service_port}/api/v1/nni/trial-log/{trial_id}/TRIAL_ERROR"
    # log.info(cmd)
    # proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,shell=True)
    # stdout = proc.stdout.read().decode(encoding="utf-8")
    # return _rjson({"logs":stdout})

    out = []
    try:
        url = f"http://{ip_address}:{service_port}/api/v1/nni/trial-log/{trial_id}/TRIAL_ERROR"
        r = requests.get(url)
        if r.status_code == 200:
            out.append(r.text)
        else:
            out.append("TRIAL_ERROR failed %s" % r.status_code)

        out.append('\n')
        url = f"http://{ip_address}:{service_port}/api/v1/nni/trial-log/{trial_id}/TRIAL_LOG"
        r = requests.get(url)
        if r.status_code == 200:
            out.append(r.text)
        else:
            out.append("TRIAL_ERROR failed %s" % r.status_code)
    except Exception as x:
        return _rjson(None, 405, "Get LOG content failed [%s]" % str(x))

    return _rjson({"logs": "\n".join(out)})


def train_task_mon_once(local_conf, db):
    '检查一轮'
    ret = db.train_task_list()
    if ret is None:
        log.info("nothing found")
        return
    for r in ret:
        expe_name, run_id, begin_time, op_time, op_note, \
        container_name, ip_addr, ctl_port, svc_port, user_name, ext_info = r
        log.info(f"check train expe_name={expe_name} cont_addr: {container_name} {ip_addr}:{svc_port}")
        # check nni restful the experiment status
        url = f"http://{ip_addr}:{svc_port}/api/v1/nni/check-status"
        try:
            r = requests.get(url)
        except Exception as x:
            log.error(x)
            # done 更新任务运行状态为 结束
            if begin_time > 30:  # 超过30分钟, 则直接更改为结束, 避免一直扫描下去
                db.train_finish(container_name, 'check-status failed')
                db.train_task_update(expe_name, run_id, f'check-status failed, regard as finish', run_status='02')
            continue
        if r.status_code != 200:
            log.error(
                f"check status of expe: {expe_name} failed, [{r.status_code}--{r.text}] cont_name={container_name}, svc_addr: {ip_addr}:{svc_port}")
        else:
            ret = r.json()
            run_status, run_errors = ret['status'], ret['errors']
            log.info(f'expe {expe_name} run_status: {run_status}, {run_errors}')
            if run_status == 'DONE' and len(run_errors) == 0:  # 成功完成： 导出， 部署， 停止
                # 先检查 其唯一的 trial 是否正常结束
                run_success = False
                r = requests.get(f"http://{ip_addr}:{svc_port}/api/v1/nni/trial-jobs")
                if r.status_code == 200:
                    ret = r.json()
                    if len(ret) == 1:
                        t = ret[0]
                        if t['status'] == 'SUCCEEDED':
                            run_success = True
                            log_path = t['logPath'].split('/')
                            exp_id, trial_id = log_path[-3], log_path[-1]
                            log.info(f"task success, exp_id: {exp_id}, trial_id: {trial_id}")
                            ext_info = json.loads(ext_info)
                            '''ext_info返回案例：
                            ext_info: [
{'default': '1', 'name': 'epoch', 'type': '01', 'required': True, 'desc': '最大训练轮数', 'edit': True, 'value': '1', 'key': 'epoch', 'node_id': '-1', 'task_name': 'yolo5'},      {'default': '60', 'name': 'max_duration', 'type': '01', 'required': True, 'desc': '最大训练时长， 单位：分钟', 'edit': True, 'value': '60', 'key': 'max_duration', 'node_id': '-1', 'task_name': 'yolo5'}, 
{'default': 'ds_yolo', 'name': 'dataset', 'type': '01', 'required': True, 'desc': '数据集id', 'edit': True, 'value': 'ds_yolo', 'key': 'dataset', 'node_id': '-1', 'task_name': 'yolo5'}, 
{'default': '20', 'name': 'test_pct', 'type': '01', 'required': True, 'desc': '测试集比例，0~100， 建议20', 'edit': True, 'value': '20', 'key': 'test_pct', 'node_id': '-1', 'task_name': 'yolo5'}, 
{'default': '13', 'name': 'random_seed', 'type': '01', 'required': True, 'desc': '拆分数据集时使用的随机数因子，-1则使用当前时间做因子', 'edit': True, 'value': '13', 'key': 'random_seed', 'node_id': '-1', 'task_name': 'yolo5'}, 
{'default': '0', 'name': 'device', 'type': '01', 'required': True, 'desc': '训练使用设备指定, i.e. 0 or 0,1,2,3 or cpu', 'edit': True, 'value': '0', 'key': 'device', 'node_id': '-1', 'task_name': 'yolo5'}]   
                            '''
                            try:
                                node_id = ext_info[0].get("node_id")
                                task_name = ext_info[0].get("task_name")
                                task = NNI_Task(local_conf, f"{ip_addr}:{ctl_port}", task_id=run_id, exp_name=expe_name,
                                                user_name=user_name, node_id=node_id)
                            except Exception as x:
                                log.error(f"NEW NNI_Task:{x}")
                            log.info("export model")
                            try:
                                task.model_export(exp_id, trial_id, expe_name, run_id)
                            except Exception as x:
                                log.error(f"model_export: {x}")
                                return
                            log.info("deploying model...")

                            # stop the train task
                            # log.debug("ext_info: {}", ext_info)
                            # task_name = json.loads(ext_info)['task_name']

                            task.stop_task(task_name=task_name)
                            task.close()

                            # Done 修改训练容器的占用状态
                            try:
                                db.update_dev_container_poole_container_status("0", container_name)
                                log.info(
                                    f"update table dev_container_pool set is_use=0 where container_name={container_name}")
                            except Exception as x:
                                log.error(
                                    f"update table dev_container_pool set is_use=0 where container_name={container_name} error: {x}")
                            # pick a test container
                            test_cont = train_task_mon_deploy(db, expe_name, run_id, user_name, node_id)

                            # update task dev_run
                            if test_cont is None:
                                db.train_task_update(expe_name, run_id, 'train finished, but not deploy',
                                                     run_status='02')
                                db.update_dev_run_node_status("02", run_id, expe_name, node_id)
                            else:
                                db.update_dev_run_node_status("02", run_id, expe_name, node_id,
                                                              bak_1=f'train finished, deployed to {test_cont}')
                                db.train_task_update(expe_name, run_id, f'train finished, deployed to {test_cont}',
                                                     run_status='02', deploy_container=test_cont)
                        elif t['status'] == "FAILED":
                            log.info("...........train FAILED.........")
                            # Done 失败的情况也需要释放训练容器， 导出文件到部署环境， 以便用户可以查看日志
                            run_success = False
                            log_path = t['logPath'].split('/')
                            exp_id, trial_id = log_path[-3], log_path[-1]
                            log.info(f"task failed, exp_id: {exp_id}, trial_id: {trial_id}")
                            try:
                                ext_info = json.loads(ext_info)
                                node_id = ext_info[0].get("node_id")
                                task_name = ext_info[0].get("task_name")
                                log.info(node_id, task_name)
                                task = NNI_Task(local_conf, f"{ip_addr}:{ctl_port}", task_id=run_id, exp_name=expe_name,
                                                user_name=user_name, node_id=node_id)
                            except Exception as x:
                                log.error(f"NEW NNI_Task:{x}")
                            # 导出训练容器中的文件至文件服务器
                            try:
                                task.model_export(exp_id, trial_id, expe_name, run_id)
                                log.info("export model")
                            except Exception as x:
                                log.error(f"model_export: {x}")
                            # 停止训练容器中进程
                            try:
                                task.stop_task(task_name=task_name)
                                log.info("stoped task of train container")
                            except Exception as x:
                                log.error(f"stop_task: {x}")
                            # 修改训练容器的占用状态
                            try:
                                db.update_dev_container_poole_container_status("0", container_name)
                                log.info(
                                    f"update table dev_container_pool set is_use=0 where container_name={container_name}")
                            except Exception as x:
                                log.error(
                                    f"update table dev_container_pool set is_use=0 where container_name={container_name} error: {x}")
                            # 更新nni任务启动状态, dev_run表, RUN_STATUS：04,同时更新/data/xx/+{run_id}-{node_id}/nni-experiments/{exp_id}/trials/{trial_id}/stderr内容至op_note
                            try:
                                ret = db.devc_update_devRun_nodeStatus("04", run_id, expe_name, op_note="")
                                log.info(
                                    f"update run_status:04 to table dev_run columns RUN_STATUS where run_id={run_id} and expe_name={expe_name}")
                            except Exception as x:
                                log.error(
                                    "update run_status to table dev_run columns RUN_STATUS error,Reason：[%s]" % str(x))
                            # 更新nni任务启动状态：dev_run_node表, RUN_STATUS：04
                            try:
                                ret = db.update_dev_run_node_status("04", run_id, expe_name, node_id)
                                log.info(
                                    f"update run_status:04 to table dev_run_node columns RUN_STATUS where run_id={run_id} and expe_name={expe_name} and node_id={node_id}")
                            except Exception as x:
                                log.error(
                                    "update run_status to table dev_run_node columns RUN_STATUS error,Reason：[%s]" % str(
                                        x))
                            # 从文件服务器下载文件至部署容器，(去掉部署到容器部分,直接从文件服务器中获取日志)
                            # test_cont = None
                            # try:
                            #     rr = db.test_pick_cont()
                            # except Exception as x:
                            #     log.error(f"test_pick_cont error: {x}")
                            # if rr is None:
                            #     log.error("no available test container!!!")
                            # else:
                            #     test_cont, test_ip, test_ctl_port, test_svc_port = rr
                            #     log.info(f"pick a test cont: {test_cont} ssh://{test_ip}:{test_ctl_port}")
                            #     # deploy the model
                            #     try:
                            #         task = NNI_Task(_conf, f"{test_ip}:{test_ctl_port}", task_id=run_id,exp_name=expe_name,user_name=user_name,node_id=node_id)
                            #         # task.model_deploy(expe_name,"-1")# -1为node_id
                            #         task.model_deploy_train_failed(expe_name,run_id,node_id)
                            #         log.info(f"download modelfile to deploy container")
                            #     except Exception as x:
                            #         log.error(f"model_deploy_train_failed: {x}")
                    else:
                        '有多个训练结果的情况, 数据自动化学习, 导出方式有所变化'
        # train_task_update(expe_name, run_id, 'hehe--1')
        # train_task_update(expe_name, run_id, 'hehe--2', run_status='01')
        # db.train_task_update(expe_name, run_id, 'hehe--2', run_status='01', deploy_container='taskrun1')


# 启动单独线程,执行nni自动部署
def train_task_mon(_conf, db):
    '自助建模训练任务状态检查'
    log.info("start train task mon...")
    while True:
        train_task_mon_once(_conf, db)
        time.sleep(10.0)


def train_task_mon_deploy(db, expe_name, run_id, user_name, node_id):
    '训练完成后， 导出的模型， 选择一个测试容器部署测试进程'
    test_cont = None
    try:
        rr = db.test_pick_cont()
    except Exception as x:
        log.error(f"test_pick_cont error: {x}")
    if rr is None:
        log.error("no available test container!!!")
    else:
        test_cont, test_ip, test_ctl_port, test_svc_port = rr
        log.info(f"pick a test cont: {test_cont} ssh://{test_ip}:{test_ctl_port}")
        # 更新部署容器名称至dev_experiment
        try:
            db.update_conName_to_devExperiment(test_cont, expe_name)
            log.info(f"update conName:{test_cont} to table devExperiment")
        except Exception as x:
            log.error("update conName to devExperiment failed:[%s]" % str(x))
        # deploy the model
        try:
            task = NNI_Task(_conf, f"{test_ip}:{test_ctl_port}", task_id=run_id, exp_name=expe_name,
                            user_name=user_name, node_id=node_id)
            # task.model_deploy(expe_name,"-1")# -1为node_id
            task.model_deploy(expe_name, run_id)
        except Exception as x:
            log.error("model_deploy failed %s [%s]", x, traceback.format_exc())
    return test_cont


def pod_exec():
    jsons = request.stream.read().decode('utf-8')
    log.info("...enter pod_exec...[%s]" % jsons)
    jo = json.loads(jsons)
    container_name = jo['container_name']
    message = jo['message']
    k8s = K8S(_conf['k8s_config_file'])
    stdout = k8s.pod_exec(container_name, message)
    return _rjson(stdout)


def experi_data_collect():
    # 出参
    experi_data = {
        'deploy_experi_data': {},
        'train_experi_data': {},
        'package_experi_data': 0
    }
    # 获取开发实验数据
    try:
        deploy_experi_data = deployment_data()
    except Exception as e:
        deploy_experi_data = f'get deploy_experi_data failed [{e}]'
    experi_data['deploy_experi_data'] = deploy_experi_data

    # 获取训练实验数据
    try:
        train_experi_data = train_data()
    except Exception as e:
        train_experi_data = f'get train_experi_data failed [{e}]'
    experi_data['train_experi_data'] = train_experi_data

    return _rjson(experi_data, 200)


def deployment_data():
    # 获取开发试验数据
    # 1 Modelops开发试验数获取
    deploy_experi_data = _db.EP.get_experi_num()
    logger.info(f"data is {deploy_experi_data}")
    return deploy_experi_data


def train_data():
    # 获取训练实验数据
    # 1 训练试验数获取
    train_experi_data = _db.EP.get_train_times()
    logger.info(f"data is {train_experi_data}")
    return train_experi_data


#
# def deploy_app():
#     try:
#         jsons = request.stream.read().decode('utf-8')
#         logger.info("...enter deploy_app...[{}]", jsons)
#         params = json.loads(jsons)['BODY']
#         user = params['user']
#         app_name = params['app_name']  # 应用名称
#         image = params['image']  # 推理镜像地址
#         inst_count = params.get('inst_count', 1)  # 运行实例数
#         res = params['res']  # 资源描述
#     except Exception as x:
#         return _rjson(None, 402, "Invalid request [%s]" % str(x))
#     k8s = K8S(_conf['k8s_config_file'])
#
#     deploy_name = f"app-{_gen_rand_str(6)}".lower()
#     params['__container'] = deploy_name
#     # # 删除该用户下同名app的日志（部署失败残留的日志）
#     # db.MD.del_deploy_logs(app_name, user_name)
#
#     # # 部署日志模板
#     # logs_params = {
#     #     "percent": "0.1",
#     #     "user_name": user_name,
#     #     "app_name": app_name,
#     #     "deploy_name": deploy_name,
#     #     "app_type": app_type,
#     #     "app_status": -1,  # 状态部署中
#     #     "note": "部署信息检查中",
#     # }
#
#     # 调用部署方法
#     body, code, msg = _deploy_app(params)
#
#     return _rjson(body, code, msg)


# 编码式建模服务部署
def deploy_app():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter deploy_app...[{}]", jsons)
        jo = json.loads(jsons)['ROOT']
        # svcId = jo['svcId']  # 服务id
        user = jo['user']
        appName = jo['appName']  # 容器名称
        instCount = jo.get('instCount', 1)  # 运行实例数
        image = jo['image']
        cpu = jo['res']["cpu"]  # 使用CPU的 cores 数量, 可以为小数
        mem = jo['res']["mem"]  # 内存使用限制, 单位 MB
        # storage = jo['res']["storage"]  # 卷空间大小, 单位MB, 挂载位置固定为 /data
        env = jo.get('env', {})

        extra_args = {}
        extra_args['gpu'] = jo['res'].get("gpu", None)  # 分配gpu，显卡个数，每张显存大小, 算力百分比，单位(第四范式mb，阿里云gb), 默认None为不分配
        extra_args['npu'] = jo['res'].get("npu", None)  # 分配npu, 卡的个数
        extra_args['gpuType'] = jo['res'].get("gpuType", None)  # 1:阿里云，2:第四范式，3:驱动 有gpu时填写

        extra_args['ext'] = jo.get('ext', {})  # 允许提供扩展的参数
        extra_args['args'] = jo.get('args', None)  # 启动命令, 可以通过参数指定, 格式 ["/app/run.sh", ""]
        extra_args['scene_type'] = jo.get('scene_type', '02')  # scene_type  01 - 训练资源池；02 - 试用资源池；03 - 推理资源池
        extra_args['calc_type'] = jo.get('calc_type', '01')  # calc_type  01-通用算力；02-GPU算力；03-NPU算力
        svc_info = {}
        svc_info["request_param"] = jo['svc']['requestParam']  # 部署服务的请求入参
        svc_info['svc_port'] = jo['svc']['svcPort']  # 部署服务的端口
        svc_info['url'] = jo['svc']['url']  # 部署服务的url
        svc_info['succeed_status'] = jo['svc']['succeedStatus']  # 部署服务正常的返回参数

    except Exception as x:
        dep_log = f'deploy failed，Invalid request [{str(x)}]'
        # db.update_deploy_info(svcId, '', '', '03', dep_log)
        db.update_jupyter_deploy_info('', '', '03', appName.lower(), dep_log)

        return _rjson(None, 402, "Invalid request [%s]" % str(x))

    logger.info("create container {} ENV: [{}]", appName, env)
    # db.update_deploy_info(svcId, '', '', '01')
    db.update_jupyter_deploy_info('', '', '01', appName.lower())

    # calc_type  01-通用算力；02-GPU算力；03-NPU算力
    if extra_args['gpu']:
        # extra_args['calc_type'] = '02'
        extra_args['resType'] = 1

    elif extra_args['npu']:
        # extra_args['calc_type'] = '03'
        extra_args['resType'] = 2

    else:
        # extra_args['calc_type'] = '01'
        extra_args['resType'] = -1

    # 获取k8s配置文件以及namespace名称
    kube_info = get_kube_info(extra_args['calc_type'], extra_args['scene_type'])
    if not kube_info:
        dep_log = 'deploy failed，get kubeconfig failed'
        # db.update_deploy_info(svcId, '', '', '03', dep_log)
        db.update_jupyter_deploy_info('', '', '03', appName.lower(), dep_log)
        return _rjson(None, 403, dep_log)

    extra_args['kubeconfig'] = kube_info['kubeconfig']
    extra_args['namespace'] = kube_info['namespace']

    # 判断申请的资源是否足够
    res_info = {
        'cpu': cpu,
        'mem': mem
    }
    flag, msg = check_namespace_res(extra_args['kubeconfig'], extra_args['namespace'], res_info)
    if not flag:
        dep_log = f'deploy failed, {msg}'
        # db.update_deploy_info(svcId, '', '', '03', dep_log)
        db.update_jupyter_deploy_info('', '', '03', appName.lower(), dep_log)

        return _rjson(None, 405, dep_log)

    th = threading.Thread(target=create_deploy_container,
                          args=(appName, image, cpu, mem, extra_args, env, svc_info, instCount))
    th.start()
    return _rjson({})


def create_deploy_container(appName, image, cpu, mem, extra_args, env, svc_info, instCount=1):
    logger.info("using image: {}", image)
    logger.info("extra_args: {}", extra_args)
    try:
        depName = appName.lower()  # deployment名称与appName名称相同
        serviceName = depName
        # if extra_args['kubeconfig']:
        #     k8s = K8S(extra_args['kubeconfig'])
        # else:
        k8s = K8S(extra_args['kubeconfig'], extra_args['namespace'])

        ports = [svc_info['svc_port']]
        args = extra_args['args']
        entry = ''
        if ';' in args:
            tmp = args.split(';')
            entry = tmp[0]
            args = tmp[1:]

        resInfo = res_configuration(extra_args['resType'], extra_args)

        # affinity = _conf.get("affinity", False)
        if not k8s.create_deployment_new(depName, image, cpu, mem, entrypoint=entry, args=args, ENVs=env,
                                         resType=extra_args['resType'], resInfo=resInfo, inst_count=instCount):
            logger.error("create deployment {} failed, delete all", depName)
            k8s.delete_all()
            dep_log = f'deploy failed，create deployment failed, delete all'
            # db.update_deploy_info(svcId, '', '', '03', dep_log)
            db.update_jupyter_deploy_info('', '', '03', depName, dep_log)

            # db.devc_update_status(name, "06")
            return
        logger.info("create deployment done!")
        ports = [[f"port-{p}", "TCP", p, None] for p in ports]
        logger.info("service ports {}", str(ports))
        ### 容器类型  01: Jupyter容器, 02: 计算容器, 03: 自动化学习容器, （现在（2022-4-21)只支持一种容器了
        # if ctype == "01":
        if not k8s.create_service(serviceName,
                                  Ports=ports,
                                  selector={"app": depName}):
            # db.devc_update_status(name, "06")
            k8s.delete_all(dep_name=depName)
            logger.error("create service failed!, delete_all {}", depName)
            dep_log = f'deploy failed，create service failed, delete all'
            # db.update_deploy_info(svcId, '', '', '03', dep_log)
            db.update_jupyter_deploy_info('', '', '03', depName, dep_log)

            return

        # 启动进程，探索jupyter容器创建及jupyter进程启动进度
        dev_info = {"kubeconfig": extra_args['kubeconfig'], "namespace": extra_args['namespace']}
        th = threading.Thread(target=_deploy_stat, args=(depName, dev_info, svc_info))
        th.start()
        logger.info("create service {} done! checking progress...", depName)

        logger.info("container info: {}", dev_info)
        # if ctype != "01":
        #     con_status = "02"
        # # 更新容器数据进数据库
        # # 01: 正在启动, 02: 运行中，06: 启动失败
        # else:
        #     con_status = "01"
        # try:
        #     logger.info(dev_info)
        #     db.devc_update_v119(name, dev_info, con_status, 0.0)
        # except Exception as x:
        #     logger.error("update database %s error failed %s" % ("dev_container", x))
    except Exception as x:
        dep_log = f'deploy failed，create container failed: {x}, {traceback.format_exc()}'
        # db.update_deploy_info(svcId, '', '', '03', dep_log)
        db.update_jupyter_deploy_info('', '', '03', depName, dep_log)

        logger.error("create container failed {}, {}", x, traceback.format_exc())
    logger.info("create jupyter container {} done!", depName)


def _deploy_stat(dep_name, dev_info, svc_info):
    logger.info("...enter _deploy_stat...")
    start = time.time()
    end = start
    deploy_success = False
    k8s = K8S(dev_info['kubeconfig'], dev_info['namespace'])
    dep_log = ''
    # while end - start <= 240:
    while end - start <= 600:
        # time.sleep(10)
        time.sleep(60)
        end = time.time()
        pod_name, node_host, ready, total, ports = k8s.query_pod(dep_name)
        if pod_name is None:
            logger.info("容器创建挂起中 {}...", dep_name)
            continue
        if node_host is None:
            logger.info("容器节点主机分配中 {}...", dep_name)
            continue
        if ready < total:
            logger.info("容器启动中 {}...", dep_name)
            continue
        p = None
        if ports is not None:
            for port in ports:  # [inner_port, 'TCP', out_port]
                if port[0] == svc_info['svc_port']:
                    p = port[2]
        # jupyter_url = f"http://{node_host}:{p}/jupyter/lab"
        logger.info(f'node_host is {node_host}')
        logger.info(f'p is {p}')

        import socket
        try:
            url = f"http://{node_host}:{p}{svc_info['url']}"
            logger.info(f"url -- {url}")

            r = requests.post(url, json=svc_info["request_param"])
            logger.info(f"r is {r}")
            logger.info(f"r.status_code is {r.status_code}")
            if int(r.status_code) == 200:
                # db.update_deploy_info(svcId, url, p, '02')
                db.update_jupyter_deploy_info(url, p, '02', dep_name)
                deploy_success = True
                break
                # r_json = json.loads(r.text)
                # if 'status' in r_json.keys():
                #     status = r_json['status']
                # elif 'Status' in r_json.keys():
                #     status = r_json['Status']
                # else:
                #     logger.info(f"return data has no 'status'")
                #
                # logger.info(f"status is {status}")
                # succeed_status = svc_info['succeed_status']
                # logger.info(f"succeed_status is {succeed_status}")
                # if status == succeed_status or (succeed_status in status):
                #     # 记录app访问ip以及port
                #     db.MD.update_deploy_info(svcId, url, p, '02')
                #     deploy_success = True
                #     logger.info("container {} check success(port ready {}:{})", dep_name, node_host, p)
                #     break
                # else:
                #     logger.info(f"return status is {json.loads(r.text)['status']}")
                #     break
            else:
                logger.info(f"r.status_code is {r.status_code}")
                dep_log = f'Request Service Interface Return : {r.status_code}'
                break
        except Exception as x:
            logger.error(x)
            dep_log = 'Service invocation failed '
            continue

    if not deploy_success:
        try:
            # 超时,  需要将资源删除
            logger.info("deleted timeout container {}", dep_name)
            # logger.error("jupyter container {} create failed, up devc return {}", dep_name, ret)
            k8s.delete_all(dep_name=dep_name, svc_name=dep_name)
            logger.info("deleted timeout container {}", dep_name)
            if dep_log == '':
                dep_log = 'deploy timeout，deleted timeout container'
            # db.update_deploy_info(svcId, '', '', '03', dep_log)
            db.update_jupyter_deploy_info('', '', '03', dep_name, dep_log)
        except Exception as x:
            if dep_log == '':
                dep_log = f'deploy timeout，deleted timeout container，del failed: {x}, {traceback.format_exc()}'
            # db.update_deploy_info(svcId, '', '', '03', dep_log)
            db.update_jupyter_deploy_info('', '', '03', dep_name, dep_log)
            logger.error("container {} del failed {} {}", dep_name, x, traceback.format_exc())


def get_kube_info(calc_type='01', scene_type='01'):
    """
        获取对应kubeconfig文件目录，命名空间名称以及存储卷类名称
    """
    # scene_type  01 - 训练资源池；02 - 试用资源池；03 - 推理资源池
    # calc_type  01-通用算力；02-GPU算力；03-NPU算力
    # 数据库获取数据
    k8s_info = db.K8sInfo.get_k8s_info(scene_type, calc_type)
    logger.info(f"k8s_info is {k8s_info}")
    logger.info(f"calc_type is {calc_type}, scene_type is {scene_type}")
    if k8s_info:
        data = []
        for row in k8s_info:
            namespace, kubeconfig_name, path, storage_class_name, s_type, c_type = row
            if path[-1] == '/':
                kubeconfig_path = path + kubeconfig_name
            else:
                kubeconfig_path = path + '/' + kubeconfig_name
            data.append(
                {'namespace': namespace,
                 'kubeconfig': kubeconfig_path,
                 'storage_class_name': storage_class_name,
                 'scene_type': s_type,
                 'calc_type': c_type
                 }
            )
            # data = {'namespace': namespace, 'kubeconfig': kubeconfig_path, 'storage_class_name': storage_class_name}
        if calc_type and scene_type:
            # 只查一个命名空间
            logger.info("only one")
            logger.info(f"calc_type is {calc_type}, scene_type is {scene_type}")
            return data[0]
        logger.info(f"data {data}")
        return data
    else:
        logger.info(f"get kubeinfo failed")
        return None


def check_namespace_res(k8s_config, namespace, res_info, calc_type=None, gpu_type=None):
    """
        检查集群资源是否足够
        k8s_config: kubeconfig文件目录
        namespace： 命名空间名称
        res_info： 申请资源信息
        calc_type： 01-通用算力；02-GPU算力；03-NPU算力
        gpu_type：如果申请GPU资源，GPU类型，1:阿里云，2:第四范式，3:驱动
    """
    # 获取命名空间下资源
    # k8s_config = "/data/.config/k8s/01/01/y13932-crmmci-jf.kubeconfig"
    # namespace = "y13932-crmmci-jf"
    res_dict = get_namespace_resource(k8s_config, namespace)
    if not res_dict:
        return False, "check failed, get resource failed"
    _res_hard = res_dict['res_hard']
    _res_used = res_dict['res_used']
    logger.info(f"res_hard {_res_hard}")
    logger.info(f"res_used {_res_used}")
    # 处理资源数据
    res_hard = process_res_data(_res_hard)
    res_used = process_res_data(_res_used)
    # 计算资源剩余量
    cpu_free = res_hard['limits.cpu'] - res_used['limits.cpu']
    mem_free = res_hard['limits.memory'] - res_used['limits.memory']
    dick_free = res_hard['requests.storage'] - res_used['requests.storage']
    cpu_request = float('%.1f' % (float(res_info['cpu'])))
    mem_request = float('%.1f' % (float(res_info['mem'])))
    if cpu_request > cpu_free:
        msg = f"cpu request {cpu_request} exceeded available {cpu_free}"
        logger.info(msg)
        return False, msg
    if mem_request > mem_free:
        msg = f"mem request {mem_request} exceeded available {mem_free}"
        logger.info(msg)
        return False, msg
    # 判断是否申请了存储资源
    if 'disk' in res_info:
        disk_request = float('%.1f' % (float(res_info['disk'])))
        if disk_request > dick_free:
            msg = f"disk request {disk_request} exceeded available {dick_free}"
            logger.info(msg)
            return False, msg
    return True, ''
    # if calc_type == '02':
    #     # 02-GPU算力, 计算gpu剩余量
    #     if gpu_type == 1:
    #         # 1: 阿里云
    #         gpu_mem_free = res_hard['requests.aliyun.com/gpu-mem'] - res_used['requests.aliyun.com/gpu-mem']
    #         request_gpu_mem = res_info['gpu'] * res_info['gpumem']
    #         if request_gpu_mem > gpu_mem_free:
    #             msg = f"gpu_mem request {request_gpu_mem} exceeded available {gpu_mem_free}"
    #             logger.info(msg)
    #             return False, msg
    #     elif gpu_type == 2:
    #         # 2:第四范式
    #         gpu_free = res_hard['requests.nvidia.com/gpu'] - res_used['requests.nvidia.com/gpu']
    #         request_gpu = res_info['gpu']
    #         if gpu_free > request_gpu:
    #             msg = f"gpu request {request_gpu} exceeded available {gpu_free}"
    #             logger.info(msg)
    #             return False, msg
    #
    #     elif gpu_type == 3:
    #         # 3: 驱动
    #
    # elif calc_type == '03':
    #     # 02-NPU算力, 计算npu剩余量


def get_namespace_resource(k8s_config_file, namespace_name):
    """
        获取命名空间的资源
    """
    k8s = K8S(k8s_config_file, namespace_name)
    try:
        res_hard, res_used = k8s.namespace_resource()
        return {"res_hard": res_hard, "res_used": res_used}
    except Exception as e:
        logger.error(f"get namespace resource failed [{e}]")
        return {}


def unit_conversion(data_tmp):
    data = None
    if data_tmp[-2:] == 'Mi':
        data = float('%.1f' % (float(data_tmp[0:-2])))
    elif data_tmp[-2:] == 'Gi':
        data = float('%.1f' % (float(data_tmp[0:-2]) * 1024))
    elif data_tmp[-2:] == 'Ti':
        data = float('%.1f' % (float(data_tmp[0:-2]) * 1024 * 1024))
    elif data_tmp[-1:] == 'k':
        data = float('%.1f' % (float(data_tmp[0:-2]) / 1024))
    elif data_tmp == '0':
        data = float('%.1f' % (float(data_tmp)))
    else:
        logger.info(f"data {data_tmp} conversion unit not considered")
    return data


def process_res_data(res_dict):
    """
        处理资源数据格式
        res_dict: 资源数据
    """
    # 处理cpu数据格式
    if "limits.cpu" in res_dict:
        cpu_tmp = res_dict['limits.cpu']
        if cpu_tmp[-1] == 'm':
            res_dict['limits.cpu'] = float('%.1f' % (float(cpu_tmp[0:-1]) / 1000))
        else:
            res_dict['limits.cpu'] = float('%.1f' % (float(cpu_tmp)))
    if 'limits.memory' in res_dict:
        # 转换为mi
        memory_tmp = res_dict['limits.memory']
        logger.info(f"res_dict['limits.memory'] {res_dict['limits.memory']}")
        res_dict['limits.memory'] = unit_conversion(memory_tmp)
    if 'requests.storage' in res_dict:
        # 转换为mi
        storage_tmp = res_dict['requests.storage']
        logger.info(f"res_dict['requests.storage'] {res_dict['requests.storage']}")
        res_dict['requests.storage'] = unit_conversion(storage_tmp)
    # # 第四范式数据格式处理
    if 'requests.nvidia.com/gpu' in res_dict:
        res_dict['requests.nvidia.com/gpu'] = int(res_dict['requests.nvidia.com/gpu'])

    if 'requests.nvidia.com/gpumem' in res_dict:  # 第四范式该配置项单位为M(容器平台提供)
        gpumem_tmp = res_dict['requests.nvidia.com/gpumem']
        logger.info(f"res_dict['requests.nvidia.com/gpumem'] {res_dict['requests.nvidia.com/gpumem']}")
        res_dict['requests.nvidia.com/gpumem'] = unit_conversion(gpumem_tmp)

    if 'requests.nvidia.com/gpucores' in res_dict:
        res_dict['requests.nvidia.com/gpucores'] = int(res_dict['requests.nvidia.com/gpucores'])

    # 阿里云数据格式处理
    if 'requests.aliyun.com/gpu-mem' in res_dict:  # 阿里云该配置项单位为G(容器平台提供)
        # 转换为mi
        gpumem_tmp = res_dict['requests.aliyun.com/gpu-mem']
        logger.info(f"res_dict['requests.aliyun.com/gpu-mem'] {res_dict['requests.aliyun.com/gpu-mem']}")
        res_dict['requests.aliyun.com/gpu-mem'] = unit_conversion(gpumem_tmp)

    # NPU数据格式处理
    if 'requests.huawei.com/Ascend910' in res_dict:  # 卡个数
        res_dict['requests.huawei.com/Ascend910'] = int(res_dict['requests.huawei.com/Ascend910'])

    return res_dict


def get_namespace_gpu():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...enter get_namespace_gpu...[{}]", jsons)
        jo = json.loads(jsons)['ROOT']
        # namespace = jo.get('namespace', None)
        calc_type = jo.get('calc_type', None)
        scene_type = jo.get('scene_type', None)
    except Exception as x:
        logger.error(f"Invalid request [{str(x)}")
        return _rjson(None, 402, "Invalid request [%s]" % str(x))

    # 判断是单独获取还是获取所有命名空间资源
    kube_info_list = get_kube_info(calc_type, scene_type)
    if not kube_info_list:
        _log = 'get kubeconfig failed'
        logger.error(_log)
        return _rjson(None, 403, _log)

    if calc_type and scene_type:
        kube_info_list = [kube_info_list]

    # 查询集群资源
    for kube_info in kube_info_list:
        k8s_config = kube_info['kubeconfig']
        namespace = kube_info['namespace']

        res_dict = get_namespace_resource(k8s_config, namespace)
        if not res_dict:
            logger.info(f"get {namespace} resource failed")
            continue
        _res_hard = res_dict['res_hard']
        _res_used = res_dict['res_used']
        logger.info(f"{namespace} resource: res_hard {_res_hard}")
        logger.info(f"{namespace} resource: res_used {_res_used}")
        # 处理资源数据
        res_hard = process_res_data(_res_hard)
        res_used = process_res_data(_res_used)
        logger.info(f"res_hard {res_hard}")
        logger.info(f"res_used {res_used}")
        gpu_mem = res_hard.get('requests.nvidia.com/gpumem', None)
        gpu_mem_used = res_used.get('requests.nvidia.com/gpumem', None)
        kube_info.update({
            'gpu_mem': gpu_mem,
            'gpu_mem_used': gpu_mem_used
        })
    return _rjson(kube_info_list)


def stop_instance_container():
    """
    停止部署实例容器。
    deploy 为 k8s 对象，可理解为应用。
    """
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("...stop service instance...[{}]", jsons)
        InDTO = json.loads(jsons)['BODY']
        deploys = InDTO['deploys']

        th = threading.Thread(target=_stop_instance_container, daemon=True, args=(deploys,))
        th.start()

        return _rjson({})

    except Exception as x:
        return _rjson(None, 402, "Invalid request [%s]" % str(x))


def _stop_instance_container(deploys):

    NO_BEGIN = "00"  # 未部署
    RUNNING = "01"  # 部署中
    FINISHED = "02"  # 部署成功
    ERROR = "03"  # 部署失败
    CLOSED = "04"  # 已停止

    for deploy in deploys:
        try:
            kbcfg_file_path = deploy["kubeconfig"]
            namespace = deploy["namespace"]
            deploy_name = deploy["deploy_name"]
            k8s = K8S(kbcfg_file_path, namespace)
            k8s.set_scale(deploy_name, 0)   # 设置 deploy 的副本数为 0，即可停止该容器
        except KeyError:
            logger.error(f"deploy params abnormal: {deploy}")
        except Exception as e:
            logger.error(f"stop deploy -- {deploy_name} -- failed: {e}, {traceback.format_exc()}")

        logger.info(f"stop deploy -- {deploy_name} -- success")

        # 更新实例状态
        res = db.update_instance_status(deploy_name, CLOSED)
        logger.info(f"update deploy status of -- {deploy_name} -- {'success' if res else 'failed'}")
