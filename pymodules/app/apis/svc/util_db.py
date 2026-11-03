import json
import logging as log
import traceback
import random
import time

from loguru import logger

from svc.util.db_base import dbfunc1, pool_init

'''
开发容器数据持久化到mysql中

数据表:
create table mng_data(
   ckey  varchar(64) primary key,
   ctype varchar(32) not null,
   p1 varchar(32) null,
   p2 varchar(32) null,
   content varchar(4096),
   op_time datetime
 );
 create index _mng_idx on mng_data(ctype);

 create table mng_data_his(
   ckey  varchar(64) not null,
   ctype varchar(32) not null,
   p1 varchar(32) null,
   p2 varchar(32) null,
   content varchar(4096),
   op_time datetime,
   primary key (ckey, op_time)
 );

ctype取值:
  开发容器 devc,   p1=user
'''

db_get_conn = None


def _init_(conf):
    global db_get_conn
    db_get_conn = conf.get('db_get_conn', None)
    log.info("util_db._init_, %s", db_get_conn)


def dbfunc(func):
    def inner(*args, **kwargs):
        db = db_get_conn()
        cur = db.cursor()
        try:
            return func(cur, *args, **kwargs)
        except Exception as x:
            log.error("db oper failed %s" % x)
            db.rollback()
            return None
        finally:
            cur.close()
            db.commit()
            db.close()
        # print("I can decorate any function")
        # return func(*args, **kwargs)

    return inner


@dbfunc1
def train_cont_find_by_exp(_db, _cur, expe_name, run_id):
    '通过实验信息, 找到训练容器'
    _cur.execute("select container_name, ip_address, control_port, service_port, op_note"
                 " from dev_container_pool "
                 "where is_valid='Y' and is_use='Y' and container_type='04'")

    for row in _cur.fetchall():
        r = [f.decode('utf8') if type(f) == bytearray else f for f in row]
        container_name, ip_address, control_port, service_port, op_note = r
        try:
            opt = json.loads(op_note)
        except:
            continue
        if opt['expe_name'] == expe_name and opt['run_id'] == run_id:
            return r
    return None


@dbfunc1
def train_pick_cont(_db, _cur, user_name, expe_name, run_id, task_type='nni', task_name=None):
    '选择一个训练容器'
    _cur.execute("select container_name, ip_address, control_port, service_port"
                 " from dev_container_pool "
                 "where is_valid='Y' and is_use='N' and container_type='04'")
    rows = _cur.fetchall()
    if len(rows) == 0:
        return None
    for row in rows:
        row = [f.decode('utf8') if type(f) == bytearray else f for f in row]

        cont_name, ip_addr, ctl_port, svc_port = row
        _cur.execute("update dev_container_pool set is_use='Y', op_note=%s, op_time=now() "
                     "where container_name=%s and is_use='N'",
                     [json.dumps(dict(user=user_name, expe_name=expe_name, run_id=run_id, task_type=task_type,
                                      task_name=task_name)),
                      cont_name])
        if _cur.rowcount < 1:
            logger.error("select training container failed: running race.")
            continue
        _cur.execute("INSERT INTO dev_container_pool_log "
                     "(LOG_ID, CONTAINER_NAME, TENANT_ID, PROJ_NAME, USER_ID, "
                     "EXPE_NAME, RUN_ID, BEGIN_TIME, END_TIME, CREATE_ID, "
                     "CREATE_TIME, OP_ID, OP_TIME, OP_NOTE) "
                     "VALUES (%s, %s, '1', '1', %s, "
                     "%s, %s, now(), NULL, '1', "
                     "now(), %s, now(), %s)",
                     [str(int(time.time() * 1000)), cont_name, user_name,
                      expe_name, run_id,
                      user_name, 'begin a training task'])
        logger.info(f"insert dev_container_pool_log begin, cont: {cont_name}, return {_cur.rowcount}")
        _cur.execute("update dev_experiment set container_name=%s where expe_name=%s", [cont_name, expe_name])
        return row


@dbfunc1
def train_finish(_db, _cur, cont_name, op_note):
    _cur.execute("select container_name, op_note, op_time"
                 " from dev_container_pool "
                 "where container_name=%s and container_type='04'", [cont_name, ])
    row = _cur.fetchone()
    if row is None:
        logger.warning(f"train container {cont_name} not found!!!")
        return False
    _cur.execute("update dev_container_pool set is_use='N', op_note=%s, op_time=now() "
                 "where container_name=%s",
                 ["", cont_name])

    j = json.loads(row[1])
    user_name, expe_name, run_id = j['user'], j['expe_name'], j['run_id']
    begin_time = row[2]
    if _cur.rowcount < 1:
        logger.warning("finish train container failed: update fail.")
        return False
    _cur.execute(
        "delete from dev_container_pool_log where CONTAINER_NAME=%s and user_id=%s and expe_name=%s and run_id=%s",
        [cont_name, user_name,
         expe_name, run_id])  # 删除开始那一条
    _cur.execute("INSERT INTO dev_container_pool_log "
                 "(LOG_ID, CONTAINER_NAME, TENANT_ID, PROJ_NAME, USER_ID, "
                 "EXPE_NAME, RUN_ID, BEGIN_TIME, END_TIME, CREATE_ID, "
                 "CREATE_TIME, OP_ID, OP_TIME, OP_NOTE) "
                 "VALUES (%s, %s, '1', '1', %s, "
                 "%s, %s, %s, now(), '1', "
                 "now(), %s, now(), %s)",
                 [str(int(time.time() * 1000)), cont_name, user_name,
                  expe_name, run_id, begin_time,
                  user_name, f"task finished: {op_note}"])
    logger.info(f"insert dev_container_pool_log finish, cont: {cont_name}, return {_cur.rowcount}")
    return True


# 选择部署容器
@dbfunc1
def deploy_pick_cont(_db, _cur, user_name, expe_name, run_id):
    '选择一个训练容器'
    _cur.execute("select container_name, ip_address, control_port, service_port"
                 " from dev_container_pool "
                 "where is_valid='Y' and is_use='N' and container_type='05'")
    rows = _cur.fetchall()
    if len(rows) == 0:
        return None
    for row in rows:
        cont_name, ip_addr, ctl_port, svc_port = row
        # 选择部署容器后，不需要更新is_use=1,因为同一个部署容器可以部署多次
        # _cur.execute("update dev_container_pool set is_use='1', op_note=%s, op_time=now() "
        #              "where container_name=%s and is_use='0'",
        #              [json.dumps(dict(user=user_name, expe_name=expe_name, run_id=run_id)), cont_name])
        if _cur.rowcount < 1:
            logger.warning("select deploying container failed: running race.")
            continue
        _cur.execute("INSERT INTO dev_container_pool_log "
                     "(LOG_ID, CONTAINER_NAME, TENANT_ID, PROJ_NAME, USER_ID, "
                     "EXPE_NAME, RUN_ID, BEGIN_TIME, END_TIME, CREATE_ID, "
                     "CREATE_TIME, OP_ID, OP_TIME, OP_NOTE) "
                     "VALUES (%s, %s, '1', '1', %s, "
                     "%s, %s, now(), NULL, '1', "
                     "now(), %s, now(), %s)",
                     [str(int(time.time() * 1000)), cont_name, user_name,
                      expe_name, run_id,
                      user_name, 'begin a deploying task'])
        logger.info(f"insert dev_container_pool_log begin, cont: {cont_name}, return {_cur.rowcount}")
        return [cont_name, ip_addr, ctl_port, svc_port]


