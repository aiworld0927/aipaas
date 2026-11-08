#coding=utf-8

# 提供生成模型晕形态相关的服务
# 接口文档:   Y11667-智能算法开放运营服务平台_全行业 V1.0.0_CRM_MCI@概要设计说明书.doc  4.3.2


import os
import logging as log
import traceback

from loguru import logger
import json
import subprocess
import threading
import time
import shutil
import uuid

import requests
import operator
from flask import Flask, request, Response, abort

from svc.util.fsutil import FSUtil
from svc.util.fsutil import FSServ
from svc.util.sshutil import hosts_sort, get_port, remote_exec
from svc.util.sshutil import RemoteClient
import svc.util_db as db
import copy

_pjoin = os.path.join


deploy_query_obj = {}

def registerHandler(app, conf):
    global image_path, rest_register_url, rest_port, local_ip
    global dev_images, dev_containers, container_list_file, deploy_status_file
    global local_ip
    global fs
    global _conf
    global pypi_server
    global k8s_docker_repository,k8s_name_space
    global fileserv_path
    global basepath_tmp
    _conf = conf

    fs = FSUtil("")

    image_path = conf['docker_image_path'] # 配置保存镜像信息及扩展包的目录
    # deploy_targets = conf['deploy_targets']
    local_ip = conf['local_ip']
    # rest_register_url = conf['rest_register_url']
    # rest_port = conf['rest_port']
    local_ip = conf['local_ip']
    pypi_server = conf['pypi_server']
    k8s_name_space = conf['k8s_name_space']
    k8s_docker_repository = conf['k8s_docker_repository']
    fileserv_path = conf['fileserv_path']
    basepath_tmp = conf['basepath_tmp']

    app.add_url_rule("/ImageList", "ImageList", ImageList, methods=["POST", "GET"])
    app.add_url_rule("/PackageSearch", "PackageSearch", PackageSearch, methods=["POST", "GET"])
    app.add_url_rule("/PackageSearchNew", "PackageSearchNew", PackageSearchNew, methods=["POST", "GET"])
    app.add_url_rule("/ImageConstruct", "ImageConstruct", ImageConstruct, methods=["POST", "GET"])
    app.add_url_rule("/ImageConstruct_pypi", "ImageConstruct_pypi", ImageConstruct_pypi, methods=["POST", "GET"])
    app.add_url_rule("/TargetSelect", "TargetSelect", TargetSelect, methods=["POST", "GET"])
    app.add_url_rule("/ImageDeploy", "ImageDeploy", ImageDeploy, methods=["POST", "GET"])
    app.add_url_rule("/ImageDeployStatus", "ImageDeployStatus", ImageDeployStatus, methods=["POST", "GET"])
    app.add_url_rule("/ImageUndeploy", "ImageUndeploy", ImageUndeploy, methods=["POST", "GET"])
    app.add_url_rule("/DevContainer", "DevContainer", DevContainer, methods=["POST", "GET"])
    app.add_url_rule("/DevImages", "DevImages", DevImages, methods=["POST", "GET"])

    app.add_url_rule("/ModelPub", "ModelPub", ModelPub, methods=["POST",])
    app.add_url_rule("/ModelQuery", "ModelQuery", ModelQuery, methods=["POST",])
    app.add_url_rule("/ModelDel", "ModelDel", ModelDel, methods=["POST",])

    app.add_url_rule("/DevContainerGet", "DevContainerGet", DevContainerGet, methods=["POST",])
    app.add_url_rule("/DevContainerQry", "DevContainerQry", DevContainerQry, methods=["POST",])
    app.add_url_rule("/DevContainerDel", "DevContainerDel", DevContainerDel, methods=["POST",])

    app.add_url_rule("/ModelPubImage_k8s", "ModelPubImage_k8s", ModelPubImage_k8s, methods=["POST",])
    app.add_url_rule("/ModelPubQuery", "ModelPubQuery", ModelPubQuery, methods=["POST",])

    container_list_file = _pjoin(os.getcwd(), ".store", "container.list") # 保存已经发布的模型服务容器列表
    deploy_status_file = _pjoin(os.getcwd(), ".store", "deploystatus.json") # 保存发布状态的过程数据
    dev_containers = _pjoin(os.getcwd(), ".store", "dev-containers.json") # 保存已经分配的开发者容器 列表
    dev_images = _pjoin(os.getcwd(), ".store", "dev-images.json") # 保存 可用的开发环境镜像, name: desc

    log.info("--deploysvc registed!!")


'''
~/dockers/devbase/create_dev_container.sh
----
user="$1"
token="$2"
image="$3"
port="$4"

base=/mci/dockers/devbase/$user/.jupyter
mkdir -p $base

if [ "F$port" = "F" ]; then
port=`python $base/../../get_port.py`
echo "-port $port"
fi

echo "c.NotebookApp.token = \"$token\"" >$base/jupyter_notebook_config.py
echo "HOME=/data jupyter notebook --port=$port --ip=\$(hostname -i) --no-browser --notebook-dir=/data" >$base/start.sh

echo "
if docker ps |grep -w userdev-$user; then
echo "alive"
else
if docker ps -a|grep -w userdev-$user; then
docker start userdev-$user
else
docker run -d --name userdev-$user -p $port:$port --user \$(id -u):\$(id -g) --workdir /data -v /mci/dockers/devbase/passwd:/etc/passwd:ro -v /mci/dockers/devbase/group:/etc/group:ro -v /mci/dockers/devbase/$user:/data $image /bin/bash /data/.jupyter/start.sh
fi
fi
" > $base/../../start-$user.sh


.store/dev-images.json
----
{
"pydev-base:1.0":{"id":"1","desc":"基本python开发环境,已安装扩展包 flask pillow opencv-python nltk scikit-learn pipdeptree jupyter seaborn"},
"pydev-tf:2.0":{"id":"2","desc":"在基础开发镜像的基础上,安装了tensorflow-2.0 keras-2.3.1"},
"pydev-torch:1.3":{"id":"3","desc":"在基础开发镜像的基础上,安装了pytorch-1.3.0 torchvision"}
}

'''

def __gen_rand_str(nc=6):
    if nc == 0:
        min_char, max_char = 24, 32
    else:
        min_char, max_char = nc, nc
    allchar = string.ascii_letters +  string.digits # string.punctuation +
    token = "".join(choice(allchar) for x in range(randint(min_char, max_char)))
    return token

def DevImages():
    '查询可用的开发者镜像列表'
    with open(dev_images, "r") as fi:
        imgs = json.load(fi)
    return _rjson({"Images": [{"name": k, "desc": v['desc']} for k, v in imgs.items()]})

def DevContainer():
    '''分配开发态容器'''
    deploy_targets = _conf['deploy_targets']
    if os.path.exists(dev_containers):
        with open(dev_containers, "r") as fi:
            cons = json.load(fi)
    else:
        cons = {}
    jsons = request.stream.read().decode('utf-8')
    log.info("...enter DevContainer...[%s]" % jsons)
    jo = json.loads(jsons)['ROOT']
    user = jo['user']  # 基础镜像目录名称
    imageName = jo.get("imageName", None)
    if imageName is None:
        return _rjson({}, rcode=1004, rmsg="Expected parameter imageName not found")

    with open(dev_images, "r") as fi:
        imgs = json.load(fi)
    img = imgs.get(imageName, None)
    if img is None:
        return _rjson({}, rcode=1005, rmsg="ImageName is invalid")
    imgid = img['id']
    user = "%s-%s"% (user, imgid)

    # 0. 检查是否已经存在
    con = cons.get(user, None)
    if con is not None:
        host = con['host']
        port = con['port']
        token = jo.get("token", "123")
        token = con['token']  #TODO token 是每次都重新生成的, 所以这里有问题, 需求去修改配置文件
        out, err = _exec("ssh %s /bin/bash dockers/devbase/start-%s.sh %s" % (host, user, token))
        log.info("start dev-con for %s return %s" % (user, out))
        return _rjson({"url": "http://%s:%s/?token=%s" %(host, port, token)})
    else:
        # 1. 选择主机,  规则是按数量来平均分配
        hosts = { h['host'] : 0 for h in deploy_targets }
        for v in cons.values():
            hosts[v['host']] += 1
        h_c = sorted([ [k, v] for k, v in hosts.items()], key=operator.itemgetter(1))
        host = h_c[0][0]
        # 2. 准备脚本
        token = jo.get("token", "123")
        out, err = _exec("ssh %s /bin/bash dockers/devbase/create_user_container.sh %s %s %s" % (host, user, token, imageName))
        log.info("host selected: %s, create return [%s], err[%s]" % (host, out, err))
        port = [i.split()[1].strip() for i in out.split("\n") if i.find("-port") >=0 ] [0]
        cons[user] = {"host":host, "port":port, "token":token, "image":imageName}
        with open(dev_containers, "w") as fo:
            json.dump(cons, fo, indent=2)
        out, err = _exec("ssh %s /bin/bash dockers/devbase/start-%s.sh" % (host, user))
        log.info("start dev-con for %s return %s" % (user, out))
        return _rjson({"url": "http://%s:%s/?token=%s" %(host, port, token), "containerid":out.strip()})


