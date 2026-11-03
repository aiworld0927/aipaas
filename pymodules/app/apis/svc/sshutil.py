import shutil
import traceback

import paramiko
import time, os, shlex
from loguru import logger
import subprocess
import re
import json
import yaml
import random
import time
import operator
import copy
import requests
from jsonpath_ng import jsonpath, parse as jsonpath_parse

class Proc:
    def __init__(self, stdin, stdout, stderr):
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr

def testy():
    import paramiko, os, time
    with paramiko.SSHClient() as ssh:
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        host, port, username = '172.18.238.17', '3022', '_base_'
        privkey = paramiko.RSAKey.from_private_key_file(os.path.expanduser("~/.ssh/id_rsa"))
        ssh.connect(host, port=int(port), username=username, pkey=privkey)
        print("connected!")

        proc = Proc( ssh.exec_command("bash -s") )
        proc.stdin.write('')
        # sftp = ssh.open_sftp()
        # t0 = time.time()
        # sftp.put('jp/package.tar.gz', '/data/1-3/package.tar.gz')
        # print("time", time.time()-t0)
        # sftp.close()

class ContainerCtl:
    def __init__(self, user, host, port, identity, app_user, home=None):
        self.ssh = None
        self.proc = None

        self.home = home
        self.host = host
        self.port = port
        self.identity = identity
        self.user = user
        self.app_user = app_user
        self._until_ = '[_PS1_]'

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()

    def disconnect(self):
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.ssh.close()

    def connect(self):
        ssh = paramiko.SSHClient()

        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        privkey = paramiko.RSAKey.from_private_key_file(self.identity)
        try:
            ssh.connect(self.host, port=int(self.port), username=self.user, pkey=privkey)
            # logger.debug("connected!")
        except Exception as x:
            logger.error("ssh connect failed {}, {}", x, traceback.format_exc())
            return False
        self.ssh = ssh
        _exec_ = self._exec_

        # env include PATH not set, only HOME and TERM
        proc = Proc(*ssh.exec_command("bash -s"))
        self.proc = proc

        out = _exec_('export PS1=%s\\\\n' % self._until_)

        if self.home is not None:
            _exec_(f'export HOME={self.home} '
                   f'&& export PATH=$PATH:{self.home}/.local/bin:/data/.local/bin'
                   )
        else:
            out = _exec_('echo $HOME', out_remove_fistline=1)
            self.home = out.strip()
            _exec_(f'&& export PATH=$PATH:{self.home}/.local/bin:/data/.local/bin' )


        if self.app_user is None:
            out = _exec_("echo $_USERNAME_", out_remove_fistline=1)
            self.app_user = out.splitlines()[-1].strip()
        else:
            _exec_(f"export _USERNAME_={self.app_user}")

        return True

    def change_home(self, newhome):
        self.home = newhome
        self._exec_(f'export HOME={self.home} '
                          f'&& export PATH=$PATH:{self.home}/.local/bin:/data/.local/bin && cd'
                          )

    def _exec_(self, cmd, ln_limit=-1, outs=None, remove_str=None, out_remove_fistline=0):
        proc = self.proc
        proc.stdin.write(cmd + '\n')
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
                    ln = re.sub(remove_str, '', ln)
                if ln.strip() == self._until_:
                    break
                outs.append(ln)
        if out_remove_fistline>0:
            return "".join(outs[out_remove_fistline:])
        else:
            return "".join(outs)

    def source_act(self, name):
        out = self._exec_('source activate %s && export PS1=%s\\\\n' % (name, self._until_) )
        return out



class NodeTask:
    def __init__(self, wf_id, task_id, proc_id, node_id, conninfo, user_name=None):
        self.wf_id = wf_id
        self.task_id = task_id
        self.proc_id = proc_id
        self.node_id = node_id
        self.conninfo = conninfo
        self.local_base_dir = '.'
        self.ssh = None
        self._base_ = ''
        self._home_ = None
        self.user_name = user_name
        self._until_ = '[_PS1_]'

    def connect(self, wd=None, run_env=None):
        ssh = paramiko.SSHClient()

        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cc = self.conninfo
        host, port, username = cc['host'], cc['port'], cc['username']
        pkey_file = cc['client_keys'][0]
        logger.info("--ssh {}@{}:{} -i {}", username, host, port, pkey_file)
        privkey = paramiko.RSAKey.from_private_key_file(pkey_file)
        try:
            ssh.connect(host, port=int(port), username=username, pkey=privkey)
            logger.debug("connected!")
        except Exception as x:
            logger.error("ssh connect failed {}, {}", x, traceback.format_exc())
            return False
        self.ssh = ssh
        _exec_ = self._exec_

        # env include PATH not set, only HOME and TERM
        proc = Proc(* ssh.exec_command("bash -s"))
        self.proc = proc

        out = _exec_('export PS1=%s\\\\n' % self._until_)
        print('---set PS1', out)

        # determine the work directory
        if wd is not None and wd != '':
            if wd[:2] == '~/':
                out = self._exec_('echo $LOGDIR')
                self.log_dir = out.strip().split('\n')[-1].strip()
                self.log_dir_c = f"{self.log_dir}/{wd[2:]}"
            else:
                self.log_dir_c = wd
        else:
            out = _exec_('echo $LOGDIR')
            self.log_dir = out.strip().split('\n')[-1].strip()
            if self.log_dir.find('$') >= 0:
                self.log_dir = '/data'
            self.log_dir_c = f"{self.log_dir}/{self.task_id}-{self.node_id}"
        _exec_(f"mkdir -p {self.log_dir_c}")
        _exec_(f"cd {self.log_dir_c}")
        logger.debug("logdir_c: {}", self.log_dir_c)

        _exec_(f'export HOME={self.log_dir_c} '
                          f'&& export PATH=$PATH:{self.log_dir_c}/.local/bin:/data/.local/bin'
                          )

        if self.user_name is None:
            out = self._exec_("echo $_USERNAME_")
            self.user_name = out.splitlines()[-1]
        else:
            _exec_(f"export _USERNAME_={self.user_name}")

        # set task environments
        if wd is not None and wd != '':
            pass
        else:
            envs = [f"LOGDIR_C=\"{self.log_dir_c}\"", f"export PROC_ID=\"{self.proc_id}\"",
                    f"WF_ID=\"{self.wf_id}\"", f"TASK_ID=\"{self.task_id}\"",
                    f"export NODE_ID=\"{self.node_id}\"", f"_base_=\"{self.log_dir_c}\""]
            for env in envs:
                self._exec_(f"export {env}")
        if run_env is not None:
            for k, v in run_env.items():
                self._exec_(f"export {k}=\"{v}\"")

        return True

    def change_home(self, newhome):
        # 删除原来的目录
        self._exec_(f"mkdir -p {newhome}")
        try:
            self._exec_(f"mv -f {self.log_dir_c}/.id_1 {newhome}/ && rm -rf {self.log_dir_c}")
        except: pass
        self.log_dir_c = newhome
        self._exec_(f"cd {self.log_dir_c}")
        logger.debug("logdir_c: {}", self.log_dir_c)

        self._exec_(f'export HOME={self.log_dir_c} '
                          f'&& export PATH=$PATH:{self.log_dir_c}/.local/bin:/data/.local/bin'
                          )

    def _exec_(self, cmd, ln_limit=-1, outs=None, remove_str=None, out_remove_fistline=0):
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
            try:
                while True:
                    ln = proc.stdout.readline()
                    if remove_str:
                        ln = re.sub(remove_str, '', ln)
                    if ln.strip() == self._until_:
                        break
                    outs.append(ln)
            except Exception as e:
                logger.error(f"Exception occurred: {e}")

            # while True:
            #     logger.debug("**********************")
            #     ln = proc.stdout.readline()
            #     logger.debug(f"ln {ln}")
            #     if remove_str:
            #         #print('---: [%s]' % ln, ln.strip()==self._until_, repr(ln.strip()), repr(self._until_))
            #         ln = re.sub(remove_str, '', ln)
            #     if ln.strip() == self._until_:
            #     # if ln.strip().endswith( self._until_ ):
            #     #     x = ln.strip()
            #     #     if len(x) > len(self._until_):
            #     #         outs.append(x[:-len(self._until_)])
            #         break
            #     outs.append(ln)
        if out_remove_fistline>0:
            return "".join(outs[out_remove_fistline:])
        else:
            return "".join(outs)

    def source_act(self, name):
        out = self._exec_('source activate %s && export PS1=%s\\\\n' % (name, self._until_) )
        return out

    def disconnect(self):
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.ssh.close()

    def install_dependency(self, dep, pypi_server=None):
        '''
- dependency
  - package\[\]  预定义的依赖包
    - name 包的名称, 包的定义中, 包含了相关文件和安装脚本, 以及环境变量
    - target_path  包的安装路径
  - pip\[\]  python包, 每个包的格式: `<pkg-name>[==<ver>]`
  - files\[\] 依赖的文件或目录
    - path   源文件或目录
    - target_path  文件上传后的目标路径, only for sftp
        '''
        out = None
        self._dep = dep
        if 'files' in dep:
            for p in dep['files']:
                self.install_file(p['path'], p['target_path'])
        if 'package' in dep:
            for p in dep['package']:
                self.install_pkg(p['name'], target_path=p['target_path'])
        if 'pip' in dep:
            out = self.install_pip(dep['pip'], pypi_server)
        return out

    def install_pip(self, pip_list, pypi_server):
        '''install pip packages'''
        if pypi_server is None or pypi_server == '':
            logger.warning("install_pip but without pypi_server given")
            return
        r = re.match(r'http://([\d.]+):\d+', pypi_server)
        if r is None:
            logger.warning("pypi_server {} not valid", pypi_server)
            return
        pypi_host = r.group(1)
        p = " ".join([f"'{i}'" for i in pip_list])

        # --user conflicted with HOME=/,  can not read stderr
        cmd = f"pip install -i {pypi_server} --trusted-host {pypi_host} -q --user {p}"
        out = self._exec_(cmd)
        return out
        # print("stderr", self.proc.stderr.readline() )

    def _scp(self, src, dst, is_upload=True, is_dir=True):
        c = self.conninfo
        if is_upload:
            args = shlex.split(f"scp -q {'-r' if is_dir else ''} -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no "
                           f"-P {c['port']} -i {c['client_keys'][0]} "
                           f"{src} "
                           f"{c['username']}@{c['host']}:{dst}")
        else:
            args = shlex.split(f"scp -q {'-r' if is_dir else ''} -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no "
                           f"-P {c['port']} -i {c['client_keys'][0]} "
                           f"{c['username']}@{c['host']}:{src} "
                           f"{dst}")
        logger.debug("scp cmd: [{}]", " ".join(args) )
        try:
            out = subprocess.check_output(args)
        except Exception as x:
            logger.error("_scp failed: {}", str(x))
            return False
        print(out)
        return True

    def install_pkg(self, name, target_path=''):
        'upload package.tar.gz and unpack it'
        print('begin to install package', name)
        print(self._exec_("pwd"))

        t0 = time.time()
        self._scp(f"{self.local_base_dir}/{name}/package.tar.gz", f"{self.log_dir_c}/{target_path}/", is_upload=True)
        print('upload time', time.time()-t0)

        envfile = f"{self.local_base_dir}/{name}/env"
        if os.path.exists(envfile):
            for ln in open(envfile):
                if ln.strip() != '':
                    self._exec_(ln.strip())

        installfile = f"{self.local_base_dir}/{name}/install.sh"
        if os.path.exists(installfile):
            self._exec_(f"_target_={self.log_dir_c}/{target_path}")
            for ln in open(installfile):
                if ln.strip() != '':
                    self._exec_(ln.strip())

        print(self._exec_("echo $PATH $LOGDIR_C"))

    def install_file(self, path, target=''):
        fpath = f"{self.local_base_dir}/{self.wf_id}/{path}"
        if not os.path.exists(fpath):
            logger.error("fpath: {} not exists", fpath)
            return
        self._scp(fpath, f"{self.log_dir_c}/{target}", is_upload=True)

    def uninstall_files(self, files):
        ff = []
        for p in files:
            ff.append(f"{self.log_dir_c}/{p['target_path']}/{p['path']}" )
        self._exec_(f"rm -rf {' '.join(ff)}")

    def uninstall_pkg(self, pkg):
        for p in pkg:
            name, target_path = p['name'], p['target_path']
            uninstallfile = f"{self.local_base_dir}/{name}/uninstall.sh"
            if os.path.exists(uninstallfile):
                self._exec_(f"_target_={self.log_dir_c}/{target_path}")
                for ln in open(uninstallfile):
                    if ln.strip() != '':
                        self._exec_(ln.strip())

    def uninstall_pip(self, pip_list):
        self._exec_(f"rm -rf {self.log_dir_c}/.local/lib")

    def uninstall_dependency(self):
        '''remove dependency on exit'''
        if 'files' in self._dep:
            self.uninstall_files(self._dep['files'])
        if 'package' in self._dep:
            self.uninstall_pkg(self._dep['package'])
        if 'pip' in self._dep:
            self.uninstall_pip(self._dep['pip'])

    def run_cmd(self, cmd):
        'run cmd in background'
        out = self._exec_(f"{cmd} 1>stdout 2>stderr &")
        # [1] 1078
        jobid = out.strip().split('\n')[-1].strip()
        print('--- jobid', jobid)
        #    [1]      1078
        self.job_idx, self.job_pid = [i.strip() for i in jobid.split(maxsplit=1)]
        return self.job_pid

    def check_cmd(self):
        '''
        check the background job status,
         return (status, exit-code)
          status: running not-run done failed
          exit-code: 0 or not-0
        '''
        if not hasattr(self, 'job_idx') or self.job_idx is None:
            return 'not-run', 0
        out = self._exec_("jobs")
        for ln in out.split('\n'):
            if ln.startswith(self.job_idx):
                print(ln)
                # [1]+  Running                 ./a.sh &
                # [1]+  Exit 1                  ./a.sh
                # [1]+  Done                    sleep 5
                ps = ln.split()
                if ps[1] == 'Running':
                    return 'running', 0
                elif ps[1] == 'Done':
                    return 'done', 0
                elif ps[1] == 'Exit':
                    return 'failed', int(ps[2])
        return 'not-run', 0

    def kill_cmd(self):
        if hasattr(self, 'job_pid'):
            self._exec_(f"kill -9 {self.job_pid}")


