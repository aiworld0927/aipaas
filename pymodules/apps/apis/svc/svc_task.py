# 任务管理服务

import os, sys
import logging as log
import json
import pickle
import queue
import subprocess
import threading
import time
import shutil
import uuid
import requests
import operator
from flask import Flask, request, Response, abort
import traceback
from loguru import logger

import svc.util_db as db
from svc.sshutil import NNI_Task, fs_scp, GM_Task, ContainerCtl, _fs_get



def registerHandler(app, conf):
    global pypi_server
    global _conf
    global task_queue

    _conf = conf

    # app.add_url_rule("/start_task", "start_task1", AML.start_task, methods=["POST"]) # nni训练任务启动
    app.add_url_rule("/aml/start_task", "start_task2", AML.start_task, methods=["POST"]) # nni训练任务启动
    app.add_url_rule("/aml/show_result", "show_result1", AML.show_result, methods=["POST"]) # nni训练任务结果查看
    app.add_url_rule("/aml/deploy_trial", "deploy_trial1", AML.deploy_trial, methods=["POST"]) # nni结果部署预测服务
    app.add_url_rule("/aml/stop_task", "stop_task1", AML.stop_task, methods=["POST"]) # nni任务停止
    app.add_url_rule("/aml/update_trial_deploy_status", "update_trial_deploy_status", AML.update_trial_deploy_status, methods=["POST"]) # 更新trial推理服务的部署状态
    app.add_url_rule("/aml/get_predict_params", "get_predict_params", AML.get_predict_params, methods=["POST"]) # 获取模块推理服务参数描述
    app.add_url_rule("/aml/start_tensorboard", "start_tensorboard", AML.start_tensorboard, methods=["POST"]) # 启动tensorboard进程
    app.add_url_rule("/aml/start_tensorboard", "start_tensorboard", AML.start_tensorboard, methods=["POST"]) # 启动tensorboard进程
    app.add_url_rule("/aml/deploy_model", "aml_deploy_model", AML.deploy_model, methods=["POST"]) # 手工部署自助建模的推理服务

    app.add_url_rule("/gm/start_task", "gm_start_task", GM.start_task, methods=["POST"]) # GM 训练任务启动
    app.add_url_rule("/gm/deploy_model_test", "gm_deploy_model_test", GM.deploy_model_test, methods=["POST"]) # GM 模型部署测试服务启动
    app.add_url_rule("/gm/deploy_model", "gm_deploy_model", GM.deploy_model, methods=["POST"]) # GM 模型上线部署
    app.add_url_rule("/gm/undeploy_model", "gm_undeploy_model", GM.undeploy_model, methods=["POST"]) # GM 模型上线部署

    db._init_(conf)
    # 启动进程监控容器信息，host_ip,内外端口等
    # th = threading.Thread(target=query_container_info)
    # th.start()
    # 启动单独线程,执行nni自动部署
    th = threading.Thread(target=train_task_mon, args=(_conf, db), daemon=True)
    th.start()

    task_queue = TaskQueue()
    th = threading.Thread(target=task_queue.run, args=(), daemon=True)
    th.start()

def _rjson(obj, rcode=0, rmsg="success"):
    ret = {"ROOT": {"HEADER": {}, "RETURN_CODE": rcode, "RETURN_MSG": rmsg, "BODY": obj}}
    r = Response(json.dumps(ret), mimetype="application/json; charset=utf-8")
    return r

class TaskQueue:
    '训练容器任务实现'
    def __init__(self):
        '初始化先从表和文件中恢复原有的排队状态'
        self.tasks = queue.Queue(maxsize=50)
        self.notify_queue = queue.Queue(maxsize=20)
        self.queue_file = f"{os.getenv('HOME')}/.store/task-queue.pkl"

        # 从文件中恢复队列任务
        if os.path.exists(self.queue_file):
            with open(self.queue_file, 'rb') as fi:
                self.tasks.queue = pickle.load(fi)
        else:
            dirname = os.path.dirname(self.queue_file)
            if not os.path.exists(dirname):
                os.makedirs(dirname)

    def enqueue(self, type_, user, exp_name, run_id, params, task_name=None):
        '添加排队任务'
        self.tasks.put((type_, user, exp_name, run_id, params, task_name))
        logger.info(f"--enqueue new task {type_} user:{user}")
        #队列内容保存到文件中
        with open(self.queue_file, 'wb') as fo:
            pickle.dump(self.tasks.queue, fo)

        # 更新任务的状态为排队中
        db.DevRun.update_task_status(exp_name, run_id, 'a1', 'enqueue')
        self.notify_queue.put("new-task")

    def cont_finish_notify(self):
        '有容器被释放的时候, 通知, 直接唤醒队列检查,  在每个调用 db.train_finish 方法的地方调用此方法'
        self.notify_queue.put('cont_return')

    def run(self):
        '任务排队主循环'
        tasks = self.tasks
        notify_queue = self.notify_queue
        logger.info("...enter  run()")
        while True:
            try:
                notify_str = notify_queue.get(timeout=300.0)
            except Exception as x:
                if str(x).find("'timeout'") >= 0:
                    # timeout
                    pass
            if tasks.qsize() == 0:
                logger.info("no task")
                continue

            logger.info("...")

            type_, user, exp_name, run_id, params, task_name = tasks.queue[0]

            logger.info(f"type_ {type_}, user {user}, exp_name {exp_name}, run_id {run_id}, params {params}, task_name {task_name}")
            try:
                # 通过 task_type="gm" 参数给 train_task_mon_once 分流, 到 gm_task_check, 以便释放容器和再更新任务状态
                cont_info = db.train_pick_cont(user, exp_name, run_id,
                       task_type=type_, task_name=task_name)  # 返回值ret=cont_name, ip_addr, ctl_port, svc_port
            except Exception as x:
                logger.error(f"train pick cont error: [{traceback.format_exc()}]")
                continue

            logger.info("...")

            if cont_info is None:
                logger.info(f"cont_info is {cont_info}")
                continue
            logger.info(f"--queue: got a container {cont_info}, tasks.length: {tasks.qsize()} task_name: {task_name}")
            if type_ == 'gm':
                logger.info(f"...enter _start_gm_task")

                th = threading.Thread(target=self._start_gm_task, args=(cont_info, user, exp_name, run_id, params))
                # TaskQueue._start_gm_task(cont_info, user, exp_name, run_id, params)
            elif type_ == 'nni':
                logger.info(f"...enter _start_nni_task")
                logger.info(f"**********************************************")
                th = threading.Thread(target=self._start_nni_task, args=(cont_info, user, exp_name, run_id, params) )
            else:
                logger.error("invalid task type {}", type_)
                continue

            logger.info("...tasks.get() begin")

            tasks.get() # 把item 取出来

            logger.info("...tasks.get() finish")

            # 保存队列数据
            logger.info("save queue data")

            with open(self.queue_file, 'wb') as fo:
                pickle.dump(self.tasks.queue, fo)
            logger.info("save queue data finish")

            th.daemon = True

            logger.info("thead start()")

            th.start()
            #更新任务状态为运行中
            db.DevRun.update_task_status(exp_name, run_id, '01', 'task running')
            logger.info(f"dequeue task, {exp_name}:{run_id}")


    def _start_gm_task(self, cont_info, user, exp_name, run_id, params):
        cont_name, ip_addr, ctl_port, svc_port = cont_info
        l_conf = {k: _conf[k] for k in ('fileserver_url', 'db_conn_str', 'CONT_IDENTITY')}
        logger.info("gm task train in container: {}", cont_name)

        try:
            with GM_Task(l_conf, f"{ip_addr}:{ctl_port}", exp_name=exp_name, run_id=run_id,
                         user_name=user, dep_path=f"/data/xx/GM_{exp_name}_{run_id}") as task:
                task.start_train(params,
                                 container_name=cont_name)  # 传入container_name 参数, 令gmstatus.py中更新 dev_run 的run_status 特殊处理, 给gm_task_check 处理的机会
        except Exception as x:
            logger.error("gm task failed {} {}", x, traceback.format_exc())
            # 这里也要释放容器
            db.train_finish(cont_name, 'gm task failed')
            self.notify_queue.put('cont-return')

    def _start_nni_task(self, cont_info, user, exp_name, run_id, jo):
        task_name = jo['task_name']  # 任务标识id， 预先定义封装的代码组合， 按照分类配置到数据库中
        node_id = jo['node_id']  # 此处用run_id (对应ssh连接中的 node_id）
        params = jo['params']
        logger.info("....enter..._start_nni_task")
        logger.info("**************************************")
        _start(run_id, run_id, exp_name, task_name, user, params, cont_info, node_id)