# # 镜像发布结果保存到数据库
# def update_database(MODEL_ID,MODEL_STATUS,ImageId,Role):
#     import mysql.connector
#     mysql_conf = dict(user=images_to_database_user, password=images_to_database_password,
#                               host=images_to_database_host, port=images_to_database_port,
#                               database=images_to_database_database)
#     cnx = mysql.connector.connect(**mysql_conf)
#     try:
#         # Done更新至数据中哪个表
#         sql = '''update mm_model_version set OP_TIME=now(), MODEL_STATUS=%s, OP_NOTE=%s where MODEL_PK_ID=%s'''
#         sql1 = '''update mm_model_image set OP_TIME=now(), PHY_IMAGE_ID=%s where MODEL_PK_ID=%s and IMAGE_ID=%s'''
#         cur = cnx.cursor()
#         cur.execute(sql, (MODEL_STATUS, '完成', MODEL_ID))
#         cur.execute(sql1,(ImageId, MODEL_ID, Role))
#         cnx.commit()
#     finally:
#         cnx.close()
		
# 模型镜像发布接口
def ModelPubImage_k8s():
    global image_path
    jsons = request.stream.read().decode('utf-8')
    log.info("...enter ModelPubImage_k8s...[%s]" % jsons)
    jo = json.loads(jsons).get('BODY', {})
    User = jo['User']
    ModelId = jo.get('ModelPkId',None)
    Nodes = jo['Nodes']
    if ModelId is None or ModelId == "":
        try:
            MODEL_STATUS = "04"
            # update_database(ModelId,MODEL_STATUS)
            db.update_model_image(ModelId, MODEL_STATUS, None, None)
            log.info(f"update_database success--{MODEL_STATUS}")
        except Exception as ex:
            log.info(f"update_database---{ex}") 
        return _rjson(None, 508, "modelpkid is null")
    ModelPkId = ModelId
    ModelId = ModelId.lower()
#    store_fpath = "/mci/work/congpq/k8s_images_%s.json" % ModelId
    store_fpath = ".store/k8s_images_%s.json" % ModelId
    # 判断镜像是否已存在
    if os.path.exists(store_fpath):
        log.info(f"model {ModelId} is already exists")
        return _rjson(None, 507, "model id %s already exists" % ModelId)  
    # 建立临时目录
    
    basepath = os.path.join(basepath_tmp, "."+ModelId)
    if os.path.exists(basepath):
        try:
            shutil.rmtree(basepath)
        except Exception as ex:
            log.info(f"shutil.rmtree fail---{basepath}")
            return _rjson(None, 509, f"{basepath} rm fail")

    os.mkdir(basepath)
    entrypath = _pjoin(basepath, "entry")
    sftpgopath = _pjoin(entrypath,"sftpgo")
    ng_homepath = _pjoin(entrypath,"ng_home")
    node_exporterpath = _pjoin(entrypath,"node_exporter")

    entrypath_source = _pjoin(image_path, "entry")
    try:
        shutil.copytree(_pjoin(entrypath_source, "dockerSub_modelMain"), entrypath)
        shutil.copytree(_pjoin(entrypath_source, "sftpgo"), sftpgopath)
        shutil.copytree(_pjoin(entrypath_source, "ng_home"), ng_homepath)
        shutil.copytree(_pjoin(entrypath_source, "node_exporter"), node_exporterpath)
    except Exception as ex:
        return _rjson(None, 503, f"{ex}")
    pkgs_path = os.path.join(entrypath, "pkgs")
    try:
        os.mkdir(pkgs_path)
    except Exception as ex:
        return _rjson(None, 504, f"{ex}")
    pack_path_copy = {}
    Dockerfile = os.path.join(basepath, "Dockerfile")
    log.info("--writing Dockerfile: %s" % Dockerfile)
    import time
    imagesNameTemp = []
    # 构建Dockerfile文件    
    with open(Dockerfile,"w+",encoding='utf-8') as fi:
        for Node in Nodes:
            Role = Node.get("Role",None)
            if Role is None:
                return _rjson({}, rcode=405, rmsg="Role not found")
            BaseImage = Node.get("BaseImage",None)  #基础镜像目录名称
            if BaseImage is None:
                return _rjson({}, rcode=406, rmsg="BaseImage not found") 
            Packages = Node.get('Packages',None)
            DataDir = Node.get("DataDir",None)
            CodeDir = Node.get("CodeDir",None)
            Ports = Node.get("Ports",None)
            if Ports is not None:
                Ports = Ports.replace(","," ") # EXPOSE 12 34
            HandlerScripts = Node.get('HandlerScripts',None)
            # 从本地拉取基础镜像，因此注释掉该调语句
#            imageName = f"{k8s_docker_repository}/{k8s_name_space}/{BaseImage}"
            # Todo 添加Mkdirs、Envs、Vols(可选的)
            Mkdirs = Node.get('Mkdirs',None)
            Envs = Node.get('Envs',None)
            Vols = Node.get('Vols',None)
#            if CodeDir is not None and CodeDir != "":
#                if os.path.exists(CodeDir):
#                    shutil.copytree(CodeDir, _pjoin(basepath, "code_"+Role))
#            if DataDir is not None and DataDir != "":
#                if os.path.exists(DataDir):
#                    shutil.copytree(DataDir, _pjoin(basepath, "data_"+Role))
            if CodeDir is not None and CodeDir != "":
                if not CodeDir.startswith('/'): 
                    CodeDir = os.path.join(fileserv_path, CodeDir)
                if os.path.exists(CodeDir):   
                    subprocess.check_output(f"chmod -R +x {CodeDir}/*",shell=True)
                    shutil.copytree(CodeDir, _pjoin(basepath, "code_"+Role))
            if DataDir is not None and DataDir != "":
                if not DataDir.startswith('/'):
                    DataDir = os.path.join(fileserv_path, DataDir)
                if os.path.exists(DataDir):
                    shutil.copytree(DataDir, _pjoin(basepath, "data_"+Role))
            # 检查{modelpkid}/1/ROOT目录是否存在
            ROOTDir = os.path.join(fileserv_path + "/" + ModelPkId + "/1", "ROOT")
            if os.path.exists(ROOTDir):
                shutil.copytree(ROOTDir, _pjoin(basepath, "ROOT_" + Role))
            pkgs_path = os.path.join(entrypath, Role)
            if not os.path.exists(pkgs_path):
                os.mkdir(pkgs_path)
            requirement_temp_path = os.path.join(pkgs_path, "requirements_temp.txt")
            requirement_path = os.path.join(pkgs_path, "requirements.txt")
            with open(requirement_temp_path,"w+",encoding='utf-8') as fii:     # 生成requirements.txt
                for pack in Packages:
                    fii.write(pack["name"])
                    if pack["version"] is not None and pack["version"] != "":
                        fii.write("==")
                        fii.write(pack["version"])
                        fii.write("\n")
                fii.write("silab"+"\n")
                fii.write("flask"+"\n")
                fii.write("psutil"+"\n")
                fii.write("django==1.11" + "\n")
            lines_pkg=set()
            outfiile=open(requirement_path,'w+',encoding='utf-8')
            f=open(requirement_temp_path,'r',encoding='utf-8')
            for line in f:    
                line_split = line.split("==")
                if line_split[0].strip().lower() not in lines_pkg:        
                    outfiile.write(line)        
                    lines_pkg.add(line_split[0].strip().lower())
            outfiile.close()
            f.close()
            # 镜像名称： modelid加上随机数
            name = __gen_rand_str().lower()
            imagesNameTemp.append(name)
            fi.write("FROM " + BaseImage + " AS "+ModelId+"_"+name+'\n')
            fi.write("COPY entry /entry"+"\n")
            # 修改扩展包安装方式为pypi私服
            fi.write(f"RUN pip install --index-url {pypi_server} --trusted-host {pypi_server[7:20]} -r /entry/{Role}/requirements.txt && rm -rf /entry/{Role} && rm -rf /root/.cache/"+'\n')
#            fi.write(f"RUN pip3 install --index-url {pypi_server} --trusted-host {pypi_server[7:20]} -r /entry/pkgs/requirements.txt && rm -rf /entry/pkgs"+'\n')
            if DataDir is not None and DataDir != "" and os.path.exists(DataDir):
                fi.write("COPY "+"data_"+Role+"  /data" + '\n')
            if CodeDir is not None and CodeDir != "" and os.path.exists(CodeDir):
                fi.write("COPY "+"code_"+Role+" /code" + '\n')
            if os.path.exists(_pjoin(basepath, "ROOT_" + Role)):
                for path in os.listdir(_pjoin(basepath, "ROOT_" + Role)):
                    if path.endswith(".tgz"):
                        fi.write(f"ADD ROOT_{Role}/{path}" + " /" + '\n')
                    if path.endswith(".tar.gz"):
                        fi.write(f"ADD ROOT_{Role}/{path}" + " /" + '\n')
            # Done 添加Mkdirs、Envs、Vols(可选的)
            Vols_temp = []
            if Vols is not None and Vols:
                fi.write("VOLUME")
                for vol in Vols:
                    fi.write(" "+vol.strip())
                fi.write('\n')
            #Done Ports、8022、8081端口均需要Expose暴漏出来
            fi.write("EXPOSE 8022 8081 "+Ports+"\n")
	    #Done ENV设置HandlerScripts放到环境变量 MODEL_SERVICE
            if HandlerScripts is not None and HandlerScripts != "":
                fi.write("ENV MODEL_SERVICE=%s\n" % HandlerScripts)
            if Envs is not None  and Envs:
                for env in Envs:
                    env_temp = env.strip()
                    if env_temp.find("=") != -1:
                        env_temp = env_temp.split("=")
                        fi.write(f"ENV {env_temp[0]}={env_temp[1]}"+"\n")
            if Mkdirs is not None and Mkdirs:
                fi.write("RUN mkdir -p "+Mkdirs[0])
                for mkdir in Mkdirs:
                    fi.write(" && mkdir -p "+mkdir.strip())
                fi.write("\n")
