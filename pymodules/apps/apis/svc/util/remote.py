# 封装 通过ssh 在容器中的操作工具类

import os
import re
import traceback
import subprocess
import shlex
import json
import time

import paramiko
import requests
from loguru import logger


class Proc:
    def __init__( stdin, stdout, stderr):
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr


class NodeCmd:
    def __init__( homedir, conninfo, user_name=None, run_env=None):
        self.conninfo = conninfo
        self.ssh = None
        self.home = homedir
        self.user_name = user_name
        self._until_ = '[_PS1_]'
        self.run_env = run_env

    def connect(self):
        ssh = paramiko.SSHClient()

        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cc = self.conninfo
        host, port, username = cc['host'], cc['port'], cc['username']
        pkey_file = cc['client_keys'][0]
        logger.info("--ssh {}@{}:{} -i {}", username, host, port, pkey_file)
        privkey = paramiko.RSAKey.from_private_key_file(pkey_file)
        try:
            ssh.connect(host, port=int(port), username=username, pkey=privkey)
            logger.debug("ssh {}@{}:{} connected!", username, host, port)
        except Exception as x:
            logger.error("ssh connect failed {}, {}", x, traceback.format_exc())
            return False
        self.ssh = ssh
        _exec_ = self._exec_

        # env include PATH not set, only HOME and TERM
        proc = Proc(*ssh.exec_command("bash -s"))
        self.proc = proc

        out = _exec_('export PS1=%s\\\\n' % self._until_)
        print('---set PS1', out)

        _exec_(f"mkdir -p {self.home}")
        _exec_(f"cd {self.home}")

        _exec_(f'export HOME={self.home} && export PATH=$PATH:{self.home}/.local/bin:/data/.local/bin')

        if self.user_name is None:
            out = self._exec_("echo $_USERNAME_")
            self.user_name = out.splitlines()[-1]
        else:
            _exec_(f"export _USERNAME_={self.user_name}")

        # set task environments
        if self.run_env is not None:
            self._exec_(" && ".join([f"export {k}=\"{v}\"" for k, v in self.run_env.items()]))

        return True

    def _exec_( cmd, ln_limit=-1, outs=None, remove_str=None, out_remove_fistline=0):
        proc = self.proc
        proc.stdin.write(cmd + '\n')
        logger.debug('====exec: {}', cmd)
        if outs is None:
            outs = []
        if ln_limit > 0:
            for i in range(ln_limit):
                ln = proc.stdout.readline()
                outs.append(ln)
        else:
            if remove_str:
                remove_str = re.compile(remove_str)
            while True:
                ln = proc.stdout.readline()
                if remove_str:
                    # print('---: [%s]' % ln, ln.strip()==self._until_, repr(ln.strip()), repr(self._until_))
                    ln = re.sub(remove_str, '', ln)
                if ln.strip() == self._until_:
                    break
                outs.append(ln)
        if out_remove_fistline > 0:
            return "".join(outs[out_remove_fistline:])
        else:
            return "".join(outs)

    def _save_text_to_file( text, fpath):
        logger.debug("_save_text_to_file {} {}", fpath, text)
        proc = self.proc
        proc.stdin.write(f"cat <<EOF >{fpath}\n")
        proc.stdin.write(text)
        proc.stdin.write("\nEOF\n")
        outs = []
        while True:
            ln = proc.stdout.readline()
            if ln.strip() == self._until_:
                break
            outs.append(ln)
        return "".join(outs)

    def source_act( name):
        out = self._exec_('source activate %s && export PS1=%s\\\\n' % (name, self._until_))
        return out

    def disconnect(self):
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.ssh.close()


def _fs_get(fs_svc_addr, cont_addr, fs_path, cont_path, is_dir=True):
    '从 fs 向容器传送文件'
    data = {"BODY": {"cont_addr": cont_addr, "is_dir": is_dir,
                     "cont_path": cont_path, "fs_path": fs_path}}
    url = f"{fs_svc_addr}/scp_get" if fs_svc_addr[:7] == 'http://' else f"http://{fs_svc_addr}/scp_get"
    r = requests.post(url, data=json.dumps(data))
    logger.debug("scp_get return {}, {}", r.status_code, r.text)
    if r.status_code != 200:
        logger.error("scp_get http failed {} url: {} data: {}",
                     r.status_code, url, data)
        return False
    rj = r.json()
    rcode = rj.get("ROOT", {}).get("RETURN_CODE", -1)
    if rcode != 0:
        logger.error("scp_get failed, return {} url: {} data: {}",
                     rcode, url, data)
        return False
    return True