import os
def main():
    nt = NodeTask(wf_id='123', task_id="1", proc_id="2", node_id="3",
                  conninfo=dict(
                      # host='172.18.238.17', port=3022,
                      #  host='127.0.0.1', port=2022,
                      host='172.18.231.76', port=3387,
                      username='_base_', client_keys=[os.path.expanduser('~/.ssh/id_rsa_tr')], known_hosts=None))
    nt.connect()

    jj = {"cmd": "spark-submit --master yarn --deploy-mode cluster --archives pyenv.tar.gz#env "
                "--conf spark.yarn.maxAppAttempts=1 app.py /tmp/train.csv /tmp/train4",
        "dependency": {
        "package":[
            {"name":"jp", "target_path":""}
        ],
        "files":[
            {"path":"pyenv.tar.gz", "target_path":""},
            {"path":"app.py", "target_path":""}
        ],
        "pip":[]
    }}

    jj = {"cmd": "sleep 4",
        "dependency": {
        "package":[],
        "files":[],
        "pip":["numpy", "py4j", "psutil>=5.6.0", "xlrd==1.2.0"]
    }}

    nt.install_dependency(jj["dependency"], pypi_server="http://172.18.243.19:1777")
    # await nt.install_pkg('jp', )

    out = nt._exec_("echo $_USERNAME_")
    print("username:---- ", out.splitlines()[-1])

    nt.run_cmd(jj["cmd"])
    while True:
        status, ecode = nt.check_cmd()
        if status != 'running':
            break
        time.sleep(2)
    print('running status', status, ecode, nt.job_pid)


    nt.disconnect()

