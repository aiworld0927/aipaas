# coding=utf-8
"""
deploysvc - FastAPI 版本
提供模型镜像构建、发布、部署、开发容器分配等服务
"""
import os
import copy
import json
import time
import uuid
import shutil
import string
import logging as log
import traceback
import subprocess
import threading
from random import choice, randint
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, APIRouter, Body, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from svc.util.fsutil import FSUtil, FSServ
from svc.util.sshutil import hosts_sort, get_port, remote_exec, RemoteClient
import svc.util_db as db

_pjoin = os.path.join
logger = log.getLogger(__name__)


# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------
class AppState:
    conf: Dict[str, Any] = {}
    fs: Optional[FSUtil] = None
    image_path: str = ""
    local_ip: str = ""
    pypi_server: str = ""
    k8s_name_space: str = ""
    k8s_docker_repository: str = ""
    fileserv_path: str = ""
    basepath_tmp: str = ""
    rest_register_url: str = ""
    rest_port: str = ""

    container_list_file: str = ""
    deploy_status_file: str = ""
    dev_containers: str = ""
    dev_images: str = ""

    deploy_query_obj: Dict[str, list] = {}


S = AppState()


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------
class RootWrapper(BaseModel):
    ROOT: Dict[str, Any]


class BodyWrapper(BaseModel):
    BODY: Dict[str, Any]


class RJson:
    """统一响应: {"ROOT": {"HEADER": {}, "RETURN_CODE": .., "RETURN_MSG": .., "BODY": ..}}"""

    @staticmethod
    def make(obj: Any = None, rcode: int = 0, rmsg: str = "success") -> JSONResponse:
        ret = {"ROOT": {"HEADER": {}, "RETURN_CODE": rcode,
                        "RETURN_MSG": rmsg, "BODY": obj}}
        return JSONResponse(content=ret)


def _read_body(request: Request) -> Dict[str, Any]:
    """兼容原实现: 直接读原始 body 再 json.loads"""
    raw = request.scope.get("_body_cache")
    if raw is None:
        raw = b""
    return json.loads(raw.decode("utf-8")) if raw else {}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def __gen_rand_str(nc: int = 6) -> str:
    if nc == 0:
        min_char, max_char = 24, 32
    else:
        min_char, max_char = nc, nc
    allchar = string.ascii_letters + string.digits
    return "".join(choice(allchar) for _ in range(randint(min_char, max_char)))