def _fs_put(fs_svc_addr, cont_addr, cont_path, fs_path, is_dir=True):
    '从容器向fs传文件'
    data = {"BODY": {"cont_addr": cont_addr, "is_dir": is_dir,
                     "cont_path": cont_path, "fs_path": fs_path}}

    url = f"{fs_svc_addr}/scp_put" if fs_svc_addr[:7] == 'http://' else f"http://{fs_svc_addr}/scp_put"
    r = requests.post(url, data=json.dumps(data))
    logger.debug("fs_put return {}, {}", r.status_code, r.text)
    if r.status_code != 200:
        logger.error("fs_put http failed {}", r.status_code)
        return False
    rj = r.json()
    rcode = rj.get("ROOT", {}).get("RETURN_CODE", -1)
    if rcode != 0:
        logger.error("fs_put failed, return {}", rcode)
        return False
    return True


def fs_scp(_conf, src, dst, is_upload=True, is_dir=False):
    # 从文件服务器获取文件, 到manager主机上
    fs_id, fs_user = _conf['FS_IDENTITY'], _conf['FS_USER']
    fs_host, fs_port = _conf['FS_ADDR'].split(":", maxsplit=1)
    if is_upload:
        args = shlex.split(
            f"scp -q {'-r' if is_dir else ''} -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no "
            f"-P {fs_port} -i {fs_id} "
            f"{src} "
            f"{fs_user}@{fs_host}:{dst}")
    else:
        args = shlex.split(
            f"scp -q {'-r' if is_dir else ''} -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no "
            f"-P {fs_port} -i {fs_id} "
            f"{fs_user}@{fs_host}:{src} "
            f"{dst}")

    logger.debug("scp cmd: [{}]", args)
    try:
        out = subprocess.check_output(args)
    except Exception as x:
        logger.error("_scp failed: {}", str(x))
        return False
    print(out)
    return True


