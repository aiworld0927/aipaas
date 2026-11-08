import os, sys
import json
from flask import Flask, request, Response, abort
import traceback
from loguru import logger

from svc.util import db
import svc.util_db as _db
from svc.util.k8s import K8S
import time

## 用于为容器分配在线端口的服务, 从 iasp76:gyf/k8s_query.py 中迁移过来


def registerHandler(app, conf):
    global _conf
    _conf = conf

    app.add_url_rule("/port/port_addon", "qry_addr", Port.port_addon, methods=["POST", "GET"]) # 查询回流任务信息

def _rjson(obj, rcode=0, rmsg="success"):
    ret = {"ROOT": {"HEADER": {}, "RETURN_CODE": rcode, "RETURN_MSG": rmsg, "BODY": obj}}
    r = Response(json.dumps(ret), mimetype="application/json; charset=utf-8")
    return r

class Port:
    @staticmethod
    def port_addon():
        '''查询容器地址和可用外部端口'''
        try:
            if request.method == 'GET':
                pod = request.args.get("pod", None)
                op = request.args.get("op", "query")
            else:
                jsons = request.stream.read().decode('utf-8')
                logger.info("...enter Port/qry_addr...[{}]", jsons)
                jo = json.loads(jsons)['BODY']
                pod = jo['pod']
                op = jo.get('op', "query")
        except Exception as x:
            return _rjson(None, 402, "Invalid request [%s]" % str(x))

        # 获取容器对应的k8s配置文件‘
        if pod:
            container_info = _db.devc_get_v119(pod)
        else:
            logger.info("has no pod name")
            return "[has no pod name]"
        logger.info(container_info)
        if container_info is None:
            logger.info(f"Failed to get pod {pod} container_info")
            return f"[Failed to get pod {pod} container_info]"

        if "kubeconfig" not in container_info.keys() or "namespace" not in container_info.keys():
            k8s = K8S(container_info.get("kubeconfig", _conf['k8s_config_file']))
        else:
            k8s = K8S(container_info["kubeconfig"], container_info["namespace"])

        lines = ['<pre>']
        # cont_addr = f"{node_host}:{[i[2] for i in ports if i[0] == 2022][0]}"
        # params['__cont_addr'] = cont_addr  # 保存容器控制端口地址
        # params['__service_addr'] = f"http://{node_host}:{[i[2] for i in ports if i[0] == 8181][0]}"
        # params['__jupyter_addr'] = f"http://{node_host}:{[i[2] for i in ports if i[0] == 8085][0]}"

        svc_name = f"{pod}--ports"
        if op == "query":
            pod_name, node_host = k8s.k8s_query_pod(pod)
            ports = k8s.query_svc(svc_name)
            node_addrs = None if ports is None else [(target_port, f"{node_host}:{node_port}") for
                                                     target_port, node_port, protocal in ports]
            if node_addrs is None:
                return "[not found]"
            else:
                lines = ['<pre>']
                for inner_port, addr in node_addrs:
                    lines.append(f"{inner_port} -- {addr}")

                lines.append('</pre>\n')
                return "\n".join(lines)
        elif op == 'del':
            # 删除现有的service
            ret = k8s.del_svc(svc_name)
            return "OK" if ret else "Failed"
        elif op == 'set':
            add_ports = request.args.get("ports").split(",")
            ports = k8s.query_svc(svc_name)  # 查询到现有的端口, 如果有则先删除
            if ports is not None:
                k8s.del_svc(svc_name)

            pod_name, node_host = k8s.k8s_query_pod(pod)
            if pod_name is None:
                return "ERROR: [pod not found]"
            ports = [[f"port{p}", "TCP", int(p), None] for p in add_ports]
            selector = {"app": pod}
            if k8s.create_svc(svc_name, ports, selector):
                for i in range(20):
                    time.sleep(0.2)
                    ports = k8s.query_svc(svc_name)
                    if ports is None:
                        continue
                    return "\n".join(
                        [f"{target_port} -- {node_host}:{node_port}" for target_port, node_port, protocol in ports])

            return "create svc [%s] failed" % svc_name