class NNI_Task:
    def __init__1(self, local_conf, cont_addr, exp_name, run_id, user_name=None, dep_path=None):
        self._conf = local_conf
        self.user_name = user_name
        self.exp_name = exp_name
        self.run_id = run_id
        self.nt = self.__conn_cont(local_conf, cont_addr, exp_name, run_id, dep_path)

    def __init__(self, local_conf, cont_addr, task_id, exp_name=None,user_name=None,node_id=None, dep_path=None, need_fs=False):
        self._conf = local_conf
        self.cont_addr = cont_addr
        self.dep_path = dep_path
        self.user_name = user_name
        self.run_id=node_id   # node_id 用作 run_id
        self.exp_name = exp_name
        self.need_fs = need_fs


    def __conn_cont(self, _conf, cont_addr, task_id, node_id, dep_path):
        host, port = cont_addr.split(":", maxsplit=1)
        nt = NodeTask(wf_id='123', task_id=task_id, proc_id="2", node_id=node_id,
                      conninfo=dict(
                          host=host, port=int(port),
                          username='_base_', client_keys=[_conf['CONT_IDENTITY']], known_hosts=None),
                      user_name=self.user_name)
        if not nt.connect(dep_path):
            raise Exception("connect ssh failed")
        self.fileserver_url = _conf['fileserver_url']
        if self.need_fs:
            nt._scp(self._conf['FS_IDENTITY'], f"{nt.log_dir_c}/.id_1", is_upload=True, is_dir=False)
            nt._exec_(f"chmod 600 {nt.log_dir_c}/.id_1")
        return nt


    def __enter__(self):
        self.nt = self.__conn_cont(self._conf, self.cont_addr, self.exp_name, self.run_id, self.dep_path)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        logger.info("--with exit--")
        self.close()

    def close(self):
        if self.need_fs:
            self.nt._exec_(f"rm {self.nt.log_dir_c}/.id_1")
        self.nt.disconnect()

    def _fs_getdir(self, src_path, dst_path):
        '从文件服务器上下载目录(only directory)'
        if not self.need_fs:
            nt = self.nt
            nt._scp(self._conf['FS_IDENTITY'], f"{nt.log_dir_c}/.id_1", is_upload=True, is_dir=False)
            nt._exec_(f"chmod 600 {nt.log_dir_c}/.id_1")
            self.need_fs = True
        fs_host, fs_port = self._conf['FS_ADDR'].split(":", maxsplit=1)
        fs_user = self._conf['FS_USER']
        out = self.nt._exec_(f"rm -rf {dst_path} && scp -r -q "
                             f" -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -P {fs_port} "
                             f"-i ./.id_1 "
                             f"{fs_user}@{fs_host}:{src_path} {dst_path} ")
        if out.find('no such file or directory') >= 0:
            return False
        return True
    def _fs_putdir(self, src_path, dst_path):
        '上传目录到文件服务器上(only directory)'
        if not self.need_fs:
            nt = self.nt
            nt._scp(self._conf['FS_IDENTITY'], f"{nt.log_dir_c}/.id_1", is_upload=True, is_dir=False)
            nt._exec_(f"chmod 600 {nt.log_dir_c}/.id_1")
            self.need_fs = True
        fs_host, fs_port = self._conf['FS_ADDR'].split(":", maxsplit=1)
        fs_user = self._conf['FS_USER']
        out = self.nt._exec_(f"scp -r "
                             f" -q -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -P {fs_port} "
                             f"-i {self.nt.log_dir_c}/.id_1 "
                             f" {src_path} {fs_user}@{fs_host}:{dst_path}/ "
                             f" && rm -rf {self.nt.log_dir_c}/.id_1")
        return out

    def _fs_exec(self, cmd):
        '在文件服务器上执行命令'
        if not self.need_fs:
            nt = self.nt
            nt._scp(self._conf['FS_IDENTITY'], f"{nt.log_dir_c}/.id_1", is_upload=True, is_dir=False)
            nt._exec_(f"chmod 600 {nt.log_dir_c}/.id_1")
            self.need_fs = True
        fs_host, fs_port = self._conf['FS_ADDR'].split(":", maxsplit=1)
        fs_user = self._conf['FS_USER']
        out = self.nt._exec_(
            f"ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p {fs_port} -i ./.id_1"
            f" {fs_user}@{fs_host} {cmd}", out_remove_fistline=2)
        return out

    def get_dataset(self, dataset):
        '在容器中下载指定数据集到， 其中的文件存放到 $LOG_DIR_C/data 目录下'
        if dataset is None:
            logger.warning("dataset is None")
            return

        if dataset.startswith('/'):
            ds_path = dataset
        else:
            ds_path = f"/data/.datasets/{self.nt.user_name}/{dataset}"
        if ds_path[-1] == '/':
            ds_path = ds_path[:-1]
        if not _fs_get(self._conf['fileserver_url'], self.cont_addr, ds_path, f"{self.nt.log_dir_c}"):
            logger.error("in get_dataset, _fs_get failed")
            return False
        self.nt._exec_(f"mv {self.nt.log_dir_c}/{os.path.basename(ds_path)} {self.nt.log_dir_c}/data")  # 更改目录名
        return True

        # put id file
        #self.nt._scp(self._conf['FS_IDENTITY'], f"{self.nt.log_dir_c}/.id_1", is_upload=True, is_dir=False)
        # fs_host, fs_port = self._conf['FS_ADDR'].split(":", maxsplit=1)
        # out = self.nt._exec_(f"chmod 600 {self.nt.log_dir_c}/.id_1 && rm -rf ./data && scp -q -r"
        #                      f" -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -P {fs_port} "
        #                      f"-i {self.nt.log_dir_c}/.id_1 "
        #                      f"{self._conf['FS_USER']}@{fs_host}:{ds_path} ./data "
        #                      f"&& rm -rf {self.nt.log_dir_c}/.id_1")
        # logger.info("scp fs to container output:{}", out)
        # out = self.nt._exec_(f"ls -l ./data")
        # if out.find("cannot access") >= 0:
        #     return False
        # else:
        #     # 检查如果 ~/data 目录中只有一个目录（这种情况下是 hdfs 同步文件后增加的一层目录), 需要把该目录下的全部文件移出来
        #     fl = out.splitlines()
        #     if len(fl) == 2 and fl[1][0] == 'd':
        #         dname = fl[1].split()[-1]
        #         self.nt._exec_(f"mv ./data/{dname}/* ./data")
        #
        #     return True

    def save_data_to_file(self, data, remote_path):
        fname = os.path.basename(remote_path)
        tmp_fname = f"{self._conf.get('tmp_dir', '/tmp')}/{fname}"
        try:
            with open(tmp_fname, 'w', encoding='utf-8') as fo:
                fo.write(data)
            self.nt._scp(tmp_fname, remote_path, is_upload=True, is_dir=False)
        finally:
            try:os.remove(tmp_fname)
            except: pass

    def _config_config(self, nt, params, t_conf, task_path):
        '''修改启动任务的配置文件, 包括 config.yaml, search_space.json
        返回 None 为成功,  否则则是错误信息
        '''
        # tmp_file = f"/tmp/{time.time()}.yaml"
        with open(f"{task_path}/config.yaml", "r", encoding="utf8") as fi:
            cc = yaml.load(fi, Loader=yaml.FullLoader)
        cmd = cc.get('trial', {}).get('command', '')
        _modified = False
        if "max_duration" in params:  # 检查设置最长训练时间（单次）
            cc['maxExecDuration'] = params["max_duration"] + "m"
            _modified = True
        if cmd.find('{') >= 0 and cmd.find('}'):  # 检查并替换 trial.command 中的变量
            cmd = cmd.format(**params)
            cc['trial']['command'] = cmd
            _modified = True

        # 检查入参中是否有 nni_ 为前缀的参数, 有则替换到对应的参数中
        for k, v in params.items():
            if k[:4] == 'nni_':
                k = k[4:]
                cc[k] = v
                _modified = True

        # done 调整启动进程的处理， 是否需要启动tensorboard
        if t_conf.get("start_tensorboard", False):
            nt._exec_(f"export _TB_PORT={self._conf['tensorboard_port']}")  # 把tb的端口放到环境变量中
            # 替换trial.command， 在前面添加 python runcmd.py
            cc['trial']['command'] = f"python runcmd.py {cmd}"
            # 上传 runcmd.py 到 .task 目录下
            nt._scp(f"{self._conf['task_base']}/runcmd.py", f"{nt.log_dir_c}/.task/", is_upload=True, is_dir=False)
            _modified = True

        if _modified:
            import io
            fo = io.StringIO()
            yaml.dump(cc, fo)
            self.save_data_to_file(fo.getvalue(), f"{nt.log_dir_c}/.task/config.yaml")
            # with open(tmp_file, 'w', encoding='utf8') as fo:
            #     yaml.dump(cc, fo)
            # nt._scp(tmp_file, f"{nt.log_dir_c}/.task/config.yaml", is_upload=True, is_dir=False)
            # os.remove(tmp_file)

        # done 检查 input_params 中是否有需要填充到 search_space.json 中的参数
        has_ss_param = False # 是否存在搜索超参在入参要求中
        for p in t_conf['input_params']:
            if 'searchspace_name' in p:
                has_ss_param = True
                break
        logger.warning("has search_space param(s) {}", has_ss_param)
        if has_ss_param:
            # tmp_file = f"/tmp/{time.time()}.json"
            ss_file = cc['searchSpacePath']
            with open(f"{task_path}/{ss_file}", "r", encoding="utf8") as fi:
                ss = json.load(fi)

            for p in t_conf['input_params']:
                if 'searchspace_name' not in p:
                    continue
                pname = p['searchspace_name'] # ss中的key
                logger.debug("inject ss param: {} in {}", pname, p['name'])
                try:
                    match = jsonpath_parse(pname).find(ss)
                    if len(match) > 0:
                        pval = params[p['name']]  # 入参中的值
                        try:
                            pval = json.loads(pval)
                        except:
                            logger.warning(f"Invalid param value of {p['name']}, must be json format")
                        match[0].value['_value'] = pval
                except:
                    logger.warning("json_path failed {} {}", pname, traceback.format_exc())
                # with open(tmp_file, 'w', encoding='utf8') as fo:
                #     json.dump(ss, fo)
                # nt._scp(tmp_file, f"{nt.log_dir_c}/.task/{ss_file}", is_upload=True, is_dir=False)
                # os.remove(tmp_file)
                self.save_data_to_file(json.dumps(ss), f"{nt.log_dir_c}/.task/{ss_file}")
        return None

    def start(self, task_name, params, progress_callback=None):
        '开启任务的进程'
        # params = self.params
        # t_conf = self.task_conf
        logger.info(f"--start nni-task: {params}")
        task_path = f"{self._conf['task_base']}/{task_name}"
        with open(f"{task_path}/define.json", "r", encoding="utf8") as fi:
            t_conf = json.load(fi)

        nt = self.nt
        progress_callback(10, '准备中。。。', init=True)

        # 0 check if the nginx is deployed
        out = nt._exec_(f"ls -l /data/ng1")
        if out.find("cannot access") > 0:
            # 上传并启动 nginx
            nt._scp(f"{self._conf['task_base']}/ng1", f"/data", is_upload=True, is_dir=True)
            out = nt._exec_(f"chmod +x /data/ng1/sbin/nginx && /data/ng1/sbin/nginx -p /data/ng1")
            print(out)

        #done 检查是否已经有进程 nnictl 进程在运行中,  check if the container was occupied
        out = nt._exec_("ps -ef|grep nni_node/node")
        if out.find("site-packages") > 0:
            logger.error("contrainer had a trainning process already. can not continue.")
            return None

        # 1.1 check pyenv,
        # if 'pyenv' in t_conf:
        #     nt.source_act(t_conf.get('pyenv'))
        #
        # # 2 检查训练依赖环境
        # dep = t_conf.get("dependency", None)
        # if dep:
        #     progress_callback(15, '安装环境依赖中。。。')
        #     logger.info("install dependencies: {}", dep)
        #     out = nt.install_dependency(dep, pypi_server=self._conf['pypi_server'])
        #     if out is not None and out.find("ERROR: ") > 0:
        #         logger.error("install dependency failed: {}", out)
        #         return None

        # 3 上传任务目录文件
        progress_callback(20, '上传模块文件中。。。')
        nt._exec_(f"rm -rf .task {task_name}")
        if not nt._scp(task_path, f"{nt.log_dir_c}/", is_upload=True, is_dir=True):
            logger.error("upload module files failed, break")
            return None
        nt._exec_(f"mv {task_name} .task")

        #done 6.0 检查并改写 config.yaml
        progress_callback(22, '处理配置数据中...')
        self._config_config(nt, params, t_conf, task_path)

        # 4 将任务参数存放到环境变量中(改为保存到文件中, 避免特殊字符hang住shell交互)
        self.save_data_to_file(json.dumps(params), f"{nt.log_dir_c}/.task/input_params.json")
        # for k, v in params.items():
        #     nt._exec_(fz'z"export _param_{k}={v}")

        # 5.0 检查配置中是否有需要下载数据集， 有则下载
        progress_callback(25, '下载数据集中。。。')
        vname = t_conf.get("load_dataset", None)
        if vname:
            ds_name = params.get(vname, None)
            if ds_name:
                logger.info("download dataset {}", ds_name)
                if not self.get_dataset(ds_name):
                    logger.error("download dataset failed")
                    return None
            else:
                logger.warning("dataset param not found!")
        else:
            logger.debug("no dataset need to download!!")

        nt._exec_("ln -s /data/.local ~/.local")

        # 5.1 检查并执行预处理脚本
        if os.path.exists(f"{task_path}/preprocess.py"):
            progress_callback(30, '数据预处理中。。。')
            # 切换目录是为了让 preprocess.py 与 train.py 所看到的data目录位置相同（../data）
            out = nt._exec_("cd .task && /app/anaconda3/envs/py38/bin/python preprocess.py && cd ..")
            print(out)
            if out.find('Traceback (most recent call last)') >= 0:
                logger.error("preprocess 处理失败! [{}]", out)
                progress_callback(-1, '数据预处理失败: '+out)
                return None

        # 6 启动训练任务
        progress_callback(40, '启动训练进程中。。。')
        nt._exec_(f"rm -rf {nt.log_dir_c}/nni-experiments")
        out = nt._exec_("export PATH=/app/anaconda3/envs/py38/bin:$PATH && echo $PATH && which nnictl")
        logger.info("PATH: {}", out)
        logger.info("---host: {}", self.cont_addr)
        out = nt._exec_(f"nnictl create --config {nt.log_dir_c}/.task/config.yaml --port {self._conf['nni_port']} 2>&1", remove_str='\x1b\\[\\d+m')
        print(out)
        tag = "The experiment id is "
        # tag = "Creating experiment, Experiment ID: "
        p = out.find(tag)
        if p < 0:
            logger.error("Experiment startup failed: {}", out)
            return None
        else:
            p += len(tag)
            exp_id = out[p: out.find('\n', p)]
            logger.info("---exp_id: {}", exp_id)
            progress_callback(50, '训练主进程启动完成。。。')
            return exp_id

    def check_status(self):
        out = self.nt._exec_("curl http://localhost:8787/api/v1/nni/check-status && echo", out_remove_fistline=1)
        try:
            r = json.loads(out)
            return r
        except Exception as x:
            logger.error("-- check_status failed: {}", x)
            return None

    def get_trials(self):
        out = self.nt._exec_("curl http://localhost:8787/api/v1/nni/trial-jobs && echo", out_remove_fistline=1)
        r = json.loads(out)
        try:
            r = json.loads(out)
            return r
        except Exception as x:
            logger.error("-- get trials failed: {}", x)
            return None

    def stop_task(self, task_name=None, remove_home=True):
        '停止任务， 如果使用了pyenv， 需要先进行切换'
        nt = self.nt
        _exec = nt._exec_

        if task_name is None:
            out = _exec("cat .task/define.json && echo", out_remove_fistline=1)
            t_conf = json.loads(out)
        else:
            task_path = f"{self._conf['task_base']}/{task_name}"
            with open(f"{task_path}/define.json", "r", encoding="utf8") as fi:
                t_conf = json.load(fi)
        if 'pyenv' in t_conf:
            nt.source_act(t_conf.get('pyenv'))

        #if t_conf.get("start_tensorboard", False):
        # 停止nginx
        # nt._exec_(f".task/ng1/sbin/nginx -p .task/ng1 -s stop")


        out = nt._exec_("nnictl stop", remove_str='\x1b\\[\\d+m') # 去掉termcolor 标记
        logger.info("nnictl stop return: {}", out)

        # 需要等到nni的进程完全退出后， 才删除目录， 否则nni进程不能完全退出
        for i in range(10):
            time.sleep(0.5)
            out = nt._exec_("ps -ef|grep nni_node/node")
            if out.find("site-packages") < 0:
                logger.info("nni process exited")
                break
        logger.info("nni --stopped!")
        #清理 nni 相关数据,  （是否提醒用户导出数据？ luogj建议对于自动化学习， 其全部的训练数据也都先保存到文件服务器）
        # _exec(f"rm -rf nni-experiments .task data *")
        if remove_home:
            _exec(f"rm -rf {nt.log_dir_c}")


    def model_export(self, nni_exp_id, nni_trial_id, model_id, model_ver):
        '模型导出， 其中 nni_exp_id 可以为None， nni_trial_id 也可以为 None， 前者为None则选择当前环境中最新的exp ID， 后者为None时， 则选择最新完成的trial'
        logger.info("........start.....model_export")
        nt = self.nt
        _exec = nt._exec_

        # 获取任务配置
        out = nt._exec_('cat .task/define.json && echo', out_remove_fistline=1)
        t_conf = json.loads(out)

        if nni_exp_id is None:
            # find the latest experiment
            out = _exec(f"cat ~/nni-experiments/.experiment && echo", out_remove_fistline=1)
            try:
                s = json.loads(out)
            except:
                logger.error("can not found experiment list")
                return False
            elist = [ [opts['id'], opts['startTime'], opts['status']] for opts in s.values()]
            elist = sorted(elist, key=operator.itemgetter(1))
            exp = elist[-1]
            nni_exp_id = exp[0]
            logger.info(f"found latest exp-id: {nni_exp_id}")

        if nni_trial_id is None:
            # find the latest trial,  But how to determine if the trial is finished successfully?
            out = _exec(f"ls -lrt ~/nni-experiments/{nni_exp_id}/trials/", out_remove_fistline=1)
            nni_trial_id = out.strip().splitlines()[-1].split()[-1]
            logger.info(f"found latest trial-id: {nni_trial_id}")

        # 创建目录， 准备好导出模型相关的文件后， 再 scp 到对应文件服务器上
        m_dir = f"{model_id}_{model_ver}"
        nt._exec_(f"rm -rf {m_dir} && mkdir -p {m_dir}/data")
        for f in t_conf['pred_files']:
            nt._exec_(f"cp -R .task/{f} {m_dir}/")
        nt._exec_(f"cp -r ~/nni-experiments/{nni_exp_id}/trials/{nni_trial_id}/* {m_dir}/data/")

        # 保存模型训练的参数， 这个参数之前保存在哪里？  由train.py 决定的
        # generate start_script
        ss = t_conf['pred_files'][0]
        if ss[-3:] == '.py':
            nt._exec_(f"echo '/app/anaconda3/envs/py38/bin/python {ss} $@' > {m_dir}/start_script")
        else:
            nt._exec_(f"echo 'sh ./{ss} $@' > {m_dir}/start_script")

        #done 保存 pred_dependency 到目录中， 以便部署的时候能预装依赖模块。
        # 额外保存 pyenv
        t = {"dependency": t_conf['pred_dependency']}
        if "pyenv" in t_conf:
            t['pyenv'] = t_conf['pyenv']
        self.save_data_to_file(json.dumps(t), f"{nt.log_dir_c}/{m_dir}/dependency.json")


        # 上传目录到 文件服务器
        if not _fs_put(self._conf['fileserver_url'], self.cont_addr, f"{self.nt.log_dir_c}/{m_dir}", f"/data/.models/{nt.user_name}"):
            logger.error("in model_export, _fs_put failed")
            return False

        logger.info("........succeed.....model_export ")
        return True

        # nt._scp(self._conf['FS_IDENTITY'], f"{nt.log_dir_c}/.id_1", is_upload=True, is_dir=False)
        # model_path = f"/data/.models/{nt.user_name}"
        # fs_host, fs_port = self._conf['FS_ADDR'].split(":", maxsplit=1)
        # # mkdir of model path
        # nt._exec_(f"chmod 600 .id_1 && ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p {fs_port} -i .id_1"
        #           f" {self._conf['FS_USER']}@{fs_host} rm -rf {model_path}/{m_dir}")
        # nt._exec_(f"ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p {fs_port} -i .id_1"
        #           f" {self._conf['FS_USER']}@{fs_host} mkdir -p {model_path}")
        # out = nt._exec_(f"chmod 600 .id_1 "
        #                 f"&& scp -r "
        #                      f" -q -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -P {fs_port} "
        #                      f"-i {self.nt.log_dir_c}/.id_1 "
        #                      f" {m_dir} {self._conf['FS_USER']}@{fs_host}:{model_path}/ "
        #                      f" && rm -rf {self.nt.log_dir_c}/.id_1")
        # print(out)
        # print(nt._exec_(f"rm -rf {m_dir}"))

    def model_deploy(self, model_id, model_ver=None):
        '将指定的模型（之前导出的）部署到指定主机上，暂时启动为服务'
        #done 模型部署并启动服务
        nt = self.nt

        # 0 check if the nginx is deployed
        out = nt._exec_(f"ls -l /data/ng1")
        if out.find("cannot access") > 0:
            # 上传并启动 nginx
            nt._scp(f"{self._conf['task_base']}/ng1", f"/data", is_upload=True, is_dir=True)
            out = nt._exec_(f"chmod +x /data/ng1/sbin/nginx && /data/ng1/sbin/nginx -p /data/ng1")
            print(out)
        #todo 检查 nginx 进程是否已经启动了.

        # 修改模型的部署目录， 其中不带 RUN_ID
        nt.change_home(f"{nt.log_dir}/{model_id}")

        # 检查是否已经有部署， 如果有， 检查进程并停止
        _dep_path_ = f"{nt.log_dir}/{model_id}/{model_id}"
        # _dep_path_ = f"{nt.log_dir}/{model_id}"
        out = nt._exec_(f"ls -l {_dep_path_}")
        if out.find("cannot access") < 0:
            logger.warning("Killing previous deployment process...")
            self.nt._scp(f"{self._conf['task_base']}/deptool.py", f"/data/", is_upload=True, is_dir=False)
            out = self.nt._exec_(f"python /data/deptool.py kill-by-path {_dep_path_}")
            print(out)

        # 1 下载模型文件
        logger.info(".....download model file......")
        nt._scp(self._conf['FS_IDENTITY'], f"{self.nt.log_dir_c}/.id_1", is_upload=True, is_dir=False)
        fs_host, fs_port = self._conf['FS_ADDR'].split(":", maxsplit=1)
        fs_user = self._conf['FS_USER']
        logger.info(f"model_ver {model_ver}")
        if model_ver is None: # Find the latest version
            dpath = f"/data/.models/{nt.user_name}/"
            out = nt._exec_(
                f"chmod 600 .id_1 && ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p {fs_port} -i .id_1"
                f" {self._conf['FS_USER']}@{fs_host} ls -lrt {dpath}")
            m = f"{model_id}_"
            for l in out.splitlines():
                if l.find(m) > 0:
                    data_path = l.strip().split()[-1].split('/')[-1]
                    data_path = f"{dpath}{data_path}"
        else:
            data_path = f"/data/.models/{nt.user_name}/{model_id}_{model_ver}"
        dep_path = model_id
        out = nt._exec_(f"chmod 600 .id_1 && rm -rf ./{dep_path} && scp -r"
                             f" -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -P {fs_port} "
                             f"-i {nt.log_dir_c}/.id_1 "
                             f"{fs_user}@{fs_host}:{data_path} ./{dep_path} "
                             f"&& rm -rf {nt.log_dir_c}/.id_1")
        logger.debug(f".............out {out}")
        logger.debug(f"*****************************")
        # 2 检查安装依赖
        # out = "\n".join(nt._exec_(f'cat {dep_path}/dependency.json && echo').splitlines()[1:])
        # dep_obj = json.loads(out)
        # print("---dependency:", dep_obj)
        # if "pyenv" in dep_obj:
        #     nt.source_act(dep_obj.get('pyenv'))
        # nt.install_dependency(dep_obj['dependency'], pypi_server=self._conf['pypi_server'])
        nt._exec_("ln -s /data/.local ~/.local")

        # 3 Pick a random port number, as restful service port
        random.seed(time.time())
        port_number = random.randint(12048, 13048)

        # 4 Start the service
        nt._exec_(f"cd ./{dep_path}")
        out = nt.run_cmd(f'nohup sh start_script {port_number}')
        print(out)

        #5 register the port to nginx
        ng_cmd = f"python /data/ng1/ng.py add {model_id} {port_number} {nt.log_dir_c}/{dep_path}"
        # logger.info("ng_cmd: {}", ng_cmd)
        out = nt._exec_(ng_cmd)
        logger.info("ng.py output: {}", out)

    def model_deploy_check_idle(self):
        '检查已经部署运行的测试进程， 闲置10分钟停止， 闲置1周删除'
        self.nt._scp(f"{self._conf['task_base']}/deptool.py", f"/data/", is_upload=True, is_dir=False)
        out = self.nt._exec_(f"python /data/deptool.py check-idle")
        print(out)

    def model_deploy_delete(self, expe_name):
        '删除已经部署运行的测试进程， 停止进程'
        self.nt._scp(f"{self._conf['task_base']}/deptool.py", f"/data/", is_upload=True, is_dir=False)
        out = self.nt._exec_(f"python /data/deptool.py delete {expe_name}")
        print(out)

    def model_deploy_check_service(self, model_id):
        '检查测试部署服务是否可用，  目录在则检查进程， 进程不在就启动，  如果目录不在， 则需要重新部署并启动'

        _exec = self.nt._exec_
        dep_path = f"{self.nt.log_dir}/{model_id}/{model_id}"
        out = _exec(f"ls -l {dep_path}")
        print(out)
        #return
        if out.find("cannot access") >= 0: # 目录不存在， 则重新部署
            logger.warning("reploy... {}", model_id)
            self.model_deploy(model_id)
            return
        # check and switch venv
        out = "\n".join(_exec(f'cat {dep_path}/dependency.json && echo').splitlines()[1:])
        dep_obj = json.loads(out)
        pyenv = dep_obj.get('pyenv', None)
        if pyenv is not None:
            self.nt.source_act(pyenv)

        logger.info("deploy exists,  check process...")
        self.nt._scp(f"{self._conf['task_base']}/deptool.py", f"/data/ng1/", is_upload=True, is_dir=False)
        out = self.nt._exec_(f"python /data/ng1/deptool.py check-service {model_id}")
        print(out)

    def model_show_tensorboard(self, model_id, model_ver=None):
        'start a tensorboard process in test container, to view the train scalar data'
        # through python /data/ng1/ng.py start-tb <path to tb data>
        nt = self.nt

        # 0 check if the nginx is deployed
        out = nt._exec_(f"ls -l /data/ng1")
        if out.find("cannot access") > 0:
            # 上传并启动 nginx
            nt._scp(f"{self._conf['task_base']}/ng1", f"/data", is_upload=True, is_dir=True)
            out = nt._exec_(f"chmod +x /data/ng1/sbin/nginx && /data/ng1/sbin/nginx -p /data/ng1")
            print(out)
        else:
            # only update ng.py
            nt._scp(f"{self._conf['task_base']}/ng1/ng.py", f"/data/ng1/", is_upload=True, is_dir=False)

        # 检查是否已经有部署， 如果有， 检查进程并停止
        _dep_path_ = f"{nt.log_dir}/{model_id}/{model_id}_tb"
        out = nt._exec_(f"ls -l {_dep_path_}")
        if out.find("cannot access") < 0:
            logger.warning("Killing previous deployment process...")
            self.nt._scp(f"{self._conf['task_base']}/deptool.py", f"/data/", is_upload=True, is_dir=False)
            out = self.nt._exec_(f"python /data/deptool.py kill-by-path {_dep_path_}")
            print(out)

        # 1 下载模型文件
        nt._scp(self._conf['FS_IDENTITY'], f"{self.nt.log_dir_c}/.id_1", is_upload=True, is_dir=False)
        fs_host, fs_port = self._conf['FS_ADDR'].split(":", maxsplit=1)
        fs_user = self._conf['FS_USER']
        if model_ver is None: # Find the latest version
            dpath = f"/data/.models/{nt.user_name}/"
            out = nt._exec_(
                f"chmod 600 .id_1 && ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p {fs_port} -i .id_1"
                f" {self._conf['FS_USER']}@{fs_host} ls -lrt {dpath}")
            m = f"{model_id}_"
            for l in out.splitlines():
                if l.find(m) > 0:
                    data_path = l.strip().split()[-1].split('/')[-1]
                    data_path = f"{dpath}{data_path}"
        else:
            data_path = f"/data/.models/{nt.user_name}/{model_id}_{model_ver}"
        dep_path = f"{model_id}_tb"
        out = nt._exec_(f"chmod 600 .id_1 && rm -rf ./{dep_path} && scp -r"
                             f" -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -P {fs_port} "
                             f"-i {nt.log_dir_c}/.id_1 "
                             f"{fs_user}@{fs_host}:{data_path} ./{dep_path} "
                             f"&& rm -rf {nt.log_dir_c}/.id_1")

        # 2 only switch venv
        out = "\n".join(nt._exec_(f'cat {dep_path}/dependency.json && echo').splitlines()[1:])
        dep_obj = json.loads(out)
        print("---dependency:", dep_obj)
        if "pyenv" in dep_obj:
            nt.source_act(dep_obj.get('pyenv'))

        # 3 Pick a random port number, as restful service port
        random.seed(time.time())
        port_number = random.randint(12048, 13048)

        #4 start tensorboard and register the port to nginx
        ng_cmd = f"python /data/ng1/ng.py start_tb {model_id}_tb {port_number} {nt.log_dir_c}/{dep_path} >/tmp/{model_id}.log 2>&1 &"
        # logger.info("ng_cmd: {}", ng_cmd)
        out = nt._exec_(ng_cmd)
        logger.info("ng.py start-tb output: {}", out)


    def train_result_export(self):
        '导出完整的搜索结果到文件服务器上'
        nt = self.nt
        _exec = nt._exec_
        logger.info("-- train_result_export")

        # 获取任务配置
        out = nt._exec_('cat .task/define.json && echo', out_remove_fistline=1)
        t_conf = json.loads(out)

        # 创建目录， 准备好导出模型相关的文件后， 再 scp 到对应文件服务器上
        m_dir = f"{self.exp_name}_{self.run_id}"
        nt._exec_(f"rm -rf {m_dir} && mkdir -p {m_dir}/task")
        for f in t_conf['pred_files']:
            nt._exec_(f"cp -R .task/{f} {m_dir}/task/")
        nt._exec_(f"cp -R .task/input_params.json {m_dir}/task/")

        self.stop_task(remove_home=False)  # 这里保留了训练数据在训练容器中, 导出结束后仍要删除

        nt._exec_(f"cp -r ~/nni-experiments {m_dir}/")

        # 保存模型训练的参数， 这个参数之前保存在哪里？  由train.py 决定的
        # generate start_script
        ss = t_conf['pred_files'][0]
        if ss[-3:] == '.py':
            nt._exec_(f"echo '/app/anaconda3/envs/py38/bin/python {ss} $@' > {m_dir}/task/start_script")
        else:
            nt._exec_(f"echo 'sh ./{ss} $@' > {m_dir}/task/start_script")

        #done 保存 pred_dependency 到目录中， 以便部署的时候能预装依赖模块。
        # 额外保存 pyenv
        t = {"dependency": t_conf['pred_dependency']}
        if "pyenv" in t_conf:
            t['pyenv'] = t_conf['pyenv']
        self.save_data_to_file(json.dumps(t), f"{nt.log_dir_c}/{m_dir}/task/dependency.json")

        # 上传目录到 文件服务器
        model_path = f"/data/.models/{nt.user_name}"
        if not _fs_put(self.fileserver_url, self.cont_addr, f"{nt.log_dir_c}/{m_dir}", model_path):
            logger.error("uploads mods failed!")
            return False
        logger.info(f"upload model files {nt.log_dir_c}/{m_dir} to {model_path} of fileserver")
        logger.info("*******************************")
        # nt._scp(self._conf['FS_IDENTITY'], f"{nt.log_dir_c}/.id_1", is_upload=True, is_dir=False)
        # fs_host, fs_port = self._conf['FS_ADDR'].split(":", maxsplit=1)
        # mkdir of model path
        # nt._exec_(f"chmod 600 .id_1 && ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p {fs_port} -i .id_1"
        #           f" {self._conf['FS_USER']}@{fs_host} rm -rf {model_path}/{m_dir}")
        # nt._exec_(f"ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p {fs_port} -i .id_1"
        #           f" {self._conf['FS_USER']}@{fs_host} mkdir -p {model_path}")
        # _exec("chmod 600 .id_1")
        # out = _exec(f"scp -r -q -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -P {fs_port} -i .id_1 {m_dir} {self._conf['FS_USER']}@{fs_host}:{model_path}/")
        # _exec(f"rm -rf .id_1")
        # logger.info("scp to fs return {}, log_dir_c: {}", out, nt.log_dir_c)
        # print(nt._exec_(f"rm -rf {m_dir}"))
        _exec(f"rm -rf {m_dir}")

        _exec(f"rm -rf {nt.log_dir_c}") # 删除所有文件

    def train_result_show(self, run_id=None):
        '把自动化学习的结果, 复制到测试容器, 并启动 nnictl view 进程,  以便查看训练的结果数据'
        nt = self.nt
        _exec = nt._exec_
        exp_name = self.exp_name

        # 修改模型的部署目录， 其中不带 RUN_ID
        nt.change_home(f"{nt.log_dir}/{exp_name}_result")

        #检查是否已经有部署， 如果有， 则终止此次部署
        _dep_path_ = f"{nt.log_dir}/{exp_name}_result/{exp_name}"
        out = nt._exec_(f"ls -l {_dep_path_}")
        if out.find("cannot access") < 0:
            logger.warning("previous deployment exists, return True")
            return True

        # 1 下载模型文件
        nt._scp(self._conf['FS_IDENTITY'], f"{self.nt.log_dir_c}/.id_1", is_upload=True, is_dir=False)
        fs_host, fs_port = self._conf['FS_ADDR'].split(":", maxsplit=1)
        fs_user = self._conf['FS_USER']
        data_path = None
        if run_id is None: # Find the latest version
            dpath = f"/data/.models/{nt.user_name}/"
            out = nt._exec_(
                f"chmod 600 .id_1 && ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p {fs_port} -i .id_1"
                f" {self._conf['FS_USER']}@{fs_host} ls -lrt {dpath}")
            m = f"{exp_name}_"
            for l in out.splitlines():
                if l.find(m) > 0:
                    data_path = l.strip().split()[-1].split('/')[-1]
                    data_path = f"{dpath}{data_path}"
        else:
            data_path = f"/data/.models/{nt.user_name}/{exp_name}_{run_id}"
        if data_path is None:
            logger.error("can not found model path of {}", dpath)
            return False
        logger.info("found data_path {}", data_path)
        dep_path = exp_name
        out = nt._exec_(f"chmod 600 .id_1 && rm -rf ./{dep_path} && scp -r"
                             f" -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -P {fs_port} "
                             f"-i {nt.log_dir_c}/.id_1 -q "
                             f"{fs_user}@{fs_host}:{data_path} ./{dep_path} "
                             f"&& rm -rf {nt.log_dir_c}/.id_1")
        print(out)

        # 检查下载是否成功
        out = nt._exec_(f"pwd && ls -l {dep_path}/nni-experiments")
        print(out)
        if out.find("cannot access") >= 0:
            logger.error("failed to download train result files")
            return False

        #启动 nnictl view 的处理方式:  上传脚本 _tools/nni_view.py, 执行它, 其它的逻辑在该脚本中实现
        # 3 Pick a random port number, as restful service port
        random.seed(time.time())
        port_number = random.randint(12048, 13048)
        if not self.nt._scp(f"{self._conf['task_base']}/ng1/ng.py", "/data/ng1/", is_upload=True, is_dir=False):
            return False
        if not self.nt._scp(f"{self._conf['task_base']}/_tools/nni_view.py", f"{nt.log_dir_c}/", is_upload=True, is_dir=False):
            return False
        out = self.nt._exec_(f"nohup python nni_view.py {dep_path} {port_number} >nni_view.out 2>&1 &")
        logger.info("nni_view.py started at {}, return {}", port_number, out)

        # 实际路径如下:
        # HOME:  /data/xx/DE1625217245330267810001_result
        # result path: DE1625217245330267810001/nni-experiments

        return True

    def train_result_deploy(self, exp_name, run_id, trial_id, svc_url, cont_name):
        '从训练结果中指定单个trail_id,  部署其预测服务到测试容器中, 服务保持运行30分钟'
        nt = self.nt

        # 0 check if the nginx is deployed
        out = nt._exec_(f"ls -l /data/ng1")
        if out.find("cannot access") > 0:
            # 上传并启动 nginx
            nt._scp(f"{self._conf['task_base']}/ng1", f"/data", is_upload=True, is_dir=True)
            out = nt._exec_(f"chmod +x /data/ng1/sbin/nginx && /data/ng1/sbin/nginx -p /data/ng1")
            print(out)

        # 修改模型的部署目录， 其中不带 RUN_ID
        nt.change_home(f"{nt.log_dir}/{exp_name}-pred")

        # 检查是否已经有部署， 如果有， 检查进程并停止
        out = nt._exec_(f"ls -l {nt.log_dir}/{exp_name}-pred/{trial_id}")
        if out.find("cannot access") < 0:
            logger.warning("previous deployment exists, stop")
            return True  # 如果存在, 则认为有效

        # 1 下载模型文件
        dep_path = trial_id
        out = self._fs_exec(f"ls -lrt /data/.models/{nt.user_name}/{exp_name}_{run_id}/nni-experiments/") # 先找出exp_id
        exp_id = out.strip().split()[-1]
        if not self._fs_getdir(f"/data/.models/{nt.user_name}/{exp_name}_{run_id}/task", f"./{dep_path}"):
            logger.error("get task folder failed: {}", f"/data/.models/{nt.user_name}/{exp_name}_{run_id}/task")
            nt._exec_(f"rm -rf ./{dep_path}")
            return False
        if not self._fs_getdir(f"/data/.models/{nt.user_name}/{exp_name}_{run_id}/nni-experiments/{exp_id}/trials/{trial_id}",
                      f"./{dep_path}/data"):
            logger.error("get task folder failed: {}", f"/data/.models/{nt.user_name}/{exp_name}_{run_id}/nni-experiments/{exp_id}/trials/{trial_id}")
            nt._exec_(f"rm -rf ./{dep_path}")
            return False

        # 2 检查安装依赖
        out = "\n".join(nt._exec_(f'cat {dep_path}/dependency.json && echo').splitlines()[1:])
        dep_obj = json.loads(out)
        logger.debug("---dependency: {}", dep_obj)
        if "pyenv" in dep_obj:
            nt.source_act(dep_obj.get('pyenv'))
        nt.install_dependency(dep_obj['dependency'], pypi_server=self._conf['pypi_server'])

        # 3 Pick a random port number, as restful service port
        random.seed(time.time())
        port_number = random.randint(12048, 13048)

        # 把额外的参数保存到 远程文件中, 以便 nni_pred.py 能获取
        params = dict(svc_url=svc_url,
                      exp_name=exp_name, run_id=run_id, trial_id=trial_id, cont_name=cont_name)
        self.save_data_to_file(json.dumps(params), f"{nt.log_dir_c}/{trial_id}/_param.json")

        if not self.nt._scp(f"{self._conf['task_base']}/_tools/nni_pred.py", f"{nt.log_dir_c}/{trial_id}/", is_upload=True, is_dir=False):
            return False
        out = self.nt._exec_(f"nohup python {trial_id}/nni_pred.py {exp_name}-pred/{trial_id} {port_number} >/tmp/nni_pred.{port_number} 2>&1 &")
        logger.info("nni_view.py started at {}, return {}", port_number, out)
        return True

    def show_tensorboard_in_testcont(self, trial_id, minutes=10, venv="py38",
                                     is_testcont=True, # 是则在测试容器中, 否则是在训练容器中
                                     ):
        'start a tensorboard process in test container, to view the train scalar data'
        nt = self.nt

        # 0 check if the nginx is deployed
        out = nt._exec_(f"ls -l /data/ng1")
        if out.find("cannot access") > 0:
            # 上传并启动 nginx
            nt._scp(f"{self._conf['task_base']}/ng1", f"/data", is_upload=True, is_dir=True)
            out = nt._exec_(f"chmod +x /data/ng1/sbin/nginx && /data/ng1/sbin/nginx -p /data/ng1")
            print(out)

        # 检查是否已经有部署， 如果有， 需要先kill原来的进程
        exp_name = self.exp_name
        run_id = self.run_id
        _dep_path_ = f"{nt.log_dir}/{exp_name}_{run_id}_tb"
        out = nt._exec_(f"ls -l {_dep_path_}")
        if out.find("cannot access") < 0:
            out = nt._exec_(f"cat {_dep_path_}/pid.txt && echo", out_remove_fistline=1)
            pid = out.strip()
            logger.warning("Killing previous tensorboard process...[{}]", pid)
            if pid == '':
                logger.warning("process not found, continue")
                nt._exec_(f"rm -rf {_dep_path_}")
            else:
                nt._exec_(f"kill {pid}")
                time.sleep(0.2)
                nt._exec_(f"rm -rf {_dep_path_}")

        if is_testcont:
            # 1 下载 _tb 数据 到测试容器中
            trial_path_on_fs = f"/data/.models/{self.user_name}/{exp_name}_{run_id}/nni-experiments/*/trials/{trial_id}/_tb"
            if not _fs_get(self._conf['fileserver_url'], self.cont_addr, trial_path_on_fs, _dep_path_):
                logger.error("get tensorboard data failed from fs: {}", trial_path_on_fs)
                return False
        else:
            # 1 数据就在容器中, 建立符号连接到真实数据目录, 先寻找trial的目录
            nt._exec_(f"mkdir -p {_dep_path_}")
            out = nt._exec_('echo $LOGDIR')
            log_dir = out.strip().split('\n')[-1].strip()
            trial_path = f"{log_dir}/{exp_name}-{run_id}/nni-experiments"
            out = nt._exec_(f"ls {trial_path}")
            experiment_id = out.strip().split()[-1].strip()
            trial_path = f"{trial_path}/{experiment_id}/trials/{trial_id}"
            out = nt._exec_(f"ls {trial_path}")
            if out.find('cannot access') >= 0:
                logger.error("trial path not found: {}", trial_path)
                return False
            tb_path = f"{trial_path}/_tb"
            out = nt._exec_(f"ls {trial_path}")
            if out.find('cannot access') >= 0:
                logger.error("train process not output tensorboard data: {}", tb_path)
                return False
            logger.info("found trial path: {}, make symbol link to {}/_cb", tb_path, _dep_path_)
            out = nt._exec_(f"ln -s {tb_path} {_dep_path_}/_tb")  # 使用符号链接方式, 会导致tb展示的时候有问题(目录递归), 还是使用copy吧 (原因是错误建立了symlink)
            if out.find('failed to create symbolic link') >= 0:
                logger.error("mk symlink of {} to {}_tb failed", tb_path, _dep_path_)
                return False
            # out = nt._exec_(f"cp -r {tb_path} {_dep_path_}/")
            # logger.info("copy _tb data return {}", out)

        # 2 only switch venv
        if venv is not None:
            nt.source_act(venv)

        # 3 上传py脚本到容器中
        if not nt._scp(f"{self._conf['task_base']}/_tools/_ck", _dep_path_, is_upload=True, is_dir=True):
            logger.error("upload tools failed")
            return False

        #done 第一次执行脚本， 向ng注册端口， 并返回端口 register the port to nginx
        tb_view_cmd = f"python {_dep_path_}/_ck/tb_view.py --op setng --id {trial_id} --path {_dep_path_}"
        out = nt._exec_(tb_view_cmd)
        logger.info("tb_view.py setng output: {}", out)
        _p = out.find('--port--')
        _port = ''
        if _p > 0:
            _p1 = out.find('\n', _p)
            _port = out[_p+len("--port--"):_p1].strip()
            logger.info("start in port: {}", _port)
        else:
            logger.error("setng failed!")
            return False

        #done 第二次执行脚本, 启动tensorboard进程, 并延迟清理退出
        tb_view_cmd = f"python {_dep_path_}/_ck/tb_view.py --op runtb --id {trial_id} --path {_dep_path_} --port {_port} --minutes {minutes} >/tmp/tb_{_port} 2>&1 &"
        out = nt._exec_(tb_view_cmd)
        logger.info("start tb return {}", out)

        logger.info("tb_show done!! /{}_ck/", trial_id)
        return True