#            fi.write("CMD /bin/bash"+"\n")
            fi.write("ENTRYPOINT [\"python\",\"/entry/model_main.py\"]"+"\n")
    log.info(f"Dockerfile------{Dockerfile}")
    # 启动容器build_push_image线程,创建镜像，并push到k8s镜像仓库
    th = threading.Thread(target=build_push_image, daemon=True,args=(Nodes,ModelId,basepath,store_fpath, imagesNameTemp))
    th.start()
    return _rjson({"return": "building images"})

	# 将docker build和docker push操作放在线程中
def build_push_image(Nodes,ModelId,basepath,store_fpath,imagesNameTemp):
    Images = []
    image_info = {}
    MODEL_STATUS = ""
    name = __gen_rand_str().lower()    
    for node in Nodes:
        index = Nodes.index(node)
        name = imagesNameTemp[index]
        temp = {}
        Role = node['Role']
        imageName = ModelId+"_"+name
        log.info("..........docker build.......")
        log.info(f"imagesName---{imageName}")
        try:
            sout = subprocess.Popen("docker build  -q -t "+imageName+" --target "+imageName+" .", cwd=basepath, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as ex:
            log.info(f"docker build fail,reason:---{ex}") 
#        log.info(sout.stdout.read().decode(encoding="utf-8"))
#        sout = subprocess.check_output("docker build -q -t "+imageName+" --target "+imageName+" .", cwd=basepath, shell=True)
#        ImageId = str(sout, encoding='utf-8')[7:-1]
#        subprocess.check_output(f"docker tag {imageName} {k8s_docker_repository}/{k8s_name_space}/{imageName}", cwd=basepath, shell=True)
#        subprocess.check_output(f"docker push {k8s_docker_repository}/{k8s_name_space}/{imageName}" , cwd=basepath, shell=True)
        stderr = sout.stderr.read().decode(encoding="utf-8")
        if stderr == "":
#        if str(sout).find("Successfully built")>=0:
#            sout_s = str(sout).split("Successfully built")
#            ImageId = sout_s[1].split("\\n")[0].strip()
            ImageId = sout.stdout.read().decode(encoding="utf-8")[7:-1]
#            ImageId = str(sout, encoding='utf-8')[7:-1]
#            MODEL_STATUS = "03" # 03 代表模型镜像发布成功
            Role = node['Role']
            temp["Role"] = Role
            temp["ImageId"] = ImageId
            temp["ImageName"] = imageName
            MODEL_STATUS = "03" # 03 代表模型镜像发布成功
            image_info[Role] = temp
            Images.append(temp)
            stdout = sout.stdout.read().decode(encoding="utf-8")
            #Done 推送镜像到镜像仓库中，subprocess.check_output(f"docker push {k8s_docker_repository}/{k8s_name_space}/imageName , cwd=basepath, shell=True)
            log.info("..........docker tag.......")
            subprocess.check_output(f"docker tag {imageName} {k8s_docker_repository}/{k8s_name_space}/{imageName}", cwd=basepath, shell=True)
            log.info("..........docker push.......")
            first_push = True
            try:
                subprocess.check_output(f"docker push {k8s_docker_repository}/{k8s_name_space}/{imageName}" , cwd=basepath, shell=True)
            except Exception as ex:
                first_push = False
                log.info(f"docker push fail,reason:---{ex}")
            if first_push is False:
                log.info(".................second push..........")
                try:
                    subprocess.check_output(f"docker push {k8s_docker_repository}/{k8s_name_space}/{imageName}",cwd=basepath, shell=True)
                except Exception as ex:
                    log.error(f"docker push fail,Reason: {ex}")
        else:
            MODEL_STATUS = "04" #04 代表模型镜像发布失败
            log.info(f"docker build exception---{stderr}") 
            break
#            return _rjson(None, 509, "docker build fail")
    # 删除basepath临时文件
#    try:
#        if os.path.exists(basepath):
#            shutil.rmtree(basepath)
#    except Exception as ex:
#        log.info(f"rm {basepath} Exception: {ex}")
#        return _rjson(None, 510, f"{basepath} rm fail") 
    # Done 将镜像发布结果及镜像ID保存至数据库
    for image_dict in Images:
        ImageId = image_dict.get("ImageId","")
        Role = image_dict.get("Role","")
        try:
            #update_database(ModelId,MODEL_STATUS,ImageId,Role)
            db.update_model_image(ModelId, MODEL_STATUS, ImageId, Role)
            log.info(f"update_database success--{MODEL_STATUS}")
        except Exception as ex:
            log.info(f"update_database---{ex}")
#        return _rjson({"update_database fail": ex})
    with open(store_fpath, "w", encoding="utf-8") as fo:
        json.dump(image_info, fo, indent=4)
    return _rjson({"Images": Images})
	
# 查询发布的模型镜像
def ModelPubQuery():
    jsons = request.stream.read().decode('utf-8')
    log.info("...enter ModelPubQuery...[%s]" % jsons)
    jo = json.loads(jsons).get('BODY', {})
    user = jo['User']
    model_id = jo.get('ModelPkId', None)
    images = [] 
    # 如果model_id为空，则返回空列表
    if model_id is None or model_id == "":
        log.info("---model_pk_id is None---")
        return _rjson({"status":"OK", "images": images})
    else:
#        store_fpath = "/mci/work/congpq/k8s_images_%s.json" % model_id
        model_id = model_id.lower()
        store_fpath = ".store/k8s_images_%s.json" % model_id
        if not os.path.exists(store_fpath): 
            log.info("---images conf file not exists---")
            return _rjson({"status":"OK", "images": images})
        # 如果该model存在，则返回镜像信息
        with open(store_fpath,"r") as fo:
            result = json.load(fo)
            images.append(result)
        log.info(f"images---{images}")
        return _rjson({"status":"OK", "images": images})


# 根据配置信息制作镜像
'''
 服务名称: /ImageConstruct
 目的: 自有模型与模型发布的衔接, 可以通过这两个参数, 把自有模型中生成的模型文件打到镜像中.
新增参数:
 - modelFilePath 模型文件路径, 指向hdfs文件路径, 如果是多个, 则以","分隔,
    这些文件, 在构建镜像的过程中, 下载存放到 /data 目录里, 代码中可以直接读取
 - serviceCode: 服务代码, 源代码, 以 utf-8 编码, 保存到 /code/main.py 中, 并注册到 /main uri上
'''
def ImageConstruct():
    global image_path

    jsons = request.stream.read().decode('utf-8')
    log.info("...enter ImageConstruct...[%s]" % jsons)
    jo = json.loads(jsons)['ROOT']
    BaseImage = jo['BaseImage']  #基础镜像目录名称
    Name = jo['Name']
    DataDir = jo.get('DataDir', None)
    CodeDir = jo['CodeDir']
    Mount = jo.get('Mount', None)
    Packages = jo['Packages']
    ModelId = jo['ModelId']
    HandlerScript = jo.get('HandlerScript',"")  #格式:  mod.py,mod1.py

    # Todo 要验证 HandlerScript 里的函数 init 和 handler

    # 建立临时目录
    basepath = os.path.join(image_path, "."+ModelId)
    if os.path.exists(basepath):
        try:
            shutil.rmtree(basepath)
        except Exception as ex:
            log.info(f"shutil.rmtree--{ex}")
	
    os.mkdir(basepath)
    entrypath = _pjoin(basepath, "entry")
    shutil.copytree(_pjoin(image_path, "entry"), entrypath)
    with open(_pjoin(entrypath, "model_conf.json"), "w") as fo:
        json.dump({"model_id": ModelId}, fo)

    BaseImage_file=os.path.join(image_path, BaseImage)  # /mci/dockers/packages/3.7

    pkgs_path = os.path.join(entrypath, "pkgs")
    os.mkdir(pkgs_path)
    requirement_path = os.path.join(pkgs_path, "requirements.txt")
    pack_path_copy = {}
    with open(requirement_path,"w+",encoding='utf-8') as fi:     # 生成requirements.txt
        for pack in Packages:
            fi.write(pack["name"])
            fi.write("\n")
            pack_path_copy[pack['path']] = 1
        fi.write("flask\npsutil\nrequests\n") # 添加主进程依赖的包
        pack_path_copy['entry_pkg'] = 1 # 主进程依赖包所在路径
    # 把所有依赖包文件都copy到 entry/pkgs 目录下
    for pp in pack_path_copy.keys():
        src_path = os.path.join(BaseImage_file, pp)
        for fname in os.listdir(src_path):
            if fname.startswith('.'): continue
            shutil.copy(os.path.join(src_path, fname), os.path.join(pkgs_path, fname))
    if not CodeDir.startswith('/'): CodeDir = "/" + CodeDir
    if os.path.exists(CodeDir):
        shutil.copytree(CodeDir, _pjoin(basepath, "code"))
    else:
        os.makedirs(_pjoin(basepath, "code"), exist_ok=True)
        _exec("scp -r iasp75:%s/* %s/code/"%(CodeDir, basepath))
    if DataDir is not None:
        if not DataDir.startswith('/'): DataDir = "/" + DataDir
        if os.path.exists(DataDir):
            shutil.copytree(DataDir, _pjoin(basepath, "data"))
        else:
            os.makedirs(_pjoin(basepath, "data"), exist_ok=True)
            _exec("scp -r iasp75:%s/* %s/data/" % (DataDir, basepath))

    if hasattr(jo, "serviceCode"): # 保存源代码到 /code/main.py 中
        with open("%s/code/main.py"%basepath, "w", encoding="utf-8") as fo:
            fo.write(jo['serviceCode'])
        if HandlerScript == '':
            HandlerScript = "main.py"
        else:
            HandlerScript = HandlerScript+",main.py"
    if hasattr(jo, "modelFilePath"):  # 从hdfs上下载文件到目录中
        for fpath in jo['modelFilePath'].split(","):
            fname = fpath[fpath.rfind('/')+1:]
            lpath = _pjoin(basepath, "data", fname)
            fs.down_file(fpath, lpath)

    with open(os.path.join(BaseImage_file,"conf"), "r") as fi:
        conf_dict = json.load(fi)
    Dockerfile = os.path.join(basepath, "Dockerfile")
    BaseImage_=conf_dict['image_name'] 
    log.info("--writing Dockerfile: %s" % Dockerfile)
    # 构建Dockerfile文件    
    with open(Dockerfile,"w+",encoding='utf-8') as fi:
        fi.write("FROM " + BaseImage_ + '\n')
        fi.write("COPY entry /entry"+"\n")
        fi.write("RUN pip3 install --no-index --find-links /entry/pkgs -r /entry/pkgs/requirements.txt && rm -rf /entry/pkgs"+'\n')
        if DataDir is not None:
            fi.write("COPY data /data" + '\n')
        fi.write("COPY code /code" + '\n')
        fi.write("ENV MODEL_SERVICE %s\n" % HandlerScript)
        if Mount is not None:
            fi.write("VOLUME")
            for dir in Mount:
                fi.write(" "+dir)
            fi.write('\n')
        fi.write("CMD /bin/bash /entry/start.sh")

    # 进入packages目录并执行dodcker build
    sout = subprocess.check_output("docker build -t "+Name+" .", cwd=basepath, shell=True)
    shutil.rmtree(basepath) # 清理文件
    log.info("docker build output: %s", sout)
    return _rjson({"return": "build success"})
# 以pypi私服方式安装扩展包
def ImageConstruct_pypi():
    global image_path

    jsons = request.stream.read().decode('utf-8')
    log.info("...enter ImageConstruct...[%s]" % jsons)
    jo = json.loads(jsons)['ROOT']
    BaseImage = jo['BaseImage']  #基础镜像目录名称
    Name = jo['Name']
    DataDir = jo.get('DataDir', None)
    CodeDir = jo['CodeDir']
    Mount = jo.get('Mount', None)
    Packages = jo['Packages']
    ModelId = jo['ModelId']
    HandlerScript = jo.get('HandlerScript',"")  #格式:  mod.py,mod1.py

    # Todo 要验证 HandlerScript 里的函数 init 和 handler

    # 建立临时目录
    basepath = os.path.join(image_path, "."+ModelId)
    if os.path.exists(basepath):
        shutil.rmtree(basepath)
    os.mkdir(basepath)
    entrypath = _pjoin(basepath, "entry")
    shutil.copytree(_pjoin(image_path, "entry"), entrypath)
    with open(_pjoin(entrypath, "model_conf.json"), "w") as fo:
        json.dump({"model_id": ModelId}, fo)

    BaseImage_file=os.path.join(image_path, BaseImage)  # /mci/dockers/packages/3.7

    pkgs_path = os.path.join(entrypath, "pkgs")
    os.mkdir(pkgs_path)
    requirement_path = os.path.join(pkgs_path, "requirements.txt")
    pack_path_copy = {}
    with open(requirement_path,"w+",encoding='utf-8') as fi:     # 生成requirements.txt
        for pack in Packages:
            fi.write(pack["name"])
            fi.write("==")
            fi.write(pack["version"])
            fi.write("\n")
            pack_path_copy[pack['path']] = 1
        fi.write("flask\npsutil\nrequests\n") # 添加主进程依赖的包
        pack_path_copy['entry_pkg'] = 1 # 主进程依赖包所在路径
    # 把所有依赖包文件都copy到 entry/pkgs 目录下
#    for pp in pack_path_copy.keys():
#        src_path = os.path.join(BaseImage_file, pp)
#        for fname in os.listdir(src_path):
#            if fname.startswith('.'): continue
#            shutil.copy(os.path.join(src_path, fname), os.path.join(pkgs_path, fname))
    if not CodeDir.startswith('/'): CodeDir = "/" + CodeDir
    if os.path.exists(CodeDir):
        shutil.copytree(CodeDir, _pjoin(basepath, "code"))
    else:
        os.makedirs(_pjoin(basepath, "code"), exist_ok=True)
        _exec("scp -r iasp75:%s/* %s/code/"%(CodeDir, basepath))
    if DataDir is not None:
        if not DataDir.startswith('/'): DataDir = "/" + DataDir
        if os.path.exists(DataDir):
            shutil.copytree(DataDir, _pjoin(basepath, "data"))
        else:
            os.makedirs(_pjoin(basepath, "data"), exist_ok=True)
            _exec("scp -r iasp75:%s/* %s/data/" % (DataDir, basepath))

    if hasattr(jo, "serviceCode"): # 保存源代码到 /code/main.py 中
        with open("%s/code/main.py"%basepath, "w", encoding="utf-8") as fo:
            fo.write(jo['serviceCode'])
        if HandlerScript == '':
            HandlerScript = "main.py"
        else:
            HandlerScript = HandlerScript+",main.py"
    if hasattr(jo, "modelFilePath"):  # 从hdfs上下载文件到目录中
        for fpath in jo['modelFilePath'].split(","):
            fname = fpath[fpath.rfind('/')+1:]
            lpath = _pjoin(basepath, "data", fname)
            fs.down_file(fpath, lpath)

    with open(os.path.join(BaseImage_file,"conf"), "r") as fi:
        conf_dict = json.load(fi)
    Dockerfile = os.path.join(basepath, "Dockerfile")
    BaseImage_=conf_dict['image_name'] 
    log.info("--writing Dockerfile: %s" % Dockerfile)
    # 构建Dockerfile文件    
    with open(Dockerfile,"w+",encoding='utf-8') as fi:
        fi.write("FROM " + BaseImage_ + '\n')
        fi.write("COPY entry /entry"+"\n")
        # 修改扩展包安装方式为pypi私服
        fi.write(f"RUN pip3 install --index-url {pypi_server} --trusted-host {pypi_server[7:20]} -r /entry/pkgs/requirements.txt && rm -rf /entry/pkgs"+'\n')
#        fi.write("RUN pip3 install --no-index --find-links /entry/pkgs -r /entry/pkgs/requirements.txt && rm -rf /entry/pkgs"+'\n')
        if DataDir is not None:
            fi.write("COPY data /data" + '\n')
        fi.write("COPY code /code" + '\n')
        fi.write("ENV MODEL_SERVICE %s\n" % HandlerScript)
        if Mount is not None:
            fi.write("VOLUME")
            for dir in Mount:
                fi.write(" "+dir)
            fi.write('\n')
        fi.write("CMD /bin/bash /entry/start.sh")

    # 进入packages目录并执行dodcker build
    sout = subprocess.check_output("docker build -t "+Name+" .", cwd=basepath, shell=True)
    shutil.rmtree(basepath) # 清理文件
    log.info("docker build output: %s", sout)
    return _rjson({"return": "build success"})

def _rjson(obj, rcode=0, rmsg="success"):
    ret = {"ROOT":{"HEADER":{}, "RETURN_CODE":rcode, "RETURN_MSG":rmsg, "BODY":obj}}
    r = Response(json.dumps(ret), mimetype="application/json; charset=utf-8")
    # r.content_type = "application/json; charset=utf-8"
    return r

# http://172.18.231.68:1192/img_columns_hist?dataset=/marketdata/uselabel20181212_10w.txt&sample=10&sep=|&cols=2,3,4,5,71
# 4.3.2.1 镜像列表查询
def ImageList():
    global image_path
    jsons = request.stream.read().decode('utf-8')
    print(jsons)
    jo = json.loads(jsons)
    query = jo.get("BODY", {}).get("Query", "")
    queryid = jo.get("BODY",{}).get("ID", "")

    imgs = []
    for fname in os.listdir(image_path):
        if fname[0] == '.': continue
        fpath = os.path.join(image_path, fname)
        if not os.path.isdir(fpath): continue
        conf_path = os.path.join(fpath, "conf")
        if not os.path.exists(conf_path): continue
        with open(conf_path, "r") as fi:
            j = json.load(fi)
        image_id = j['image_id']
        if queryid == image_id:  # 只有 ID 的查询, 才返回Packages, 减少返回报文大小
            pkg_file = os.path.join(fpath, "conf_base_pkg")
            if not os.path.exists(pkg_file): continue
            with open(pkg_file, "r") as fi:
                pkgs = [f.strip() for f in fi.readlines()]
        else:
            pkgs = ""
        image_name  = j['image_name']
        if query != "" and image_name.find(query) < 0:
            continue
        imgs.append({"ID":fname, "Name":image_name, "Description":j['desc'], "Packages":pkgs,
                     "Size":j['size'], "Created":j['created'], "ImageId":image_id, "BaseVer":j.get("BaseVer", None)})

    return _rjson({"Images":imgs})

def _pip_match_version(name, pypi_url, pyver=(3, 7, 0)):
    from pip._internal.commands.download import DownloadCommand
    from pip._internal.models.format_control import FormatControl
    from pip._internal.models.target_python import TargetPython
    from pip._internal.cli.cmdoptions import make_target_python
    from optparse import Values
    from pip._internal.req.constructors import  install_req_from_line
    import re

    m = re.search(r'^http://([^:]+):[\d]+', pypi_url)
    trusted_host = m.group(1)
    options = Values({'help': None, 'isolated_mode': False, 'require_venv': False, 'verbose': 0, 'version': None, 'quiet': 0, 'log': None,
               'no_input': False, 'proxy': '', 'retries': 5, 'timeout': 15, 'exists_action': [], 'trusted_hosts': [trusted_host],
               'cert': None, 'client_cert': None, 'cache_dir': '/data/.cache/pip', 'disable_pip_version_check': False,
               'no_color': False, 'no_python_version_warning': False, 'features_enabled': [], 'deprecated_features_enabled': [],
               'constraints': [], 'requirements': [], 'build_dir': None, 'ignore_dependencies': False, 'global_options': None,
               'format_control': FormatControl(set(), set()), 'prefer_binary': False, 'src_dir': '/data/.app/envs/pip38/src',
               'pre': False, 'require_hashes': False, 'progress_bar': 'on', 'build_isolation': True, 'use_pep517': None,
               'ignore_requires_python': None, 'download_dir': '/data/.app/envs/pip38/_p', 'platforms': None, 'python_version': None,
               'implementation': None, 'abis': None, 'index_url': pypi_url, 'extra_index_urls': [],
               'no_index': False, 'find_links': [], 'no_clean': False, 'ignore_installed': True, 'editables': []})
    # options.ensure_value('platform', None)
    # options.ensure_value('abi', None)
    # target_python = make_target_python(options)
    target_python = TargetPython(py_version_info=pyver)
    print(target_python.py_version)
    # args = ['numpy']  # paramiko
    d = DownloadCommand(name="download", summary="")
    with d.main_context():
        session = d.get_default_session(options)
        finder = d._build_package_finder(
            options=options,
            session=session,
            target_python=target_python,
            ignore_requires_python=options.ignore_requires_python,
        )
        return [str(x.version) for x in finder.find_all_candidates(name)]  #args[0]):
        #     print(x.name, x.version)
        # return [x.version for x in ]

# _pip_match_version("numpy", pypi_url='http://172.18.243.19:1777', pyver=(3,6,0))

def __pipsearch(keyword, pypi_server):
    from pip._internal.commands.search import SearchCommand

    from optparse import OptionParser

    options__ = {'isolated_mode': False, 'require_venv': False, 'verbose': 0, 'version': None, 'quiet': 1,
               'log': None, 'no_input': False, 'proxy': '', 'retries': 1, 'timeout': 15, 'skip_requirements_regex': '',
               'exists_action': [], 'trusted_hosts': [], 'cert': None, 'client_cert': None, 'cache_dir': '/tmp/.cache_pip',
               'disable_pip_version_check': True, 'no_color': False, 'index': pypi_server}
    parser = OptionParser()
    for k, v in options__.items():
        parser.add_option("--"+k, dest=k, default=v)
    (options, args) = parser.parse_args([])

    query = [keyword, ]
    search = SearchCommand(name="search", summary="", )
    with search.main_context():
        return search.search(query, options)
        # for ret in search.search(query, options):
        #     print(ret['name'], ret['version'], ret['summary'])

'''
[mci@iasp68 3.7]$ cat opencv-python-4.1.1.26.list
opencv-python==4.1.1.26
  - numpy [required: >=1.14.5, installed: 1.17.2]
'''
def PackageSearch():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("enter PackageSearch: [{}]", jsons)
        jo = json.loads(jsons)

        query = jo['ROOT']['BODY']['Query']
        basever = jo['ROOT']['BODY'].get("basever", "")
        pkgs = []
        if query is None or query == "":
            return _rjson({"Packages": pkgs, "BaseVer": basever})
        # Done 入参Query用逗号分割
        queryList = query.split(",")
        qret = {}
        pypi_url = _conf['pypi_server']
        for pkg in queryList:
            for ret in __pipsearch(pkg, pypi_url):
                #pkgs.append({"Name": ret['name'], "Description": ret['summary'], "Version": ret['version'], "Path": '.'})
                qret[ret['name']] = ret['summary']
        # #剔重 和 排序
        # k = { f"{x['Name']}=={x['Version']}":1 for x in pkgs}
        # k1 = sorted(k.keys())
        # pkgs = [{"Name":x.split("==")[0], "Version":x.split("==")[1]} for x in k1]
        if basever:
            pyver = [ int(x) for x in basever.split('.') ]

            for name, summary in qret.items():
                versions = _pip_match_version(name, pypi_url, pyver=pyver)
                for ver in versions:
                    pkgs.append({"Name": name, "Version":ver} )
        else:
            for name, summary in qret.items():
                versions = _pip_match_version(name, pypi_url)
                for ver in versions:
                    pkgs.append({"Name": name, "Version": ver})

        #pkgs.sort(key=lambda i: (i["Name"], i["Version"]))
        return _rjson({"Packages": pkgs, "BaseVer": basever})
    except Exception as x:
        logger.error("search failed  {}, {}", x, traceback.format_exc())
        return _rjson(None, 500, f"search failed {x}")

    # base_path = os.path.join(image_path, basever)
    # if not os.path.exists(base_path):
    #     available = [v for v in os.listdir(image_path) if v[0]!='.' and os.path.isdir(os.path.join(image_path, v))]
    #     return _rjson(None, 1001, "BaseVer not found, available ver: %s"%repr(available))
    #
    # pkgs = []
    # for fname in os.listdir(base_path):
    #     if not fname.endswith('.list'): continue
    #     with open(os.path.join(base_path, fname), "r") as fi:
    #         fl = fi.readlines()
    #     print("fname -- query", fname, query)
    #     for line in fl:
    #         if line.find('==') > 0:  # opencv-python==4.1.1.26
    #             x = line.split("==")
    #             if x[0].find(query) >= 0:
    #                 pkgs.append({"Name": x[0].strip(), "Description": "", "Version": x[1].strip(), "Path": fname[:-5]})
    #         else: # - numpy [required: >=1.14.5, installed: 1.17.2]
    #             x = line.strip().split()
    #             if x[1].find(query) >= 0:
    #                 pkgs.append({"Name": x[1].strip(), "Description": "", "Version": x[3].strip(), "Path": fname[:-5]})
    # return _rjson({"Packages":pkgs, "BaseVer":basever})


def PackageSearchNew():
    try:
        jsons = request.stream.read().decode('utf-8')
        logger.info("enter PackageSearchNew: [{}]", jsons)
        jo = json.loads(jsons)

        query = jo['ROOT']['BODY']['Query']
        # versionDict = jo['ROOT']['BODY']['versionDict']
        basever = jo['ROOT']['BODY'].get("basever", "")

        pkgs = []
        if query is None or query == "":
            return _rjson({"Packages": pkgs, "BaseVer": basever})
        # Done 入参Query用逗号分割
        queryList = copy.deepcopy(query)
        qret = {}
        pypi_url = _conf['pypi_server']
        for pkginfo in queryList:
            pkg = pkginfo['relyName']
            version = pkginfo['relyVersion']
            logger.info(f'xxxxxxxxxx {__pipsearch(pkg, pypi_url)}')
            find = False
            versions = []
            tmp_name = ''
            for ret in __pipsearch(pkg, pypi_url):
                if ret['name'] == pkg:
                      tmp_name = ret['name']
                      if version == ret['version']:
                          pkginfo['isValid'] = 'Y'
                          find = True
                          break
            if not find and tmp_name:
                  versions = _pip_match_version(tmp_name, pypi_url)
                  pkginfo['isValid'] = versions
            elif not find and not tmp_name:
                pkginfo['isValid'] = 'N'
            #     versions = _pip_match_version(ret['name'], pypi_url)
            # [{'_pypi_ordering': 236, 'version': '7.1.1', 'name': 'click', 'summary': '7.1.1'},
            #  {'_pypi_ordering': 237, 'version': '7.1.1', 'name': 'click', 'summary': '7.1.1'},
            #  {'_pypi_ordering': 238, 'version': '7.1.2', 'name': 'click', 'summary': '7.1.2'},
            #  {'_pypi_ordering': 239, 'version': '1.1.1', 'name': 'click_plugins', 'summary': '1.1.1'},
            #  {'_pypi_ordering': 802, 'version': '7.1.2', 'name': 'click', 'summary': '7.1.2'}]

            # for ret in __pipsearch(pkg, pypi_url):
            #     #pkgs.append({"Name": ret['name'], "Description": ret['summary'], "Version": ret['version'], "Path": '.'})
            #     qret[ret['name']] = ret['summary']
        # #剔重 和 排序
        # k = { f"{x['Name']}=={x['Version']}":1 for x in pkgs}
        # k1 = sorted(k.keys())
        # pkgs = [{"Name":x.split("==")[0], "Version":x.split("==")[1]} for x in k1]
        # if basever:
        #     pyver = [ int(x) for x in basever.split('.') ]
        #
        #     for name, summary in qret.items():
        #         versions = _pip_match_version(name, pypi_url, pyver=pyver)
        #         for ver in versions:
        #             if versionDict and (name in versionDict.keys) and versionDict['name'] == ver:
        #                 pkgs_dict.update({
        #                     'name': name,
        #                     'version': ver
        #                 })
        #                 # pkgs.append({"Name": name, "Version":ver} )
        # else:
        #     for name, summary in qret.items():
        #         versions = _pip_match_version(name, pypi_url)
        #         for ver in versions:
        #             pkgs.append({"Name": name, "Version": ver})

        #pkgs.sort(key=lambda i: (i["Name"], i["Version"]))
        return _rjson({"Packages": queryList, "BaseVer": basever})
    except Exception as x:
        logger.error("search failed  {}, {}", x, traceback.format_exc())
        return _rjson(None, 500, f"search failed {x}")

    # base_path = os.path.join(image_path, basever)
    # if not os.path.exists(base_path):
    #     available = [v for v in os.listdir(image_path) if v[0]!='.' and os.path.isdir(os.path.join(image_path, v))]
    #     return _rjson(None, 1001, "BaseVer not found, available ver: %s"%repr(available))
    #
    # pkgs = []
    # for fname in os.listdir(base_path):
    #     if not fname.endswith('.list'): continue
    #     with open(os.path.join(base_path, fname), "r") as fi:
    #         fl = fi.readlines()
    #     print("fname -- query", fname, query)
    #     for line in fl:
    #         if line.find('==') > 0:  # opencv-python==4.1.1.26
    #             x = line.split("==")
    #             if x[0].find(query) >= 0:
    #                 pkgs.append({"Name": x[0].strip(), "Description": "", "Version": x[1].strip(), "Path": fname[:-5]})
    #         else: # - numpy [required: >=1.14.5, installed: 1.17.2]
    #             x = line.strip().split()
    #             if x[1].find(query) >= 0:
    #                 pkgs.append({"Name": x[1].strip(), "Description": "", "Version": x[3].strip(), "Path": fname[:-5]})
    # return _rjson({"Packages":pkgs, "BaseVer":basever})

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
    log.debug('[[%s]]' % sout)
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


def TargetSelect():
    '目标主机选择服务, 主要是计算目标主机的资源使用情况'
    deploy_targets = _conf['deploy_targets']
    weight_cpu, weight_mem, weight_disk = 0.1, 0.2, 0.7
    hosts_stat = []
    for h in deploy_targets:
        host, user, disk = h['host'], h['user'], h['disk']
        sout = subprocess.check_output("ssh %s@%s iostat -c && free -m && df -k %s" % (user, host, disk), shell=True)
        pct_cpu, pct_mem, pct_disk = _parse_stat(sout.decode('utf8'))
        total_stat = pct_cpu * weight_cpu + pct_mem * weight_mem + pct_disk * weight_disk
        log.debug("TargetSelect, host:%s cpu=%.2f%% mem=%.2f%% disk=%.2f%% summary=%.2f" %(
            host, pct_cpu, pct_mem, pct_disk, total_stat))
        hosts_stat.append(total_stat)
    idxs = sorted(range(len(hosts_stat)), key=lambda k: hosts_stat[k]) # 排序,取序号
    h = deploy_targets[idxs[0]]
    log.debug("  selected: %s", h['host'])
    return _rjson({"Host":h['host'], "User":h['user'], "Load":hosts_stat[idxs[0]]})

def _update_status(qry_id, pct=0, code=0, msg=None):
    global deploy_query_obj
    if qry_id not in deploy_query_obj:
        log.error("deploy_id: %s not found" % qry_id)
        return
    if msg == '_del':
        del deploy_query_obj[qry_id]
    s = deploy_query_obj[qry_id]
    s[0] = pct
    s[2] = time.time()
    s[3] = code
    s[4] = msg
    with open(deploy_status_file, "w") as fo:
        json.dump(deploy_query_obj, fo, indent=2)

def _exec(cmd_str):
    proc = subprocess.Popen(cmd_str, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout = proc.stdout.read()
    stderr = proc.stderr.read()
    return stdout.decode('utf8'), stderr.decode('utf8')

def _thread_deploy(jo, qry_id):
    '镜像部署线程, 完成一个阶段就将情况更新到 deploy_query_obj 中'
    global rest_register_url
    try:
        ImageName = jo['ImageName']
        ModelId = jo['ModelId']
        TargetHostUser = jo['TargetHostUser']
        p = TargetHostUser.find(':')
        port_scp = ""
        port_ssh = ""
        if p > 0: #处理ssh端口:
            port = TargetHostUser[p+1:].strip()
            TargetHostUser = TargetHostUser[:p]
            port_scp = " -P "+port
            port_ssh = " -p" +port
        local_tmp = "%s/.tmp" % os.getenv("HOME")
        if not os.path.exists(local_tmp):
            os.mkdir(local_tmp)
        ssh_prefix = "ssh%s %s" %(port_ssh, TargetHostUser)

        p = ImageName.find(':')
        if p > 0:
            img_rep, img_tag = ImageName.split(":")
        else:
            img_rep, img_tag = ImageName, 'latest'

        if TargetHostUser.find(local_ip) < 0:# 如果是本机的话， 这部分要忽略
            _update_status(qry_id, pct=10, msg="saving image..." )
            sout = subprocess.check_output("docker save %s|gzip -c > %s/%s.gz"%(ImageName, local_tmp, ModelId), shell=True)
            log.info('docker save return:%s' % sout.decode('utf8'))

            _update_status(qry_id, pct=20, msg="sending image...")
            subprocess.check_output("ssh%s %s mkdir -p ~/.tmp" % (port_ssh, TargetHostUser), shell=True) # 创建临时目录, -p参数可以建多层, 重复也不会报错
            subprocess.check_output("scp%s %s/%s.gz %s:.tmp/" %(port_scp, local_tmp, ModelId, TargetHostUser), shell=True)
            sout = subprocess.check_output("ssh%s %s ls ~/.tmp" % (port_ssh, TargetHostUser), shell=True)
            if sout.decode('utf8').find(ModelId) < 0: # 检查文件是否存在
                log.error("scp upload file failed!")
                raise Exception('scp upload file failed!')

            os.remove("%s/%s.gz" %(local_tmp, ModelId)) # 清理本地文件

            _update_status(qry_id, pct=40, msg="restoring image...")
            sout, _ = _exec("%s docker images" % (ssh_prefix,))
            img_id = _find_image_id(sout, img_rep, img_tag)
            if img_id is not None:
                while True:
                    log.warning("remove orignal image: %s" % img_id)
                    stdout, stderr = _exec("%s docker rmi -f %s" % (ssh_prefix, img_id)) #强制删除原有的名称
                    # Done  Error response from daemon: conflict: unable to delete 0f4ca8241096 (cannot be forced) - image is being used by running container c5698b5a92ff
                    if stderr.find("unable to delete") > 0:
                        c_id = stderr.strip().split()[-1]
                        log.warning("----need to kill container: %s" % c_id)
                        stdout, stderr = _exec("%s docker kill %s" % (ssh_prefix, c_id))
                    else:
                        break
            sout, _ = _exec("%s gunzip -c ~/.tmp/%s.gz\\|docker load" % (ssh_prefix, ModelId))
            log.info("docker load return: %s" % sout)
        else:
            log.info("---deploy on local: %s" % local_ip)

        # 检查是否恢复正常
        sout, _ = _exec("%s docker images" % (ssh_prefix ))
        img_id = _find_image_id(sout, img_rep, img_tag)
        if img_id is None:
            raise Exception("Image restore failed")

        sout, _ = _exec("%s rm ~/.tmp/%s.gz" % (ssh_prefix, ModelId)) # 删除中间文件

        _update_status(qry_id, pct=80, msg="starting container...")
        # 获得一个远程可用的监听端口
        with open(_pjoin(local_tmp, "get_port.py"), "w") as fo:
            fo.write('import socket as st;s=st.socket(st.AF_INET,st.SOCK_STREAM);s.bind(("", 0));s.listen(1);print(s.getsockname()[1]);s.close()\n')
        subprocess.check_output("scp%s %s/get_port.py %s:.tmp/" % (port_scp, local_tmp, TargetHostUser), shell=True)
        sout, _ = _exec("%s python ~/.tmp/get_port.py"%(ssh_prefix,) )
        bind_port = sout.strip()
        int(bind_port)  # 检查是否数字
        log.info("docker run bind port : %s" % bind_port)
        # 启动容器
        cmd_str = "docker run -d --rm -p {port}:{port} -e MAIN_PORT={port} -e REGISTER_URL={reg_url} -e HOST_IP={host} {img_id}".format(
            port=bind_port, reg_url=rest_register_url, img_id=img_id, host=TargetHostUser.split("@")[1]
        )
        log.info("cmd_str: [[%s]]" % cmd_str)
        sout, _ = _exec("%s %s"%(ssh_prefix, cmd_str))
        container_id = sout.strip()[:12]
        log.info("docker run return container_id: %s" % container_id)

        _update_status(qry_id, 100, 0, "all done, img_id: %s container_id : %s" % (img_id, container_id) )
        # Done 保存容器的启动记录, 以便后台线程进行监控(端口就还是使用原来的端口)
        _save_container_list(container_id, img_rep+":"+img_tag, ssh_prefix, cmd_str, model_id=ModelId)
        log.info("--container model_id:%s saved img_id: %s  container_id: %s" % (
            ModelId, img_id, container_id) )
    except Exception as ex:
        log.error("ImageDeploy failed", ex)
        _update_status(qry_id, 100, -2, "exception occurs:"+str(ex))

def _save_container_list(container_id, img_name, ssh_prefix, cmd_str, old_container_id=None, model_id=None):
    _fname = container_list_file
    if os.path.exists(_fname):
        with open(_fname, "r") as fi:
            clist = json.load(fi)
        if old_container_id is not None:
            del clist[old_container_id]  # 删除原有的 container_id
    else:
        clist = {}
    if container_id is not None:
        clist[container_id] = [img_name, ssh_prefix, cmd_str, model_id]
    with open(_fname, "w") as fo:
        json.dump(clist, fo, indent=2)

def _thread_check_containers():
    _fname = container_list_file
    while True:
        time.sleep(30.0)
        if os.path.exists(_fname):
            with open(_fname, "r") as fi:
                clist = json.load(fi)
        else:
            continue
        for c_id, vals in clist.items():
            img_name, ssh_prefix, cmd_str, model_id = vals
            sout, _ = _exec("%s docker ps" % (ssh_prefix,))
            found = False
            for ln in sout.split("\n"):
                if ln.startswith(c_id):
                    found = True
                    break
            if not found:
                log.warning("container %s %s not found, restart it" %(c_id, vals))
                sout, _ = _exec("%s %s" % (ssh_prefix, cmd_str))
                container_id = sout.strip()[:12]
                log.warning("docker rerun return: %s, old_container: %s" % (container_id, c_id))
                _save_container_list(container_id, img_name, ssh_prefix, cmd_str, old_container_id=c_id, model_id=model_id)

def _find_image_id(stdout, img_rep, img_tag):
    img_id = None
    for ln in stdout.split('\n'):
        if ln.startswith("%s " % img_rep) and ln.find(" %s " % img_tag) > 0:
            #log.info("Image restore success")
            img_id = ln.split()[2]
            break
    if img_id is None:
        log.info("Image not found %s:%s"%(img_rep, img_tag))
    return img_id


def ImageDeploy():
    '镜像部署服务, 构建的模型镜像部署到目标主机'
    #rest_register_url = conf['rest_register_url']
    global deploy_query_obj
    '如果镜像已经存在, 怎么办?   --先删除再load'
    jsons = request.stream.read().decode('utf-8')
    log.info("request ImageDeploy: [%s]" % jsons)
    jo = json.loads(jsons)
    qry_id = str(uuid.uuid1())
    deploy_query_obj[qry_id] = [5, time.time(), time.time(), 0, 'init...']  # 百分比, 开始时间, 更新时间, return_code, return_msg
    th = threading.Thread(target=_thread_deploy, args=[jo['ROOT'], qry_id], daemon=True)
    th.start()
    return _rjson({"QryId":qry_id})

def ImageDeployStatus():
    '查询部署状态'
    global deploy_query_obj
    if len(deploy_query_obj) == 0:
        if os.path.exists(deploy_status_file):
            with open(deploy_status_file, "r") as fi:
                deploy_query_obj = json.load(fi)
    jo = json.load(request.stream)
    qry_id = jo['ROOT']['QryId']
    if qry_id not in deploy_query_obj:
        return _rjson(None, rcode=1000, rmsg="QryId not found!")
    s = deploy_query_obj[qry_id]
    return _rjson({"PCT":s[0], 'LastUpdateTime':s[2], 'TimeElapsed':s[2]-s[1]}, rcode=s[-2], rmsg=s[-1])

def ImageUndeploy():
    '镜像取消启动'
    global container_list_file, rest_register_url
    _fname = container_list_file
    if os.path.exists(_fname):
        with open(_fname, "r") as fi:
            clist = json.load(fi)
    else:
        return _rjson({"Info":"Model Image Deploy not found"}, 1000, "not found!")
    jo = json.load(request.stream)
    ModelId = jo['ROOT']['ModelId']
    container_id = None
    for c_id, vals in clist.items():
        img_name, ssh_prefix, cmd_str, model_id = vals
        if model_id == ModelId:
            container_id = c_id
            break
    if container_id is None:
        return _rjson({"Info": "Model Image Deploy not found"}, 1001, "not found!")

    reg_url = rest_register_url.replace("/restadd", "/restdel")
    r = requests.post(reg_url, data='{"ROOT":{"uri":"%s"}}'%ModelId, headers={'Content-type': 'application/json;charset=utf-8'})
    found = False
    if r.status_code == 200:
        ro = json.loads(r.text)
        rcode = ro['ROOT']['RETURN_CODE']
        if rcode == 0:
            log.info("unregister successfully!! %s" % ModelId)
            found = True
    if not found:
        return _rjson(None, 1002, "unregister failed")

    # Todo 删除配置
    _save_container_list(container_id=None, img_name=None, ssh_prefix=None, cmd_str=None, old_container_id=container_id, model_id=None)

    sout, _ = _exec("%s docker kill %s" % (ssh_prefix, container_id))
    sout, _ = _exec("%s docker rmi %s" % (ssh_prefix, img_name))

    return _rjson({"Info":"unDeploy success!"})


# 支持集群模式的模型发布
# 直接启动容器, 没有提前创建镜像, 这里跟模型管理的过程有些矛盾, 后期再讨论看怎么处理
'''
export MAIN_PORT=5566
export MODEL_ID=123
export NODE_ID=0
export HOST_IP=172.18.231.68
export FILE_SERVER=http://172.18.231.68:1191
export REGISTER_URL=http://172.18.231.68:1293/modelservices/restadd
'''
def ModelPub():
    deploy_targets = _conf['deploy_targets']
    jsons = request.stream.read().decode('utf-8')
    log.info("...enter ModelPub...[%s]" % jsons)
    jo = json.loads(jsons)['BODY']
    model_id = jo['ModelId']
    nodes = jo.get('Nodes', None)
    if nodes is None:
        log.error("Nodes node not found in request BODY")
        return abort(506)


    idxs = hosts_sort(deploy_targets, len(nodes)) # 获取主机按资源排序的序号

    #Todo 处理主机互斥规则

    #Done 保存请求报文到临时文件中
    import tempfile

    file_server = "http://%s:%s" % (local_ip, rest_port)
    fs = FSServ(file_server)
    tmp_fileno, tmp_filename = tempfile.mkstemp()
    fo = open(tmp_fileno, "w", encoding="utf-8")
    fo.write(jsons)
    fo.close()

    # 拼接容器启动参数
    env_all = {}
    host_nodes = []
    for hidx, node in zip(idxs, nodes):
        container_args = ["-d", "-v /tmp:/tmp1", "-v ~/dockers/packages:/pkgs:ro", "--privileged --device=/dev/fuse", "--entrypoint /pkgs/entry/run.sh"]
        host = deploy_targets[hidx]
        node_id = node.get('NodeId', None)
        env_all["HOST_%s" % node['Name']] = host['host']
        container_args.append("-e HOST_IP=%s"%host['host'])
        container_args.append("-e MODEL_ID=%s"%model_id)  # 允许在单个节点的时候, NodeId不存在, model_main.py中 也要做相应的处理
        if node_id is not None:
            container_args.append("-e NODE_ID=%s" % node_id)
        container_args.append("-e FILE_SERVER=%s"% file_server)
        container_args.append("-e REGISTER_URL=%s"%rest_register_url)
        #基础端口 MAIN_PORT, 其余端口[PORT_${n} for n in Ports.split(",")]
        ports_name = ["MAIN_PORT",]
        ports = node.get("Ports", None)
        if ports is not None:
            ports_name.extend([ "PORT_%s"% (n.strip(),) for n in ports.split(",")])
        h_ports = get_port(host, len(ports_name))
        for i in range(1, len(ports_name)):
            env_all[ports_name[i]] = str(h_ports[i])
            container_args.append("-p {port}:{port}".format(port=h_ports[i]) )
        container_args.append("-e MAIN_PORT={port} -p {port}:{port}".format(port=h_ports[0]))
        BaseImage = node['BaseImage']
        host_nodes.append( {"args":container_args, "baseimage":BaseImage, "host":host, "node_id":node_id, "name":node['Name']} )

        #Done DataDir/CodeDir 上传文件目录, 暂时设计的情况是web界面直接上传到file_server 上, 因此不需要额外处理了.
        #Done 上传请求报文到文件服务器
        fs.upload(tmp_filename, "models/%s/%s/param.json" % (model_id, node_id))

        #Done 处理外部目录mount
        if "Mounts" in node:
            for n in node['Mounts']:
                if n['Type'] == 'host':
                    container_args.append("-v %s:%s"%(n["Source"], n["Target"]))

        #Done Envs 的处理, k=v,k=v格式
        if "Envs" in node:
            for x in node['Envs'].split(","):
                if x.find("=") < 0: continue
                k, v = [s.strip() for s in x.split("=", 1)]
                container_args.append("-e %s=%s" % (k, v))

    os.remove(tmp_filename)

    #Done 为每个节点补充全部的环境变量
    for h in host_nodes:
        container_args = h['args']
        for k, v in env_all.items():
            container_args.append("-e %s=%s" % (k, v))

    #Done 启动并保存容器列表
    started_containers = []
    for h in host_nodes:
        # container_args, base_image, host, node_id = h
        container_cmd = "docker run %s %s" %(" ".join(h["args"]), h['baseimage'])
        log.info("---cluster container: %s", container_cmd)
        h['cmd'] = container_cmd
        # Done 到目标主机上执行, 并获取 container_id
        sout = remote_exec(h['host'], container_cmd)
        container_id = sout.strip()[:12]
        h['container_id'] = container_id
        log.info("docker run return container_id: %s" % container_id)
        started_containers.append({"host":h['host']['host'], "container_id":container_id, "name":h['name']})
    #  并保存配置, 保存到 .store/model_container_{model_id}.json
    with open(".store/model_container_%s.json"%model_id, "w", encoding="utf-8") as fo:
        json.dump(host_nodes, fo, indent=4)

    #Done 返回结果, 是否返回启动容器的情况呢?
    return _rjson({"status":"OK", "started_containers": started_containers})

# 发布的模型删除
def ModelDel():
    jsons = request.stream.read().decode('utf-8')
    log.info("...enter ModelDel...[%s]" % jsons)
    jo = json.loads(jsons)['BODY']
    model_id = jo['ModelId']

    #Done 验证modelid是否存在
    fpath = ".store/model_container_%s.json"%model_id
    if not os.path.exists(fpath):
        log.error("ModelDel, model_id not found: [%s]", model_id)
        return abort(404)
    with open(".store/model_container_%s.json"%model_id, "r", encoding="utf-8") as fi:
        host_nodes = json.load(fi)



    # file_server = "http://%s:%s" % (local_ip, rest_port)
    # fs = FSServ(file_server)

    for node in host_nodes:
        node_id = node['node_id']

        #Todo 停止并删除远程主机上的container
        container_id = node['container_id']
        sout = remote_exec(node['host'], "docker rm -f %s" % container_id)
        log.info("docker rm -f %s return [%s]", model_id, sout)

        #Todo 删除本地json文件
        _bak_path = ".store/_bak"
        if not os.path.exists(_bak_path):
            os.makedirs(_bak_path)

        #Todo 删除文件服务器上的文件, 这个文件由web前台上传, 按理也应该有web前台来删除, 因此暂时不处理才对
        # rpath = "/models/%s/%s" % (model_id, node_id)
        # ret = fs.rm_dir(rpath)
        # log.info("remove remote file [%s] return %s", rpath, ret)

        #Done 需要删除nginx上的转发配置
        rest_unregister_url = rest_register_url.replace("/restadd", "/restdel")
        if node_id is None:
            data = {"ROOT": {"uri": "/%s/" % (model_id), }}
        else:
            data = {"ROOT": {"uri": "/%s-%s/" % (model_id, node_id), }}
        r = requests.post(rest_unregister_url, data=json.dumps(data),
                          headers={"Content-Type": "application/json; charset=utf8"})
        print(r.status_code, r.text)
        assert r.status_code == 200

        log.info("rm container %s %s finished", model_id, node_id)

    os.rename(fpath, os.path.join(_bak_path, "model_container_%s-%.3f.json"%(model_id, time.time())))

    return _rjson({"status": "OK"})


# 已发布的模型列表查询
def ModelQuery():
    jsons = request.stream.read().decode('utf-8')
    log.info("...enter ModelQuery...[%s]" % jsons)
    jo = json.loads(jsons)['BODY']
    if "ModelId" in jo:
        model_id = jo['ModelId']
        fpath = ".store/model_container_%s.json" % model_id
        if not os.path.exists(fpath):
            log.error("ModelQuery, model_id not found: [%s]", model_id)
            return abort(404)
        with open(".store/model_container_%s.json" % model_id, "r", encoding="utf-8") as fi:
            host_nodes = json.load(fi)
        return _rjson({"model_id":model_id, "nodes":host_nodes})
    else:
        models = []
        for fname in os.listdir(".store"):
            if fname.startswith("model_container_") and fname.endswith(".json"):
                model_id = fname[16:-5]
                models.append(model_id)
        return _rjson({"models":models})

import string
from random import choice, randint

def __gen_rand_str(nc=6):
    if nc == 0:
        min_char, max_char = 24, 32
    else:
        min_char, max_char = nc, nc
    allchar = string.ascii_letters +  string.digits # string.punctuation +
    token = "".join(choice(allchar) for x in range(randint(min_char, max_char)))
    return token

# 开发环境的容器分配
def DevContainerGet():
    '''分配开发态容器'''
    deploy_targets = _conf['deploy_targets']
    listfile = ".store/dev_jupyters.json"
    if os.path.exists(listfile):
        with open(listfile, "r") as fi:
            cons = json.load(fi)
    else:
        cons = {}
    jsons = request.stream.read().decode('utf-8')
    log.info("...enter GetDevContainer...[%s]" % jsons)
    jo = json.loads(jsons)['ROOT']
    user = jo['user']
    containername = jo.get("containername", None)
    is_create = True
    if containername is not None: # 启动已有的容器
        con = cons.get(containername, None)
        if con is None:  #配置中不存在, 报错
            return _rjson({}, rcode=1005, rmsg="dev container not found")
        if con['containerid'] != '': # 暂时就不到远程主机上去检查了
            return _rjson({}, rcode=1009, rmsg="容器在启动状态 [%s] [%s]"%(con['host']['host'], con['containerid']))

        is_create = False
        res = con['resources']
        port = con["port"]
        imageName = con["imageName"]
        h = con['host']
        token = jo.get("token", "123")
    else: # 创建新的容器
        imageName = jo.get("imageName")
        token = jo.get("token", "123")
        containername = "%s-%d"%(user, time.time())  #Todo 需要考虑失败后的清理
        pypiserver = "172.18.231.68:1871"

        # 1. 选择主机,  规则是按数量来平均分配
        hosts = { h['host'] : 0 for h in deploy_targets }
        for v in cons.values():
            hosts[v['host']['host']] += 1
        h_c = sorted([ [k, v] for k, v in hosts.items()], key=operator.itemgetter(1))
        h = [ h for h in  deploy_targets if h['host']==h_c[0][0]] [0]
        print("---select host:", h['host'])

        # Done 设置资源限制
        res = jo.get("resources", {})

    host = h['host']
    mem = "%d" % (res.get("mem", 1024) * 1024 * 1024)
    cpu = "%d" % (res.get("cpu", 1))
    uri = "jupyter-%s" % __gen_rand_str()  # Done uri要随机生成

    with RemoteClient(h['host'], h['user'], h['id_file']) as r:
        # 2. 准备启动脚本
        if is_create:
            r.upload_file(".store/create_dev_container.sh", "dockers/devbase/create_dev_container.sh")
            sout = r.execute(f"/bin/bash ~/dockers/devbase/create_dev_container.sh {containername} 0 {pypiserver}" )
            port = [i.split()[1].strip() for i in sout.split("\n") if i.find("-port") >= 0][0]
            print("--- create %s/.jupyter/start.sh return port: [%s]" % (containername, port))

        #Done 3. 启动容器
        cmd = f"""docker run -d --rm -p {port}:{port} --user $(id -u):$(id -g) --workdir /data \\
  -v /mci/dockers/devbase/passwd:/etc/passwd:ro -v /mci/dockers/devbase/group:/etc/group:ro \\
  -v /mci/dockers/devbase/{containername}:/data \\
  --entrypoint /data/.jupyter/start.sh \\
  --memory {mem} --cpu-shares {cpu} {imageName} {uri} {token}"""
        print("--cmd", cmd)
        sout = r.execute(cmd)
        containerid = sout.strip()[:12]
        print("---containerid:", containerid)

    #Done 4. 注册url
    real_url = f"{host}:{port}"
    data = {"ROOT": {"uri": uri, "url": real_url, "type":"jupyter"}}
    r = requests.post(rest_register_url, data=json.dumps(data),
                      headers={"Content-Type": "application/json; charset=utf8"})
    print("--restadd", data, "return", r.status_code, r.text)
    baseurl = rest_register_url[:rest_register_url.find('/', 8)]
    visit_url = f"{baseurl}/{uri}/?token={token}"

    #Done 5. 保存记录
    cons[containername] = {"name":containername, "cmd":cmd, "host": h,
                           "containerid":containerid, "uri":uri, "user":user,
                           "url":visit_url, "resources":res, "port": port, "imageName":imageName}
    with open(listfile, "w", encoding="utf-8") as fo:
        json.dump(cons, fo)

    return _rjson({"url": visit_url,
                   "containerid": containerid,
                   "name": containername})

def DevContainerQry():
    '''分配开发态容器'''
    listfile = ".store/dev_jupyters.json"
    if os.path.exists(listfile):
        with open(listfile, "r") as fi:
            cons = json.load(fi)
    else:
        cons = {}
    jsons = request.stream.read().decode('utf-8')
    log.info("...enter GetDevContainer...[%s]" % jsons)
    jo = json.loads(jsons)['ROOT']
    user = jo['user']
    result = []
    for con in cons.values():
        if con['user'] != user:
            continue
        result.append({"name":con['name'], "id":con['containerid'], "url":con['url'],
                       "host":con['host']['host'], "resources":con['resources']})
    return _rjson({"containers": result })

def DevContainerDel():
    '''分配开发态容器'''
    listfile = ".store/dev_jupyters.json"
    if os.path.exists(listfile):
        with open(listfile, "r") as fi:
            cons = json.load(fi)
    else:
        return _rjson(None, 404, "no container found!")

    jsons = request.stream.read().decode('utf-8')
    log.info("...enter GetDevContainer...[%s]" % jsons)
    jo = json.loads(jsons)['ROOT']
    user = jo['user']
    containername = jo['containername']
    keepuserdir = jo.get("keepuserdir", False)

    if containername not in cons:
        return _rjson(None, 405, "no container [%s] found!" % containername)
    co = cons.get(containername)
    if co['user'] != user:
        return _rjson(None, 406, "user not match!")

    #Done 删除远端容器
    h = co['host']
    with RemoteClient(h['host'], h['user'], h['id_file']) as r:
        if co.get("containerid", "") != "":
            sout = r.execute("docker kill %s" % co['containerid'])
            log.info("删除容器 %s return [%s]", co['containerid'], sout)

        #Done 删除用户目录
        if not keepuserdir:
            sout = r.execute(f"rm -rf /mci/dockers/devbase/{containername}")
            log.info("删除用户目录 %s return [%s]",containername, sout)

    #Done 删除 nginx注册
    if co.get("uri", "") != "":
        reg_url = rest_register_url.replace("/restadd", "/restdel")
        r = requests.post(reg_url, data='{"ROOT":{"uri":"%s"}}'%co['uri'],
                          headers={'Content-type': 'application/json;charset=utf-8'})
        log.debug("删除nginx 注册 [%s] return http_status: %d", co['uri'], r.status_code)

    #Done 删除文件中的项
    if keepuserdir:
        co['uri'] = ''
        co['containerid'] = '' # 用于标记该容器是停止状态
    else:
        del cons[containername]
    with open(listfile, "w", encoding="utf-8") as fo: # 保存文件
        json.dump(cons, fo)
    return _rjson({}, 0, "remove container completed")
