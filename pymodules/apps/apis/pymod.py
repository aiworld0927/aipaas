import base64
import json
import os


def test_base64():
    base_str = "eyJkYl90eXBlIjogIm15c3FsIiwgInVzZXIiOiAicm9vdCIsICJwYXNzd29yZCI6ICIxMjM0NTYiLCAiaG9zdCI6ICIxMjcuMC4wLjEiLCAicG9ydCI6IDMzMDYsICJkYXRhYmFzZSI6ICJpYXNwIn0="
    conn_param = json.loads(base64.decodebytes(base_str.encode("utf-8")))
    print(conn_param)

def test_mod():
    import importlib, sys
    vpath = os.path.abspath(__file__)
    print(vpath)
    sys.path.insert(0, os.path.dirname(vpath))
    mod_list = [f[:-3] for f in os.listdir(os.path.dirname(vpath) + "/svc") if f[-3:] == '.py' and f[:2] != '__']
    print(mod_list)
    for mod_name in mod_list:
        try:
            _mod = importlib.import_module("svc.%s" % mod_name)
        except Exception as x:
            print("import module {} failed {}", mod_name, x)
            continue
        if hasattr(_mod, 'registerHandler'):
            print(" --begin register module {}", mod_name)
            _reg_func = getattr(_mod, 'registerHandler')
            # try:
            #     _reg_func(self.app, conf)
            # except Exception as x:
            #     logger.error(" module {} register failed {}", mod_name, x)
            #     continue
    #         logger.info("module {} registered", mod_name)
    # logger.info("start pyserv on port: {}", self.port)
    # self.app.add_url_rule("/config", "config", _conf_query, methods=["GET"])


if __name__ == '__main__':
    # conf = {}
    test_mod()