def _fs_get(fs_svc_addr, cont_addr, fs_path, cont_path, is_dir=True):
    '从 fs 向容器传送文件'
    data = {"BODY": {"cont_addr": cont_addr, "is_dir": is_dir,
                     "cont_path": cont_path, "fs_path": fs_path}}
    url = f"{fs_svc_addr}/scp_get" if fs_svc_addr[:7]=='http://' else f"http://{fs_svc_addr}/scp_get"
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


def train_task_mon_deploy(db, expe_name, run_id, user_name):
    '训练完成后， 导出的模型， 选择一个测试容器部署测试进程'
    test_cont = None
    rr = db.test_pick_cont()
    if rr is None:
        logger.error("no available test container!!!")
    else:
        test_cont, test_ip, test_ctl_port, test_svc_port = rr
        logger.info("pick a test cont: {} ssh://{}:{}", test_cont, test_ip, test_ctl_port)

        # deploy the model
        task = NNI_Task(local_conf, f"{test_ip}:{test_ctl_port}", expe_name=expe_name, run_id=run_id,
                        user_name=user_name)
        task.model_deploy(expe_name)
    # todo 如果数据文件中带有tensorboard 的数据， 则启动tensorboard

    # todo 部署一个静态文件服务， 可以让使用者下载stderr文件内容
    return test_cont