# 选择部署容器
@dbfunc1
def test_pick_cont(_db, _cur):
    '自助建模 -- 选择一个测试容器'
    logger.info("----------------test_pick_cont")
    _cur.execute("select container_name, ip_address, control_port, service_port"
                 " from dev_container_pool "
                 "where is_valid='Y' and is_use='N' and container_type='05'")
    rows = _cur.fetchall()
    logger.info("---test container count: {}", len(rows))
    if len(rows) == 0:
        return None
    import random
    random.seed(time.time())
    r = random.choice(rows)
    r = [f.decode('utf8') if type(f) == bytearray else f for f in r]
    return r


# 选择部署容器
@dbfunc1
def test_pick_cont_type(_db, _cur, cont_type):
    '自助建模 -- 选择一个测试容器'
    logger.info("----------------test_pick_cont")
    _cur.execute("select container_name, ip_address, control_port, service_port"
                 " from dev_container_pool "
                 "where is_valid='Y' and is_use='N' and container_type=%s", [cont_type, ])
    rows = _cur.fetchall()
    logger.info("---test container count: {}", len(rows))
    if len(rows) == 0:
        return None
    import random
    random.seed(time.time())
    r = random.choice(rows)
    r = [f.decode('utf8') if type(f) == bytearray else f for f in r]
    return r


# def timediff_mi(tmdiff_ret):
#     '将 mysql timediff 函数返回的数据格式 H:Mi:S 转换为 以 minutes 为单位的 整数'
#     k = tmdiff_ret.strip().split(":")
#     d = int(k[0]) * 60 + int(k[1])
#     return d

class NNI_RESULT:
    @staticmethod
    @dbfunc1
    def in_db(_db, _cur, r1, r2):
        'nni 训练结果入库的sqli执行, 由 svc_task.nni_result2db 调用'

        # 先清理数据, 避免重复跑同一个实验导致主键冲突
        run_id, exp_name = r1[:2]
        _cur.execute('delete from dev_run_result where RUN_ID=%s and EXPE_NAME=%s', [run_id, exp_name])
        _cur.execute('delete from dev_run_trial_result where RUN_ID=%s and EXPE_NAME=%s', [run_id, exp_name])

        sql = '''
        INSERT INTO dev_run_result(RUN_ID, EXPE_NAME, WF_ID, LOG_DIR, BAK1,
        BAK2, CREATE_ID, CREATE_TIME, OP_ID, OP_TIME,
        OP_NOTE) 
        values(
            %s, %s, 'nni-task', %s, null,
            null, %s, %s, 'system', now(), 
            'model export')'''
        _cur.execute(sql, r1)

        sql = '''INSERT INTO dev_run_trial_result (
            RUN_ID, EXPE_NAME, TRIAL_ID, WF_ID, LOG_DIR, 
            METRIC_INFO, BEGIN_TIME, END_TIME, DEPLOY_STATUS, DEPLOY_NOTE, 
            TRIAL_STATUS, BAK1, BAK2, CREATE_ID, CREATE_TIME, 
            OP_ID, OP_TIME, OP_NOTE) 
        VALUES (
            %s, %s, %s, 'nni-task', %s, 
            %s, %s, %s, null, null, 
            %s, %s, null, %s, %s, 
            'system', now(), 'result_export')'''

        for r in r2:
            _cur.execute(sql, r)

    @staticmethod
    @dbfunc1
    def update_deploy_status(_db, _cur, exp_name, run_id, trial_id, status, url=None):
        '更新 trial 的部署情况'
        # select * from c_code_enum where code_id='DEPLOY_STATUS';
        _cur.execute('''update dev_run_trial_result set deploy_status=%s, deploy_note=%s
            where run_id=%s and expe_name=%s and trial_id=%s''',
                     [status, url, run_id, exp_name, trial_id])
        if _cur.rowcount <= 0:
            return False
        return True

    @staticmethod
    @dbfunc1
    def get_deploy_filepath(_db, _cur, exp_name, run_id, trial_id):
        '更新 trial 的部署情况'
        # select * from c_code_enum where code_id='DEPLOY_STATUS';
        _cur.execute('''select log_dir from dev_run_trial_result where run_id=%s and expe_name=%s and trial_id=%s''',
                     [run_id, exp_name, trial_id])
        result = _cur.fetchone()
        if result:
            return result[0]
        else:
            return None

    @staticmethod
    @dbfunc1
    def get_container_addr(_db, _cur, cont_name):
        _cur.execute("select ip_address, control_port, service_port"
                     " from dev_container_pool "
                     "where CONTAINER_NAME=%s and is_valid='Y'", [cont_name, ])
        rows = _cur.fetchall()
        if len(rows) == 0:
            return None
        row = rows[0]
        return [f.decode('utf8') if type(f) == bytearray else f for f in row]

    @staticmethod
    @dbfunc1
    def get_traintask_status(_db, _cur, expe_name, run_id):
        '查询自动化学习的运行 状态，  返回 status--运行状态 01-在运行 02-结束； container_name--如果在运行，则是其训练容器'
        _cur.execute("select a.run_status, b.container_name from dev_run a, dev_experiment b "
                     "where a.expe_name=%s and a.run_id=%s and a.expe_name=b.expe_name", [expe_name, run_id])
        rows = _cur.fetchall()
        if len(rows) == 0:
            return None, None
        row = rows[0]
        return [f.decode('utf8') if type(f) == bytearray else f for f in row]


class AML:
    @staticmethod
    @dbfunc1
    def get_task_name(_db, _cur, expe_name):
        _cur.execute('''select pre_model_id from dev_expe_auto_learning 
         where expe_name=%s ''', [expe_name, ])
        rows = _cur.fetchall()
        if len(rows) == 0:
            return None
        row = rows[0]
        return [f.decode('utf8') if type(f) == bytearray else f for f in row][0]


