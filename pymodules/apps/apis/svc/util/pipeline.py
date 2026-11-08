
import socket
import json
import traceback
import hashlib
import time
import re
import os

import requests

from loguru import logger
from svc.util.rds import DelayTask
from svc.util import db
from svc.util.k8s import K8S
from svc.util import _gen_rand_str, _rjson
from svc.util import remote
from svc.util import ng

# 容器资源模板
_res_templates = {
    "_2c4g16g" : {"cpu":2, "mem":4096, "disk":16*1024},
    "_4c8g32g" : {"cpu":4, "mem":8192, "disk":32*1024},
    "_8c16g32g": {"cpu": 8, "mem": 16384, "disk": 32 * 1024},
    "_4c8g32ggpu" : {"cpu":4, "mem":8192, "disk":32*1023, "gpu": 8},
    "_def": {"cpu":2, "mem":4096, "disk":16*1024}
}

# 为了兼容向 k8s 容器部署的过渡, /pyserv -> /data/.app 了
_base_dir = '/pyserv' if os.path.exists('/pyserv') else '/data/.app'

def failed_func(log_id, deal_log, note, errcode: int = -1):
    if errcode > 0:
        errcode = 0-errcode
    logger.error("failed {}: {}", log_id, deal_log)
    db.PL.update_progress(log_id, -1, deal_log, f"{errcode} - {note}")

def prog_func(log_id, percent, deal_log):
    logger.info("update progress {} {} {}", log_id, percent, deal_log)
    db.PL.update_progress(log_id, percent, deal_log, '-')


