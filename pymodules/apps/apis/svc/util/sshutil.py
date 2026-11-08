#coding=utf-8

import sys
from loguru import logger
from os import system
from paramiko import SSHClient, AutoAddPolicy, RSAKey
from paramiko.auth_handler import AuthenticationException, SSHException
from scp import SCPClient, SCPException

import logging as log

logger.add(sys.stderr,
           format="{time} {level} {message}",
           filter="client",
           level="INFO")
logger.add('logs/log_{time:YYYY-MM-DD}.log',
           format="{time} {level} {message}",
           filter="client",
           level="ERROR",
           rotation='1 day',
           retention="5 days")

class RemoteClient:
    def __init__(self, host, user, ssh_key_filepath, remote_path=None):
        self.host = host
        self.user = user
        self.ssh_key_filepath = ssh_key_filepath
        self.remote_path = remote_path
        self.client = None
        self.scp = None
        # self.__upload_ssh_key()

    def __enter__(self):
        self.__connect()
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()

    def __get_ssh_key(self):
        """Fetch locally stored SSH key."""
        try:
            self.ssh_key = RSAKey.from_private_key_file(self.ssh_key_filepath)
            logger.info(f'Found SSH key at self {self.ssh_key_filepath}')
        except SSHException as error:
            logger.error(error)
        return self.ssh_key

    def __upload_ssh_key(self):
        try:
            system(f'ssh-copy-id -i {self.ssh_key_filepath} {self.user}@{self.host}>/dev/null 2>&1')
            system(f'ssh-copy-id -i {self.ssh_key_filepath}.pub {self.user}@{self.host}>/dev/null 2>&1')
            logger.info(f'{self.ssh_key_filepath} uploaded to {self.host}')
        except FileNotFoundError as error:
            logger.error(error)

    def __connect(self):
        """Open connection to remote host."""
        try:
            self.client = SSHClient()
            self.client.load_system_host_keys()
            self.client.set_missing_host_key_policy(AutoAddPolicy())
            self.client.connect(self.host,
                                username=self.user,
                                key_filename=self.ssh_key_filepath,
                                look_for_keys=True,
                                timeout=5000)
            self.scp = SCPClient(self.client.get_transport())  # For later
        except AuthenticationException as error:
            logger.info('Authentication failed: did you remember to create an SSH key?')
            logger.error(error)
            raise error
        finally:
            return self.client

    def disconnect(self):
        """Close ssh connection."""
        if self.client is not None:
            self.scp.close()
            self.client.close()
            self.client = None

    def execute(self, cmd):
        if self.client is None:
            self.__connect()
        stdin, stdout, stderr = self.client.exec_command(cmd)
        stdout.channel.recv_exit_status()
        return stdout.read().decode('utf-8')

    def execute_commands(self, commands):
        """Execute multiple commands in succession."""
        if self.client is None:
            self.client = self.__connect()
        for cmd in commands:
            stdin, stdout, stderr = self.client.exec_command(cmd)
            stdout.channel.recv_exit_status()
            response = stdout.readlines()
            for line in response:
                logger.info(f'INPUT: {cmd} | OUTPUT: {line}')

    def bulk_upload(self, files, remote_path=None):
        """Upload multiple files to a remote directory."""
        if self.client is None:
            self.__connect()
        if remote_path is None:
            remote_path = self.remote_path
        uploads = [self.upload_file(file, remote_path) for file in files]
        logger.info(f'Uploaded {len(uploads)} files to {remote_path} on {self.host}')

    def upload_file(self, file, remote_path):
        if self.client is None:
            self.__connect()
        """Upload a single file to a remote directory."""
        try:
            self.scp.put(file,
                         recursive=True,
                         remote_path=remote_path)
        except SCPException as error:
            logger.error(error)
            raise error
        finally:
            return file

    def download_file(self, file, local_path=None):
        """Download file from remote host."""
        if self.client is None:
            self.__connect()
        self.scp.get(file, local_path=local_path)

'''Linux 3.10.0-693.el7.x86_64 (iasp76) 	10/29/2019 	_x86_64_	(8 CPU)

avg-cpu:  %user   %nice %system %iowait  %steal   %idle
           0.29    0.00    0.14    0.17    0.01   99.40

              total        used        free      shared  buff/cache   available
Mem:          32013       10558        2114        1323       19339       19640
Swap:          8191        1005        7186
Filesystem                1K-blocks      Used Available Use% Mounted on
/dev/mapper/datavg-lv_mci 349745040 260114932  72879664  79% /mci'''
def _parse_stat(sout):
    '解析命令行输出的字符串, 分解出资源使用率, 返回: pct_cpu, pct_mem, pct_disk'
    #logger.debug('[[%s]]' % sout)
    if type(sout) == bytes:
        sout = sout.decode('utf-8')
    lines = sout.split("\n")
    for i in range(len(lines)):
        ln = lines[i]
        if ln.find('avg-cpu:') >= 0: # avg-cpu:  %user   %nice %system %iowait  %steal   %idle
            ln = lines[i+1] #            0.29    0.00    0.14    0.17    0.01   99.40
            pct_cpu = 100.0 - float(ln.strip().split()[-1])
        elif ln.find('buff/cache') > 0: # total        used        free      shared  buff/cache   available
            ln = lines[i+1]  # Mem:          32013       10558        2114        1323       19339       19640
            fi = ln.strip().split()
            pct_mem = 100.0 * float(fi[-1]) / float(fi[1])
        elif ln.find('Mounted on') > 0: # Filesystem                1K-blocks      Used Available Use% Mounted on
            ln = lines[i+1] # /dev/mapper/datavg-lv_mci 349745040 260114932  72879664  79% /mci
            fi = ln.strip().split()
            pct_disk = 100.0 * float(fi[-4]) / (float(fi[-4]) + float(fi[-3]))
    return pct_cpu, pct_mem, pct_disk

