# 北京联通数据回流需求相关 服务

import os, sys
import json
from flask import Flask, request, Response, abort
import traceback
from loguru import logger

import svc.util_db as db



def registerHandler(app, conf):
    global _conf

    _conf = conf

    app.add_url_rule("/reflux/qry_id", "reflux_qry_id", Reflux.qry_id, methods=["POST"]) # 查询回流任务信息
    app.add_url_rule("/reflux/upd_status", "reflux_upd_status", Reflux.upd_status, methods=["POST"]) # 更新回流任务状态

def _rjson(obj, rcode=0, rmsg="success"):
    ret = {"ROOT": {"HEADER": {}, "RETURN_CODE": rcode, "RETURN_MSG": rmsg, "BODY": obj}}
    r = Response(json.dumps(ret), mimetype="application/json; charset=utf-8")
    return r


class Reflux:
    @staticmethod
    def qry_id():
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter reflux/qry_id...[{}]", jsons)
            jo = json.loads(jsons)['BODY']
            dataset_id = jo['dataset_id'] #
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))

        try:
            r = db.RefluxDO.qry_id(dataset_id)
            if r is None:
                logger.error("dataset query failed{}", traceback.format_exc())
                return _rjson(None, 405, "dataset info query failed")
            reflux_run_id, dataset_name, store_path, label_id, labels = r

            labels = {v: k for k, v in labels.items()}  # 改为 name 作 key

        except Exception as x:
            logger.error("query db filed: {}", x)
        return _rjson({"reflux_run_id": reflux_run_id, "dataset_name":dataset_name, "store_path":store_path, "labels":labels})

    @staticmethod
    def upd_status():
        try:
            jsons = request.stream.read().decode('utf-8')
            logger.info("...enter reflux/upd_status...[{}]", jsons)
            jo = json.loads(jsons)['BODY']
            dataset_id = jo['dataset_id']
            reflux_run_id = jo['reflux_run_id']
            status = jo['status']
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))

        ret = db.RefluxDO.update_status(dataset_id, reflux_run_id, int(status))
        if not ret:
            logger.error("reflux update failed {}", traceback.format_exc())
            return _rjson(None, 405, "reflux update status failed")
        return _rjson({"status":"OK"})

'''
import requests,json

# 数据回流查询
data = {"BODY":{"dataset_id":"TD1631950005938554310001"}}
r = requests.post('http://172.18.231.75:3191/reflux/qry_id', data=json.dumps(data))
print(r.status_code, r.text)


数据回流处理完成后manager更新dev_reflux_run表endTime字段
'''