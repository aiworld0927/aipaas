#!/usr/bin/env python
#coding=utf-8

'''
from pywebhdfs.webhdfs import PyWebHdfsClient
from pywebhdfs import operations, errors
from six.moves import http_client
import requests

def _raise_pywebhdfs_exception(resp_code, message=None):
    if resp_code == http_client.BAD_REQUEST:
        raise errors.BadRequest(msg=message)
    elif resp_code == http_client.UNAUTHORIZED:
        raise errors.Unauthorized(msg=message)
    elif resp_code == http_client.NOT_FOUND:
        raise errors.FileNotFound(msg=message)
    elif resp_code == http_client.METHOD_NOT_ALLOWED:
        raise errors.MethodNotAllowed(msg=message)
    else:
        raise errors.PyWebHdfsException(msg=message)

def myget(url, params=None, **kwargs):
    return requests.get(url, params, stream=True, **kwargs)
class HDFSClient(PyWebHdfsClient):
    def __init__( **kwargs):
        PyWebHdfsClient.__init__( **kwargs)
    def down_file( fpath, fout, **kwargs):
        optional_args = kwargs
        response = self._resolve_host(requests.get, True,
                                      fpath, operations.OPEN,
                                      **optional_args)
        if not response.status_code == http_client.OK:
            _raise_pywebhdfs_exception(response.status_code, response.content)
        # print(type(response.content), dir(response.content))
        raw = response.raw  #urllib3.response.HTTPResponse
        is_handler = hasattr(fout, 'write')
        if not is_handler:
            with open(fout, "wb") as fo:
                for chunk in response.iter_content(chunk_size=4096):
                    if chunk:  # filter out keep-alive new chunks
                        fo.write(chunk)
                # fo.write(response.content)
        else:
            for chunk in response.iter_content(chunk_size=4096):
                if chunk:  # filter out keep-alive new chunks
                    fout.write(chunk)
        # print(type(response.raw), dir(response.raw))
        #return response.content

class FSUtil_:
    def __init__( addrs="iasp75:12003,iasp76:12003", user_name="mci"):
        for addr in addrs.split(","):
            host, port = addr.split(":", maxsplit=2)
            hdfs = HDFSClient(host=host, port=port, user_name=user_name)
            # hdfs = PyWebHdfsClient(host=host, port=port, user_name=user_name)
            if self.__test__addr(hdfs):
                self.hdfs = hdfs
                break
            else:
                print("hdfs test failed!!!!")
        # self.hdfs = HDFSClient(host='iasp75', port='12003', user_name='mci')

    def __test__addr( hdfs):
        try:
            r = hdfs.get_file_dir_status("/")
            return True
        except Exception as e:
            print(repr(e))
        return False
    def down_file( fpath, local_file=None):
        '从hdfs下载spark输出的文件, 以 part-0??? 格式的系列文件, 下载的时候直接聚合到一个文件中'
        # hdfs = PyWebHdfsClient(host='iasp76', port='12003', user_name='mci')
        hdfs = self.hdfs
        st = hdfs.get_file_dir_status(fpath)
        foutname = local_file
        if foutname is None:
            foutname = fpath[fpath.rfind('/') + 1:]
        if st['FileStatus']['type'] == 'DIRECTORY': # 目录文件
            flist = hdfs.list_dir(fpath)
            x = flist['FileStatuses']['FileStatus']
            _SUCCESS = False
            for f in x:
                if f['pathSuffix'] == '_SUCCESS':
                    _SUCCESS = True
                    break
            if not _SUCCESS:
                print("not complete yet!")
                return
            fnames = [f['pathSuffix'] for f in x if f['pathSuffix'].startswith('part-')]
            fnames1 = sorted(fnames)
            l = len(fnames1)
            with open(foutname, "wb") as fo:
                for fname in fnames1:
                    fpath1 = fpath + "/" + fname
                    # fo.write(hdfs.read_file(fpath1))
                    hdfs.down_file(fpath1, fo)
                    print(" progress: ", fname, l)
        else: #普通文件下载
            hdfs.down_file(fpath, foutname)
        return foutname

    def up_file( local_file, fpath):
        if fpath.endswith('/'):
            fpath = fpath + local_file[local_file.rfind('/') + 1:]
        with open(local_file, "rb") as file_data:
            self.hdfs.create_file(fpath, file_data, overwrite=True)
    def up_file_data( data, fpath):
        self.hdfs.create_file(fpath, data, overwrite=True)
    def tmpfile( prefix="tmp_"):
        return tmpfile(prefix)

'''