def _exec(cmd_str: str):
    proc = subprocess.Popen(cmd_str, shell=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout = proc.stdout.read()
    stderr = proc.stderr.read()
    return stdout.decode("utf8"), stderr.decode("utf8")


def _update_status(qry_id: str, pct: int = 0, code: int = 0, msg: Optional[str] = None):
    if qry_id not in S.deploy_query_obj:
        logger.error("deploy_id: %s not found", qry_id)
        return
    if msg == "_del":
        del S.deploy_query_obj[qry_id]
        _persist_deploy_status()
        return
    s = S.deploy_query_obj[qry_id]
    s[0] = pct
    s[2] = time.time()
    s[3] = code
    s[4] = msg
    _persist_deploy_status()


def _persist_deploy_status():
    with open(S.deploy_status_file, "w") as fo:
        json.dump(S.deploy_query_obj, fo, indent=2)


def _find_image_id(stdout: str, img_rep: str, img_tag: str) -> Optional[str]:
    for ln in stdout.split("\n"):
        if ln.startswith(f"{img_rep} ") and f" {img_tag} " in ln:
            return ln.split()[2]
    logger.info("Image not found %s:%s", img_rep, img_tag)
    return None


def _save_container_list(container_id, img_name, ssh_prefix, cmd_str,
                         old_container_id=None, model_id=None):
    _fname = S.container_list_file
    if os.path.exists(_fname):
        with open(_fname) as fi:
            clist = json.load(fi)
        if old_container_id is not None:
            clist.pop(old_container_id, None)
    else:
        clist = {}
    if container_id is not None:
        clist[container_id] = [img_name, ssh_prefix, cmd_str, model_id]
    with open(_fname, "w") as fo:
        json.dump(clist, fo, indent=2)


def _parse_stat(sout: str):
    pct_cpu = pct_mem = pct_disk = 0.0
    lines = sout.split("\n")
    for i in range(len(lines)):
        ln = lines[i]
        if "avg-cpu:" in ln:
            ln = lines[i + 1]
            pct_cpu = 100.0 - float(ln.strip().split()[-1])
        elif "buff/cache" in ln:
            ln = lines[i + 1]
            fi = ln.strip().split()
            pct_mem = 100.0 * float(fi[-1]) / float(fi[1])
        elif "Mounted on" in ln:
            ln = lines[i + 1]
            fi = ln.strip().split()
            pct_disk = 100.0 * float(fi[-4]) / (float(fi[-4]) + float(fi[-3]))
    return pct_cpu, pct_mem, pct_disk


def _pip_match_version(name: str, pypi_url: str, pyver=(3, 7, 0)):
    from pip._internal.commands.download import DownloadCommand
    from pip._internal.models.format_control import FormatControl
    from pip._internal.models.target_python import TargetPython
    from optparse import Values
    import re

    m = re.search(r"^http://([^:]+):[\d]+", pypi_url)
    trusted_host = m.group(1)
    options = Values({
        "help": None, "isolated_mode": False, "require_venv": False, "verbose": 0,
        "version": None, "quiet": 0, "log": None, "no_input": False, "proxy": "",
        "retries": 5, "timeout": 15, "exists_action": [],
        "trusted_hosts": [trusted_host], "cert": None, "client_cert": None,
        "cache_dir": "/data/.cache/pip", "disable_pip_version_check": False,
        "no_color": False, "no_python_version_warning": False,
        "features_enabled": [], "deprecated_features_enabled": [],
        "constraints": [], "requirements": [], "build_dir": None,
        "ignore_dependencies": False, "global_options": None,
        "format_control": FormatControl(set(), set()), "prefer_binary": False,
        "src_dir": "/data/.app/envs/pip38/src", "pre": False,
        "require_hashes": False, "progress_bar": "on", "build_isolation": True,
        "use_pep517": None, "ignore_requires_python": None,
        "download_dir": "/data/.app/envs/pip38/_p", "platforms": None,
        "python_version": None, "implementation": None, "abis": None,
        "index_url": pypi_url, "extra_index_urls": [], "no_index": False,
        "find_links": [], "no_clean": False, "ignore_installed": True,
        "editables": [],
    })
    target_python = TargetPython(py_version_info=pyver)
    d = DownloadCommand(name="download", summary="")
    with d.main_context():
        session = d.get_default_session(options)
        finder = d._build_package_finder(
            options=options, session=session, target_python=target_python,
            ignore_requires_python=options.ignore_requires_python,
        )
        return [str(x.version) for x in finder.find_all_candidates(name)]


def __pipsearch(keyword: str, pypi_server: str):
    from pip._internal.commands.search import SearchCommand
    from optparse import OptionParser

    options__ = {
        "isolated_mode": False, "require_venv": False, "verbose": 0,
        "version": None, "quiet": 1, "log": None, "no_input": False,
        "proxy": "", "retries": 1, "timeout": 15,
        "skip_requirements_regex": "", "exists_action": [],
        "trusted_hosts": [], "cert": None, "client_cert": None,
        "cache_dir": "/tmp/.cache_pip", "disable_pip_version_check": True,
        "no_color": False, "index": pypi_server,
    }
    parser = OptionParser()
    for k, v in options__.items():
        parser.add_option(f"--{k}", dest=k, default=v)
    options, _ = parser.parse_args([])
    search = SearchCommand(name="search", summary="")
    with search.main_context():
        return search.search([keyword], options)


# ---------------------------------------------------------------------------
# 后台线程
# ---------------------------------------------------------------------------
def _thread_check_containers():
    while True:
        time.sleep(30.0)
        _fname = S.container_list_file
        if not os.path.exists(_fname):
            continue
        with open(_fname) as fi:
            clist = json.load(fi)
        for c_id, vals in list(clist.items()):
            img_name, ssh_prefix, cmd_str, model_id = vals
            sout, _ = _exec(f"{ssh_prefix} docker ps")
            if not any(ln.startswith(c_id) for ln in sout.split("\n")):
                logger.warning("container %s not found, restart", c_id)
                sout, _ = _exec(f"{ssh_prefix} {cmd_str}")
                new_id = sout.strip()[:12]
                _save_container_list(new_id, img_name, ssh_prefix, cmd_str,
                                     old_container_id=c_id, model_id=model_id)


def _thread_deploy(jo: Dict[str, Any], qry_id: str):
    try:
        ImageName = jo["ImageName"]
        ModelId = jo["ModelId"]
        TargetHostUser = jo["TargetHostUser"]
        p = TargetHostUser.find(":")
        port_scp = port_ssh = ""
        if p > 0:
            port = TargetHostUser[p + 1:].strip()
            TargetHostUser = TargetHostUser[:p]
            port_scp = f" -P {port}"
            port_ssh = f" -p{port}"
        local_tmp = f"{os.getenv('HOME')}/.tmp"
        os.makedirs(local_tmp, exist_ok=True)
        ssh_prefix = f"ssh{port_ssh} {TargetHostUser}"

        if ":" in ImageName:
            img_rep, img_tag = ImageName.split(":", 1)
        else:
            img_rep, img_tag = ImageName, "latest"

        if S.local_ip not in TargetHostUser:
            _update_status(qry_id, 10, msg="saving image...")
            subprocess.check_output(
                f"docker save {ImageName}|gzip -c > {local_tmp}/{ModelId}.gz",
                shell=True)
            _update_status(qry_id, 20, msg="sending image...")
            subprocess.check_output(f"ssh{port_ssh} {TargetHostUser} mkdir -p ~/.tmp", shell=True)
            subprocess.check_output(
                f"scp{port_scp} {local_tmp}/{ModelId}.gz {TargetHostUser}:.tmp/", shell=True)
            sout = subprocess.check_output(f"ssh{port_ssh} {TargetHostUser} ls ~/.tmp", shell=True)
            if ModelId not in sout.decode("utf8"):
                raise Exception("scp upload file failed!")
            os.remove(f"{local_tmp}/{ModelId}.gz")

            _update_status(qry_id, 40, msg="restoring image...")
            sout, _ = _exec(f"{ssh_prefix} docker images")
            img_id = _find_image_id(sout, img_rep, img_tag)
            if img_id is not None:
                while True:
                    _, stderr = _exec(f"{ssh_prefix} docker rmi -f {img_id}")
                    if "unable to delete" in stderr:
                        c_id = stderr.strip().split()[-1]
                        _exec(f"{ssh_prefix} docker kill {c_id}")
                    else:
                        break
            _exec(f"{ssh_prefix} gunzip -c ~/.tmp/{ModelId}.gz\\|docker load")
        else:
            logger.info("---deploy on local: %s", S.local_ip)

        sout, _ = _exec(f"{ssh_prefix} docker images")
        img_id = _find_image_id(sout, img_rep, img_tag)
        if img_id is None:
            raise Exception("Image restore failed")
        _exec(f"{ssh_prefix} rm ~/.tmp/{ModelId}.gz")

        _update_status(qry_id, 80, msg="starting container...")
        with open(_pjoin(local_tmp, "get_port.py"), "w") as fo:
            fo.write('import socket as st;s=st.socket(st.AF_INET,st.SOCK_STREAM);'
                     's.bind(("", 0));s.listen(1);print(s.getsockname()[1]);s.close()\n')
        subprocess.check_output(
            f"scp{port_scp} {local_tmp}/get_port.py {TargetHostUser}:.tmp/", shell=True)
        sout, _ = _exec(f"{ssh_prefix} python ~/.tmp/get_port.py")
        bind_port = int(sout.strip())

        cmd_str = ("docker run -d --rm -p {p}:{p} -e MAIN_PORT={p} "
                   "-e REGISTER_URL={reg} -e HOST_IP={host} {img}").format(
            p=bind_port, reg=S.rest_register_url, img=img_id,
            host=TargetHostUser.split("@")[1])
        sout, _ = _exec(f"{ssh_prefix} {cmd_str}")
        container_id = sout.strip()[:12]
        _update_status(qry_id, 100, 0,
                       f"all done, img_id: {img_id} container_id : {container_id}")
        _save_container_list(container_id, f"{img_rep}:{img_tag}", ssh_prefix, cmd_str,
                             model_id=ModelId)
    except Exception as ex:
        logger.error("ImageDeploy failed: %s", ex)
        _update_status(qry_id, 100, -2, "exception occurs:" + str(ex))


def _build_push_image(Nodes, ModelId, basepath, store_fpath, imagesNameTemp):
    Images, image_info = [], {}
    MODEL_STATUS = ""
    for node in Nodes:
        index = Nodes.index(node)
        name = imagesNameTemp[index]
        imageName = f"{ModelId}_{name}"
        try:
            sout = subprocess.Popen(
                f"docker build -q -t {imageName} --target {imageName} .",
                cwd=basepath, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as ex:
            logger.info("docker build fail: %s", ex)
            MODEL_STATUS = "04"
            break
        stderr = sout.stderr.read().decode("utf-8")
        if stderr != "":
            MODEL_STATUS = "04"
            logger.info("docker build exception: %s", stderr)
            break

        ImageId = sout.stdout.read().decode("utf-8")[7:-1]
        Role = node["Role"]
        temp = {"Role": Role, "ImageId": ImageId, "ImageName": imageName}
        MODEL_STATUS = "03"
        image_info[Role] = temp
        Images.append(temp)

        subprocess.check_output(
            f"docker tag {imageName} {S.k8s_docker_repository}/{S.k8s_name_space}/{imageName}",
            cwd=basepath, shell=True)
        try:
            subprocess.check_output(
                f"docker push {S.k8s_docker_repository}/{S.k8s_name_space}/{imageName}",
                cwd=basepath, shell=True)
        except Exception as ex:
            logger.info("docker push fail, retry: %s", ex)
            try:
                subprocess.check_output(
                    f"docker push {S.k8s_docker_repository}/{S.k8s_name_space}/{imageName}",
                    cwd=basepath, shell=True)
            except Exception as ex2:
                logger.error("docker push fail again: %s", ex2)

    for image_dict in Images:
        try:
            db.update_model_image(ModelId, MODEL_STATUS,
                                  image_dict.get("ImageId", ""),
                                  image_dict.get("Role", ""))
        except Exception as ex:
            logger.info("update_database: %s", ex)
    with open(store_fpath, "w", encoding="utf-8") as fo:
        json.dump(image_info, fo, indent=4)


# ---------------------------------------------------------------------------
# 路由实现
# ---------------------------------------------------------------------------
router = APIRouter()


@router.post("/ImageList")
async def image_list(request: Request):
    jo = _read_body(request)
    query = jo.get("BODY", {}).get("Query", "")
    queryid = jo.get("BODY", {}).get("ID", "")

    imgs = []
    for fname in os.listdir(S.image_path):
        if fname[0] == ".":
            continue
        fpath = os.path.join(S.image_path, fname)
        if not os.path.isdir(fpath):
            continue
        conf_path = os.path.join(fpath, "conf")
        if not os.path.exists(conf_path):
            continue
        with open(conf_path) as fi:
            j = json.load(fi)
        if queryid == j["image_id"]:
            pkg_file = os.path.join(fpath, "conf_base_pkg")
            if not os.path.exists(pkg_file):
                continue
            with open(pkg_file) as fi:
                pkgs = [f.strip() for f in fi.readlines()]
        else:
            pkgs = ""
        if query != "" and query not in j["image_name"]:
            continue
        imgs.append({"ID": fname, "Name": j["image_name"], "Description": j["desc"],
                     "Packages": pkgs, "Size": j["size"], "Created": j["created"],
                     "ImageId": j["image_id"], "BaseVer": j.get("BaseVer")})
    return RJson.make({"Images": imgs})


@router.post("/PackageSearch")
async def package_search(request: Request):
    try:
        jo = _read_body(request)
        query = jo["ROOT"]["BODY"]["Query"]
        basever = jo["ROOT"]["BODY"].get("basever", "")
        pkgs = []
        if not query:
            return RJson.make({"Packages": pkgs, "BaseVer": basever})
        qret: Dict[str, str] = {}
        for pkg in query.split(","):
            for ret in __pipsearch(pkg, S.pypi_server):
                qret[ret["name"]] = ret["summary"]
        pyver = tuple(int(x) for x in basever.split(".")) if basever else (3, 7, 0)
        for name in qret:
            for ver in _pip_match_version(name, S.pypi_server, pyver=pyver):
                pkgs.append({"Name": name, "Version": ver})
        return RJson.make({"Packages": pkgs, "BaseVer": basever})
    except Exception as x:
        logger.error("search failed: %s\n%s", x, traceback.format_exc())
        return RJson.make(None, 500, f"search failed {x}")


@router.post("/PackageSearchNew")
async def package_search_new(request: Request):
    try:
        jo = _read_body(request)
        query = copy.deepcopy(jo["ROOT"]["BODY"]["Query"])
        basever = jo["ROOT"]["BODY"].get("basever", "")
        for pkginfo in query:
            pkg = pkginfo["relyName"]
            version = pkginfo["relyVersion"]
            find, tmp_name = False, ""
            for ret in __pipsearch(pkg, S.pypi_server):
                if ret["name"] == pkg:
                    tmp_name = ret["name"]
                    if version == ret["version"]:
                        pkginfo["isValid"] = "Y"
                        find = True
                        break
            if not find and tmp_name:
                pkginfo["isValid"] = _pip_match_version(tmp_name, S.pypi_server)
            elif not find and not tmp_name:
                pkginfo["isValid"] = "N"
        return RJson.make({"Packages": query, "BaseVer": basever})
    except Exception as x:
        logger.error("search failed: %s\n%s", x, traceback.format_exc())
        return RJson.make(None, 500, f"search failed {x}")


@router.post("/ImageConstruct")
async def image_construct(request: Request):
    jo = _read_body(request)["ROOT"]
    BaseImage, Name = jo["BaseImage"], jo["Name"]
    DataDir, CodeDir = jo.get("DataDir"), jo["CodeDir"]
    Mount, Packages = jo.get("Mount"), jo["Packages"]
    ModelId = jo["ModelId"]
    HandlerScript = jo.get("HandlerScript", "")

    basepath = os.path.join(S.image_path, "." + ModelId)
    if os.path.exists(basepath):
        shutil.rmtree(basepath)
    os.mkdir(basepath)
    entrypath = _pjoin(basepath, "entry")
    shutil.copytree(_pjoin(S.image_path, "entry"), entrypath)
    with open(_pjoin(entrypath, "model_conf.json"), "w") as fo:
        json.dump({"model_id": ModelId}, fo)

    BaseImage_file = os.path.join(S.image_path, BaseImage)
    pkgs_path = os.path.join(entrypath, "pkgs")
    os.mkdir(pkgs_path)
    requirement_path = os.path.join(pkgs_path, "requirements.txt")
    pack_path_copy = {}
    with open(requirement_path, "w+", encoding="utf-8") as fi:
        for pack in Packages:
            fi.write(pack["name"] + "\n")
            pack_path_copy[pack["path"]] = 1
        fi.write("flask\npsutil\nrequests\n")
        pack_path_copy["entry_pkg"] = 1
    for pp in pack_path_copy:
        src_path = os.path.join(BaseImage_file, pp)
        for fname in os.listdir(src_path):
            if not fname.startswith("."):
                shutil.copy(os.path.join(src_path, fname), os.path.join(pkgs_path, fname))

    if not CodeDir.startswith("/"):
        CodeDir = "/" + CodeDir
    if os.path.exists(CodeDir):
        shutil.copytree(CodeDir, _pjoin(basepath, "code"))
    else:
        os.makedirs(_pjoin(basepath, "code"), exist_ok=True)
        _exec(f"scp -r iasp75:{CodeDir}/* {basepath}/code/")
    if DataDir is not None:
        if not DataDir.startswith("/"):
            DataDir = "/" + DataDir
        if os.path.exists(DataDir):
            shutil.copytree(DataDir, _pjoin(basepath, "data"))
        else:
            os.makedirs(_pjoin(basepath, "data"), exist_ok=True)
            _exec(f"scp -r iasp75:{DataDir}/* {basepath}/data/")

    if "serviceCode" in jo:
        with open(f"{basepath}/code/main.py", "w", encoding="utf-8") as fo:
            fo.write(jo["serviceCode"])
        HandlerScript = "main.py" if HandlerScript == "" else HandlerScript + ",main.py"
    if "modelFilePath" in jo:
        for fpath in jo["modelFilePath"].split(","):
            fname = fpath[fpath.rfind("/") + 1:]
            S.fs.down_file(fpath, _pjoin(basepath, "data", fname))

    with open(os.path.join(BaseImage_file, "conf")) as fi:
        conf_dict = json.load(fi)
    Dockerfile = os.path.join(basepath, "Dockerfile")
    with open(Dockerfile, "w+", encoding="utf-8") as fi:
        fi.write(f"FROM {conf_dict['image_name']}\n")
        fi.write("COPY entry /entry\n")
        fi.write("RUN pip3 install --no-index --find-links /entry/pkgs "
                 "-r /entry/pkgs/requirements.txt && rm -rf /entry/pkgs\n")
        if DataDir is not None:
            fi.write("COPY data /data\n")
        fi.write("COPY code /code\n")
        fi.write(f"ENV MODEL_SERVICE {HandlerScript}\n")
        if Mount is not None:
            fi.write("VOLUME" + "".join(" " + d for d in Mount) + "\n")
        fi.write("CMD /bin/bash /entry/start.sh")
    subprocess.check_output(f"docker build -t {Name} .", cwd=basepath, shell=True)
    shutil.rmtree(basepath)
    return RJson.make({"return": "build success"})


@router.post("/ImageConstruct_pypi")
async def image_construct_pypi(request: Request):
    jo = _read_body(request)["ROOT"]
    BaseImage, Name = jo["BaseImage"], jo["Name"]
    DataDir, CodeDir = jo.get("DataDir"), jo["CodeDir"]
    Mount, Packages = jo.get("Mount"), jo["Packages"]
    ModelId = jo["ModelId"]
    HandlerScript = jo.get("HandlerScript", "")

    basepath = os.path.join(S.image_path, "." + ModelId)
    if os.path.exists(basepath):
        shutil.rmtree(basepath)
    os.mkdir(basepath)
    entrypath = _pjoin(basepath, "entry")
    shutil.copytree(_pjoin(S.image_path, "entry"), entrypath)
    with open(_pjoin(entrypath, "model_conf.json"), "w") as fo:
        json.dump({"model_id": ModelId}, fo)

    BaseImage_file = os.path.join(S.image_path, BaseImage)
    pkgs_path = os.path.join(entrypath, "pkgs")
    os.mkdir(pkgs_path)
    requirement_path = os.path.join(pkgs_path, "requirements.txt")
    with open(requirement_path, "w+", encoding="utf-8") as fi:
        for pack in Packages:
            fi.write(f"{pack['name']}=={pack['version']}\n")
        fi.write("flask\npsutil\nrequests\n")

    if not CodeDir.startswith("/"):
        CodeDir = "/" + CodeDir
    if os.path.exists(CodeDir):
        shutil.copytree(CodeDir, _pjoin(basepath, "code"))
    else:
        os.makedirs(_pjoin(basepath, "code"), exist_ok=True)
        _exec(f"scp -r iasp75:{CodeDir}/* {basepath}/code/")
    if DataDir is not None:
        if not DataDir.startswith("/"):
            DataDir = "/" + DataDir
        if os.path.exists(DataDir):
            shutil.copytree(DataDir, _pjoin(basepath, "data"))
        else:
            os.makedirs(_pjoin(basepath, "data"), exist_ok=True)
            _exec(f"scp -r iasp75:{DataDir}/* {basepath}/data/")

    if "serviceCode" in jo:
        with open(f"{basepath}/code/main.py", "w", encoding="utf-8") as fo:
            fo.write(jo["serviceCode"])
        HandlerScript = "main.py" if HandlerScript == "" else HandlerScript + ",main.py"
    if "modelFilePath" in jo:
        for fpath in jo["modelFilePath"].split(","):
            fname = fpath[fpath.rfind("/") + 1:]
            S.fs.down_file(fpath, _pjoin(basepath, "data", fname))

    with open(os.path.join(BaseImage_file, "conf")) as fi:
        conf_dict = json.load(fi)
    Dockerfile = os.path.join(basepath, "Dockerfile")
    with open(Dockerfile, "w+", encoding="utf-8") as fi:
        fi.write(f"FROM {conf_dict['image_name']}\n")
        fi.write("COPY entry /entry\n")
        fi.write(f"RUN pip3 install --index-url {S.pypi_server} "
                 f"--trusted-host {S.pypi_server[7:20]} "
                 f"-r /entry/pkgs/requirements.txt && rm -rf /entry/pkgs\n")
        if DataDir is not None:
            fi.write("COPY data /data\n")
        fi.write("COPY code /code\n")
        fi.write(f"ENV MODEL_SERVICE {HandlerScript}\n")
        if Mount is not None:
            fi.write("VOLUME" + "".join(" " + d for d in Mount) + "\n")
        fi.write("CMD /bin/bash /entry/start.sh")
    subprocess.check_output(f"docker build -t {Name} .", cwd=basepath, shell=True)
    shutil.rmtree(basepath)
    return RJson.make({"return": "build success"})


@router.post("/TargetSelect")
async def target_select(request: Request):
    deploy_targets = S.conf["deploy_targets"]
    weight_cpu, weight_mem, weight_disk = 0.1, 0.2, 0.7
    hosts_stat = []
    for h in deploy_targets:
        sout = subprocess.check_output(
            f"ssh {h['user']}@{h['host']} iostat -c && free -m && df -