def train_task_mon_once(local_conf, db):
    '检查一轮'
    ret = db.train_task_list()
    if ret is None:
        logger.info("nothing found")
    else:
        for r in ret:
            expe_name, run_id, begin_time, op_time, op_note, \
            container_name, ip_addr, ctl_port, svc_port, user_name, ext_info = r
            logger.info("expe_name={} cont_addr: {}:{}", expe_name, ip_addr, svc_port)

            # check nni restful the experiment status
            url = f"http://{ip_addr}:{svc_port}/api/v1/nni/check-status"
            r = requests.get(url)
            if r.status_code != 200:
                logger.error("check status of expe: {} failed, [{}--{}] cont_name={}, svc_addr: {}:{}",
                             expe_name, r.status_code, r.text, container_name, ip_addr, svc_port)
            else:
                ret = r.json()
                run_status, run_errors = ret['status'], ret['errors']
                logger.info('expe {} run_status: {}, {}', expe_name, run_status, run_errors)
                if run_status == 'DONE' and len(run_errors) == 0: # 成功完成： 导出， 部署， 停止
                    # 先检查 其唯一的 trial 是否正常结束
                    run_success = False
                    r = requests.get(f"http://{ip_addr}:{svc_port}/api/v1/nni/trial-jobs")
                    if r.status_code == 200:
                        ret = r.json()
                        if len(ret)  == 1:
                            t = ret[0]
                            if t['status'] == 'SUCCEEDED':
                                run_success = True
                                log_path = t['logPath'].split('/')
                                exp_id, trial_id = log_path[-3], log_path[-1]
                                logger.info("task success, exp_id: {}, trial_id: {}", exp_id, trial_id)
                                task = NNI_Task(local_conf, f"{ip_addr}:{ctl_port}", expe_name=expe_name, run_id=run_id,
                             user_name=user_name)
                                logger.info("export model")
                                task.model_export(exp_id, trial_id, expe_name, run_id)
                                logger.info("deploying model...")

                                #stop the train task
                                # logger.debug("ext_info: {}", ext_info)
                                # task_name = json.loads(ext_info)['task_name']
                                task.stop_task(task_name=None)
                                task.close()

                                #修改训练容器的占用状态, 释放
                                db.train_finish(container_name, 'release train_container')

                                #pick a test container
                                test_cont = train_task_mon_deploy(db, expe_name, run_id, user_name)

                                # update task dev_run
                                if test_cont is None:
                                    db.train_task_update(expe_name, run_id, 'train finished, but not deploy', run_status='02')
                                else:
                                    db.train_task_update(expe_name, run_id, f'train finished, deployed to {test_cont}', run_status='02', deploy_container=test_cont)
                        #todo 失败的情况也需要释放训练容器， 导出文件到部署环境， 以便用户可以查看日志

            # train_task_update(expe_name, run_id, 'hehe--1')
            # train_task_update(expe_name, run_id, 'hehe--2', run_status='01')
            # db.train_task_update(expe_name, run_id, 'hehe--2', run_status='01', deploy_container='taskrun1')