class ContainerPool:
    '容器池相关表操作'

    @staticmethod
    @dbfunc1
    def list_idle(_db, _cur):
        '列出闲置容器'
        _cur.execute("""select container_name, ip_address, control_port, service_port, container_type
                 from dev_container_pool 
                 where is_valid='Y' and is_use='N'""")
        rows = _cur.fetchall()
        return [[f.decode('utf8') if type(f) == bytearray else f for f in row] for row in rows]

    # @staticmethod
    # @dbfunc1
    # def create_container_res(_db, _cur, res_list):
    #     '记录容器资源'
    #     _cur.execute("INSERT INTO dev_container_res (cpu_total, cpu_used, mem_total, mem_used, "
    #                  "disk_total, disk_used, gpu_total, gpu_used, create_time"
    #                  "VALUES (%s, %s, %s, %s,"
    #                  "%s, %s, %s, %s, now(),"
    #                  [res_list['cpu_total'], res_list['cpu_used'], res_list['mem_total'], res_list['mem_used'],
    #                  res_list['disk_total'], res_list['disk_used'], res_list['gpu_total'], res_list['gpu_used']])
    #     log.info("insert dev_container_res begin, return {}", _cur.rowcount)
    #     return


class DevRun:
    @staticmethod
    @dbfunc1
    def get_task_status(_db, _cur, expe_name, run_id):
        '查询自动化学习的运行 状态，  返回 status--运行状态 01-在运行 02-结束； container_name--如果在运行，则是其训练容器'
        _cur.execute("""select a.run_status, a.op_note from dev_run a 
                     where a.expe_name=%s and a.run_id=%s""", [expe_name, run_id])
        rows = _cur.fetchall()
        if len(rows) == 0:
            return None, None
        row = rows[0]
        return [f.decode('utf8') if type(f) == bytearray else f for f in row]

    @staticmethod
    @dbfunc1
    def update_task_status(_db, _cur, expe_name, run_id, run_status, op_note):
        _cur.execute("""update dev_run set run_status=%s, op_note=%s, op_time=now()
                             where expe_name=%s and run_id=%s""",
                     [run_status, op_note, expe_name, run_id])
        return _cur.rowcount > 0


class RefluxDO:
    # 北京联通 数据回流任务 相关的数据库更新相关函数
    @staticmethod
    @dbfunc1
    def qry_id(_db, _cur, dataset_id):
        '通过datasetid, 查询到回流任务的信息: 数据集路径等'
        err_ret = None  # reflux_run_id, dataset_name, store_path, reflux_id, label_id, labels
        _cur.execute('''select reflux_run_id, dataset_name, store_info from t_dataset
            where dataset_id=%s''',
                     [dataset_id, ])
        rows = _cur.fetchall()
        if len(rows) == 0:
            return err_ret
        row = [f.decode('utf8') if type(f) == bytearray else f for f in rows[0]]
        try:
            j = json.loads(row[2])  # 获取其存储路径
            row[2] = j['path']
        except:
            logger.error("store_info field data error {}", traceback.format_exc())
            return err_ret
        reflux_run_id, dataset_name, store_path = row

        _cur.execute('''select a.reflux_id, a.label_id from dev_reflux a, dev_reflux_run b 
 where b.run_id=%s and a.reflux_id=b.reflux_id ''', [reflux_run_id, ])
        rows = _cur.fetchall()
        if len(rows) < 1:
            return None
        reflux_id, label_id = [f.decode('utf8') if type(f) == bytearray else f for f in rows[0]]

        _cur.execute('''select label_id, label_name from c_label where parent_label_id=%s''', [label_id, ])
        rows = _cur.fetchall()
        if len(rows) < 1:
            return None
        labels = {}
        for l_id, l_name in rows:
            # l_id, l_name = l_id.decode('utf8'), l_name.decode('utf8')
            labels[l_id] = l_name

        return reflux_run_id, dataset_name, store_path, label_id, labels

    @staticmethod
    @dbfunc1
    def update_status(_db, _cur, dataset_id, reflux_run_id, status=1):
        '更新回流任务的文件更新状态,  status: 1-成功, 2-失败'
        _cur.execute('''update t_dataset set FINISH_STATUS=%s
                    where dataset_id=%s''',
                     ['312' if status == 1 else '313',
                      dataset_id])  # select * from c_code_enum where c_code_enum='FINISH_STATUS'
        if _cur.rowcount <= 0:
            return False
        _cur.execute('''update dev_reflux_run set RUN_STATUS=%s, END_TIME=now() 
                            where run_id=%s''',
                     ['02' if status == 1 else '03',
                      reflux_run_id])  # select * from c_code_enum where c_code_enum='FINISH_STATUS'
        if _cur.rowcount <= 0:
            return False
        return True


class K8sInfo:
    @staticmethod
    @dbfunc1
    def get_k8s_info(_db, _cur, scene_type=None, calc_type=None):
        '''获取对应kubeconfig文件目录以及命名空间名称 '''
        if scene_type and calc_type:
            _cur.execute(
                "select namespace, k8s_config_file_name, k8s_config_file_path, storage_class_name, scene_type, calc_type from c_res_space where SCENE_TYPE=%s and CALC_TYPE=%s",
                [
                    scene_type, calc_type])
        else:
            # 获取所有的命名空间的信息
            _cur.execute(
                "select namespace, k8s_config_file_name, k8s_config_file_path, storage_class_name, scene_type, calc_type from c_res_space group by calc_type, scene_type", )

        rows = _cur.fetchall()
        if len(rows) == 0:
            return None

        return rows

    @staticmethod
    @dbfunc1
    def get_k8s_info_by_namespace(_db, _cur, namespace=None):
        '''获取对应kubeconfig文件目录以及命名空间名称 '''
        if namespace:
            _cur.execute(
                "select namespace, k8s_config_file_name, k8s_config_file_path, storage_class_name from c_res_space where namespace=%s group by namespace",
                [namespace, namespace])
        else:
            _cur.execute(
                "select namespace, k8s_config_file_name, k8s_config_file_path, storage_class_name from c_res_space group by namespace",
                [namespace, namespace])
        rows = _cur.fetchall()
        if len(rows) == 0:
            return None

        return rows

    @staticmethod
    @dbfunc1
    def get_k8s_train_info(_db, _cur, scene_type):
        '''
            获取训练集群的k8s相关信息
        '''
        _cur.execute(
            "select NAMESPACE, K8S_CONFIG_FILE_NAME, K8S_CONFIG_FILE_PATH from c_res_space where SCENE_TYPE=%s group BY CALC_TYPE, SCENE_TYPE",
            [scene_type])
        rows = _cur.fetchall()
        if len(rows) == 0:
            return None
        return rows


