# import logging as log
import json
import os
import threading
import shutil
import subprocess

_pjoin = os.path.join

from loguru import logger as log
from loguru import logger
import svc.util_db as db

from flask import Flask, request, Response, render_template, send_from_directory
from waitress import serve

app = Flask("pl-service", template_folder="./.app/templates")

_conf = dict(
    k8s_docker_repository="133.0.212.76:30012",  # k8s docker 仓库地址
    k8s_name_space="iasp",
    basepath_tmp="/tmp",
    docker_image_path="/home/iasp/aipaas/svc/packages",
    kubectl="/home/iasp/bin/kubectl",
    k8s_config_file="/home/iasp/.kube/config",
    fileserve_pod="manager-7fb5f5646f-gnxm6",
    pypi_server="http://133.0.212.75:31005",
)
conn_params = {"user": "iasp", "password": "Iasp!@#$1234", "host": "133.0.212.46", "port": 3306,
               "database": "iasp"}


def _rjson(obj, rcode=0, rmsg="success"):
    ret = {"ROOT": {"HEADER": {}, "RETURN_CODE": rcode, "RETURN_MSG": rmsg, "BODY": obj}}
    r = Response(json.dumps(ret), mimetype="application/json; charset=utf-8")
    return r
    # print(ret)


@app.route('/ping', methods=['GET'])
def ping1():
    return 'ok'


@app.route('/ModelPubImage_k8s_v119', methods=['POST'])
def ModelPubImage_k8s_v119():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info(f"...enter ModelPubImage_k8s_v119...{[jsons]}")
        jo = json.loads(jsons)['BODY']
        # jo = {"BODY":{"ImageName":"iris", "ImageTag":"v1", "LocalFile":[],"ModelFile":[],
        #               "JupyterFile":{"HandlerScripts":"python /entry/jupyterfile/detectdemo.py",
        #                              "filepath":["/data/iris.pickle","/data/detectdemo.py"],
        #                              "contaniername":"jupyter-1651134634301658310001"},
        #               "Packages":[{"Version":"1.20.2","id":"numpy@1.20.2","Name":"numpy"},
        #                           {"Version":"1.3.4","id":"pandas@1.3.4","Name":"pandas"},
        #                           {"Version":"0.11.2","id":"seaborn@0.11.2","Name":"seaborn"},
        #                           {"Version":"1.0.2","id":"scikit_learn@1.0.2","Name":"scikit_learn"},
        #                           {"Version":"2.0.1","id":"Flask@2.0.1","Name":"Flask"}],"Envs":[],
        #               "ImageId":"1651212726193_iasp788710001",
        #               "BaseImage": "taskrun:v2.1"}}
        # jo = jo['BODY']
        ImageId = jo['ImageId']
        ImageName = jo['ImageName']
        ImageTag = jo['ImageTag']
        HandlerScripts = jo.get('HandlerScripts', None)
        BaseImage = jo.get('BaseImage', "mconda37:v2")
        ModelFile = jo.get('ModelFile', [])
        LocalFile = jo.get('LocalFile', [])
        JupyterFile = jo.get('JupyterFile', {})
        Packages = jo.get('Packages', [])
        Envs = jo.get('Envs', [])
        Ports = jo.get("Ports", "")
    except Exception as x:
        # DONE 更新镜像发布状态,镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(ImageId, "05")
        return _rjson(None, 402, "Invalid request [%s]" % str(x))
    # 建立临时目录
    basepath = os.path.join(_conf['basepath_tmp'], "." + ImageId)
    if not os.path.exists(basepath):
        os.makedirs(basepath, exist_ok=True)
    logger.info("basepath: {}", basepath)

    th = threading.Thread(target=_build_push_image, daemon=True, args=
    (ImageId, BaseImage, HandlerScripts, ModelFile, LocalFile, JupyterFile, Packages, Envs, Ports, ImageName, ImageTag
     , basepath))
    th.start()
    return _rjson({"return": "building images"})
    # _build_push_image(ImageId ,BaseImage ,HandlerScripts ,ModelFile ,LocalFile ,JupyterFile ,Packages ,
    #                   Envs ,Ports ,ImageName ,ImageTag, basepath)


