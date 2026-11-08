# _*_ coding:utf-8_*_
import logging as log
import json
from flask import request, Response
from svc.util.rds import RedisHelper
import string
from random import choice, randint

def registerHandler(app, conf):
    global redis_nodes
    global redis_password
    global redis_channel

    # redis_nodes = conf['redis_nodes']
    # redis_password = conf['redis_password']
    # redis_channel = conf['redis_channel']

    app.add_url_rule("/jsPushConfig", "jsPushConfig", jsPushConfig, methods=["POST"]) #推送配置
    app.add_url_rule("/jsTaskStart", "jsTaskStart", jsTaskStart, methods=["POST"]) #单次启动工作流
    app.add_url_rule("/jsTaskStop", "jsTaskStop", jsTaskStop, methods=["POST"]) #停止工作流
    app.add_url_rule("/jsTaskResume", "jsTaskResume", jsTaskResume, methods=["POST"]) #重启失败的任务
    app.add_url_rule("/jsScheduleStep", "jsScheduleStep", jsScheduleStep, methods=["POST"]) #单点执行工作流的一个节点
    app.add_url_rule("/jsSchedulePublish", "jsSchedulePublish", jsSchedulePublish, methods=["POST"]) #启动时间调度任务
    app.add_url_rule("/jsScheduleModify", "jsScheduleModify", jsScheduleModify, methods=["POST"]) #改变工作流调度状态

def _rjson(obj, rcode=0, rmsg="success"):
    ret = {"ROOT": {"HEADER": {}, "RETURN_CODE": rcode, "RETURN_MSG": rmsg, "BODY": obj}}
    r = Response(json.dumps(ret), mimetype="application/json; charset=utf-8")
    return r

def __gen_rand_str(nc=0):
    if nc == 0:
        min_char, max_char = 24, 32
    else:
        min_char, max_char = nc, nc
    allchar = string.ascii_letters +  string.digits # string.punctuation +
    token = "".join(choice(allchar) for x in range(randint(min_char, max_char)))
    return token

# 推送配置
def jsPushConfig():
    try:
        req = json.load(request.stream)
        req_body=req.get("BODY")
        log.debug("request to jsPushConfig: %s", req)
        wfId = req_body['wfId']
    except Exception as x:
        return _rjson(None, 501, "receive request parameter failed %s"%x)
    configWfId = "config_wfid_"+wfId
    try:
        obj = RedisHelper(redis_nodes=redis_nodes,
                      channel=redis_channel,
                      password=redis_password)
        obj.set(configWfId,json.dumps(req)) # 保留6min
    except Exception as ex:
        log.error(ex)
        return _rjson({"error": ex})
    return _rjson({})

# 单次启动工作流
def jsTaskStart():
    try:
        req = json.load(request.stream)
        req_body = req['BODY']
        log.debug("request to jsTaskStart: %s", req)
        wfId = req_body['wfId']
    except Exception as x:
        return _rjson(None, 501, "receive request parameter failed %s"%x)
    # 随机产生taskId
    randomId = __gen_rand_str(6)
    taskId = "once_start_"+randomId
    log.info(f"runId---{taskId}")
    # 发布消息
    try:
        obj = RedisHelper(redis_nodes=redis_nodes,
                      channel=redis_channel,
                      password=redis_password)
        obj.set(taskId,json.dumps(req))
        obj.public("taskExec",taskId)
    except Exception as ex:
        log.error(ex)
        return _rjson({"error": ex})
    return _rjson({"taskId":randomId})