@dbfunc1
def train_task_list(_db, _cur):
    '扫表， 检查自助建模的任务状态， 以便正常结束后自动触发后续动作'
    # timediff return 'H:Mi:S',  返回类型为 datetime.timedelta
    _cur.execute('''select a.run_id, a.expe_name, a.run_status, timediff(now(),a.begin_time), a.op_time,
                 c.op_note, b.container_name, a.create_id, a.ext_info
        from dev_run a, dev_experiment b, dev_container_pool c
                where a.expe_name=b.expe_name and b.container_name=c.container_name and a.run_status='01' ''')
    ret = []
    for row in _cur.fetchall():
        row = [f.decode('utf8') if type(f) == bytearray else f for f in row]  # 得手工转换编码， 返回的varchar是 bytearray
        run_id, expe_name, run_status, begin_time, op_time, \
        op_note, container_name, user_name, ext_info = row
        tt = begin_time.days * 24 * 60 + int(begin_time.seconds / 60)  # 转换为 分钟
        # logger.info("--- container_name=[{}]", container_name)
        _cur.execute("select ip_address, control_port, service_port "
                     "from dev_container_pool where container_name=%s", [container_name, ])
        r = _cur.fetchone()
        if r is None:
            logger.error(f"container {container_name}  of train task {expe_name}-{run_id} not found record")
            continue
        ip_addr, ctl_port, svc_port = r
        # ip_addr = ip_addr.decode('utf8')
        ret.append([expe_name, run_id, tt, op_time, op_note,
                    container_name, ip_addr, ctl_port, svc_port, user_name, ext_info])
    return ret


@dbfunc1
def train_task_ck(_db, _cur):
    # 取占用中的 训练容器, 检查端口, 判断是否需要清理
    _cur.execute("select container_name, ip_address, control_port, service_port, op_note, timediff(now(),op_time) "
                 "from dev_container_pool where container_type='04' and is_valid='Y' and is_use='Y'")
    ret = []
    for r in _cur.fetchall():
        container_name, ip_address, control_port, service_port, op_note, op_time = r
        tt = op_time.days * 24 * 60 + int(op_time.seconds / 60)
        try:
            # {"user": "iasp", "expe_name": "DE1635832312863422610001", "run_id": "RUN_1", "task_type": "nni", "task_name": "ofa"}
            j = json.loads(op_note)
            expe_name = j['expe_name']
            run_id = j['run_id']
            user_name = j['user']
        except Exception as x:
            expe_name, run_id, user_name = None, None, None
        ret.append([expe_name, run_id, tt, op_time, op_note,
                    container_name, ip_addr, ctl_port, svc_port, user_name, None])
    return ret


@dbfunc1
def train_task_update(_db, _cur, expe_name, run_id, op_note,
                      run_status=None, deploy_container=None, train_container=None, user_name=None, ext_info=None):
    "更新 自助建模任务的状态, run_status 定义： select * from c_code_enum where code_id='RUN_STATUS';"
    if run_status is None:
        _cur.execute("update dev_run set op_note=%s, op_time=now() where run_id=%s and expe_name=%s",
                     [op_note, run_id, expe_name])
    else:
        if run_status in ('02', '03', '04'):
            _cur.execute("update dev_run set op_note=%s, run_status=%s, op_time=now(), end_time=now()"
                         " where run_id=%s and expe_name=%s",
                         [op_note, run_status, run_id, expe_name])
        elif run_status == '01':
            _cur.execute(
                "update dev_run set op_note=%s, run_status=%s, op_time=now(), begin_time=now(), wf_id='nni-task', ext_info=%s where run_id=%s and expe_name=%s",
                [op_note, run_status, ext_info, run_id, expe_name])
        else:
            _cur.execute("update dev_run set op_note=%s, run_status=%s, op_time=now() where run_id=%s and expe_name=%s",
                         [op_note, run_status, run_id, expe_name])
    logger.info(f"train_task_update dev_run update return rowcount={_cur.rowcount}")
    if _cur.rowcount < 1:
        _cur.execute("INSERT INTO dev_run (RUN_ID, EXPE_NAME, PROJ_NAME, WF_ID, DATASET_ID,"
                     " EXT_INFO, RUN_STATUS, BEGIN_TIME, END_TIME, CREATE_ID, "
                     "CREATE_TIME, OP_ID, OP_TIME, OP_NOTE) "
                     "VALUES (%s, %s, '-proj', 'nni-task', '-', "
                     "'{}', '01', now(), NULL, %s,"
                     " now(), '-', now(), %s)",
                     [run_id, expe_name, user_name, op_note])
    if deploy_container is not None:
        _cur.execute("update dev_experiment set container_name=%s where expe_name=%s",
                     [deploy_container, expe_name])
        logger.info(f"update dev_experiment return rowcount={_cur.rowcount}")
    elif train_container is not None:
        _cur.execute("update dev_experiment set container_name=%s where expe_name=%s",
                     [train_container, expe_name])
        logger.info(f"update dev_experiment return rowcount={_cur.rowcount}")
    return True


# 更新CONTAINER_NAME入表dev_experiment
@dbfunc1
def update_conName_to_devExperiment(_db, _cur, cont_name, EXPE_NAME):
    _cur.execute("update dev_experiment set CONTAINER_NAME=%s, op_time=now() "
                 "where EXPE_NAME=%s",
                 [cont_name, EXPE_NAME])
    if _cur.rowcount < 1:
        log.warning("update conName to devExperiment failed: record not found %s %s.", cont_name, EXPE_NAME)
        return False
    return True


@dbfunc1
def devc_update_devRun_nodeStatus(_db, _cur, status, run_id, expe_name, op_note=""):
    if op_note == "":
        _cur.execute("update dev_run set RUN_STATUS=%s, END_TIME=now(),op_time=now() where RUN_ID=%s and EXPE_NAME=%s",
                     [status, run_id, expe_name])
        logger.info(
            f"update dev_run set RUN_STATUS={status}, END_TIME=now(),op_time=now() where RUN_ID={run_id} and EXPE_NAME={expe_name}")
    else:
        _cur.execute(
            "update dev_run set RUN_STATUS=%s, END_TIME=now(),op_note=%s ,op_time=now() where RUN_ID=%s and EXPE_NAME=%s",
            [status, op_note, run_id, expe_name])
        logger.info(
            "update dev_run set RUN_STATUS={status}, END_TIME=now(),op_note={op_note} ,op_time=now() where RUN_ID={run_id} and EXPE_NAME={expe_name}")
    if _cur.rowcount < 1:
        return False
    return True


# nni预处理阶段，日志信息，不再更新至表dev_run字段op_note了，转而更新至表 dev_run_note 的 BAK_1 中
@dbfunc1
def update_opNote_to_devRun(_db, _cur, op_note, run_id, expe_name):
    _cur.execute("update dev_run set OP_NOTE=%s, OP_TIME=now() "
                 "where RUN_ID=%s and EXPE_NAME=%s",
                 [op_note, run_id, expe_name])
    if _cur.rowcount < 1:
        log.warning("update op_note to devRun failed: update fail.")
        return False
    return True