class GM:
    @staticmethod
    def start_task():
        '启动图形化建模的训练任务'
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter gm/start_task...[{}]", jsons)
            jo = json.loads(jsons)['ROOT']
            user = jo['taskInfo']['user']
            wfId = jo['taskInfo']['wfId']
            run_id = jo['taskInfo']['runId']
            exp_name = jo['taskInfo']['expe_name']

        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))

        task_queue.enqueue('gm', user, exp_name, run_id, jo)
        return _rjson({})
        # # 选择训练容器
        # try:
        #     # 通过 task_type="gm" 参数给 train_task_mon_once 分流, 到 gm_task_check, 以便释放容器和再更新任务状态
        #     ret = db.train_pick_cont(user,exp_name, run_id, task_type='gm') # 返回值ret=cont_name, ip_addr, ctl_port, svc_port
        # except Exception as x:
        #     logger.error("train pick cont error: [{}]", traceback.format_exc())
        #     return _rjson(None, 403, "can not pick a train container")
        # if ret is None:
        #     logger.error("No available train container for {} {}", exp_name, run_id)
        #     return _rjson(None, 405, "No available train container")
        #
        # cont_name, ip_addr, ctl_port, svc_port = ret
        # l_conf = { k: _conf[k] for k in ('fileserver_url', 'db_conn_str', 'CONT_IDENTITY')}
        # logger.info("gm task train in container: {}", cont_name)
        #
        # try:
        #     with GM_Task(l_conf, f"{ip_addr}:{ctl_port}", exp_name=exp_name, run_id=run_id,
        #                           user_name=user, dep_path=f"/data/xx/GM_{exp_name}_{run_id}") as task:
        #         task.start_train(jo, container_name=cont_name) # 传入container_name 参数, 令gmstatus.py中更新 dev_run 的run_status 特殊处理, 给gm_task_check 处理的机会
        # except Exception as x:
        #     logger.error("gm task failed {} {}", x, traceback.format_exc())
        #     # 这里也要释放容器
        #     db.train_finish(cont_name, 'gm task failed')
        #     return _rjson(None, 415, "gm task start failed %s"%str(x) )
        #
        # # 什么时候释放容器, 交给 train_task_mon 的话, 它现在只处理 nni 的任务类型
        # return _rjson({})

    @staticmethod
    def deploy_model_test():
        '启动图形化建模的推理服务'
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter gm/deploy_model_test ...[{}]", jsons)
            jo = json.loads(jsons)['ROOT']
            user = jo['user']
            wfId = jo['wfId']
            run_id = jo['runId']
            exp_name = jo['expe_name']
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))
        # 选择测试容器
        try:
            # 通过 task_type="gm" 参数给 train_task_mon_once 分流, 到 gm_task_check, 以便释放容器和再更新任务状态
            rr = db.test_pick_cont()
            if rr is None:
                return _rjson(None, 403, "can not pick a test container")
            cont_name, test_ip, test_ctl_port, test_svc_port = rr
            logger.info(f"pick a test cont: {cont_name} ssh://{test_ip}:{test_ctl_port}")
        except Exception as x:
            logger.error("train pick cont error: [{}]", traceback.format_exc())
            return _rjson(None, 403, "can not pick a train container")

        try:
            with GM_Task(_conf, f"{test_ip}:{test_ctl_port}", exp_name=exp_name, run_id=run_id,
                                  user_name=user, dep_path=f"/data/xx/GM_{exp_name}_{run_id}") as task:
                if not task.deploy_model_test():
                    logger.error("deploy model failed")
                    return _rjson(None, 507, "deploy model failed")
        except Exception as x:
            logger.error("start gm inference task failed {} {}", x, traceback.format_exc())
            return _rjson(None, 415, "gm task start failed %s"%str(x) )

        return _rjson({"url":f"http://{test_ip}:{test_svc_port}/GM_{exp_name}_{run_id}/predict"})

    @staticmethod
    def deploy_model():
        '启动图形化建模的推理服务上线， 非临时测试用'
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter gm/deploy_model...[{}]", jsons)
            jo = json.loads(jsons)['ROOT']
            user = jo['user']
            app_id = jo['app_id']  # 部署应用id
            # container_name = jo['container_name'] # 用于部署服务的容器名称
            model_path = jo['model_path']  # 模型管理 中的模型路径
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))
        # 选择测试容器
        try:
            # front end select a container from dev_container_pool where type=06
            rr = db.test_pick_cont_type('06')
            if rr is None:
                return _rjson(None, 403, "can not pick a deploy container")
            # rr = db.NNI_RESULT.get_container_addr(container_name)
            # if rr is None:
            #     logger.error("container not found {}", container_name)
            #     return _rjson(None, 403, "container not found")
            # ip_address, control_port, service_port = rr
            container_name, ip_address, control_port, service_port = rr
            logger.info(f"deploy model in cont: {container_name} ssh://{ip_address}:{control_port}")
        except Exception as x:
            logger.error("deploy_model pick cont error: [{}]", traceback.format_exc())
            return _rjson(None, 403, "can not pick a deploy container")

        try:
            with GM_Task(_conf, f"{ip_address}:{control_port}", exp_name='', run_id='',
                                  user_name=user, dep_path=f"/data/xx/GM_{app_id}") as task:
                if not task.deploy_model(model_path, app_id):
                    logger.error("deploy model failed")
                    return _rjson(None, 507, "deploy model failed")
        except Exception as x:
            logger.error("start gm inference task failed {} {}", x, traceback.format_exc())
            return _rjson(None, 415, "gm task start failed %s"%str(x) )

        return _rjson({"container_name": container_name,
                       "url":f"http://{ip_address}:{service_port}/GM_{app_id}/predict"})

    @staticmethod
    def undeploy_model():
        '启动图形化建模的推理服务 下线'
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter gm/undeploy_model...[{}]", jsons)
            jo = json.loads(jsons)['ROOT']
            user = jo['user']
            app_id = jo['app_id']
            container_name = jo['container_name'] # 用于部署服务的容器名称
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))
        # 选择测试容器
        try:
            rr = db.NNI_RESULT.get_container_addr(container_name)
            if rr is None:
                logger.error("container not found {}", container_name)
                return _rjson(None, 403, "container not found")
            ip_address, control_port, service_port = rr
            logger.info(f"undeploy model in cont: {container_name} ssh://{ip_address}:{control_port}")
        except Exception as x:
            logger.error("train pick cont error: [{}]", traceback.format_exc())
            return _rjson(None, 403, "can not pick a train container")

        try:
            with GM_Task(_conf, f"{ip_address}:{control_port}", exp_name='', run_id='',
                                  user_name=user, dep_path=f"/data/xx/GM_{app_id}") as task:
                if not task.undeploy_model(app_id):
                    logger.error("undeploy model failed")
                    return _rjson(None, 507, "undeploy model failed")
        except Exception as x:
            logger.error("undeploy model failed {} {}", x, traceback.format_exc())
            return _rjson(None, 415, "undeploy model failed %s"%str(x) )

        return _rjson({})