def check_one_host(args):
    '获取单个主机的资源使用状态'
    keyfile, h = args
    remote = RemoteClient(h['host'], h['user'], keyfile)
    sout = remote.execute(f"iostat -c && free -m && df -k {h['disk']}")
    remote.disconnect()
    pct_cpu, pct_mem, pct_disk = _parse_stat(sout)
    return h['host'], pct_cpu, pct_mem, pct_disk

from multiprocessing.pool import ThreadPool

def TargetSelect(deploy_targets=None):
    '目标主机选择服务, 主要是计算目标主机的资源使用情况'
    keyfile = "/home/guanyf/.ssh/id_rsa"
    weight_cpu, weight_mem, weight_disk = 0.1, 0.2, 0.7
    with ThreadPool(processes=3) as pool:
        hosts_stat = pool.map(check_one_host, [(keyfile, h) for h in deploy_targets])
    hosts_load = []
    for host, pct_cpu, pct_mem, pct_disk in hosts_stat:
        total_stat = pct_cpu * weight_cpu + pct_mem * weight_mem + pct_disk * weight_disk
        logger.debug("TargetSelect, host:%s cpu=%.2f%% mem=%.2f%% disk=%.2f%% summary=%.2f" %(
            host, pct_cpu, pct_mem, pct_disk, total_stat))
        hosts_load.append(total_stat)
    idxs = sorted(range(len(hosts_load)), key=lambda k: hosts_load[k]) # 排序,取序号
    h = deploy_targets[idxs[0]]
    logger.debug(f"  selected: {h['host']}")

def hosts_sort(deploy_targets, count=1):
    '目标主机按照资源使用情况排序, 从大到小, 主机数量小于count, 则重复, 返回排序的序号'
    # {"host":"172.18.231.68","user":"mci","disk":"/mci","id_file":"/mci/.ssh/id_rsa"}
    def one_host(args):
        '获取单个主机的资源使用状态'
        h = args
        remote = RemoteClient(h['host'], h['user'], h['id_file'])
        sout = remote.execute(f"iostat -c && free -m && df -k {h['disk']}")
        remote.disconnect()
        pct_cpu, pct_mem, pct_disk = _parse_stat(sout)
        return h['host'], pct_cpu, pct_mem, pct_disk
    weight_cpu, weight_mem, weight_disk = 0.1, 0.2, 0.7
    with ThreadPool(processes=3) as pool:
        hosts_stat = pool.map(one_host, deploy_targets)
    hosts_load = []
    for host, pct_cpu, pct_mem, pct_disk in hosts_stat:
        total_stat = pct_cpu * weight_cpu + pct_mem * weight_mem + pct_disk * weight_disk
        logger.debug("TargetSelect, host:%s cpu=%.2f%% mem=%.2f%% disk=%.2f%% summary=%.2f" %(
            host, pct_cpu, pct_mem, pct_disk, total_stat))
        hosts_load.append(total_stat)
    idxs = sorted(range(len(hosts_load)), key=lambda k: hosts_load[k]) # 排序,取序号
    if len(deploy_targets) < count:
        n = idxs * ( int(count / len(deploy_targets) ) + 1)
        return n[:count]
    else:
        return idxs[:count]

'''get_port.py
import socket as st, sys
count = 1
if len(sys.argv) > 1:
    count = int(sys.argv[1])
for i in range(count):
	s=st.socket(st.AF_INET,st.SOCK_STREAM)
	s.bind(("", 0))
	s.listen(1)
	print(s.getsockname()[1])
	s.close()
'''
def get_port(target_host, port_count=1):
    h = target_host
    remote = RemoteClient(h['host'], h['user'], h['id_file'])
    sout = remote.execute(f"{sys.executable} ~/dockers/packages/entry/get_port.py {port_count}")
    log.debug("  ---get_port.py return [%s]", sout)
    remote.disconnect()
    return [int(n.strip()) for n in sout.strip().split("\n")]

def remote_exec(h, cmd):
    remote = RemoteClient(h['host'], h['user'], h['id_file'])
    sout = remote.execute(cmd)
    remote.disconnect()
    return sout

def __test__():
    deploy_hosts = [
        {"host":"172.18.231.68","user":"mci","disk":"/mci"},
        {"host":"172.18.231.75","user":"mci","disk":"/mci"},
        {"host":"172.18.231.76","user":"mci","disk":"/mci"}]

    TargetSelect(deploy_hosts)

    # remote = RemoteClient("172.18.231.68", "mci", "/home/guanyf/.ssh/id_rsa", "/mci")
    # remote.execute_commands(['cd /var/www/ && ls',
    #                          'tail /var/log/nginx/access.log',
    #                          'ps aux | grep python'])
    # remote.disconnect()

def __test1__():
    r = RemoteClient("172.18.231.68", "mci", "/home/guanyf/.ssh/id_rsa", "/mci")
    # out = r.execute("ls -l /tmp")
    # print(out)
    # r.download_file("/tmp/tmputnz8y8s", "/tmp")
    # r.download_file("/tmp/tmputnz8y8s", "/tmp/222")
    r.upload_file("/tmp/222", "/tmp/111")
    r.upload_file("/tmp/222", "/tmp")
    r.disconnect()

if __name__ == '__main__':
    __test1__()