# nni进程启动前, 训练任务的前置处理过程由服务记录到表 dev_run_note 的 BAK_1 中
@dbfunc1
def update_bak1_to_devRunNode(_db, _cur, bak_1, run_id, expe_name):
    _cur.execute("update dev_run_node set BAK_1=%s, OP_TIME=now() "
                 "where RUN_ID=%s and EXPE_NAME=%s",
                 [bak_1, run_id, expe_name])
    if _cur.rowcount < 1:
        log.warning("update bak_1 to devRunNode failed: update fail.")
        return False
    return True


# nni进程启动前, 训练任务的前置处理过程由服务记录到表 dev_run_note 的 BAK_1 中
@dbfunc1
def update_bak1_to_devRunNode_(_db, _cur, msg, run_id, exp_name, init=False):
    if msg.find('\n') >= 0:
        msg = msg.replace('\n', '')
    _cur.execute("select bak_1 from dev_run_node where RUN_ID=%s and EXPE_NAME=%s", [run_id, exp_name])
    r = _cur.fetchall()
    if r is None or len(r) == 0:
        # 记录不存在， 添加一个
        _cur.execute('''INSERT INTO dev_run_node (RUN_ID, EXPE_NAME, NODE_ID, CONTAINER_NAME, RUN_STATUS, BEGIN_TIME, END_TIME, ERROR_MSG,
 WF_ID, WF_NODE_ID, CREATE_ID, CREATE_TIME, OP_ID, OP_TIME, OP_NOTE, BAK_1, BAK_2) 
 VALUES (%s, %s, '-1', '-1', '02', now(), NULL, NULL, NULL, NULL, '--', now(), '--', now(), '--', NULL, NULL)''',
                     [run_id, exp_name])
        rmsg = ''
    else:
        rtxt = r[0][0]
        if rtxt is None:
            rtxt = b''
        rmsg = rtxt  # .decode('utf-8')
    if init:
        rmsg = f"{time.strftime('%H:%M:%S')} - {msg}"
    else:
        rmsg = f"{rmsg}\n{time.strftime('%H:%M:%S')} - {msg}"
    if len(rmsg) > 2048:
        rmsg = rmsg[:2047]
    _cur.execute("update dev_run_node set BAK_1=%s, OP_TIME=now() "
                 "where RUN_ID=%s and EXPE_NAME=%s",
                 [rmsg, run_id, exp_name])
    if _cur.rowcount < 1:
        log.warning("update bak_1 to devRunNode failed: update fail.")
        return False
    return True


# # 根据cont_name获取cont_addr，
@dbfunc1
def get_contAddr_from_contName(_db, _cur, cont_name):
    '选择一个训练容器'
    _cur.execute("select ip_address, control_port"
                 " from dev_container_pool "
                 "where CONTAINER_NAME=%s", [cont_name])
    rows = _cur.fetchall()
    if len(rows) == 0:
        return None
    for row in rows:
        ip_addr, ctl_port = row
    return [ip_addr, ctl_port]


if __name__ == '__main__':
    conn_param = dict(user='iasp_119', password='iasp_119',
                      host='172.18.233.159', port=8066, database='iasp_dev_119')
    pool_init(conn_param)

    ret = train_pick_cont('guanyf', 'expe1', 'RUN_1')
    if ret is not None:
        cont_name, ip_addr, ctl_port, svc_port = ret
        log.info("cont_name: {}, ip: {}", cont_name, ip_addr)
        ret = train_finish(cont_name, 'end failed haha')
        log.info("finish return {}", ret)


# 容器类型  01: Jupyter容器, 02: 计算容器, 03: 自动化学习容器
@dbfunc1
def devc_get_v119(_db, _cur, con_name):
    '根据容器名称, 查询容器数据'
    _cur.execute("select CONTAINER_INFO from dev_container where CONTAINER_NAME=%s", [con_name])
    r = _cur.fetchone()
    if r is None:
        return None
    if r[0] is None:
        return {"depName": con_name, "pvcName": con_name, "serviceName": con_name}
    return json.loads(r[0])


@dbfunc1
def devc_get_status(_db, _cur, con_name):
    '根据容器名称, 查询容器状态'
    _cur.execute("select CONTAINER_STATUS from dev_container where CONTAINER_NAME=%s", [con_name])
    r = _cur.fetchone()
    if r[0] is None:
        return None
    ret = r[0]
    if type(ret) == bytearray:
        ret = ret.decode('utf-8')
    return ret


@dbfunc1
def devc_get_by_user(_db, _cur, con_type, user):
    _cur.execute("select content from dev_container where CONTAINER_TYPE=%s and OP_ID=%s", [con_type, user])
    return [json.loads(r[0]) for r in _cur.fetchall()]


@dbfunc1
def devc_get_all(_db, _cur, con_type):
    _cur.execute("select content from dev_container where CONTAINER_TYPE=%s", [con_type])
    return _cur.fetchall()


#    return [json.loads(r[0]) for r in _cur.fetchall()]

# 查询所有正在运行的容器信息
# 容器状态  01: 正在启动, 02: 运行中, 03: 正在关闭, 04: 已关闭, 05: 关闭失败, 06: 启动失败, 97: 正在销毁, 98: 销毁失败, 99: 已销毁
@dbfunc1
def devc_get_all_container_info(_db, _cur):
    con_info_list = []
    '根据容器名称, 查询容器数据'
    _cur.execute("select CONTAINER_INFO, container_name from dev_container where CONTAINER_STATUS='02'")
    for r in _cur.fetchall():
        if r[0] is None:
            continue
        else:
            r1 = r[1]
            if type(r1) == bytearray:
                r1 = r1.decode('utf-8')
            con_info_list.append((json.loads(r[0]), r1))
    return con_info_list


#    return [r[0].decode('utf-8') for r in _cur.fetchall()]


@dbfunc1
def devc_new(_db, _cur, con_name, con_type, user, obj):
    '新增开发容器记录'
    _cur.execute(
        "insert into dev_container(CONTAINER_NAME, CONTAINER_TYPE, OP_ID, CONTAINER_INFO, op_time) values(%s, %s, %s, %s, now())",
        [con_name, con_type, user, json.dumps(obj)])
    if _cur.rowcount < 1:
        return False
    return True


