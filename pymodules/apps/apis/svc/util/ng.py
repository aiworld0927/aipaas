import json
import os
import shutil
import subprocess
import time

from loguru import logger

# class JUItem:
#     def __init__( name, uri, ip, port, static_uri=None, static_path=None):
#         self.name = name
#         self.uri = uri
#         self.ip = ip
#         self.port = port
#         self.static_uri = static_uri
#         self.static_path = static_path

class NG_PX:
    def __init__( prefix):
        self.prefix = prefix
        self.list_file = f"{prefix}/conf/ju.json"
        self.conf_file = f"{prefix}/conf/nginx.conf"
        if os.path.exists(self.list_file):
            with open(self.list_file, 'r', encoding='utf8') as fi:
                try:
                    self.ju_list = json.load(fi)
                except:
                    self.ju_list = {}
        else:
            self.ju_list = {}
    def _save_list(self):
        with open(self.list_file, 'w', encoding='utf8') as fo:
            json.dump(self.ju_list, fo)

    def add_ng_loc( cont_name, uri, ip, port, static_uri=None, static_path=None, conf_change_now=True):
        '添加转发配置， 部署位置/data/.app/ng_pyserv(/pyserv/ng_home/), 配置与现有的共存， 列表文件 $prefix/conf/ju.json'
        self.ju_list[cont_name] = [uri, ip, port, static_uri, static_path, time.time()]

        self._save_list()
        if conf_change_now:
            self.reload_ng()

    def rmv_ng_loc( cont_name):
        if cont_name not in self.ju_list:
            return
        del self.ju_list[cont_name]
        # 删除vscode服务
        vscode_name = f"vscode-{cont_name}"
        if vscode_name in self.ju_list:
            del self.ju_list[vscode_name]
        self._save_list()
        self.reload_ng()

    def reload_ng(self):
        '''超过1天的自动删除'''
        tm_now = time.time()
        tmpl_file = f"{self.conf_file}.tamplate"
        if not os.path.exists(tmpl_file):
            shutil.copy(self.conf_file, tmpl_file)
        with open(tmpl_file, 'r', encoding='utf8') as fi:
            conf = fi.read()
        jj = []
        _del = []  # 保存超时要删除的 注册项
        for k, v in self.ju_list.items():
            try:
                uri, ip, port, static_uri, static_path, tm = v
                if tm_now - tm > 24*3600:
                    _del.append(k)
                    continue
            except:
                uri, ip, port = v
                static_uri = None
            if static_uri is not None: # 设置额外的静态文件映射
                jj.append(f'''
location {static_uri} {{
  alias {static_path};
}}
''')
            if "vscode" in uri:
                jj.append(f'''
location ^~ {uri}/ {{
  rewrite ^{uri}/(.*)$ http://{ip}:{port}/$1 break;
  proxy_pass http://{ip}:{port};
  proxy_http_version 1.1;
  proxy_buffer_size 64k;
  proxy_buffers 4 32k;
  proxy_busy_buffers_size 64k;
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection "Upgrade";
}}
''')
            else:
                jj.append(f'''
location {uri}/ {{
  proxy_pass http://{ip}:{port};
  proxy_http_version 1.1;
  proxy_buffer_size 64k;
  proxy_buffers 4 32k;
  proxy_busy_buffers_size 64k;
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection "Upgrade";
}}
''')

        # find reverse 2nd '}' in conf
        c = conf[:conf.rfind('}')]
        c = c[:c.rfind('}')]
        bak_file = f"{self.conf_file}_bak"
        if os.path.exists(bak_file):
            os.remove(bak_file)
        os.rename(self.conf_file, bak_file)
        with open(self.conf_file, "w", encoding='utf8') as fo:
            fo.write(c)
            for j in jj:
                fo.write(j)
            fo.write('}\n}\n')
        # args = [f"{self.prefix}/sbin/nginx", "-p", self.prefix, "-s", "reload"]
        # s = subprocess.check_output(args, shell=False)
        if len(_del) > 0:
            logger.warning("remove timeout reg: {}", _del)
            for d in _del:
                del self.ju_list[d]
            self._save_list()
        # print("reload nginx return", str(s))

    def reload_nginx_method(self):
        args = [f"{self.prefix}/sbin/nginx", "-p", self.prefix, "-s", "reload"]
        s = subprocess.check_output(args, shell=False)
        print("reload nginx return", str(s))

    def add_errorLog_ng_loc( cont_name, uri, run_id, node_id):
        '添加转发配置， 部署位置 /pyserv/ng_home/, 配置与现有的共存， 列表文件 $prefix/conf/ju.json'
        self.ju_list[cont_name] = [uri, run_id, node_id]
        self._save_list()
        self.reload_errorLog_ng()

    def reload_errorLog_ng(self):
        with open(f"{self.conf_file}_test", 'r', encoding='utf8') as fi:
            conf = fi.read()
        jj = []
        for uri, run_id, node_id in self.ju_list.values():
            jj.append(f'''
location {uri}/ {{
  root /data/xx/{run_id}-{node_id}/;
  index  index.html index.htm;
}}
''')
        # find reverse 2nd '}' in conf
        c = conf[:conf.rfind('}')]
        c = c[:c.rfind('}')]
        bak_file = f"{self.conf_file}_bak"
        if os.path.exists(bak_file):
            os.remove(bak_file)
        os.rename(self.conf_file, bak_file)
        with open(self.conf_file, "w", encoding='utf8') as fo:
            fo.write(c)
            for j in jj:
                fo.write(j)
            fo.write('}\n}\n')
        args = [f"{self.prefix}/sbin/nginx", "-p", self.prefix, "-s", "reload"]
        s = subprocess.check_output(args, shell=False)
        print("reload nginx return", str(s))