# 终止工作流
def jsTaskStop():
    try:
        req = json.load(request.stream)
        req_body = req['BODY']
        log.debug("request to jsTaskStop: %s", req)
        taskId = req_body['taskId']  # "taskId":["ZfKEVk","ERKEVk"]
        wfId = req_body['wfId']
    except Exception as x:
        return _rjson(None, 501, "receive request parameter failed %s"%x)
    if len(taskId) == 0:
        return _rjson(None, 501, "taskId is empty list")
    # 发布消息
    req_tmp = req
    try:
        obj = RedisHelper(redis_nodes=redis_nodes,
                      channel=redis_channel,
                      password=redis_password)
        for tid in taskId:
            # 先判断当前任务是否在运行
            run_key = f"wf_run_{wfId}_{tid}"
            if not obj.exists(run_key):
                continue
            req_tmp["BODY"]["taskId"] = tid
            obj.set("wf_stop_"+tid, json.dumps(req_tmp))
            obj.public("taskExec", "wf_stop_"+tid)
    except Exception as ex:
        log.error(ex)
        return _rjson({"error": ex})
    return _rjson({})

# 重试失败的调度
def jsTaskResume():
    try:
        req = json.load(request.stream)
        req_body = req['BODY']
        log.debug("request to jsTaskResume: %s", req)
        taskId = req_body['taskId']
    except Exception as x:
        return _rjson(None, 501, "receive request parameter failed %s"%x)
    taskResumeId = "task_resume_"+taskId
    # 发布消息
    try:
        obj = RedisHelper(redis_nodes=redis_nodes,
                      channel=redis_channel,
                      password=redis_password)
        obj.set(taskResumeId,json.dumps(req))
        obj.public("taskExec",taskResumeId)
    except Exception as ex:
        log.error(ex)
        return _rjson({"error": ex})
    return _rjson({})

#单点执行工作流的一个节点
def jsScheduleStep():
    try:
        req = json.load(request.stream)
        req_body = req['BODY']
        taskId = req_body["taskId"]
        log.debug("request to jsScheduleStep: %s", req)
    except Exception as x:
        return _rjson(None, 501, "receive request parameter failed %s"%x)
    runId = __gen_rand_str(6)
    oneStepId = "one_step_"+runId
    # 发布消息
    try:
        obj = RedisHelper(redis_nodes=redis_nodes,
                      channel=redis_channel,
                      password=redis_password)
        obj.set(oneStepId,json.dumps(req))
        obj.public("taskExec",oneStepId)
    except Exception as ex:
        log.error(ex)
        return _rjson({"error": ex})
    return _rjson({"runId":runId})
#启动时间调度任务
def jsSchedulePublish():
    try:
        req = json.load(request.stream)
        req_body = req['BODY']
        log.debug("request to jsSchedulePublish: %s", req)
        wfId = req_body['wfId']
        schedule = req_body['schedule']
    except Exception as x:
        return _rjson(None, 501, "receive request parameter failed %s"%x)
    # 随机产生scheduleId
    randomId = __gen_rand_str(6)
    scheduleId = "schedule_start_"+randomId
    log.info(f"scheduleId---{scheduleId}")
    # 发布消息
    try:
        obj = RedisHelper(redis_nodes=redis_nodes,
                      channel=redis_channel,
                      password=redis_password)
        obj.set(scheduleId,json.dumps(req))
        obj.public("taskExec",scheduleId)
    except Exception as ex:
        log.error(ex)
        return _rjson({"error": ex})
    return _rjson({"scheduleId":randomId})
#改变工作流调度状态
def jsScheduleModify():
    try:
        req = json.load(request.stream)
        req_body = req['BODY']
        log.debug("request to jsScheduleModify: %s", req)
        wfId = req_body['wfId']
    except Exception as x:
        return _rjson(None, 501, "receive request parameter failed %s"%x)
    schedule_modify = "schedule_modify_"+wfId
    log.info(f"schedule_modify---{schedule_modify}")
    # 发布消息
    try:
        obj = RedisHelper(redis_nodes=redis_nodes,
                          channel=redis_channel,
                          password=redis_password)
        obj.set(schedule_modify, json.dumps(req))
        obj.public("taskExec",schedule_modify)
    except Exception as ex:
        log.error(ex)
        return _rjson({"error":ex})
    return _rjson({})