@dbfunc1
def devc_update_v119(_co, _cur, con_name, obj, con_status, percent):
    '更新开发容器记录'
    # _cur.execute("insert into mng_data_his(ckey, ctype, p1, p2, content, op_time) "
    #              "select ckey, ctype,  p1, p2, content, op_time from mng_data where ckey=%s and ctype=%s",
    #              [con_name, "devc"])  # 插入历史表
    if con_status is None and percent is None:
        _cur.execute("update dev_container set CONTAINER_INFO=%s, op_time=now() where CONTAINER_NAME=%s",
                     [json.dumps(obj), con_name])
    elif con_status is not None and percent is None:
        if con_status == '02':
            _cur.execute(
                "update dev_container set CONTAINER_INFO=%s, is_valid='Y', CONTAINER_STATUS=%s,op_time=now() where CONTAINER_NAME=%s",
                [json.dumps(obj), con_status, con_name])
        else:
            _cur.execute(
                "update dev_container set CONTAINER_INFO=%s, CONTAINER_STATUS=%s,op_time=now() where CONTAINER_NAME=%s",
                [json.dumps(obj), con_status, con_name])
        # if con_status == '02':
        #     _cur.execute("update dev_container set is_valid='Y' where CONTAINER_NAME=%s", [con_name, ])
    elif con_status is not None and percent is not None:
        _cur.execute(
            "update dev_container set CONTAINER_INFO=%s, CONTAINER_STATUS=%s,STATUP_PERCENT=%s,op_time=now() where CONTAINER_NAME=%s",
            [json.dumps(obj), con_status, percent, con_name])
    if _cur.rowcount < 1:
        if con_status is None:
            con_status = '01'
        _cur.execute('''INSERT INTO dev_container (CONTAINER_NAME, PROJ_NAME, CONTAINER_DESC, CONTAINER_TYPE, RES_SPEC,
         RES_USE_ID, IMAGE_ID, is_private_image, CONTAINER_STATUS, CONTAINER_INFO, STATUP_PERCENT, 
         ERROR_MSG, PORT_MAPPING, IS_VALID, CREATE_ID, CREATE_TIME, OP_ID, OP_TIME, OP_NOTE)
          VALUES (%s, 'test-proj', 'jupyter', '01', 'c.1c.2m', 
          'IRU1619681081350754610001', 'jupyterlab:2', '', %s, %s, 0.00, 'test-note', NULL, 'N', 'test', now(), 
          'test', now(), 'makeup record by mgr')''',
                     [con_name, con_status, json.dumps(obj)])
    return True


# 镜像发布结果保存到数据库
@dbfunc1
def update_model_image(_co, _cur, MODEL_ID, MODEL_STATUS, ImageId, Role):
    # Done更新至数据中哪个表
    sql = '''update mm_model_version set OP_TIME=now(), MODEL_STATUS=%s, OP_NOTE=%s where MODEL_PK_ID=%s'''
    sql1 = '''update mm_model_image set OP_TIME=now(), PHY_IMAGE_ID=%s where MODEL_PK_ID=%s and IMAGE_ID=%s'''
    _cur.execute(sql, (MODEL_STATUS, '完成', MODEL_ID))
    _cur.execute(sql1, (ImageId, MODEL_ID, Role))


# 根据containername更新CONTAINER_INFO
@dbfunc1
def devc_update_containerinfo_v119(_db, _cur, con_name, obj):
    _cur.execute("update dev_container set CONTAINER_INFO=%s,op_time=now() where CONTAINER_NAME=%s",
                 [json.dumps(obj), con_name])
    if _cur.rowcount < 1:
        return False
    return True


# 创建容器失败时，更新错误信息至dev_container表，ERROR_MSG字段
@dbfunc1
def devc_update_errormsg(_db, _cur, con_name, error_msg):
    '更新开发容器记录'
    # _cur.execute("insert into mng_data_his(ckey, ctype, p1, p2, content, op_time) "
    #              "select ckey, ctype,  p1, p2, content, op_time from mng_data where ckey=%s and ctype=%s",
    #              [con_name, "devc"])  # 插入历史表
    _cur.execute("update dev_container set ERROR_MSG=%s,op_time=now() where CONTAINER_NAME=%s", [error_msg, con_name])
    if _cur.rowcount < 1:
        return False
    return True


@dbfunc1
def devc_update_status(_db, _cur, con_name, con_status, error_msg=None, op_id=None):
    '更新开发容器记录'
    # _cur.execute("insert into mng_data_his(ckey, ctype, p1, p2, content, op_time) "
    #              "select ckey, ctype,  p1, p2, content, op_time from mng_data where ckey=%s and ctype=%s",
    #              [con_name, "devc"])  # 插入历史表
    if error_msg is None:
        if op_id is None:
            _cur.execute("update dev_container set CONTAINER_STATUS=%s, op_time=now() where CONTAINER_NAME=%s",
                         [con_status, con_name])
        else:
            _cur.execute(
                "update dev_container set CONTAINER_STATUS=%s, op_time=now(), op_id=%s where CONTAINER_NAME=%s",
                [con_status, op_id, con_name])
    else:
        if op_id is None:
            _cur.execute(
                "update dev_container set CONTAINER_STATUS=%s, ERROR_MSG=%s,op_time=now() where CONTAINER_NAME=%s",
                [con_status, error_msg, con_name])
        else:
            _cur.execute(
                "update dev_container set CONTAINER_STATUS=%s, ERROR_MSG=%s,op_time=now(),op_id=%s where CONTAINER_NAME=%s",
                [con_status, error_msg, op_id, con_name])
    ret = _cur.rowcount > 0
    logger.warning("update dev_container {} to {}", con_name, con_status)
    return ret


@dbfunc1
def devc_del(_db, _cur, con_name):
    '更新开发容器记录'
    # _cur.execute("insert into mng_data_his(ckey, ctype,  p1, p2, content, op_time) "
    #              "select ckey, ctype,  p1, p2, content, op_time from mng_data where ckey=%s and ctype=%s",
    #              [con_name, "devc"])  # 插入历史表
    _cur.execute("delete from dev_container where CONTAINER_NAME=%s and CONTAINER_TYPE=%s",
                 [con_name])
    if _cur.rowcount < 1:
        return False
    return True


@dbfunc1
def images_del(_db, _cur, imageId):
    '删除镜像记录'
    _cur.execute("delete from dev_image where IMAGE_ID=%s",
                 [imageId])
    if _cur.rowcount < 1:
        return False
    return True


# 查询dev_run_node
@dbfunc1
def devc_get_all_node_status(_db, _cur):
    run_node_status = []
    '根据容器名称, 查询容器数据'
    _cur.execute("select RUN_ID,EXPE_NAME,NODE_ID,CONTAINER_NAME from dev_run_node")
    for r in _cur.fetchall():
        if r[0] is None:
            continue
        else:
            run_node_status.append(r)
    return run_node_status


