import string
import json
from random import choice, randint, choices

from flask import Response


def _rjson(obj, rcode=0, rmsg="success"):
    ret = {"ROOT": {"HEADER": {}, "RETURN_CODE": rcode, "RETURN_MSG": rmsg, "BODY": obj}}
    r = Response(json.dumps(ret), mimetype="application/json; charset=utf-8")
    return r


__allchar = string.ascii_letters + string.digits  # string.punctuation +


def _gen_rand_str(nc=0):
    if nc == 0:
        nc = randint(4, 8)
    return "".join(choices(__allchar, k=nc))