class AML:
    @staticmethod
    def deploy_trial():
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter aml/deploy_trial...[%s]" % jsons)
            jo = json.loads(jsons)['BODY']
            run_id = jo['run_id'] #  此参数用作task_id
            exp_name = jo['exp_name'] # 实验名称(ID)
            user_name = jo['user_name'] # 启动任务的用户名
            trial_id = jo['trial_id'] # 指定的trial-id
        except Exception as x:
            logger.error("invalid request {}", traceback.format_exc())
            return _rjson(None, 402, "Invalid request [%s]" % str(x))

        # 检查是否支持文件下载
        task_def = None
        try:
            task_name = db.AML.get_task_name(exp_name)
            task_def = os.path.join(_conf["nni_code"], task_name, "define.json")
            logger.info("task_def file: {}", task_def)
            if not os.path.exists(task_def):
                return _rjson(None, 403, "Invalid request: model_code not found")
            with open(task_def, 'r', encoding='utf8') as fi:
                infos = json.load(fi)
            files = infos.get('output_files', None)
            if files is not None:
                # 文件下载的， 没有推理服务， 直接更改状态为成功， 然后在参数服务（get_predict_params)中返回文件列表
                db.NNI_RESULT.update_deploy_status(exp_name, run_id, trial_id, "04", "no service, download file instead!")
                # return _rjson({"filelist": [{"filepath":"", "name":f} for f in files]})
                return _rjson({})
        except Exception as x:
            # logger.error("load json from {} {} failed {}", task_name, task_def, traceback.format_exc())
            logger.error("failed {}", traceback.format_exc())
            return _rjson(None, 403, "invalid task data")

        # 选择部署容器
        try:
            db_ret = db.test_pick_cont() # 返回值ret=cont_name, ip_addr, ctl_port, svc_port
        except Exception as x:
            logger.error("train pick cont error: {}", traceback.format_exc())
            return _rjson(None, 403, "can not pick a test container")

        task_ret = False
        try:
            container_name, ip_address, control_port, service_port = db_ret
            logger.info("deploy_trial in test container: {}", container_name)
            test_cont = f"{ip_address}:{control_port}"

            with NNI_Task(_conf, test_cont, None,
                            exp_name=exp_name, user_name=user_name, node_id=run_id, need_fs=True) as task:
                svc_url = f"{_conf['__svc_addr__']}/aml/update_trial_deploy_status"
                task_ret = task.train_result_deploy(exp_name, run_id, trial_id, svc_url, container_name)
                logger.info("train_result_deploy return {}", task_ret)

        except Exception as x:
            logger.error("deploy_trial failed: {}", traceback.format_exc())
        if task_ret:
            svc_url = f"http://{ip_address}:{service_port}/{exp_name}-pred/{trial_id}/predict"
            return _rjson({"svc_url": svc_url})
            # try:
            #     deploy_status = '04'
            #     row = db.NNI_RESULT.get_container_addr(container_name)
            #     if row is None:
            #         logger.error("container {}  not found", container_name)
            #         return _rjson(None, 405, "contrainer not found")
            #
            #     ip, _, svc_port = row
            #     svc_url = f"http://{ip}:{svc_port}/{exp_name}-pred/{trial_id}/predict"
            #     ret = db.NNI_RESULT.update_deploy_status(exp_name, run_id, trial_id, deploy_status, svc_url)
            #     if not ret:
            #         logger.error("update trial deploy status failed! rowcount==0")
            #         return _rjson(None, 501, "update failed: rowcount==0")
            #     else:
            #         logger.info("update trial deploy status success {} {} {} {} {}", exp_name, run_id, trial_id,
            #                     deploy_status, svc_url)
            #         return _rjson({})
            # except Exception as x:
            #     logger.error("deploy_trial failed {} {}", x, traceback.format_exc())
            #     return _rjson(None, 509, "deploy predict service failed %s" %x)
            # return _rjson({"service_addr":f"http://{ip_address}:{service_port}/{exp_name}-pred/{trial_id}/predict",
            #                "container":container_name})
        else:
            logger.error("deploy failed, return None")
            return _rjson(None, 409, "deploy result show failed")

    @staticmethod
    def update_trial_deploy_status():
        '由部署脚本(nni_pred.py)调用的服务, 用于更新trial推理服务的部署状态'
        # return ''
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter aml/update_trial_deploy_status ...[%s]" % jsons)
            jo = json.loads(jsons)['BODY']
            run_id = jo['run_id'] #  此参数用作task_id
            exp_name = jo['exp_name'] # 实验名称(ID)
            trial_id = jo['trial_id'] # 指定的trial-id
            deploy_status = jo['deploy_status'] # 部署状态
            cont_name = jo['cont_name'] # 部署容器名称

        except Exception as x:
            logger.error("invalid result {}", traceback.format_exc())
            return _rjson(None, 402, "Invalid request [%s]" % str(x))

        if deploy_status == '04':
            row = db.NNI_RESULT.get_container_addr(cont_name)
            if row is None:
                logger.error("container {}  not found", cont_name)
                return _rjson(None, 405, "contrainer not found")

            ip, _, svc_port = row
            # http://172.18.243.17:32771/DE1625217245330267810001-pred/Eg38D/predict
            svc_url = f"http://{ip}:{svc_port}/{exp_name}-pred/{trial_id}/predict"
        else:
            svc_url = None
        if deploy_status == '01': deploy_status = None   # 前端只判断了 None状态， 没有管 01 状态， 导致不能再次部署
        try:
            ret = db.NNI_RESULT.update_deploy_status(exp_name, run_id, trial_id, deploy_status, svc_url)
            if not ret:
                logger.error("update trial deploy status failed! rowcount==0")
                return _rjson(None, 501, "update failed: rowcount==0")
            else:
                logger.info("update trial deploy status success {} {} {} {} {}",
                            exp_name, run_id, trial_id, deploy_status, svc_url)
                return _rjson({})
        except Exception as x:
            logger.error("update db failed {} {}", x, traceback.format_exc())
            return _rjson(None, 502, "update failed: exception %s"%x)


    @staticmethod
    def show_result():
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter aml/show_result...[%s]" % jsons)
            jo = json.loads(jsons)['BODY']
            run_id = jo['run_id'] #  此参数用作task_id
            exp_name = jo['exp_name'] # 实验名称(ID)
            user_name = jo['user_name'] # 启动任务的用户名
        except Exception as x:
            logger.error("invalid result {}", traceback.format_exc())
            return _rjson(None, 402, "Invalid request [%s]" % str(x))
        # 选择训练容器
        try:
            db_ret = db.test_pick_cont() # 返回值ret=cont_name, ip_addr, ctl_port, svc_port
        except Exception as x:
            logger.error("train pick cont error: {}", traceback.format_exc())
            return _rjson(None, 403, "can not pick a test container")

        if db_ret is None:
            logger.error("no available test container, test_pick_cont return None")
            return _rjson(None, 411, 'no available test container')


        task_ret = False
        try:
            container_name, ip_address, control_port, service_port = db_ret
            logger.info("show_result in test container: {}", container_name)
            test_cont = f"{ip_address}:{control_port}"

            with NNI_Task(_conf, test_cont, None,
                            exp_name=exp_name, user_name=user_name, node_id=run_id) as task:
                task_ret = task.train_result_show()
        except Exception as x:
            logger.error("train_result_show failed: {}", traceback.format_exc())
        if task_ret:
            return _rjson({"service_addr":f"{ip_address}:{service_port}", "container":container_name})
        else:
            return _rjson(None, 409, "deploy result show failed")

    @staticmethod
    def stop_task():
        '''自动化学习, 需要保存全部的训练数据, 以便在测试容器中启动 nnictl view
        保存的数据为 nni-experiments 整个目录'''
        try:
            jsons = request.stream.read().decode('utf-8')
            log.info("...enter aml/start_task...[%s]" % jsons)
            jo = json.loads(jsons)['BODY']
            run_id = jo['run_id'] #  此参数用作task_id
            exp_name = jo['exp_name'] # 实验名称
            user_name = jo['user_name'] # 启动任务的用户名
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))
        # 先找到对应的训练任务所在容器
        try:
            r = db.train_cont_find_by_exp(exp_name, run_id)
        except Exception as x:
            logger.error("stop_task: find train container for {} {} failed {}", exp_name, run_id, x)
            return _rjson(None, 403, "find train container failed [%s]" % str(x))
        if r is None:
            logger.error("not found train container for {} {}", exp_name, run_id)
            return _rjson(None, 404, "not found train container")
        th = threading.Thread(target=_thread_stop_task, args=(r, exp_name, run_id, user_name), daemon=True)
        th.start()

        return _rjson({"status":"task is stopping, please check train task status."})

    @staticmethod
    def start_task():
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter aml/start_task...[{}]", jsons)
            jo = json.loads(jsons)['BODY']
            run_id = jo['run_id'] #  此参数用作task_id
            exp_name = jo['exp_name'] # 实验名称
            task_name = jo['task_name'] # 任务标识id， 预先定义封装的代码组合， 按照分类配置到数据库中
            node_id = jo['node_id'] # 此处用run_id (对应ssh连接中的 node_id）
            user_name = jo['user_name'] # 启动任务的用户名
            params = jo['params']
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))

        task_queue.enqueue('nni', user_name, exp_name, run_id, jo, task_name=task_name)  # 直接加入队列
        return _rjson({})
        # # 选择训练容器
        # try:
        #     ret = db.train_pick_cont(user_name,exp_name,run_id) # 返回值ret=cont_name, ip_addr, ctl_port, svc_port
        # except Exception as x:
        #     logger.error("train pick cont error: [{}]", traceback.format_exc())
        #     # todo 取不到训练容器, 则加入等待队列中
        #     return _rjson(None, 403, "can not pick a train container")
        # if ret is None:
        #     logger.error("No available train container for {} {}", exp_name, run_id)
        #     return _rjson(None, 405, "No available train container")
        #
        # cont_name, ip_addr, ctl_port, svc_port = ret
        #
        # th = threading.Thread(target=_start, args=(run_id,run_id,exp_name,task_name,user_name,params,ret, node_id))
        # th.start()
        # return _rjson({"container":cont_name, "service_addr":f"{ip_addr}:{svc_port}", "status":"task is starting..."})

    @staticmethod
    def get_predict_params():
        # 从配置(define.json)中读取配置的推理服务参数描述
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter get_predict_params...[{}]", jsons)
            jo = json.loads(jsons)['BODY']
            model_code = jo['model_code']
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))
        # 解析配置文件，读取model_type对应的配置参数
        # 拼接预先定义封装的代码路径
        task_name_dir = os.path.join(_conf["nni_code"], model_code, "define.json")
        if not os.path.exists(task_name_dir):
            return _rjson(None, 403, "Invalid request: model_code not found")
        with open(task_name_dir, 'r', encoding='utf-8') as fi:
            infos = json.load(fi)
            logger.info("load {} success", task_name_dir)

        desc = infos.get('desc_predict_svc', None)
        # 检查是否支持文件下载
        files = infos.get('output_files', None)
        if files is not None and desc is not None:
            o = desc['/predict']['outparams']
            o.update({"type": "downloadlist", "filelist": [{"filepath":"", "name":f} for f in files]})

        return _rjson({ "desc_predict_svc": desc})

    @staticmethod
    def start_tensorboard():
        # 启动tensorboard进程, 查看训练进程输出的指标数据或者网络结构
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter start_tensorboard...[{}]", jsons)
            jo = json.loads(jsons)['BODY']
            exp_name = jo['expe_name']
            run_id = jo['run_id']
            trial_id = jo['trial_id']
            user_name = jo['user_name']
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))

        try:
            status, cont_name = db.NNI_RESULT.get_traintask_status(exp_name, run_id)
            if status is None:
                return _rjson(None, 503, "run_id of experiment not found!")
        except Exception as x:
            logger.error("query task status failed {}", x)
            return _rjson(None, 504, "query task status failed [%s]" % str(x))
        logger.info("task status {}", status)

        if status == '01': # running task
            try:
                r = db.NNI_RESULT.get_container_addr(cont_name) # 查询训练容器的地址
                if r is None:
                    return _rjson(None, 506, "find train container failed, not available")
            except Exception as x:
                return _rjson(None, 507, "find train container failed [%s]" % str(x))
            ip_address, control_port, service_port = r
            test_cont = f"{ip_address}:{control_port}"
            try:
                with NNI_Task(_conf, test_cont, None,
                              exp_name=exp_name, user_name=user_name, node_id=run_id,
                              dep_path=f"~/${exp_name}_${run_id}_tb") as task:
                    if task.show_tensorboard_in_testcont(trial_id, is_testcont=False): # 部署成功, 返回访问地址
                        return _rjson({"url": f"http://{ip_address}:{service_port}/{trial_id}_ck/"})
                    else:
                        return _rjson(None, 508, "Start tensorboard process failed")
            except Exception as x:
                logger.error("start tb failed {}", traceback.format_exc())
        elif status == '02':
            try:
                r = db.test_pick_cont()
                if r is None:
                    return _rjson(None, 506, "find test container failed, not available")
            except Exception as x:
                return _rjson(None, 507, "find test container failed [%s]" % str(x))
            container_name, ip_address, control_port, service_port = r
            test_cont = f"{ip_address}:{control_port}"
            try:
                with NNI_Task(_conf, test_cont, None,
                              exp_name=exp_name, user_name=user_name, node_id=run_id,
                              dep_path=f"~/${exp_name}_${run_id}_tb") as task:
                    if task.show_tensorboard_in_testcont(trial_id): # 部署成功, 返回访问地址
                        return _rjson({"url": f"http://{ip_address}:{service_port}/{trial_id}_ck/"})
                    else:
                        return _rjson(None, 508, "Start tensorboard process failed")
            except Exception as x:
                logger.error("start tb failed {}", traceback.format_exc())
        else:
            return _rjson(None, 509, "Invalid task status, %s" % status)

    @staticmethod
    def deploy_model():
        '手工部署 (自助建模)的 推理服务'
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter aml/deploy_model...[%s]" % jsons)
            jo = json.loads(jsons)['BODY']
            run_id = jo['run_id'] #  此参数用作task_id
            exp_name = jo['exp_name'] # 实验名称(ID)
            user_name = jo['user_name'] # 启动任务的用户名
            node_id = jo['node_id'] # 自助建模只有单次的trial, 所以不需要指定的trial-id
        except Exception as x:
            logger.error("invalid result {}", traceback.format_exc())
            return _rjson(None, 402, "Invalid request [%s]" % str(x))
        # 选择训练容器
        test_cont = train_task_mon_deploy(db, exp_name, run_id, user_name, node_id)
        if test_cont is not None:
            logger
            return _rjson()
        else:
            logger.error("deploy failed, return None")
            return _rjson(None, 409, "deploy result show failed")