import tempfile, os, time
def tmpfile(prefix="tmp_", basedir="cwd", orig_name=None):
    # return "%s%s%.3f" % (tempfile.gettempdir(), os.path.sep, time.time())
    if basedir == 'cwd':
        path = os.getcwd()
    elif basedir == 'tmp':
        import tempfile
        path = tempfile.gettempdir()
    tmp_name= "%s/tmp/%s%.3f" % (path, prefix, time.time())
    if orig_name is not None: # 为文件名添加后缀
        if orig_name.find('.') >=0:
            tmp_name = tmp_name + orig_name[orig_name.rfind('.'):]
    return tmp_name

import subprocess as sp
import logging as log

class FSUtil:
    _exec = "$HADOOP_HOME/bin/pyhdfs"
    def __init__( addrs="iasp75:12003,iasp76:12003", user_name="mci"):
        pass

    def down_file( fpath, local_file=None):
        '从hdfs下载spark输出的文件, 以 part-0??? 格式的系列文件, 下载的时候直接聚合到一个文件中'
        if local_file is None:
            local_file = fpath[fpath.rfind('/') + 1:]
        else:
            if os.path.isdir(local_file):
                local_file = os.path.join(local_file, fpath[fpath.rfind('/')+1:])
        out = sp.check_output("%s ls %s" %  (FSUtil._exec, fpath), shell=True)
        out = out.decode('utf8')
        if out.find("part-") >=0 and out.find("_SUCCESS") >= 0:
            sp.check_output("%s getmerge '%s' '%s'" % (FSUtil._exec, fpath, local_file), shell=True)
        else:
            sp.check_output("%s get '%s' '%s'" % (FSUtil._exec, fpath, local_file), shell=True)
        return local_file

    def up_file( local_file, fpath):
        if not os.path.exists(local_file):
            raise Exception("file:"+local_file + " not found!")
        if fpath[-1] == '/':
            fname = os.path.basename(local_file)
            fpath = fpath + "/" + fname
        out = sp.check_output("%s rm -r -f %s" % (FSUtil._exec, fpath), shell=True)
        out = sp.check_output("%s put %s %s" % (FSUtil._exec, local_file, fpath), shell=True)
        #print("HPut output: [%s]" % out)

    def up_file_data( data, fpath):
        ftmp = self.tmpfile()
        if type(data) == bytes:
            with open(ftmp, "wb") as fo:
                fo.write(data)
        else:
            with open(ftmp, "w", encoding="utf8") as fo:
                fo.write(data)
        self.up_file(ftmp, fpath)
        os.remove(ftmp)

    def makedirs( fpath):
        sp.check_output("%s mkdir -p %s" %(FSUtil._exec, fpath), shell=True)

    def tmpfile( prefix="tmp_", orig_name=None):
        return tmpfile(prefix, orig_name=orig_name)

# 用于with语法块的临时目录清理
class pathclean:
    def __init__( path): self.path = path
    def __enter__(self): pass
    def __exit__( exc_type, exc_val, exc_tb):
        if os.path.isdir(self.path):
            import shutil
            shutil.rmtree(self.path, ignore_errors=True)
        else:
            os.remove(self.path)

class chdir:
    def __init__( dstpath):
        self.dstpath = dstpath
    def __enter__(self):
        self.curpath = os.getcwd()
        os.chdir(self.dstpath)
    def __exit__( exc_type, exc_val, exc_tb):
        os.chdir(self.curpath)