# 更新run node状态
@dbfunc1
def devc_update_node_status(_db, _cur, status, run_id, expe_name, node_name, error_msg=None):
    '根据容器名称, 查询容器数据'
    _cur.execute(
        "update dev_run_node set RUN_STATUS=%s, ERROR_MSG=%s, op_time=now() where RUN_ID=%s and EXPE_NAME=%s and NODE_ID=%s",
        [status, error_msg, run_id, expe_name, node_name])
    if _cur.rowcount < 1:
        return False
    return True


# 镜像生成，更新IMAGE_STATUS、NAMESPACE、IMAGE_NAME、TAG
# dev_image表-》IMAGE_STATUS字段  01：创建中  02：待发布  03：待发布审批  04：已发布  05：创建失败  97：删除中  98：删除失败  99：已删除
@dbfunc1
def update_devImage_status(_db, _cur, imageId, imageStatus, nameSpace, imageName, tag):
    log.info("update dev_image set IMAGE_STATUS=%s,NAMESPACE=%s, IMAGE_NAME=%s,TAG=%s, op_time=now() where IMAGE_ID=%s",
             [imageStatus, nameSpace, imageName, tag, imageId])
    _cur.execute(
        "update dev_image set IMAGE_STATUS=%s,NAMESPACE=%s, IMAGE_NAME=%s,TAG=%s, op_time=now() where IMAGE_ID=%s",
        [imageStatus, nameSpace, imageName, tag, imageId])
    if _cur.rowcount < 1:
        return False
    return True


# 镜像生成,只更新镜像状态
@dbfunc1
def update_devImage_onlyStatus(_db, _cur, imageId, imageStatus):
    log.info("update dev_image set IMAGE_STATUS=%s,op_time=now() where IMAGE_ID=%s", [imageStatus, imageId])
    _cur.execute("update dev_image set IMAGE_STATUS=%s,op_time=now() where IMAGE_ID=%s", [imageStatus, imageId])
    if _cur.rowcount < 1:
        return False
    return True


# 镜像生成，更新IMAGE_INFO,json格式更新
# dev_image表-》IMAGE_STATUS字段 
@dbfunc1
def update_devImage_imageInfo(_db, _cur, imageId, imageInfo):
    _cur.execute("update dev_image set IMAGE_INFO=%s, op_time=now() where IMAGE_ID=%s", [imageInfo, imageId])
    if _cur.rowcount < 1:
        return False
    return True


@dbfunc1
def get_image_tag_from_devimage(_db, _cur):
    image_tags = []
    '根据容器名称, 查询容器数据'
    _cur.execute("select IMAGE_NAME,TAG from dev_image where IMAGE_STATUS=02")
    for r in _cur.fetchall():
        if r[0] is None:
            continue
        else:
            image_tags.append(r)
    return image_tags


# 更新镜像发布时的状态
@dbfunc1
def update_devApp_status(_db, _cur, appId, appStatus):
    log.info("update dev_application set APP_STATUS=%s, op_time=now() where APP_ID=%s", [appStatus, appId])
    _cur.execute("update dev_application set APP_STATUS=%s, op_time=now() where APP_ID=%s", [appStatus, appId])
    if _cur.rowcount < 1:
        return False
    return True


# 更新镜像发布时的APP_TYPE和APP_PARAM
@dbfunc1
def update_apptype_param(_db, _cur, appId, appType, appParam):
    log.info("update dev_application set APP_TYPE=%s, APP_PARAM=%s,op_time=now() where APP_ID=%s",
             [appType, appParam, appId])
    _cur.execute("update dev_application set APP_TYPE=%s, APP_PARAM=%s,op_time=now() where APP_ID=%s",
                 [appType, appParam, appId])
    if _cur.rowcount < 1:
        return False
    return True


# 根据appId,查询APP_PARAM
@dbfunc1
def get_appInfo(_db, _cur, appId):
    appInfos = []
    '根据容器名称, 查询容器数据'
    _cur.execute("select APP_PARAM from dev_application where APP_ID=%s", [appId])
    for r in _cur.fetchall():
        if r[0] is None:
            continue
        else:
            appInfos.append(r)
    return appInfos


# 镜像部署新增记录
@dbfunc1
def insert_app(_db, _cur, appId, appName, appDesc, projectName, appType, appStatus, imageID, appParam, createId, opId,
               opNote):
    sql = '''insert into dev_application(APP_ID,APP_NAME,APP_DESC,PROJECT_NAME,APP_TYPE,APP_STATUS,IMAGE_ID,APP_PARAM,IS_VALID,CREATE_ID,CREATE_TIME,OP_ID,OP_TIME,OP_NOTE) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'Y',%s,now(),%s,now(),%s)'''
    _cur.execute(sql,
                 (appId, appName, appDesc, projectName, appType, appStatus, imageID, appParam, createId, opId, opNote))
    if _cur.rowcount < 1:
        return False
    return True


# 更新进程启动进度
@dbfunc1
def devc_jupyter_percent(_db, _cur, name, percent):
    '根据容器名称, 查询容器数据'
    _cur.execute("update dev_container set STATUP_PERCENT=%s, op_time=now() where CONTAINER_NAME=%s", [percent, name])
    if _cur.rowcount < 1:
        return False
    return True


# 更新进程启动进度
@dbfunc1
def devc_jupyter_percent(_db, _cur, name, percent):
    '根据容器名称, 查询容器数据'
    _cur.execute("update dev_container set STATUP_PERCENT=%s, op_time=now() where CONTAINER_NAME=%s", [percent, name])
    if _cur.rowcount < 1:
        return False
    return True


# 模型文件的按版本管理功能实现--模型注册,更新模型训练时的指标入库dev_model_version
@dbfunc1
def model_register(_db, _cur, model_id, version_id, version_desc, model_path, is_best, is_valid, create_id):
    if model_path is None:
        '新增开发容器记录'
        _cur.execute(
            "insert into dev_model_version(MODEL_ID, VERSION_ID, VERSION_DESC, IS_BEST,IS_VALID,CREATE_ID,CREATE_TIME,OP_ID,OP_TIME,OP_NOTE) values(%s, %s, %s, %s,%s,%s,now(),%s, now(),%s)",
            [model_id, version_id, json.dumps(version_desc), is_best, is_valid, create_id, create_id, "automl"])
        if _cur.rowcount < 1:
            return False
    else:
        '新增开发容器记录'
        _cur.execute(
            "insert into dev_model_version(MODEL_ID, VERSION_ID, VERSION_DESC, MODEL_PATH, IS_BEST,IS_VALID,CREATE_ID,CREATE_TIME,OP_ID,OP_TIME,OP_NOTE) values(%s, %s, %s, %s,%s,%s,%s,now(),%s, now(),%s)",
            [model_id, version_id, json.dumps(version_desc), model_path, is_best, is_valid, create_id, create_id,
             "automl"])
        if _cur.rowcount < 1:
            return False
    return True