class PLTask:
    '''
    流水线任务处理相关功能实现,  从manager到流水线执行容器的操作
    '''

    def __init__( local_conf, cont_addr, exp_name, run_id, user_name=None):
        self._conf = local_conf
        self.user_name = user_name
        self.exp_name = exp_name
        self.run_id = run_id
        self.home = f"/data/xx/{exp_name}_{run_id}"  # home目录规则
        self.cont_addr = cont_addr

    def __conn_cont(self):
        _conf = self._conf
        host, port = self.cont_addr.split(":", maxsplit=1)
        nt = NodeCmd(self.home,
                     conninfo=dict(
                         host=host, port=int(port),
                         username=_conf['CONT_USER'], client_keys=[_conf['CONT_IDENTITY']], known_hosts=None),
                     user_name=self.user_name)
        if not nt.connect():
            raise Exception("connect ssh failed")
        self.user_name = nt.user_name
        self.fileserver_url = _conf['fileserver_url']
        return nt

    def __enter__(self):
        self.nt = self.__conn_cont()
        # 1 切换 venv
        # out = self.nt.source_act('gm')
        # logger.info("pyenv return {}", out)
        return self

    def __exit__( exc_type, exc_val, exc_tb):
        logger.info("--with exit--")
        self.close()

    def close(self):
        self.nt.disconnect()

    def start_jupyter( node_id):
        '在容器中 启动图形化建模的训练过程'
        nt = self.nt
        app_dir = '.app'

        # 当前目录: $LOGDIR/GM_${EXPE_NAME}_${RUN_ID}
        # logger.info("clear path...")
        # nt._exec_(f"rm -rf {app_dir} .cache .datasets .config * && mkdir -p {app_dir} .jupyter .library")
        nt._exec_(f"mkdir -p .jupyter {app_dir}")

        token = "123"
        base_uri = "/jupyter-gm"
        _conf = {"NotebookApp": {
            # "password": sha1_token,
            "token": token,
            "open_browser": False,
            "base_url": base_uri,
            "allow_origin": "*"},
            "ServerApp": {
                "allow_origin": "*", "ip": "*"}}
        nt._save_text_to_file(json.dumps(_conf), f"{self.home}/.jupyter/jupyter_notebook_config.json")

        nt._exec_(f"mkdir -p {node_id}")
        # 启动jupyter到后台 (这里直接启动， 会产生 defunct 进程， 比较难看， 准备交给mng.py进程来处理
        out = nt._exec_("which jupyter", out_remove_fistline=1)
        ju_path = out.strip()
        start_cmd = {"cwd": f"{self.home}/{node_id}", "env": {
            "PATH": f"{os.path.dirname(ju_path)}:{self.home}/.local/bin", "HOME": self.home},
                     "args": f"jupyter notebook --port=8178 --notebook-dir={self.home}/{node_id} --ip=0.0.0.0 --no-browser".split()}
        nt._save_text_to_file(json.dumps(start_cmd), f"/tmp/.app/once_proc/{time.time()}.json")
        # out = nt._exec_(f"echo \"jupyter notebook --port=8178 --notebook-dir=./{node_id} --ip=0.0.0.0 --no-browser >stdout 2>&1 \" >.start.sh",
        #                 out_remove_fistline=1)
        # out = nt._exec_("sh .start.sh &")
        logger.info("start jupyter return {}", out)

        # out = nt._exec_("cat stdout && echo", out_remove_fistline=1)
        # logger.info("stdout: {}", out)

    def stop_jupyter(self):
        nt = self.nt

        # out = nt._exec_(f"jupyter notebook stop 8178",   out_remove_fistline=1)
        # logger.info("start jupyter return {}", out)
        out = nt._exec_("which jupyter", out_remove_fistline=1)
        ju_path = out.strip()
        start_cmd = {"cwd": self.home, "env": {
            "PATH": f"{os.path.dirname(ju_path)}:{self.home}/.local/bin", "HOME": self.home},
                     "args": f"jupyter notebook stop 8178".split()}
        nt._save_text_to_file(json.dumps(start_cmd), f"/tmp/.app/once_proc/{time.time()}.json")

    def prepare_env( container_name, params):
        '''启动流水线编辑, 分配容器完成后, 从文件服务器上恢复数据到容器中
        计算容器中的路径: f"/data/xx/{exp_name}_{run_id}"
        文件服务器上的路径:  /data/.models/${username}/PL_${exp_name}_${run_id}
        '''
        nt = self.nt
        # tmp_dir = '.app'
        _conf = self._conf

        # logger.info("upload mods files...")
        # # 3 上传模块代码目录,  模块代码文件作为整体, 存放在文件服务器上 /data/.app/guimod.tgz, 下载到容器 .datasets 下
        # if not _fs_get(self.fileserver_url, self.cont_addr, '/data/.app/guimod.tgz',
        #                f"{self.home}/{tmp_dir}/", is_dir=False):
        #     logger.error("uploads mods failed!")
        #     return False
        # out = nt._exec_(f"tar xzf ./{tmp_dir}/guimod.tgz -C ./{tmp_dir} && rm -f ./{tmp_dir}/guimod.tgz")
        # logger.info("update mods file return {}", out)

        # 从fs恢复运行数据 (失败则忽略)
        # 运行数据在fs上的存储位置为: /data/.models/${username}/PL_${expe_name}_${run_id},  如果是新建的则可能没有数据, 最后一层目录名与容器中home相同
        # 2022-3-23 使用用户卷后则不再恢复数据
        # pname = f"PL_{self.exp_name}_{self.run_id}"
        # remote_dir = f"/data/.models/{self.user_name}/{pname}"
        # if not _fs_get(self.fileserver_url, self.cont_addr, remote_dir, self.home):
        #     logger.warning("download pipeline run status data failed, maybe a new pipeline")
        # else:
        #     # 把目录下的文件都 移动到当前目录(注意会有 .隐藏文件,  使用 shopt -s dotglob)
        #     out = nt._exec_(f"shopt -s dotglob && mv -f ./{pname}/* ./ && rmdir ./{pname}")
        #     logger.info("mv files out: {}", out)

        # 上传环境配置，  需要在恢复运行数据后进行， 否则会被覆盖掉
        _conf = {"fileserver_url": self.fileserver_url, "db_conn_str": _conf['db_conn_str'],
                 "user": self.user_name, "manager_svc_addr": _conf['__svc_addr__'],
                 "container_name": container_name, "cont_sftp_addr": self.cont_addr, "image": params['__image']
                 }
        nt._save_text_to_file(json.dumps(_conf), ".conf.json")

        # # 启动文件服务到端口8181上
        # py_path = nt._exec_("which python", out_remove_fistline=1).strip()
        # start_cmd = {"cwd": self.home, "env": {
        #     "PATH": f"{os.path.dirname(py_path)}:{self.home}/.local/bin", "HOME": self.home,
        #     "PYTHONPATH":"/app/_app/guimod"
        # },
        #     "args": [py_path, "-c", f"import sitech.aipaas.gm.runner.plservice as pl; pl.main(8181)"]}
        # nt._save_text_to_file(json.dumps(start_cmd), f"/tmp/.app/once_proc/plservice.json")

        # 需要再延迟检查 服务是否可用 /pl/ping
        return True

    def tmp_duplicate( params):
        '''
        流水线模板导出为新的流水线后，由于self.home目录下的.local文件夹重新生成
        缺少原本流水线的私有依赖包，由此会出现流水线运行缺包的情况，因此使用此功能在流水线初次导出的时候，
        将依赖包导入.local目录，以保证程序正常运行
        '''
        logger.info('start tmp_duplicate')
        # 复制.local环境包
        _fs_get(self.fileserver_url, self.cont_addr, f"{params['tmp_dir']}/.local", self.home)
        # 复制run目录下执行代码
        _fs_get(self.fileserver_url, self.cont_addr, f"{params['tmp_dir']}/run", self.home)

        return True

    def backup_env(self):
        '退出流水线编辑模式,  容器内环境文件备份到文件服务器上, 对应的反向过程为 prepare_env'
        nt = self.nt

        # shutdown plservice
        out = nt._exec_(f"kill `cat ~/.app/plservice.pid`")
        logger.info("stop plservice return {}", out)

        # shutdown jupyter if exists
        self.stop_jupyter()

        # # upload directory to fs
        # 2022-3-23 用户卷后不再备份数据
        # dname = f"PL_{self.exp_name}_{self.run_id}"
        # nt._exec_(f"rm -rf /data/xx/{dname} && ln -s {self.home} /data/xx/{dname}")
        #
        # if not _fs_put(self.fileserver_url, self.cont_addr,
        #                cont_path=f"/data/xx/{dname}",
        #                fs_path=f"/data/.models/{self.user_name}/", is_dir=True):
        #     logger.error("backup pipeline data failed user:{} path: {}", self.user_name, dname)
        #     return False

        return True

    def run_task( flow_doc, container_name=None, run_to=None, open_nb=False):
        '在容器中 启动图形化建模的训练过程'
        nt = self.nt
        tmp_dir = '.app'

        # logger.info("clear path...")
        # # 当前目录: $LOGDIR/GM_${EXPE_NAME}_${RUN_ID}
        # nt._exec_(f"rm -rf {tmp_dir} .cache .datasets .config * && mkdir {tmp_dir}")

        logger.info("upload configure files ...")
        # 1 上传流程文档
        nt._save_text_to_file(json.dumps(flow_doc), f"{self.home}/src.atf")

        # 2 上传配置
        _conf = {"fileserver_url": self.fileserver_url, "db_conn_str": self._conf['db_conn_str'],
                 "cont_sftp_addr": self.cont_addr, "user": self.user_name, "container_name": container_name}
        nt._save_text_to_file(json.dumps(_conf), f"{self.home}/_conf.json")

        # logger.info("upload mods files...")
        # #3 上传模块代码目录,  模块代码文件作为整体, 存放在文件服务器上 /data/.app/guimod.tgz, 下载到容器 .datasets 下
        # if not _fs_get(self.fileserver_url, self.cont_addr, '/data/.app/guimod.tgz', f"{self.home}/{tmp_dir}/"):
        #     logger.error("uploads mods failed!")
        #     return False
        # out = nt._exec_(f"tar xzf ./{tmp_dir}/guimod.tgz -C ./{tmp_dir}")
        # logger.info("update mods file return {}", out)
        pypath = "/app/_app/guimod"
        nt._exec_(f"export PYTHONPATH={pypath}")
        logger.info("set PYTHONPATH to {}", pypath)

        # 5 启动训练进程
        # out = nt._exec_(f"python -c \"from sitech.aipaas.gm.runner.main import main_train; main_train('src.atf', '_conf.json')\" >stdout 2>&1 &",
        #                 out_remove_fistline=1)
        # logger.info("start process return {}", out)
        #
        # out = nt._exec_("cat stdout && echo", out_remove_fistline=1)
        # logger.info("stdout: {}", out)

        # return out.find('Traceback') < 0 and out.find(' Exit') < 0
        py_path = nt._exec_("which python", out_remove_fistline=1).strip()
        start_cmd = {"cwd": self.home, "env": {
            "PATH": f"{os.path.dirname(py_path)}:{self.home}/.local/bin", "HOME": self.home,
            "PYTHONPATH": pypath
        },
                     "args": [py_path, "-c",
                              f"from sitech.aipaas.gm.runner.main import main_train; main_train('src.atf', '_conf.json', run_to={repr(run_to)}, open_nb={open_nb})"]}
        nt._save_text_to_file(json.dumps(start_cmd), f"/tmp/.app/once_proc/{time.time()}.json")

        return True