def _thread_stop_task(r, exp_name, run_id, user_name):
    logger.info("begin stop and export full result {} {}...", exp_name, run_id)
    try:
        container_name, ip_address, control_port, service_port, op_note = r
        with NNI_Task(_conf, f"{ip_address}:{control_port}", task_id=run_id, exp_name=exp_name,
                      user_name=user_name, node_id=run_id) as task:
            task.train_result_export()
        logger.info("train task {} {} stopped", exp_name, run_id)
    except Exception as x:
        logger.error("export full result failed: {}, {}", x, traceback.format_exc())

    # 释放训练容器, 以及修改任务状态
    try:
        db.train_finish(container_name, 'aml task stopped')
        task_queue.cont_finish_notify()
        db.train_task_update(exp_name, run_id, 'aml task stopped',
                             run_status='02')
        logger.info("update task status as finished successfully. --")
    except Exception as x:
        logger.error("update task status failed {}", x)

    try:
        nni_result2db(_conf, exp_name, run_id, user_name)
        logger.info("nni_result2db success with {} {} {}", exp_name, run_id, user_name)
    except Exception as x:
        logger.error("nni_result2db failed {}, {}", x, traceback.format_exc())


def _start(task_id, run_id, exp_name, task_name, user_name, params, cont_info, node_id):
    logger.info("....enter..._start")
    logger.info("******************************")
    if cont_info is None:
        # 更新nni任务状态，无可用容器时，更新 07:预处理阶段异常结束
        try:
            ret = db.devc_update_devRun_nodeStatus("07", task_id, exp_name, op_note="No available container!")
            logger.info(
                f"update run_status:07 op_note:Not available container! to table dev_run columns RUN_STATUS where run_id={task_id} and exp_name={exp_name}")
        except Exception as x:
            logger.error("update run_status to table dev_run columns RUN_STATUS error,Reason：[%s]" % str(x))
        try:
            # 更新nni任务状态，无可用容器时，更新 07:预处理阶段异常结束
            ret = db.update_dev_run_node_status("07", task_id, exp_name, node_id, bak_1="Not available container!")
            logger.info(
                f"update run_status:07 to table dev_run_node columns RUN_STATUS where task_id={task_id} and exp_name={exp_name} and node_id={node_id}")
        except Exception as x:
            logger.error(f"update run_status to table dev_run_node columns RUN_STATUS error,Reason：[{str(x)}]")
        return _rjson("No available container!")
    else:
        cont_name, ip_addr, ctl_port, svc_port = cont_info
        logger.info(f"run train task {task_name} in  -- cont_name: {cont_name}, addr: {ip_addr}:{ctl_port}")
        logger.info(f"run train task ----Params:{params}")
        try:
            db.train_task_update(exp_name, run_id, 'starting training task...', run_status='01', user_name=user_name)
        except Exception as x:
            logger.error(f"update dev_run to 01 failed: {x} {traceback.format_exc()}")
    # 启动nni进程
    cont_name = cont_name
    cont_addr = ip_addr + ":" + str(ctl_port)
    exp_id = None

    def prog_callback(percent, msg, init=False):
        logger.info("percent: {} % - {}", percent, msg)
        mm = f"{percent}% - {msg}"
        try:
            db.update_bak1_to_devRunNode_(mm, run_id, exp_name, init)
        except Exception as x:
            log.error("update msg dev_run_node failed %s", str(x))

    try:
        logger.info("-- begin start train task...")
        with NNI_Task(_conf, cont_addr, task_id=task_id, exp_name=exp_name, user_name=user_name, node_id=run_id, ) as task:
            exp_id = task.start(task_name=task_name, params=params, progress_callback=prog_callback)
    except Exception as x:
        logger.error("start process error,Reason: {}, {}", str(x), traceback.format_exc())
    # 训练容器中，task启动失败情况
    if exp_id is None:
        # 训练进程启动失败, 停止训练容器中进程
        logger.info("Experiment startup failed")
        # 修改训练容器的占用状态
        try:
            db.train_finish(cont_name, 'start task failed')
            task_queue.cont_finish_notify()
            logger.info(f"update table dev_container_pool set is_use=0 where cont_name={cont_name}")
        except Exception as x:
            logger.error(f"update table dev_container_pool set is_use=0 where cont_name={cont_name} error: {x}")
        # 更新nni任务启动状态：dev_run_node表, RUN_STATUS：04 -- 异常结束
        try:
            ret = db.update_dev_run_node_status("07", task_id, exp_name, node_id)
            logger.info(
                f"update run_status:07 to table dev_run_node columns RUN_STATUS where run_id={task_id} and exp_name={exp_name} and node_id={node_id}")
        except Exception as x:
            logger.error(f"update run_status to table dev_run_node columns RUN_STATUS error,Reason：[{str(x)}]")
        # 更新nni任务启动状态, dev_run表, RUN_STATUS：04 -- 异常结束
        try:
            ret = db.devc_update_devRun_nodeStatus("07", task_id, exp_name, op_note='训练任务启动失败')
            logger.info(
                f"update run_status:07 to table dev_run columns RUN_STATUS where run_id={task_id} and exp_name={exp_name}")
        except Exception as x:
            logger.error(f"update run_status to table dev_run columns RUN_STATUS error,Reason：[{str(x)}]")
        return
    # 更新训练容器名称进表dev_run_node、字段CONTAINER_NAME
    try:
        db.update_dev_run_node_container_name(cont_name, task_id, exp_name, node_id)
        logger.info(
            f"update conName:{cont_name} to table dev_run_node where task_id={task_id} and exp_name={exp_name} and node_id={node_id}")
    except Exception as x:
        logger.error(f"update_dev_run_node_container_name [{str(x)}]")
    try:
        db.update_conName_to_devExperiment(cont_name, exp_name)
        logger.info(f"update conName:{cont_name} to table devExperiment")
    except Exception as x:
        logger.error(f"update conName to devExperiment failed:[{str(x)}]")


