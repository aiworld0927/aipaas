
import json
import string
from random import choices, randint

from flask import Flask, request, Response, abort

def _rjson(obj, rcode=0, rmsg="success"):
    ret = {"ROOT": {"HEADER": {}, "RETURN_CODE": rcode, "RETURN_MSG": rmsg, "BODY": obj}}
    r = Response(json.dumps(ret), mimetype="application/json; charset=utf-8")
    return r

def _gen_rand_str(nc=6):
    if nc == 0:
        #min_char, max_char = 24, 32
        k = randint(24, 32)
    else:
        k = nc
    allchar = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789' # string.ascii_letters +  string.digits # string.punctua/tion +
    token = "".join(choices(allchar, k=k ) )
    return token