def train_task_mon(local_conf, db):
    '自助建模训练任务状态检查'
    while True:
        train_task_mon_once(local_conf, db)
        time.sleep(10.0)

import sys
def main_nni(local_conf, db):
    '''验证启动NNI全过程的启动入口'''

    # 调度配置
    # task = NNI_Task(local_conf, "172.18.231.76:3387", task_id="1", node_id="3")
    # task = NNI_Task(local_conf, "172.18.238.17:3387", task_id="gg", node_id="3")
    # task = NNI_Task(local_conf, "172.18.243.17:32769", task_id="gg", node_id="3") #taskrun
    task = NNI_Task(local_conf, "172.18.238.17:32773",
                    expe_name="img-cls", run_id="RUN_1", user_name='guanyf')  # taskrun1 svc_port http://172.18.238.17:32774
    # task = NNI_Task(local_conf, "172.18.243.17:32770",
    #                 expe_name="expe-name", run_id="RUN_2", user_name='guanyf')  # taskrun3 svc_port http://172.18.243.17:32771

    if len(sys.argv) > 1:
        t = sys.argv[1]
        if t == 'start':
            t0 = time.time()

    #         task.start(task_name="txt-clf", params=dict(
    #     #dataset="newsgroup", lang="en"))
    # dataset = "toutiao", stopwords = 'cn_stopwords.txt', lang = "cn"))
            db.train_task_update(task.exp_name, task.run_id, '任务启动', run_status='01',
                                 train_container='taskrun2', user_name=task.user_name)
            task.start(task_name='yolo5', params=dict(epoch=20,
                                     dataset='ds_yolo', test_pct=20, random_seed="13", max_duration="60", device="3"))
            print("---- time:", time.time()-t0)
        elif t == 'stop':
            task.stop_task(task_name='yolo5')
        elif t == 'check':
            print(task.check_status())
        elif t == 'trials':
            print(task.get_trials())
        elif t == 'export':
            # task.model_export("r9qhG1Dc", 'hLS4R', "odt", "v1")
            task.model_export(None, None, "gg", "3")
        elif t == 'deploy':
            task.model_deploy('odt')
        elif t == 'check-idle':
            task.model_deploy_check_idle()
        elif t == 'check-svc':
            task.model_deploy_check_service('odt')

        elif t == 'deploy1':
            task.close()
            train_task_mon_deploy(db, task.exp_name, task.run_id, task.user_name)
    else:
        print(task.check_status())

    task.close()