# 检查容器中的gm任务类型
def gm_task_check(expe_name, run_id, container_name):
    '暂时只检查表中的任务状态'
    run_status, op_note = db.DevRun.get_task_status(expe_name, run_id)
    if run_status is None:
        logger.error("check gm task failed, get_traintask_status return None")
        return
    if op_note is not None and op_note[:3] == '-- ':
        _, run_status, op_note = op_note.split(maxsplit=2)  # 原来的格式 "-- {run_status} {opnote}"
        logger.warning("task {}:{} exit with status: {}", expe_name, run_id, run_status)
        try:
            ret = db.DevRun.update_task_status(expe_name, run_id, run_status, op_note)
        except Exception as x:
            logger.error("update dev_run failed {} {}", x, traceback.format_exc())
        try:
            db.train_finish(container_name, 'gm task finished with %s' % run_status)
            task_queue.cont_finish_notify()
            logger.info("update task status as finished successfully. --")
        except Exception as x:
            logger.error("update task status failed {} {}", x, traceback.format_exc())
    else:
        logger.info("gm task {}:{} still running", expe_name, run_id)

def train_task_mon_once(local_conf, db):
    '检查一轮'
    ret = db.train_task_list()
    if ret is None or len(ret) == 0:
        # logger.info("no train_task_list found")
        return
    for r in ret:
        expe_name, run_id, begin_time, op_time, op_note, \
        container_name, ip_addr, ctl_port, svc_port, user_name, ext_info = r
        logger.info(f"check train expe_name={expe_name}:{run_id} cont_addr: {container_name} {ip_addr}:{svc_port}")
        try:
            task_name = None
            j = json.loads(op_note)
            if j.get('task_type', None) == 'gm':
                gm_task_check(expe_name, run_id, container_name)
                continue
            task_name = j.get("task_name", None)
        except Exception as x:
            logger.error(f"op_note failed [{op_note}]")
        # check nni restful the experiment status
        url = f"http://{ip_addr}:{svc_port}/api/v1/nni/check-status"
        try:
            r = requests.get(url)
        except Exception as x:
            logger.error(f"request url [{url}] failed: [{x}]")
            #done 更新任务运行状态为 结束
            if begin_time > 30: # 超过30分钟, 则直接更改为结束, 避免一直扫描下去
                logger.warning("set task finish {}:{}, begin_time: {} [check failed, and timeout]", expe_name, run_id, begin_time)
                db.train_finish(container_name, 'check-status failed')
                task_queue.cont_finish_notify()
                db.train_task_update(expe_name, run_id, f'check-status failed, regard as finish', run_status='02')
            else:
                logger.warning("check train failed")
            continue
        if r.status_code != 200:
            logger.error(f"check status of expe: {expe_name} failed, [{r.status_code}--{r.text}] cont_name={container_name}, svc_addr: {ip_addr}:{svc_port}")
        else:
            ret = r.json()
            run_status, run_errors = ret['status'], ret['errors']
            logger.info(f"expe {expe_name} run_status: {run_status}, {run_errors}")
            if run_status == 'DONE' and len(run_errors) == 0: # 成功完成： 导出， 部署， 停止
                # 先检查 其唯一的 trial 是否正常结束
                run_success = False
                r = requests.get(f"http://{ip_addr}:{svc_port}/api/v1/nni/trial-jobs")
                if r.status_code == 200:
                    ret = r.json()
                    logger.info(f"-- task_name: {task_name}, jobs-count: {len(ret)}")
                    if task_name in ("ofa", "yolo5"):  # 暂时只有这两个是自助建模
                    # if len(ret)  == 1:  # 唯一的trial任务,  则认为是自助建模,  这个需要调整
                        try:
                            self_service_model_export(ret, local_conf, container_name, ip_addr,
                                                      ctl_port, expe_name, run_id,
                                                      user_name, ext_info, task_name)
                        except Exception as x:
                            logger.error(f"self_service_model_export failed: {x} {traceback.format_exc()}")
                        continue

                    else:
                        '有多个训练结果的情况, 属于自动化学习, 需要保存全部的训练数据, 以便在测试容器中启动 nnictl view'
                        '保存的数据为 nni-experiments 整个目录'
                        logger.info("export full result...")
                        try:
                            with NNI_Task(local_conf, f"{ip_addr}:{ctl_port}", task_id=run_id, exp_name=expe_name,
                                            user_name=user_name, node_id=run_id) as task:
                                task.train_result_export()
                        except Exception as x:
                            logger.error("export full result failed: {}, {}", x, traceback.format_exc())

                        # 释放训练容器, 以及修改任务状态
                        try:
                            db.train_finish(container_name, 'aml task finished success')
                            task_queue.cont_finish_notify()
                            db.train_task_update(expe_name, run_id, 'aml task finished success',
                                             run_status='02')
                            logger.info("update task status as finished successfully. --")
                        except Exception as x:
                            logger.error("update task status failed {}", x)

                        try:
                            nni_result2db(_conf, expe_name, run_id, user_name)
                            logger.info("nni_result2db success with {} {} {}", expe_name, run_id, user_name)
                        except Exception as x:
                            logger.error("nni_result2db failed {}, {}", x, traceback.format_exc())
            elif run_status == 'ERROR':
                # 启动失败, 清理环境, 释放容器状态
                try:
                    _nni_task_failed(local_conf, container_name, ip_addr, ctl_port, expe_name, run_id, user_name)
                except Exception as x:
                    logger.error("_nni_task_failed failed {}", traceback.format_exc())

        # train_task_update(expe_name, run_id, 'hehe--1')
        # train_task_update(expe_name, run_id, 'hehe--2', run_status='01')
        # db.train_task_update(expe_name, run_id, 'hehe--2', run_status='01', deploy_container='taskrun1')