# 将docker build和docker push操作放在线程中
def _build_push_image(imageId, BaseImage, HandlerScripts, ModelFile, LocalFile, JupyterFile, Packages, Envs, Ports
                      , imageName, imageTag, basepath):
    k8s_name_space = _conf['k8s_name_space']
    k8s_docker_repository = _conf['k8s_docker_repository']

    log.info("...enter _build_push_image...")
    entrypath = _pjoin(basepath, "entry")
    sftpgopath = _pjoin(entrypath, "sftpgo")
    ng_homepath = _pjoin(entrypath, "ng_home")
    node_exporterpath = _pjoin(entrypath, "node_exporter")
    entrypath_source = _pjoin(_conf['docker_image_path'], "entry_temp")
    log.info("target_path: [{}]", entrypath)
    try:
        if os.path.exists(basepath):
            shutil.rmtree(basepath)
            os.makedirs(basepath, exist_ok=True)
        shutil.copytree(_pjoin(entrypath_source, "dockerSub_modelMain"), entrypath)
        shutil.copytree(_pjoin(entrypath_source, "sftpgo"), sftpgopath)
        shutil.copytree(_pjoin(entrypath_source, "ng_home"), ng_homepath)
        shutil.copytree(_pjoin(entrypath_source, "node_exporter"), node_exporterpath)
    except Exception as ex:
        # DONE 更新镜像发布状态,镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(imageId, "05")
        return _rjson(None, 503, f"{ex}")
    pkgs_path = os.path.join(entrypath, "pkgs")
    try:
        os.makedirs(pkgs_path, exist_ok=True)
    except Exception as ex:
        # DONE 更新镜像发布状态,镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(imageId, "05")
        return _rjson(None, 504, f"{ex}")
    # copy ModelFile、LocalFile、JupyterFile至本地
    if ModelFile is not None and len(ModelFile) > 0:
        modelfile_path = os.path.join(entrypath, "modelfile")
        try:
            os.makedirs(modelfile_path, exist_ok=True)
        except Exception as ex:
            # DONE 更新镜像发布状态,镜像生成失败05:创建失败
            db.update_devImage_onlyStatus(imageId, "05")
            return _rjson(None, 505, f"{ex}")
    if LocalFile is not None and len(LocalFile) > 0:
        LocalFile_path = os.path.join(entrypath, "localfile")
        try:
            os.makedirs(LocalFile_path, exist_ok=True)
        except Exception as ex:
            # DONE 更新镜像发布状态,镜像生成失败05:创建失败
            db.update_devImage_onlyStatus(imageId, "05")
            return _rjson(None, 506, f"{ex}")
    if JupyterFile is not None and len(JupyterFile) > 0:
        jupyterfile_path = os.path.join(entrypath, "jupyterfile")
        try:
            os.makedirs(jupyterfile_path, exist_ok=True)
        except Exception as ex:
            # DONE 更新镜像发布状态,镜像生成失败05:创建失败
            db.update_devImage_onlyStatus(imageId, "05")
            return _rjson(None, 507, f"{ex}")
    if ModelFile is not None and len(ModelFile) > 0:
        for mfile in ModelFile:
            if os.path.isdir(mfile):
                cmd_copy_mfile = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {_conf['fileserve_pod']}:{mfile} {modelfile_path}"  # 将容器内的文件及文件夹copy至/tmp/modelfile目录下
            else:
                file_name = os.path.basename(mfile)
                cmd_copy_mfile = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {_conf['fileserve_pod']}:{mfile} {modelfile_path}/{file_name}"  # 将容器内的文件及文件夹copy至/tmp/modelfile目录下
            proc = subprocess.Popen(cmd_copy_mfile, shell=True)
            try:
                rcode = proc.wait()
                log.info(f"{cmd_copy_mfile} success")
            except Exception as x:
                errinfo = f"{str(x)}"
                log.error(f"cmd_copy_mfile fail: {cmd_copy_mfile} fail Reason", errinfo)
    if LocalFile is not None and len(LocalFile) > 0:
        for lfile in LocalFile:
            if os.path.isdir(lfile):
                cmd_copy_lfile = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {_conf['fileserve_pod']}:{lfile} {LocalFile_path}"  # 将容器内的文件及文件夹copy至/tmp/modelfile目录下
            else:
                file_name = os.path.basename(lfile)
                cmd_copy_lfile = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {_conf['fileserve_pod']}:{lfile} {LocalFile_path}/{file_name}"  # 将容器内的文件及文件夹copy至/tmp/modelfile目录下
            roc = subprocess.Popen(cmd_copy_lfile, shell=True)
            try:
                rcode = proc.wait()
                log.info(f"{cmd_copy_lfile} success")
            except Exception as x:
                errinfo = f"{str(x)}"
                log.error(f"cmd_copy_lfile fail: {cmd_copy_lfile} fail Reason", errinfo)
    # 根据jupyter容器名称，获取pod名称
    jcontaniername = JupyterFile.get("contaniername", "")
    container_info = db.devc_get_v119(jcontaniername)
    if container_info is not None:
        jpod = container_info.get("pod_name", None)
        for jfile in JupyterFile.get("filepath", []):
            if os.path.isdir(jfile):
                cmd_copy_jupyterfile = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {jpod}:{jfile} {jupyterfile_path}"  # 将容器内的文件及文件夹copy至/tmp/modelfile目录下
            else:
                file_name = os.path.basename(jfile)
                cmd_copy_jupyterfile = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {jpod}:{jfile} {jupyterfile_path}/{file_name}"  # 将容器内的文件及文件夹copy至/tmp/modelfile目录下
            proc = subprocess.Popen(cmd_copy_jupyterfile, shell=True)
            try:
                rcode = proc.wait()
                log.info(f"{cmd_copy_jupyterfile} success")
            except Exception as x:
                errinfo = f"{str(x)}"
                log.error(f"cmd_copy_jupyterfile fail: {cmd_copy_jupyterfile} fail Reason", errinfo)
        cmd1 = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {jpod}:.local {basepath}/data/.local"
        proc = subprocess.Popen(cmd1, shell=True)
        try:
            rcode = proc.wait()
            log.info(f"{cmd1} success")
        except Exception as x:
            errinfo = f"{str(x)}"
            log.error(f"cmd1 fail: {cmd1} fail Reason", errinfo)
        cmd2 = f"{_conf['kubectl']} --kubeconfig={_conf['k8s_config_file']} cp {jpod}:.local {basepath}/root/.local"
        proc = subprocess.Popen(cmd2, shell=True)
        try:
            rcode = proc.wait()
            log.info(f"{cmd2} success")
        except Exception as x:
            errinfo = f"{str(x)}"
            log.error(f"cmd2 fail: {cmd2} fail Reason", errinfo)
    # 更新镜像生成进度，dev_progress表-》FINISH_PERCENT字段
    # log_id="LOG"+_gen_rand_str(11)
    # try:
    #     _insert_dev_progress(log_id,create_id,create_id,obj_id,async_task_type)
    #     log.info("_insert_database")
    # except Exception as x:
    #     log.error(x)

    jHandlerScripts = JupyterFile.get("HandlerScripts", None)
    # 将jupyter容器内/data/.local目录copy至
    if Ports is not None:
        Ports = Ports.replace(",", " ")  # EXPOSE 12 34
    Dockerfile = os.path.join(basepath, "Dockerfile")
    log.info("--writing Dockerfile: %s" % Dockerfile)
    with open(Dockerfile, "w", encoding='utf-8') as fi:
        requirement_temp_path = os.path.join(pkgs_path, "requirements_temp.txt")
        requirement_path = os.path.join(pkgs_path, "requirements.txt")
        with open(requirement_temp_path, "w+", encoding='utf-8') as fii:  # 生成requirements.txt
            for pack in Packages:
                fii.write(pack["Name"])
                if pack["Version"] is not None and pack["Version"] != "":
                    fii.write("==")
                    fii.write(pack["Version"])
                fii.write("\n")

            fii.write("flask " + "\n")
            fii.write("psutil==5.7.0 " + "\n")
            fii.write("django==1.11" + "\n")
        lines_pkg = set()
        outfiile = open(requirement_path, 'w+', encoding='utf-8')
        f = open(requirement_temp_path, 'r', encoding='utf-8')
        for line in f:
            line_split = line.split("==")
            if line_split[0].strip().lower() not in lines_pkg:
                outfiile.write(line)
                lines_pkg.add(line_split[0].strip().lower())
        outfiile.close()
        f.close()
        fi.write("FROM " + BaseImage + '\n')
        fi.write("USER root\n")
        fi.write("COPY entry /entry" + "\n")
        fi.write("COPY data/.local /data/.local" + "\n")
        fi.write("COPY root/.local /root/.local" + "\n")
        # 修改扩展包安装方式为pypi私服
        pypi_server = _conf['pypi_server']  # for  :  http://132.91.147.152:31377
        fi.write(
            f"RUN pip install --index-url {pypi_server} --trusted-host {pypi_server[7:pypi_server.rfind(':')]} -r /entry/pkgs/requirements.txt" + '\n')
        # Done Ports、8022、8081端口均需要Expose暴漏出来
        fi.write("EXPOSE 8022 8081 " + Ports + "\n")
        # Done ENV设置HandlerScripts放到环境变量 MODEL_SERVICE
        if HandlerScripts is not None and HandlerScripts != "":
            fi.write("ENV MODEL_SERVICE=%s\n" % HandlerScripts)
        if Envs is not None and Envs:
            for env in Envs:
                env_temp = env.strip()
                if env_temp.find("=") != -1:
                    env_temp = env_temp.split("=")
                    fi.write(f"ENV {env_temp[0]}={env_temp[1]}" + "\n")
        if jHandlerScripts is None:
            fi.write("ENTRYPOINT python /entry/model_main.py")
        else:
            fi.write(f"ENTRYPOINT python /entry/model_main.py & {jHandlerScripts}")
        # fi.write("ENTRYPOINT [\"python\",\"/entry/model_main.py\"]"+"\n")
        log.info(f"Dockerfile------{Dockerfile}")

    subprocess.check_output("chmod -R 755 entry data/.local root/.local", shell=True, cwd=basepath)
    cmd = f"docker build  -q -t {imageName}:{imageTag} ."
    log.info("..........docker build begin....... [{}]", cmd)
    try:
        sout = subprocess.Popen(cmd, cwd=basepath, shell=True, stdout
        =subprocess.PIPE, stderr=subprocess.PIPE)
        exit_code = sout.wait()
        stderr = sout.stderr.read()
        stdout = sout.stdout.read()
        if exit_code != 0:
            log.error("docker build failed {}", stderr)
        else:
            log.info("..........docker build success.......[{}]", stdout)
    except Exception as ex:
        log.info(f"docker build fail,reason:---{ex}")
        # DONE 更新镜像发布状态,镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(imageId, "05")
        return
    log.info("..........docker tag begin.......")
    try:
        subprocess.check_output(
            f"docker tag {imageName}:{imageTag} {k8s_docker_repository}/{k8s_name_space}/{imageName}:{imageTag}", cwd=
            basepath, shell=True)
        log.info("..........docker tag success.......")
    except Exception as ex:
        log.info(f"docker tag fail,reason:---{ex}")
        # DONE 更新镜像发布状态,镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(imageId, "05")
        return
    log.info("..........docker first push begin.......")
    try:
        proc = subprocess.Popen(f"docker push {k8s_docker_repository}/{k8s_name_space}/{imageName}:{imageTag}",
                                shell=True)
        rcode = proc.wait()
        log.info("..........docker first push success.......")
    except Exception as ex:
        log.info(f"docker first push fail,reason:---{ex}")
    log.info("...............docker second push..........")
    try:
        proc = subprocess.Popen(f"docker push {k8s_docker_repository}/{k8s_name_space}/{imageName}:{imageTag}",
                                shell=True)
        rcode = proc.wait()
        log.info("...............docker second push success..........")
    except Exception as ex:
        log.error(f"docker second push fail,Reason: {ex}")
        # DONE 更新镜像发布状态,镜像生成失败05:创建失败
        db.update_devImage_onlyStatus(imageId, "05")
    # 镜像生成成功
    log.info(f"update table dev_image IMAGE_ID={imageId},IMAGE_STATUS=02,IMAGE_NAME={imageName},TAG={imageTag}")
    db.update_devImage_status(imageId, "02", k8s_name_space, imageName, imageTag)
    # 更新镜像信息(image_size)
    cmd = f"docker images|grep {imageName}"
    out = ""
    try:
        out = subprocess.check_output(cmd, shell=True)
    except Exception as ex:
        log.error(f"ex: {ex}")
    str_out = out.decode(encoding="utf-8")
    image_size = None
    if str_out is not None and str_out != " ":
        image_size = list(str_out.split(" "))[-1]
        log.info(f"image_size: {image_size}")
        # Done 更新镜像信息，
        imageInfo = json.dumps({"image_size": image_size})
        db.update_devImage_imageInfo(imageId, imageInfo)
    # 删除临时文件 basepath
    if os.path.exists(basepath):
        try:
            shutil.rmtree(basepath)
            log.info(f"rm {basepath} success")
        except Exception as ex:
            log.info(f"rm {basepath} fail,Reason {ex}")
            return _rjson(None, 509, f"{basepath} rm fail")
    cmd_del_images = f"docker rmi -f {imageName}:{imageTag}"
    cmd_del_images_ = f"docker rmi -f {k8s_docker_repository}/{k8s_name_space}/{imageName}:{imageTag}"
    try:
        subprocess.check_output(cmd_del_images, shell=True)
        log.info(f".................rmi {cmd_del_images} success..........")
    except Exception as ex:
        log.error(f"rmi {cmd_del_images} fail,Reason: {ex}")
    try:
        subprocess.check_output(cmd_del_images_, shell=True)
        log.info(f".................rmi {cmd_del_images_} success..........")
        ret = db.update_devImage_onlyStatus(imageId, "04")
        log.info("---update_devImage_onlyStatus {} -> 04 return {}", imageId, ret)
    except Exception as ex:
        log.error(f"rmi {cmd_del_images} fail,Reason: {ex}")
    log.info("done!!!")


# @app.route('/pl/PipelineSectionRun', methods=['GET', 'POST'])
# def viewlog():
#     return send_from_directory("/mci/gyf/proc/ss", "sshserv.json")


def main():
    # conn_params = {"db_type": "mysql", "user": "iasp", "password": "Iasp!@#$1234", "host": "133.0.212.46", "port": 3306,
    #                "database": "iasp"}
    # db.pool_init(conn_params, max_conn=2)

    import mysql.connector as my
    def db_get_conn():
        return my.connect(**conn_params)

    _conf['db_get_conn'] = db_get_conn
    db._init_(_conf)

    # ModelPubImage_k8s_v119()
    serve(app, host="0.0.0.0", port=8711)