# 模型文件的按版本管理功能实现--模型条件查询--按用户查询查询模型
@dbfunc1
def inquire_model_by_user(_db, _cur, create_id):
    '根据用户，查询模型'
    model_paths = []
    _cur.execute("select MODEL_PATH from dev_model_version where CREATE_ID=%s and OP_NOTE=%s", [create_id, "automl"])
    for r in _cur.fetchall():
        if r[0] is None:
            continue
        else:
            r1 = r[0]
            if type(r1) == bytearray:
                r1 = r1.decode('utf-8')
            model_paths.append(r1)
    return model_paths


# 模型文件的按版本管理功能实现--模型条件查询--按查询最新时间查询模型
@dbfunc1
def inquire_model_by_maxTime(_db, _cur, model_id):
    '根据最新时间，查询模型'
    _cur.execute("select MODEL_PATH from dev_model_version where MODEL_ID=%s ORDER BY CREATE_TIME DESC LIMIT 0,1",
                 [model_id])
    r = _cur.fetchone()
    if r is None:
        return None
    r1 = r[0]
    if type(r1) == bytearray:
        r1 = r1.decode('utf-8')
    return r1


# 模型文件的按版本管理功能实现--模型条件查询--按指标最值查询模型
@dbfunc1
def inquire_model_by_metrics(_db, _cur, metric, model_id):
    '根据指标最值，查询模型'
    _cur.execute(
        f"select MODEL_PATH,VERSION_DESC->'$.{metric}' AS maxNum FROM dev_model_version WHERE MODEL_ID=%s ORDER BY maxNum",
        [model_id])
    metrics = []
    for r in _cur.fetchall():
        metrics.append(r[0].decode("utf-8"))
    return metrics
    # return [r[0] for r in _cur.fetchall()]


# nni启动训练，更新exp_id
@dbfunc1
def update_dev_run_expId(_db, _cur, exp_id, task_id, exp_name):
    '根据容器名称, 查询容器数据'
    _cur.execute("update dev_run set WF_ID=%s, op_time=now() where RUN_ID=%s and EXPE_NAME=%s",
                 [exp_id, task_id, exp_name])
    if _cur.rowcount < 1:
        return False
    return True


# nni启动训练，更新训练状态run_status
# 01:运行中;02:运行中;03:停止成功;04:停止成功;05:停止失败;06:预处理阶段
@dbfunc1
def update_dev_run_node_status(_db, _cur, run_status, task_id, exp_name, node_id, bak_1=""):
    '根据容器名称, 查询容器数据'
    if bak_1 == "":
        _cur.execute(
            "update dev_run_node set RUN_STATUS=%s,END_TIME=now(),op_time=now(), where RUN_ID=%s and EXPE_NAME=%s and NODE_ID=%s",
            [run_status, task_id, exp_name, node_id])
    else:
        _cur.execute(
            "update dev_run_node set RUN_STATUS=%s,END_TIME=now(),op_time=now(), BAK_1=%s where RUN_ID=%s and EXPE_NAME=%s and NODE_ID=%s",
            [run_status, bak_1, task_id, exp_name, node_id])
    if _cur.rowcount < 1:
        return False
    return True


# nni启动训练，更新表
# 01:训练中;02:训练完成;03:训练失败；04:训练取消
@dbfunc1
def update_dev_run_node_container_name(_db, _cur, container_name, run_id, exp_name, node_id):
    '根据容器名称, 查询容器数据'
    _cur.execute(
        "update dev_run_node set CONTAINER_NAME=%s, op_time=now() where RUN_ID=%s and EXPE_NAME=%s and NODE_ID=%s",
        [container_name, run_id, exp_name, node_id])
    if _cur.rowcount < 1:
        return False
    return True


# nni启动训练，更新容器状态
# 01:未占用  02:占用
@dbfunc1
def update_dev_container_poole_container_status(_db, _cur, is_user, container_name):
    '根据容器名称, 查询容器数据'
    _cur.execute("update dev_container_pool set IS_USE=%s, op_time=now() where CONTAINER_NAME=%s",
                 [is_user, container_name])
    if _cur.rowcount < 1:
        return False
    return True


@dbfunc1
def update_deploy_info(_db, _cur, svc_id, svc_url, svc_src_port, dep_status, dep_log=''):
    '''
        更新部署服务的相关信息
        -- svc_url： 部署服务的访问地址
        -- svc_src_port： 部署服务的外部访问接口
        -- dep_status：部署状态
    '''
    _cur.execute(
        "update dev_model_service set SERVICE_URL=%s, SERVICE_SRC_PORT=%s, DEP_STATUS=%s, DEP_LOG=%s where SERVICE_ID=%s",
        [svc_url, svc_src_port, dep_status, dep_log, svc_id])
    return _cur.rowcount > 0


@dbfunc1
def update_jupyter_deploy_info(_db, _cur, svc_url, svc_src_port, dep_status, deploy_name, dep_log=''):
    '''
        更新部署服务的相关信息
        -- svc_url： 部署服务的访问地址
        -- svc_src_port： 部署服务的外部访问接口
        -- dep_status：部署状态
    '''
    _cur.execute(
        "update dev_service_instance set SERVICE_URL=%s, SERVICE_SRC_PORT=%s, DEP_STATUS=%s, DEP_LOG=%s, OP_TIME=now(), OP_NOTE='更新部署结果' where DEPLOY_NAME=%s",
        [svc_url, svc_src_port, dep_status, dep_log, deploy_name])
    return _cur.rowcount > 0

@dbfunc1
def get_deploy_info(_db, _cur, deploy_name):
    '''
        更新部署服务的相关信息
        -- svc_url： 部署服务的访问地址
        -- svc_src_port： 部署服务的外部访问接口
        -- dep_status：部署状态
    '''
    _cur.execute("select dep_log"
                 " from dev_service_instance "
                 "where deploy_name=%s", [deploy_name, ])
    rows = _cur.fetchall()
    if len(rows) == 0:
        return None
    r = rows[0]
    return r[0]

@dbfunc1
def get_coding_mode(_db, _cur, con_name):
    '获取启动容器需要启动的工具'
    _cur.execute("select encoding_mode"
                 " from dev_jupyter "
                 "where container_name=%s", [con_name, ])
    _cur.execute(
        "select encoding_mode from dev_jupyter where container_name=%s",
        [con_name])
    rows = _cur.fetchall()
    if len(rows) == 0:
        return None
    r = rows[0]
    return r[0]


@dbfunc1
def update_instance_status(_db, _cur, deploy_name, status):
    _cur.execute(
        "update dev_service_instance set DEP_STATUS=%s, OP_TIME=now(), OP_NOTE='更新停止结果' where DEPLOY_NAME=%s",
        [status, deploy_name])
    return _cur.rowcount > 0