def _nni_task_failed(local_conf, container_name, ip_addr, ctl_port, expe_name, run_id, user_name):
    logger.info("clear failed task...")
    try:
        with NNI_Task(local_conf, f"{ip_addr}:{ctl_port}", task_id=run_id, exp_name=expe_name,
                      user_name=user_name, node_id=run_id) as task:
            task.stop_task()
    except Exception as x:
        logger.error("export full result failed: {}, {}", x, traceback.format_exc())

    try:
        db.train_finish(container_name, 'aml task finished failed')
        task_queue.cont_finish_notify()
        db.train_task_update(expe_name, run_id, 'aml task finished failed',
                             run_status='02')
        logger.info("update task status as finished failed. --")
    except Exception as x:
        logger.error("update task status failed {}", x)

def self_service_model_export(ret, local_conf, container_name, ip_addr, ctl_port,
                              expe_name, run_id, user_name, ext_info, task_name=None):
    logger.info("--- Self-service modeling")
    logger.info("*********************************")
    t = ret[0]
    logger.info(f"t {t}")
    logger.info(t['status'] == 'SUCCEEDED')
    if t['status'] == 'SUCCEEDED':
        logger.info("***********************")
        run_success = True
        log_path = t['logPath'].split('/')
        exp_id, trial_id = log_path[-3], log_path[-1]
        logger.info(f"task success, exp_id: {exp_id}, trial_id: {trial_id}")
        #ext_info = json.loads(ext_info)
        '''ext_info返回案例：  这个估计是前端保存的参数（对应define.json中定义的），
        ext_info: [
{'default': '1', 'name': 'epoch', 'type': '01', 'required': True, 'desc': '最大训练轮数', 'edit': True, 'value': '1', 'key': 'epoch', 'node_id': '-1', 'task_name': 'yolo5'},      {'default': '60', 'name': 'max_duration', 'type': '01', 'required': True, 'desc': '最大训练时长， 单位：分钟', 'edit': True, 'value': '60', 'key': 'max_duration', 'node_id': '-1', 'task_name': 'yolo5'}, 
{'default': 'ds_yolo', 'name': 'dataset', 'type': '01', 'required': True, 'desc': '数据集id', 'edit': True, 'value': 'ds_yolo', 'key': 'dataset', 'node_id': '-1', 'task_name': 'yolo5'}, 
{'default': '20', 'name': 'test_pct', 'type': '01', 'required': True, 'desc': '测试集比例，0~100， 建议20', 'edit': True, 'value': '20', 'key': 'test_pct', 'node_id': '-1', 'task_name': 'yolo5'}, 
{'default': '13', 'name': 'random_seed', 'type': '01', 'required': True, 'desc': '拆分数据集时使用的随机数因子，-1则使用当前时间做因子', 'edit': True, 'value': '13', 'key': 'random_seed', 'node_id': '-1', 'task_name': 'yolo5'}, 
{'default': '0', 'name': 'device', 'type': '01', 'required': True, 'desc': '训练使用设备指定, i.e. 0 or 0,1,2,3 or cpu', 'edit': True, 'value': '0', 'key': 'device', 'node_id': '-1', 'task_name': 'yolo5'}]   
        '''
        try:
            node_id = "-1" #ext_info[0].get("node_id")
            # task_name = ext_info[0].get("task_name")
            with NNI_Task(local_conf, f"{ip_addr}:{ctl_port}", task_id=run_id,
                          exp_name=expe_name, user_name=user_name,
                          node_id=run_id) as task:
                logger.info("export model")
                logger.info("***********************")
                task.model_export(exp_id, trial_id, expe_name, run_id)
                task.stop_task(task_name=task_name)
        except Exception as x:
            logger.error("nni task exec failed:{} {}", x, traceback.format_exc())
            return

        logger.info(f"update train container [{container_name}] status")
        # Done 修改训练容器的占用状态
        try:
            db.train_finish(container_name, 'task finished successful')
            task_queue.cont_finish_notify()
            logger.info(f"update table dev_container_pool set is_use=0 where container_name={container_name}")
        except Exception as x:
            logger.error(f"update table dev_container_pool set is_use=0 where container_name={container_name} error: {x}")
        # pick a test container
        logger.info("..............pick a test container.............")
        test_cont = train_task_mon_deploy(db, expe_name, run_id, user_name, node_id)
        logger.info(f"test_cont {test_cont}")
        # update task dev_run
        if test_cont is None:
            db.train_task_update(expe_name, run_id, 'train finished, but not deploy', run_status='02')
            db.update_dev_run_node_status("02", run_id, expe_name, node_id)
        else:
            db.update_dev_run_node_status("02", run_id, expe_name, node_id,
                                          bak_1=f'train finished, deployed to {test_cont}')
            db.train_task_update(expe_name, run_id, f'train finished, deployed to {test_cont}', run_status='02',
                                 deploy_container=test_cont)
    elif t['status'] == "FAILED":
        logger.info("...........train FAILED.........")
        # Done 失败的情况也需要释放训练容器， 导出文件到部署环境， 以便用户可以查看日志
        run_success = False
        log_path = t['logPath'].split('/')
        exp_id, trial_id = log_path[-3], log_path[-1]
        logger.info(f"task failed, exp_id: {exp_id}, trial_id: {trial_id}")
        try:
            # ext_info = json.loads(ext_info)
            node_id = "-1" # ext_info[0].get("node_id")
            # task_name = ext_info[0].get("task_name")
            logger.info(f"node_id [{node_id}] task_name [{task_name}]")
            with NNI_Task(local_conf, f"{ip_addr}:{ctl_port}", task_id=run_id, exp_name=expe_name, user_name=user_name,
                          node_id=run_id) as task:
                # 导出训练容器中的文件至文件服务器
                task.model_export(exp_id, trial_id, expe_name, run_id)
                logger.info("failed model data export successfully! begin stop_task")
                # 停止训练容器中进程
                task.stop_task(task_name=task_name, remove_home=True)
                logger.info("---task stopped!")
        except Exception as x:
            logger.error(f"NNI Task failed: {task_name} {x} {traceback.format_exc()}")
        # 修改训练容器的占用状态
        try:
            db.train_finish(container_name, 'task failed')
            task_queue.cont_finish_notify()
            logger.info(f"update table dev_container_pool set is_use=0 where container_name={container_name}")
        except Exception as x:
            logger.error(f"update table dev_container_pool set is_use=0 where container_name={container_name} error: {x}")
        # 更新nni任务启动状态, dev_run表, RUN_STATUS：04,同时更新/data/xx/+{run_id}-{node_id}/nni-experiments/{exp_id}/trials/{trial_id}/stderr内容至op_note
        try:
            ret = db.devc_update_devRun_nodeStatus("04", run_id, expe_name, op_note="")
            logger.info(
                f"update run_status:04 to table dev_run columns RUN_STATUS where run_id={run_id} and expe_name={expe_name}")
        except Exception as x:
            logger.error("update run_status to table dev_run columns RUN_STATUS error,Reason：[%s]" % str(x))
        # 更新nni任务启动状态：dev_run_node表, RUN_STATUS：04
        try:
            ret = db.update_dev_run_node_status("04", run_id, expe_name, node_id)
            logger.info(
                f"update run_status:04 to table dev_run_node columns RUN_STATUS where run_id={run_id} and expe_name={expe_name} and node_id={node_id}")
        except Exception as x:
            logger.error(f"update run_status to table dev_run_node columns RUN_STATUS error,Reason：[{str(x)}]")
    else:
        logger.info(f"t {t}")