class GM_Task:
    def __init__(self, local_conf, cont_addr, exp_name, run_id, user_name=None, dep_path=None):
        self._conf = local_conf
        self.user_name = user_name
        self.exp_name = exp_name
        self.run_id = run_id
        self.dep_path = dep_path
        self.cont_addr = cont_addr

    def __conn_cont(self, _conf, cont_addr, task_id, node_id, dep_path, user_name):
        host, port = cont_addr.split(":", maxsplit=1)
        nt = NodeTask(wf_id='123', task_id=task_id, proc_id="2", node_id=node_id,
                      conninfo=dict(
                          host=host, port=int(port),
                          username='_base_', client_keys=[_conf['CONT_IDENTITY']], known_hosts=None),
                      user_name=user_name)
        if not nt.connect(dep_path):
            raise Exception("connect ssh failed")
        self.user_name = nt.user_name
        self.fileserver_url = _conf['fileserver_url']
        return nt

    def __enter__(self):
        self.nt = self.__conn_cont(self._conf, self.cont_addr, self.exp_name, self.run_id,
                                   self.dep_path, self.user_name)
        self.home = self.nt.log_dir_c if self.dep_path is None else self.dep_path
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        logger.info("--with exit--")
        self.close()

    def close(self):
        self.nt.disconnect()

    def save_data_to_file(self, data, remote_path):
        fname = os.path.basename(remote_path)
        tmp_fname = f"{self._conf.get('tmp_dir', '/tmp')}/{fname}"
        try:
            with open(tmp_fname, 'w', encoding='utf-8') as fo:
                fo.write(data)
            logger.info("==save_data_to_file {} size:{} : \n {}", remote_path, os.path.getsize(tmp_fname), data)
            self.nt._scp(tmp_fname, f"{remote_path}_tmp", is_upload=True, is_dir=False)
            self.nt._exec_(f"mv {remote_path}_tmp {remote_path}")
        finally:
            try:
                os.remove(tmp_fname)
            except:
                pass

    def start_train(self, flow_doc, container_name=None, run_to=None, open_nb=False):
        '在容器中 启动图形化建模的训练过程'
        nt = self.nt
        tmp_dir = '.app'

        # logger.info("clear path...")
        # # 当前目录: $LOGDIR/GM_${EXPE_NAME}_${RUN_ID}
        # nt._exec_(f"rm -rf {tmp_dir} .cache .datasets .config * && mkdir {tmp_dir}")

        logger.info("upload configure files ...")
        #1 上传流程文档
        self.save_data_to_file(json.dumps(flow_doc), f"{self.home}/src.atf")

        #2 上传配置
        _conf = {"fileserver_url":self.fileserver_url, "db_conn_str":self._conf['db_conn_str'],
                 "cont_sftp_addr": self.cont_addr, "user": self.user_name, "container_name": container_name}
        self.save_data_to_file(json.dumps(_conf), f"{self.home}/_conf.json")

        logger.info("upload mods files...")
        #3 上传模块代码目录,  模块代码文件作为整体, 存放在文件服务器上 /data/.app/guimod.tgz, 下载到容器 .datasets 下
        if not _fs_get(self.fileserver_url, self.cont_addr, '/data/.app/guimod.tgz', f"{self.home}/{tmp_dir}/"):
            logger.error("uploads mods failed!")
            return False
        out = nt._exec_(f"tar xzf ./{tmp_dir}/guimod.tgz -C ./{tmp_dir}")
        logger.info("update mods file return {}", out)
        nt._exec_(f"export PYTHONPATH={self.home}/{tmp_dir}/guimod")
        logger.info("set PYTHONPATH to {}/{}/guimod", self.home, tmp_dir)

        #4 切换 venv
        out = nt.source_act('gm')
        logger.info("pyenv return {}", out)

        #5 启动训练进程
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
            "PYTHONPATH":f"{self.home}/{tmp_dir}/guimod"
        },
            "args": [py_path, "-c", f"from sitech.aipaas.gm.runner.main import main_train; main_train('src.atf', '_conf.json', run_to={repr(run_to)}, open_nb={open_nb})"]}
        self.save_data_to_file(json.dumps(start_cmd), f"/data/.app/once_proc/{time.time()}.json")

        return True

    def deploy_model_test(self):
        '图形化建模的 训练结果模型 部署为测试服务'
        nt = self.nt
        logger.info('downloading model files...')
        fs_path = f"/data/.models/{self.user_name}/GM_{self.exp_name}_{self.run_id}"
        cont_path = '/'.join(self.home.split('/')[:-1])
        if not _fs_get(self.fileserver_url, self.cont_addr, fs_path, cont_path):
            logger.error("uploads model files failed!")
            return False
        nt._exec_("cd")  # 主目录被 _fs_get 重新建立， 需要重新搞一次
        # 切换 venv
        out = nt.source_act('gm')
        logger.info("pyenv return {}， home: [{}]", out, self.home)

        # 启动训练进程
        nt._exec_(f"export PYTHONPATH={self.home}/.app/guimod")
        logger.info("set PYTHONPATH to {}/.app/guimod", self.home)
        live_time_minutes = 30  # 存活的时间（单位： 分钟）
        out = nt._exec_(f"python -c \"from sitech.aipaas.gm.runner.modelrun import run_model; run_model('GM_{self.exp_name}_{self.run_id}', {live_time_minutes})\" >stdout 2>&1 &",
                        out_remove_fistline=1)
        logger.info("start process return {}", out)

        out = nt._exec_("cat stdout && echo", out_remove_fistline=1)
        logger.info("stdout: {}", out)
        return out.find('Traceback') < 0

    def deploy_model(self, model_path, app_id):
        '图形化建模的 训练结果模型 部署为测试服务'
        nt = self.nt
        logger.info('downloading model files...')
        #fs_path = f"/data/.models/{self.user_name}/GM_{self.exp_name}_{self.run_id}"
        fs_path = model_path
        nt._exec_(f"cd .. && rm -rf {self.home}")
        cont_path = '/'.join(self.home.split('/')[:-1])
        if not _fs_get(self.fileserver_url, self.cont_addr, fs_path, cont_path):
            logger.error("uploads model files failed!")
            return False
        model_dirname = os.path.basename(model_path)
        nt._exec_(f"mv {model_dirname} {self.home} && cd")  # 主目录被 _fs_get 重新建立， 需要重新 cd 一次
        # 切换 venv
        out = nt.source_act('gm')
        logger.info("pyenv return {}， home: [{}]", out, self.home)

        # 启动训练进程
        nt._exec_(f"export PYTHONPATH={self.home}/.app/guimod")
        logger.info("set PYTHONPATH to {}/.app/guimod", self.home)
        live_time_minutes = -1  # 存活的时间（单位： 分钟） -1 则不主动清理
        out = nt._exec_(f"python -c \"from sitech.aipaas.gm.runner.modelrun import run_model; run_model('GM_{app_id}', {live_time_minutes})\" >stdout 2>&1 &",
                        out_remove_fistline=1)
        logger.info("start process return {}", out)

        out = nt._exec_("cat stdout && echo", out_remove_fistline=1)
        logger.info("stdout: {}", out)
        return out.find('Traceback') < 0

    def undeploy_model(self, app_id):
        '删除部署'
        # 实现上是找到进程后 执行一个kill操作就可以了， 进程名称中包含了 GM_{self.exp_name}_{self.run_id}
        #todo 下线
        sig = f"GM_{app_id}"
        out = self.nt._exec_(f"ps -efwww|grep {sig}|grep -v grep", out_remove_fistline=1)
        logger.info("undeploy: ps output {}", out)
        line = out.strip().splitlines()[-1]
        if line.find(sig) < 0:
            logger.warning("process not found, exit")
            return False
        pid = line.split()[1]
        out = self.nt._exec_(f"kill {pid}")
        logger.info("kill {} return {}", pid, out)

        for i in range(10):
            # 再次检查进程是否存在
            time.sleep(0.2)
            out = self.nt._exec_(f"ps -efwww|grep -w {sig}|grep -v grep", out_remove_fistline=1)
            logger.info("undeploy: ps output（after kill) {}", out)
            if out.find(sig) < 0:
                return True
        logger.warning("process {} still exist", pid)
        return False

    def start_jupyter(self, node_id):
        '在容器中 启动图形化建模的训练过程'
        nt = self.nt
        app_dir = '.app'

        # 当前目录: $LOGDIR/GM_${EXPE_NAME}_${RUN_ID}
        #logger.info("clear path...")
        #nt._exec_(f"rm -rf {app_dir} .cache .datasets .config * && mkdir -p {app_dir} .jupyter .library")
        nt._exec_(f"mkdir -p .jupyter {app_dir}")

        # 1 切换 venv
        out = nt.source_act('gm')
        logger.info("pyenv return {}", out)

        token = "123"
        base_uri = "/jupyter-gm"
        _conf = {"NotebookApp": {
            # "password": sha1_token,
            "token": token,
            "open_browser": False,
            "base_url": base_uri,
            "allow_origin":"*" },
            "ServerApp":{
                "allow_origin":"*", "ip": "*" }}
        self.save_data_to_file(json.dumps(_conf), f"{self.home}/.jupyter/jupyter_notebook_config.json")

        nt._exec_(f"mkdir -p {node_id}")
        # 启动jupyter到后台 (这里直接启动， 会产生 defunct 进程， 比较难看， 准备交给mng.py进程来处理
        out = nt._exec_("which jupyter", out_remove_fistline=1)
        ju_path = out.strip()
        start_cmd = {"cwd": f"{self.home}/{node_id}", "env":{
            "PATH":f"{os.path.dirname(ju_path)}:{self.home}/.local/bin", "HOME":self.home},
            "args": f"jupyter notebook --port=8178 --notebook-dir={self.home}/{node_id} --ip=0.0.0.0 --no-browser".split()}
        self.save_data_to_file(json.dumps(start_cmd), f"/data/.app/once_proc/{time.time()}.json")
        # out = nt._exec_(f"echo \"jupyter notebook --port=8178 --notebook-dir=./{node_id} --ip=0.0.0.0 --no-browser >stdout 2>&1 \" >.start.sh",
        #                 out_remove_fistline=1)
        # out = nt._exec_("sh .start.sh &")
        logger.info("start jupyter return {}", out)

        # out = nt._exec_("cat stdout && echo", out_remove_fistline=1)
        # logger.info("stdout: {}", out)

    def stop_jupyter(self):
        nt = self.nt
        out = nt.source_act('gm')
        logger.info("pyenv return {}", out)

        # out = nt._exec_(f"jupyter notebook stop 8178",   out_remove_fistline=1)
        # logger.info("start jupyter return {}", out)
        out = nt._exec_("which jupyter", out_remove_fistline=1)
        ju_path = out.strip()
        start_cmd = {"cwd": self.home, "env": {
            "PATH": f"{os.path.dirname(ju_path)}:{self.home}/.local/bin", "HOME": self.home},
                     "args": f"jupyter notebook stop 8178".split()}
        self.save_data_to_file(json.dumps(start_cmd), f"/data/.app/once_proc/{time.time()}.json")