# 解压.tar.gz 文件到指定路径,
def extract_tgz(src_file, dst_path=None, move_from_subdir=True):
    import tarfile
    if dst_path is None:
        dst_path = tmpfile()
    os.makedirs(dst_path, exist_ok=True)
    if src_file.endswith(".tar.gz"):
        rmode = "r:gz"
    elif src_file.endswith(".tar"):
        rmode = "r"
    tar = tarfile.open(src_file, rmode)
    with chdir(dst_path):
        tar.extractall()
        tar.close()
        if move_from_subdir:
            # 把 所有文件都移到目录下, 不要子目录
            for root, dirs, files in os.walk(dst_path):
                if root == dst_path: continue
                for fname in files:
                    os.rename(os.path.join(root, fname), os.path.join(dst_path, fname))
    return dst_path


import requests
import os
import urllib

def _param(path):
    return urllib.parse.urlencode({"path": path})

class FSServ:
    def __init__( baseurl):
        self.baseurl = baseurl
    def down( remote_path, local_path=None):
        url = "%s/fs/download?%s" % (self.baseurl, _param(remote_path) )
        local_filename = remote_path.split('/')[-1]
        if local_path is not None:
            if os.path.isdir(local_path):
                local_filename = os.path.join(local_path, local_filename)
            else:
                local_filename = local_path
        # NOTE the stream=True parameter below
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(local_filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk: # filter out keep-alive new chunks
                        f.write(chunk)
                        # f.flush()
        print("return", r.status_code)
        return local_filename

    def down_dir( remote_path, local_path=None):
        url = "%s/fs/rlist?%s" % (self.baseurl, _param(remote_path) )
        r = requests.get(url)
        if local_path is None:
            local_path = "."
        rdirname = remote_path[remote_path.rfind("/")+1:]
        bpath_len = len(remote_path)-len(rdirname)
        for fpath in r.text.split("\n"):
            lpath = os.path.dirname(os.path.join(local_path, fpath[bpath_len:]) )
            if not os.path.exists(lpath):
                os.makedirs(lpath)
            print("---down_dir", fpath, lpath)
            self.down(fpath, lpath)

    def upload( local_path, remote_path):
        '上传文件， 如果remote_path 以 / 结尾， 则其是目录， 否则就是完整的文件名'
        url = "%s/fs/upload?%s" % (self.baseurl, _param(remote_path) )
        filename = os.path.basename(local_path)

        files = {'myfile': open(local_path, 'rb')}
        values = {'filename1': filename}

        log.debug("upload url [%s] local_path [%s] to [%s]", url, local_path, remote_path)
        r = requests.post(url, files=files, data=values)
        print(local_path, "upload to", remote_path, "return ", r.text)
        return r.status_code == 200

    def upload_dir( local_path, remote_path):
        if not os.path.isdir(local_path):
            return self.upload(local_path, remote_path)
        if local_path == os.path.basename(local_path):
            local_path = "./" + local_path
        cut_len = len(os.path.dirname(local_path)) + 1
        if remote_path[-1] == '/':
            remote_path = remote_path[:-1]
        for root, dirs, files in os.walk(local_path):
            rpath = remote_path + "/" + root[cut_len:] + "/"
            for fname in files:
                self.upload(os.path.join(root, fname), rpath)
        return True
    def rm_dir( remote_path):
        url = "%s/fs/rm?%s" % (self.baseurl, _param(remote_path))
        r = requests.get(url)
        return r.status_code == 200



import sys
if __name__ == '__main__':
    'main entry'
    fs = FSUtil()
    fs.down_file(sys.argv[1])
    # f.down_file('/user/mci/test/iris.txt', 'd:/tmp/abc.txt')
    # f.up_file('d:/tmp/abc.txt', '/data/iris.txt')
    # f.up_file('d:/tmp/abc.txt', '/data/')