class ENTER_ED:
    @staticmethod
    def enter_edit_mode(worker, params):
        '流水线进入编辑状态'

        #创建容器, 并等待完成
        user_name = params.get("user_name")
        proj_name = params.get('proj_name')
        expe_name = params.get('expe_name')
        run_id = params.get('run_id')
        res_tmpl = params.get('res_tmpl', None)
        image = params.get("image", None)

        log_id = params.get("__log_id") # 这个是由调用此函数的服务产生的, 不是客户端提供, 以 __ 为前缀做区分
        logger.info("using log_id {}", log_id)

        db.PL.update_dev_run_status(expe_name, run_id, 'BJ-OPE', '分配容器中')

        _conf = worker.conf

        fsGroup = 8989  # 是否使用root启动容器
        if proj_name is None or proj_name == '' or 'user_ns' not in _conf:
            k8s = worker.k8s  # 使用公共 namespace
        else:
            k8s = K8S(_conf['user_ns'][proj_name])

        params['__k8s'] = k8s.kubeconfig
        container_name = params.get('__container', None)
        if container_name is None:
            container_name = f"plcont-{_gen_rand_str(6).lower()}"  # k8s name must be lower case
        if image is None:
            image = _conf['pipeline_base_image']

        if res_tmpl is None or res_tmpl not in _res_templates:
            logger.warning("invalid res_tmpl {},  using default template!")
            res_tmpl = "_def"
        res = _res_templates.get(res_tmpl)
        logger.info("--res: {} - {}", res_tmpl, res)
        params['__res'] = res

        token = _gen_rand_str(6)
        params['__token'] = token
        params['__uri'] = uri = f"/jupyter-{int(time.time()*1000)}"
        #todo 用户卷处理: 先查询用户卷是否存在, 不存在则创建, 否则就直接挂载到
        pvc_name = f"uservol-{hashlib.sha1(user_name.encode('utf8')).hexdigest()}"
        phase, capacity, access_mode = k8s.query_pvc(pvc_name)
        if phase is not None:
            logger.info("pvc {} already exists, capacity: {}", pvc_name, capacity)
        else:
            logger.info("pvc {} not exists, create it with 16Gi")
            if not k8s.create_pvc(pvc_name, 16*1024,  # res.get("disk"),
                                  storage_class_name=params['__k8s_storage_class']):
                return failed_func(log_id, "create pvc failed!", "contact admin please", 701)
            prog_func(log_id, 0.1, "创建数据卷中...")
        extra_mounts = [['app1', '/app', True]]
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

        if not k8s.create_deployment(container_name, image, res['cpu'], res['mem'],
                                     entrypoint='/app/anaconda3/bin/python', args=['/app/mng.py', '/app/plcont.json'],
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
        #todo 创建 dev_container 记录

        # 启动容器检查任务, 延迟
        dt = DelayTask("ppl.container_check", params, delay_sec=5, timeout_secs=120,
                       timeout_task_name="ppl.container_create_failed")
        worker.add_delayed_task(dt)
        return "container %s creating" % container_name

    @staticmethod
    def container_create(worker, params):
        '启动创建容器'
        # info = db.PL.get_cont_info('taskrun1')
        # logger.info("---taskrun1 info: {}", info, params)
        # print("container_create ---", info)
        # params['counter'] = 0
        # dt = DelayTask("ppl.container_check", params, delay_sec=5, timeout_secs=120, timeout_task_name="ppl.container_create_failed")
        # worker.add_delayed_task(dt)
        # return "container creating"

    @staticmethod
    def container_check(worker, dt):
        '创建容器后检查可用性'

        params = dt.params
        log_id = params['__log_id']
        container_name = params['__container']
        k8s = K8S(params['__k8s'])

        def _ck_continue(percent, note):
            prog_func(log_id, percent, note)
            dt.delay(5)  # 延迟5秒后再检查
            worker.add_delayed_task(dt)
            return 'continue check'

        pod_name, node_host, ready, total, ports = k8s.query_pod(container_name)
        if pod_name is None:
            return _ck_continue( 0.35, "容器创建挂起中...")
        if node_host is None:
            return _ck_continue(0.40, "容器节点主机分配中...")
        if ready < total:
            return _ck_continue(0.42, "容器启动中...")
        try:
            # 检查 2022 端口是否可用
            node_port = [i[2] for i in ports if i[0] == 2022][0]
            cc = socket.create_connection((node_host, node_port), 1.0)
            cc.close()
            prog_func(log_id, 0.5, "容器创建成功, 启动容器内端口服务...")
        except Exception as x:
            return _ck_continue(0.45, f"容器 {container_name} 内应用状态检查中(控制端口 {node_host}:{node_port} 探测) {x}...")
        return ENTER_ED.prepare_env(worker, params, node_host, ports)

    @staticmethod
    def container_create_failed(worker, dt):
        '容器创建失败, 清理资源'
        params = dt.params
        log_id = params['__log_id']
        container_name = params['__container']

        ENTER_ED._enter_edit_mode_failed(params)

        failed_func(log_id, f"create container [{container_name}] failed[TIMEOUT]!", "contact admin please", 703)
        return "container create failed, all cleared"

    @staticmethod
    def _enter_edit_mode_failed(params):
        '创建环境失败,  进行清理工作: 删除容器,  删除表记录'
        log_id = params['__log_id']
        container_name = params['__container']
        k8s = K8S(params['__k8s'])

        k8s.delete_all(dep_name=container_name, svc_name=container_name)
        # todo 删除 dev_container 记录

        expe_name = params.get('expe_name')
        run_id = params.get('run_id')
        db.PL.update_dev_run_status(expe_name, run_id, 'CK-OFF', '容器分配失败-检查状态超时，可能资源不足')

    @staticmethod
    def prepare_env(worker, params, node_host, ports):
        '准备容器中的流水线编辑环境'
        container_name = params['__container']
        log_id = params['__log_id']

        cont_addr = f"{node_host}:{[i[2] for i in ports if i[0] == 2022][0]}"
        params['__cont_addr'] = cont_addr  # 保存容器控制端口地址
        params['__service_addr'] = f"http://{node_host}:{[i[2] for i in ports if i[0] == 8181][0]}"
        params['__jupyter_addr'] = f"http://{node_host}:{[i[2] for i in ports if i[0] == 8085][0]}"

        # todo 更新容器访问地址到 dev_container 表中
        logger.info("container: {},  cont_addr: {}", container_name, cont_addr)

        # 恢复运行数据
        try:
            with remote.PLTask(worker.conf, cont_addr, params['expe_name'], params['run_id'], params['user_name']) as task:
                ret = task.prepare_env(container_name)
        except Exception as x:
            ret = False
            logger.error("prepare_env failed, {}, {}", x, traceback.format_exc())

        prog_func(log_id, 0.8, "等待容器服务启动中...")

        if not ret:
            logger.error("prepare_env failed, clear...")
            ENTER_ED._enter_edit_mode_failed(params)
            failed_func(log_id, "容器内环境准备失败", "contact admin please", 709)
            return 'failed'

        # 检查容器服务是否可用, 以判断启动是否完成, 该服务功能是用于获取容器内的数据文件
        dt = DelayTask("ppl.check_service_port", params, delay_sec=3, timeout_secs=30,
                       timeout_task_name="ppl.container_create_failed")
        worker.add_delayed_task(dt)
        return "check restful port"

    @staticmethod
    def check_service_port(worker, dt: DelayTask):
        '延迟检查容器中服务是否可用, 用于判断环境最终是否可用'
        params = dt.params
        container_name = params['__container']
        log_id = params['__log_id']
        service_addr = params['__service_addr']

        def _ck_continue(percent, note):
            prog_func(log_id, percent, note)
            dt.delay(5)  # 延迟5秒后再检查
            worker.add_delayed_task(dt)
            return 'continue check restful port'

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
                                      json.dumps({"container_name":container_name, "service_addr": service_addr}),
                                      f"plcont start success")
                # 调用ng注册, 将 uri 注册到 manager 的ng 端口
                uri, serv = params['__uri'], params['__jupyter_addr']
                logger.warning("--nginx register {}: {} -> {}", container_name, uri, serv)
                # uri = "/jupyter-1648182772802"
                # serv = "http://172.18.238.28:37703"
                # from svc.util import ng
                x = ng.NG_PX(prefix=f'{_base_dir}/ng_pyserv')
                host, port = serv[serv.find('//')+2:].split(':')
                x.add_ng_loc(container_name, uri, host, port)

                # 添加容器服务端口注册,  以便能让js通过ng直接调用到容器服务
                uri_svc = f"/pl-{uri.split('-')[-1]}"
                serv = params['__service_addr']
                host, port = serv[serv.find('//') + 2:].split(':')
                x.add_ng_loc(f"{container_name}_pl", uri_svc, host, port)

                return "done!"
        except Exception as x:
            logger.warning("check failed, retrying... {}", str(x) )

        return _ck_continue(0.9, "服务端口检查中...")

    @staticmethod
    def exit_edit_mode(worker, params):
        '退出流水线编辑状态, 备份数据并释放容器'

        # 创建容器, 并等待完成
        user_name = params.get("user_name")
        proj_name = params.get('proj_name')
        exp_name = params.get('expe_name')
        run_id = params.get('run_id')
        container_name = params.get('container_name')

        ext_info = db.PL.get_dev_run_ext(exp_name, run_id) # 先取出 ext_info
        j = None
        try:
            j = json.loads(ext_info)
        except:
            logger.error("can not parse json of ext_info {} {}, [{}], use query_pod instead", exp_name, run_id, ext_info)
            #todo 表中没有数据, 则通过 k8s 查询获取访问地址
            # return "done"
        _conf = worker.conf
        if proj_name is None or proj_name == '' or 'user_ns' not in _conf:
            k8s = worker.k8s  # 使用公共 namespace
        else:
            k8s = K8S(_conf['user_ns'][proj_name])
        params['__k8s'] = k8s.kubeconfig
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
        if pod_name is None or node_host is None or ready<total:
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

        #todo 等待容器退出,  容器中的ng端口是否可用
        dt = DelayTask("ppl.exit_check", params, delay_sec=5, timeout_secs=120,
                       timeout_task_name="ppl.exit_force_update")
        worker.add_delayed_task(dt)

        return 'wait and checking'

    @staticmethod
    def exit_check(worker, dt):
        '''
        延迟检查容器是否退出, 之后再更新表状态
        '''
        params = dt.params
        log_id = params.get("__log_id")

        def _ck_continue(percent, note):
            prog_func(log_id, percent, note)
            dt.delay(5)  # 延迟5秒后再检查
            worker.add_delayed_task(dt)
            return 'continue check'

        # 服务删除后端口不能访问了,  但容器可能还在, 不能通过端口检查来判断容器是否 down 掉
        try:
            # 检查 pod 是否存在
            __cont_name = params['__cont_name']
            k8s = K8S(params['__k8s'])
            pod_name, node_host, ready, total, ports = k8s.query_pod(__cont_name)
            if pod_name is not None:
                return _ck_continue(0.45, f"退出容器内应用状态检查中pod是否存在, still running...")
        except:
            prog_func(log_id, 0.5, "容器退出成功, 更新表状态...")

        prog_func(log_id, 1.0, '容器删除完成')

        exp_name = params.get('expe_name')
        run_id = params.get('run_id')
        # 更新dev_run 表的状态
        db.PL.update_dev_run(exp_name, run_id, 'CK-OFF', '', None)  # 更新运行状态

        # 删除ng注册 (要先查询到 dev_run 的 uri 注册信息)
        uri = params['__uri']
        if uri:
            logger.warning("---unregister nginx {} -> {}", __cont_name, uri)
            x = ng.NG_PX(prefix=f'{_base_dir}/ng_pyserv')
            x.rmv_ng_loc(f"{__cont_name}_pl")
            x.rmv_ng_loc(__cont_name)
        else:
            logger.warning("not found URI of {}, ignore nginx unregister", __cont_name)
        return 'done'

    @staticmethod
    def exit_force_update(worker, dt):
        params = dt.params
        __cont_name = params['__cont_name']
        log_id = params.get("__log_id")
        logger.warning("exit edit mode wait timeout, update status forced of {}, manually delete it",
                       __cont_name)

        prog_func(log_id, 1.0, '容器删除完成')

        exp_name = params.get('expe_name')
        run_id = params.get('run_id')
        # 更新dev_run 表的状态
        db.PL.update_dev_run(exp_name, run_id, 'CK-OFF', '', None)  # 更新运行状态

        # 删除ng注册 (要先查询到 dev_run 的 uri 注册信息)
        uri = params['__uri']
        __cont_name = params['__cont_name']
        logger.warning("---unregister nginx {}} {}", __cont_name, uri)
        x = ng.NG_PX(prefix=f'{_base_dir}/ng_pyserv')
        x.rmv_ng_loc(f"{__cont_name}_pl")
        x.rmv_ng_loc(__cont_name)
        return 'force updated'

_task_mapper = {
    "ppl.enter_edit_mode": ENTER_ED.enter_edit_mode,
    "ppl.container_create": ENTER_ED.container_create,
    "ppl.container_check": ENTER_ED.container_check,
    "ppl.container_create_failed": ENTER_ED.container_create_failed,
    # "ppl.prepare_env": prepare_env,  # 由 container_check 直接调用, 无需外部触发
    "ppl.check_service_port": ENTER_ED.check_service_port, # 启动环境的最后阶段, 检查容器中PL服务是否可用

    "ppl.exit_edit_mode": ENTER_ED.exit_edit_mode, # 退出编辑模式
    "ppl.exit_check": ENTER_ED.exit_check,  # 退出编辑模式 状态检查
    "ppl.exit_force_update": ENTER_ED.exit_force_update,  # 退出等待超时， 强制修改状态
}
