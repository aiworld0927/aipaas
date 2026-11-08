

import json
import os
import pickle
import string
from random import choice, randint
from flask import request, Response
import hashlib
import requests
import logging as log


def _rjson(obj, rcode=0, rmsg="success"):
    ret = {"ROOT": {"HEADER": {}, "RETURN_CODE": rcode, "RETURN_MSG": rmsg, "BODY": obj}}
    r = Response(json.dumps(ret), mimetype="application/json; charset=utf-8")
    return r


def _gen_rand_str(nc=0):
    if nc == 0:
        min_char, max_char = 4, 8
    else:
        min_char, max_char = nc, nc
    allchar = string.ascii_letters +  string.digits # string.punctuation +
    token = "".join(choice(allchar) for x in range(randint(min_char, max_char)))
    return token

def digest(s: str):
    # m = hashlib.sha256()
    # m.update(s.encode('utf-8'))
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


# 获取当前 k8s 集群的资源闲置数据, 返回格式(失败返回str类型的错误信息):
'''
{"cpu":{"cores_limit":111,"cores_used":111},
 "mem":{"mb_limit":111, "mb_used":111},
 "store":{"mb_limit":111, "mb_used":111},
 }
'''
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
        #labs = ["hard", "used", "resourceType"]
        for d in jo:
            res_type, limit, used = d['resourceType'], d['hard'], d['used']
            if res_type == 'cpu':
                ret['cpu'] = {"cores_limit": float(limit), "cores_used": float(used[:-1]) / 1000}
            elif res_type == 'memory':
                ret['mem'] = {"mb_limit":__sz2mb(limit), "mb_used":__sz2mb(used)}
            elif res_type == 'requests.storage':
                ret['store'] = {"mb_limit":__sz2mb(limit), "mb_used":__sz2mb(used)}
        return ret
    else:
        return f'get res info failed {r.status_code} -- {r.text}'

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

def __sz2mb(x):
    if x[-2:] == 'Gi':
        return float(x[:-2]) * 1024
    elif x[-2:] == 'Mi':
        return float(x[:-2])
    elif x[-2:] == 'Ti':
        return float(x[:-2]) * 1024 * 1024
    else:
        return float(x) / 1024 / 1024

def _uri_map(app, func, methods):
    app.add_url_rule(f"/{func.__name__}", func.__name__, func, methods=methods)


from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
import base64

def _gateway_login(url_ep, user, upass, sess_file=None):
    '''
    登录EP 微服务网关, 以便调用java服务,
    返回  已经验证成功的 requests.session,  以及服务url地址
    '''
    sess = requests.session()

    # 1.1 /EP/genKeyPair
    r1 = sess.get(f"{url_ep}/EP/genKeyPair/{user}")
    print(r1.status_code)  # , r1.text)


    pubkey = r1.json()['ROOT']['BODY']['OUT_DATA']['entity']['publicKey']

    rsa = RSA.importKey(base64.b64decode(pubkey.encode('ascii')))
    cipher = PKCS1_v1_5.new(rsa)

    ciphertext = cipher.encrypt(upass.encode('utf8'))
    userpass = base64.b64encode(ciphertext).decode('ascii')
    print(userpass)

    # 1.2 /EP/login
    login_data = {"ROOT": {"BODY": {"loginNo": user, "password": userpass, "loginType": "2"}}}
    r2 = sess.post(f'{url_ep}/EP/login', json=login_data,
                   headers={"Content-Type": "application/json; charset=utf-8"})
    print(r2.status_code, r2.text)

    # 1.3 /EP/checkPassword
    login_data = {"ROOT": {"BODY": {"phone": user, "password": userpass}}}
    r3 = sess.post(f'{url_ep}/EP/checkPassword', json=login_data,
                   headers={"Content-Type": "application/json; charset=utf-8"})
    print(r3.status_code, r3.text)

    # 1.4 /EP/getOrgList
    url = f'{url_ep}/EP/getOrgList'
    login_data = {"ROOT": {"BODY": {"loginNo": user, "password": userpass}}}
    r4 = sess.post(url, json=login_data,
                   headers={"Content-Type": "application/json; charset=utf-8"})
    print(r4.status_code, r4.text)
    r4_r = r4.json()['ROOT']['BODY']['OUT_DATA']
    org_id = str(r4_r['entity'][0]['org_id'])
    org_name = r4_r['entity'][0]['org_name']

    # 1.5 /login

    url = f'{url_ep}/login'
    payload = dict(username=user, password=userpass, quickLoginFlag="",
                   orgId=org_id, orgName=org_name)
    r5 = sess.post(url, data=payload)
    print(r5.status_code)  # , r5.text)
    # print(r5.headers)
    print(r5.url)

    from urllib.parse import urlparse
    uu = urlparse(r5.url)
    code = uu.query.split("=")[-1]
    redi_base = f"{uu.scheme}://{uu.netloc}"
    redi_netloc = uu.netloc
    print(code)

    # 1.6 /rest/login/oauth2/code/ep-login

    query = f"redirect_uri=http:%252F%252F{redi_netloc}%252F%23%2FloginSelf&registration_id=ep-login&code={code}"
    url = f"{redi_base}/rest/login/oauth2/code/ep-login?{query}"

    r6 = sess.get(url)
    print(r6.status_code, r6.text)
    if r6.status_code == 200:
        obj = r6.json()
        role_id = obj['orgRole'][0]['roleId']

    if sess_file is not None:
        with open(sess_file, 'wb') as fo:
            pickle.dump((sess, redi_base), fo)

    return sess, redi_base

def _call_web_service(uri, payload, _conf):
    session_file = "/tmp/sess.pickle"
    if not os.path.exists(session_file):
        sess, redi_base = _gateway_login(_conf['ep_url'], _conf['ep_user'], _conf['ep_pass'])
    else:
        with open(session_file, 'rb') as fi:
            (sess, redi_base) = pickle.load(fi)

    r = sess.post(f"{redi_base}{uri}", json=payload)
    if r.status_code == 403:  # 如果报错未验证, 则尝试登录
        sess, redi_base = _gateway_login(_conf['ep_url'], _conf['ep_user'], _conf['ep_pass'])
        r = sess.post(f"{redi_base}{uri}", json=payload)
    r.raise_for_status()
    return r.json()