def test2(local_conf, step=1):
    if len(sys.argv) > 1:
        step = int(sys.argv[1])
    if step == 1:
        # train
        task = NNI_Task(local_conf, "172.18.238.17:32773", None,
                        exp_name="img-cls", user_name='guanyf', node_id="RUN_1")

        def prog_callback(percent, msg):
            pass
        task.start(task_name='ofa',
                   params=dict(dataset='ds_img_cls', max_duration="60",
                               batch_size="32", epoch="100", train_percent="70", device="cuda:3"),
                   progress_callback=prog_callback)
        # 通过下面的地址查看训练进程：  http://172.18.238.17:32774/
    elif step == 2:
        # export model
        task = NNI_Task(local_conf, "172.18.238.17:32773", None,
                        exp_name="img-cls", user_name='guanyf', node_id="RUN_1")
        task.model_export(None, None, task.exp_name, task.run_id)
    elif step == 3:
        task = NNI_Task(local_conf, "172.18.238.17:32773", None,
                        exp_name="img-cls", user_name='guanyf', node_id="RUN_1")
        task.stop_task(task_name="ofa")
    elif step == 4:
        # deploy model,  启动成功后， 访问 http://172.18.243.17:32771/img-cls/
        task = NNI_Task(local_conf, "172.18.243.17:32770",
                        exp_name="img-cls", run_id="RUN_1", user_name='guanyf')
        task.model_deploy(task.exp_name)
    elif step == 5:
        task = NNI_Task(local_conf, "172.18.243.17:32770",
                        expe_name="img-cls", run_id="RUN_1", user_name='guanyf')
        task.model_deploy_delete(task.exp_name)
    elif step == 6:
        task = NNI_Task(local_conf, "172.18.243.17:32770",
                        expe_name="DE1625647681290313210001", run_id="-1", user_name='guanyf',
                        dep_path=f"~/{'DE1625647681290313210001'}")
        task.model_show_tensorboard(task.exp_name)



if __name__ == '__main__':
    db_param = dict(user='iasp_119', password='iasp_119',
                    host='172.18.233.159', port=8066, database='iasp_dev_119')
    local_conf = {
        "FS_ADDR": '172.18.243.23:39913',
        "fileserver_url":"http://172.18.243.23:35527", # 文件服务器的restful服务地址
        "FS_USER": "_dd_",
        "FS_IDENTITY": "/home/laog/.ssh/id_rsa_fs",  # 文件服务器私有密钥
        "CONT_IDENTITY": "/home/laog/.ssh/id_rsa_tr",  # 计算容器的ssh连接密钥文件
        "CONT_USER": "_base_",  # 计算容器的默认用户名
        "task_base": "/home/laog/worksrc/iasp-pymod/ml/aml",  # 任务模块的代码存放路径
        "pypi_server": "http://172.18.243.19:1777",
        "tensorboard_port": 9191,  # 如果训练进程要求启动 tensorboard， 此为其监听端口
        "nni_port": 8787,  # nni webui 的监听端口
    }

    # if True:
    #     test2(local_conf)
    # else:
    #     from svc import util_db as db
    #     db.pool_init(db_param)
    #
    #     if len(sys.argv) > 1:
    #         main_nni(local_conf, db)
    #     else:
    #         train_task_mon(local_conf, db)


# import sys
# sys.path.insert(0, '/data/.app')
# from svc.sshutil import NNI_Task
def t1():
    local_conf = {
        "FS_ADDR": '192.168.40.123:31702',
        "fileserver_url": "http://192.168.40.123:31701",  # 文件服务器的restful服务地址
        "FS_USER": "_dd_",
        "FS_IDENTITY": "/data/.app/nni_id_rsa/id_rsa_fs",  # 文件服务器私有密钥
        "CONT_IDENTITY": "/data/.app/nni_id_rsa/id_rsa_tr",  # 计算容器的ssh连接密钥文件
        "CONT_USER": "_base_",  # 计算容器的默认用户名
        "task_base": "/data/.app/ml/aml",  # 任务模块的代码存放路径
        "pypi_server": "http://192.168.40.124:31705",
        "tensorboard_port": 9191,  # 如果训练进程要求启动 tensorboard， 此为其监听端口
        "nni_port": 8787,  # nni webui 的监听端口
    }
    test_ip, test_ctl_port = "192.168.40.124", 32022
    expe_name, run_id = "DE1654760884192159210001", "RUN_1"
    user_name, node_id = "SU1654071711161394010001", "-1"
    with NNI_Task(local_conf, f"{test_ip}:{test_ctl_port}", task_id=run_id, exp_name=expe_name,
                  user_name=user_name, node_id=node_id, need_fs=True) as task:
        task.model_deploy(expe_name, run_id)