import operator
import sqlite3

def ts2tm(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(t)/1000))
def tm2ts(s):
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))*1000

def nni_result2db(_conf, exp_name, run_id, user_name):
    '将NNI的运行结果导出后, 解析信息入表,  数据从fileserver上copy下来'
    tmp_dir = '/tmp/nni_result2db' if os.path.sep != '\\' else 'd:/tmp/nni_result2db'
    os.makedirs(tmp_dir, exist_ok=True)
    fs_path = f"/data/.models/{user_name}/{exp_name}_{run_id}"
    remote_file = f"{fs_path}/nni-experiments/.experiment"
    if not fs_scp(_conf, remote_file, tmp_dir, is_upload=False, is_dir=False):
        logger.error("download {} from fileserver failed!", remote_file)
        return False
    with open(f'{tmp_dir}/.experiment', 'r', encoding='utf-8') as fi:
        ee = json.load(fi)
    lt = [[e['startTime'], e_id, e] for e_id, e in ee.items()]
    lt.sort(key=operator.itemgetter(0))
    # print(lt)
    e = lt[-1][-1]
    exp_id = e['id']

    log_dir = f"{fs_path}/nni-experiments/{exp_id}/log"
    create_time = ts2tm(e['startTime'])

    r1 = [run_id, exp_name, log_dir, user_name, create_time, ]

    remote_file = f"{fs_path}/nni-experiments/{exp_id}/db/nni.sqlite"
    if not fs_scp(_conf, remote_file, tmp_dir, is_upload=False, is_dir=False):
        logger.error("download {} from fileserver failed", remote_file)
        return False

    _db = sqlite3.connect(f'{tmp_dir}/nni.sqlite')
    cur = _db.cursor()
    # 获取指标数据
    metrics = {}
    cur.execute('select trialJobId, data from MetricData where type=?', ['FINAL'])
    for r  in cur.fetchall():
        metrics[r[0]] = r[1]

    # 获取起止时间和完成状态
    state = {}  # [begin_time, end_time, status, metrics, params]
    cur.execute('select timestamp, trialJobId, event, data  from TrialJobEvent order by trialJobId, timestamp')
    for r in cur.fetchall():
        timestamp, trialJobId, event, params = r
        if event == 'WAITING':
            state[trialJobId] = [ts2tm(timestamp), None, None, metrics.get(trialJobId, None), params]
            print("----", trialJobId)
        elif event == 'RUNNING':
            continue
        else:
            s = state.get(trialJobId, None)
            if s is None:
                print("not found", trialJobId, event)
                continue
            s[1] = ts2tm(timestamp)
            s[2] = "01" if event == "SUCCEEDED" else "02"

    cur.close()
    _db.close()

    r2  = []
    for trial_id, (begin_time, end_time, status, metrics, params) in state.items():
        log_dir = f"{fs_path}/nni-experiments/{exp_id}/trials/{trial_id}"
        if end_time is None:
            end_time = begin_time
        if status is None:
            status = '00'
        r2.append( [run_id, exp_name, trial_id, log_dir,
                            metrics, begin_time, end_time,
                            status, params, user_name, begin_time])

    try:
        db.NNI_RESULT.in_db(r1, r2)
        shutil.rmtree(tmp_dir)
        logger.info("nni_result2db into db success! {} {} {}", exp_name, run_id, user_name)
    except Exception as x:
        logger.error("nni_result2db failed {}, {}", x, traceback.format_exc())
        raise x


# 启动单独线程, 跟踪训练任务的状态
def train_task_mon(_conf, db):
    '自助建模训练任务状态检查'
    logger.info("=====start train task mon...")
    while True:
        try:
            train_task_mon_once(_conf, db)
            #check_container_status(_conf, db)
        except Exception as x:
            logger.error(f"train_task_mon failed {x} {traceback.format_exc()}")
        time.sleep(10.0)


def train_task_mon_deploy(db, expe_name, run_id, user_name, node_id):
    '(自助建模)训练完成后， 导出的模型， 选择一个测试容器部署测试进程'
    test_cont = None
    try:
        rr = db.test_pick_cont()
    except Exception as x:
        logger.error(f"test_pick_cont error: {x}")

    logger.info(f"........test pick cont info: {rr}..........")
    if rr is None:
        logger.error("no available test container!!!")
    else:
        test_cont, test_ip, test_ctl_port, test_svc_port = rr
        logger.info(f"pick a test cont: {test_cont} ssh://{test_ip}:{test_ctl_port}")
        # 更新部署容器名称至dev_experiment
        try:
            db.update_conName_to_devExperiment(test_cont,expe_name)
            logger.info(f"update conName:{test_cont} to table devExperiment")
        except Exception as x:
            logger.error(f"update conName to devExperiment failed:[{str(x)}]")
        # deploy the model
        try:
            with NNI_Task(_conf, f"{test_ip}:{test_ctl_port}", task_id=run_id,exp_name=expe_name,
                          user_name=user_name,node_id=node_id, need_fs=True) as task:
                logger.info(f".....model deploy....begin")
                logger.info(f"**********************************")
                task.model_deploy(expe_name,run_id)
        except Exception as x:
            logger.error(x)
    return test_cont


last_cont_ck_time = 0.0 # 上次检查的时间， 检查间隔5m
def check_container_status(_conf, db):
    '检查容器池中的闲置容器池环境， 缺少的进行弥补'
    global last_cont_ck_time

    tm = time.time()
    if tm - last_cont_ck_time < 300.0:
        return
    last_cont_ck_time = tm

    cont_list = db.ContainerPool.list_idle()
    for container_name, ip_address, control_port, service_port, container_type in cont_list:
        # logger.info("checking {} type: {}", container_name, container_type)
        # 检查nginx是否正常
        r = requests.get(f"http://{ip_address}:{service_port}/")
        if r.status_code == 200 or r.status_code == 502:
            # logger.info("container {} ok", container_name)
            continue
        logger.warning("nginx of {} not ok, check process", container_name)
        # check if path exists
        with ContainerCtl(_conf['CONT_USER'], ip_address, control_port, _conf['CONT_IDENTITY'], "data") as h:
            out = h._exec_("ls -l /data/ng1")
            if out.find('No such file or directory') >= 0:
                # 目录不存在，  从 FS 下载 ng1 到容器中
                if not _fs_get(_conf['fileserver_url'], f"{ip_address}:{control_port}", '/data/.app/ng1', f"/data/"):
                    logger.error("uploads ng1 to {} failed!", container_name)
                    continue

            # 则尝试启动nginx进程
            out = h._exec_("/data/ng1/sbin/nginx -p /data/ng1")
            logger.info("start nginx return {}", out)

    #todo 检查venv是否都具备 (有个问题， 部分envs目录是只读挂载， 无法送容器中更新）, 所以先只做告警
    for container_name, ip_address, control_port, service_port, container_type in cont_list:
        with ContainerCtl(_conf['CONT_USER'], ip_address, control_port, _conf['CONT_IDENTITY'], "data") as h:
            # 检查容器中 envs 中的目录
            out = h._exec_("ls -F /app/anaconda3/envs/", out_remove_fistline=1)
            # logger.warning("__envs of {}:  {}", container_name, out)




