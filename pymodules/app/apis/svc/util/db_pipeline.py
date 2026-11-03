from svc.util.db_base import dbfunc1, pool_init
import time
from svc import util
import datetime


class PL:
    @staticmethod
    @dbfunc1
    def get_cont_info(_db, _cur, cont_name):
        _cur.execute('''select container_name, container_type, ip_address, control_port, service_port, is_use, is_valid, op_time, op_note
 from dev_container_pool where container_name=%s ''', [cont_name, ])
        rows = _cur.fetchall()
        if len(rows) == 0:
            return None
        row = rows[0]
        # print("---", row)
        return [f.decode('utf8') if type(f) == bytearray else f for f in row]

    @staticmethod
    @dbfunc1
    def create_progress(_db, _cur, obj_id, user_name, note):
        '创建进度记录'
        log_id = f"{int(time.time())}-{util._gen_rand_str(4)}"
        _cur.execute("""INSERT INTO dev_progress (LOG_ID, OBJ_ID, ASYNC_TASK_TYPE, FINISH_PERCENT, DEAL_LOG, CREATE_ID, CREATE_TIME, OP_ID, OP_TIME, OP_NOTE)
         VALUES (%s, %s, '03', 0,  %s, %s, now(), %s,now(), %s)""",
                     [log_id, obj_id, note, user_name, user_name, note])
        if _cur.rowcount > 0:
            return log_id
        else:
            return None

    @staticmethod
    @dbfunc1
    def update_progress(_db, _cur, log_id, percent, deal_log, note=''):
        '更新进度'
        _cur.execute(
            """update dev_progress set FINISH_PERCENT=%s, deal_log=%s, op_note=%s, op_time=now() where log_id=%s""",
            [percent, deal_log, note, log_id])
        return _cur.rowcount > 0

    @staticmethod
    @dbfunc1
    def update_dev_run(_db, _cur, expe_name, run_id, status_code, ext_info, container_name):
        '将容器信息保存到 dev_run 中'
        if ext_info:
            _cur.execute(
                """update dev_run set run_status=%s, ext_info=%s, op_time=now() where run_id=%s and expe_name=%s""",
                [status_code, ext_info, run_id, expe_name])
        else:
            _cur.execute(
                """update dev_run set run_status=%s, op_time=now() where run_id=%s and expe_name=%s""",
                [status_code, run_id, expe_name])
        ret = _cur.rowcount > 0
        if container_name is not None:
            _cur.execute("""update dev_experiment set container_name=%s where expe_name=%s""",
                         [container_name, expe_name])
        return ret

    @staticmethod
    @dbfunc1
    def update_dev_run_status(_db, _cur, expe_name, run_id, status_code, op_note):
        '变更过程中， 同步流水线状态到 dev_run.run_status 中'
        if status_code == 'CK-OFF':
            sql = """update dev_run set run_status=%s, op_note=%s, ext_info=null, op_time=now() where run_id=%s and expe_name=%s"""
        else:
            sql = """update dev_run set run_status=%s, op_note=%s, op_time=now() where run_id=%s and expe_name=%s"""
        _cur.execute(sql, [status_code, op_note, run_id, expe_name])
        ret = _cur.rowcount > 0
        return ret

    @staticmethod
    @dbfunc1
    def get_dev_run_ext(_db, _cur, expe_name, run_id):
        '获取dev_run 的 ext_info 字段内容'
        _cur.execute('''select ext_info from dev_run where run_id=%s and expe_name=%s''', [run_id, expe_name])
        rows = _cur.fetchall()
        if len(rows) == 0:
            return None
        return rows[0][0]

    @staticmethod
    @dbfunc1
    def get_experiment(_db, _cur, cont_name):
        '获取dev_run 的 ext_info 字段内容'
        _cur.execute('''select expe_name from dev_experiment where container_name=%s''', [cont_name,])
        rows = _cur.fetchall()
        if len(rows) == 0:
            return None
        return rows[0][0]


class JU:
    '''jupyter容器相关的数据库更改'''

    @staticmethod
    @dbfunc1
    def get_gpu_res(_db, _cur, gpu_id):
        '''检查 gpu 资源记录, 是否预占状态
        status: 资源使用状态01使用中02空闲03预占

        返回:  status,  container_name
        '''
        _cur.execute('''select status, container_name from c_res_gpu where gpu_id=%s''', [gpu_id])
        rows = _cur.fetchall()
        if len(rows) == 0:
            return None
        r = rows[0]
        return (r[0], r[1])

    @staticmethod
    @dbfunc1
    def update_gpu_res(_db, _cur, gpu_id, status):
        '''更新 gpu 资源记录
        status: 资源使用状态01使用中02空闲03预占

        返回:  更新记录是否成功
        '''
        _cur.execute('''update c_res_gpu set status=%s, op_time=now() where gpu_id=%s''', [status, gpu_id])
        return _cur.rowcount > 0


# Model-Deploy db operates
class MD:
    @staticmethod
    @dbfunc1
    def get_app_info(_db, _cur, user_name, app_name, is_new=False):
        '''获取实验对应的app是否存在,

        返回: None-- 无有效部署app, 否则返回 app_id'''
        if is_new:
            # 名称唯一
            _cur.execute(
                """select app_id, app_name, IS_VALID from dev_application where APP_NAME=%s """,
                [app_name, ])
        # tag = f"{expe_name}^{node_id}"
        # _cur.execute("select app_id, app_name, IS_VALID from dev_application where image_id=%s and is_valid='Y' ", [tag,])
        else:
            _cur.execute(
                """select app_id, app_name, IS_VALID from dev_application where CREATE_ID=%s and is_valid='Y' and APP_NAME like %s"%%" """,
                [user_name, app_name])

        rows = _cur.fetchall()
        if len(rows) == 0:
            return None
        return rows

    @staticmethod
    @dbfunc1
    def get_model_info(_db, _cur, model_path, expe_name):
        '''获取实验对应的app是否存在,

        返回: None-- 无有效部署app, 否则返回 app_id'''
        _cur.execute(
            """ select dev_model.model_id, version_id, version_desc, model_name
                from dev_model_version
                left join dev_model 
                on dev_model.model_id = dev_model_version.model_id
                where model_path = %s and expe_name = %s;""",
            [model_path, expe_name])

        rows = _cur.fetchall()
        if len(rows) == 0:
            return None
        model_info = {
            'model_id': rows[0][0],
            'version_id': rows[0][1],
            'version_desc': rows[0][2],
            'model_name': rows[0][3],
        }

        return model_info

    @staticmethod
    @dbfunc1
    def update_app_info(_db, _cur, app_id, user_name, status, valid='Y', op_note='update'):
        '''更新app的状态'''
        _cur.execute(
            "update dev_application set app_status=%s, op_id=%s, is_valid=%s, op_time=now(), op_note=%s where app_id=%s",
            [status, user_name, valid, op_note, app_id])
        return _cur.rowcount > 0

    @staticmethod
    @dbfunc1
    def add_app_ip(_db, _cur, app_id, app_ip, app_port):
        '''更新app的访问ip和port'''
        _cur.execute(
            "update dev_application set app_ip=%s, app_port=%s where app_id=%s",
            [app_ip, app_port, app_id])
        return _cur.rowcount > 0

    @staticmethod
    @dbfunc1
    def update_deploy_info(_db, _cur, svc_id, svc_url, svc_src_port, dep_status, dep_log=''):
        '''
            更新部署服务的相关信息
            -- svc_url： 部署服务的访问地址
            -- svc_src_port： 部署服务的外部访问接口
            -- dep_status：部署状态
        '''
        _cur.execute(
            "update dev_model_service set SERVICE_URL=%s, SERVICE_SRC_PORT=%s, DEP_STATUS=%s, DEP_LOG=%s where svc_id=%s",
            [svc_url, svc_src_port, dep_status, dep_log, svc_id])
        return _cur.rowcount > 0

    @staticmethod
    @dbfunc1
    def new_app_info(_db,
                     _cur,
                     app_id,
                     app_name,
                     desc,
                     proj_name,
                     user_name,
                     expe_name,
                     node_id,
                     model_id=None,
                     model_name=None,
                     version_id=None,
                     version_desc=None):
        '''流水线模型服务,  ws模式, APP_TYPE=11, IMAGE_ID 对应 expe_name'''
        tag = f"{expe_name}^{node_id}"
        sql = '''INSERT INTO dev_application (APP_ID, APP_NAME, APP_DESC, PROJECT_NAME, APP_TYPE,
         APP_STATUS, IMAGE_ID, APP_PARAM, IS_VALID, CREATE_ID, 
         CREATE_TIME, OP_ID, OP_TIME, OP_NOTE, APP_IP, APP_PORT, MODEL_ID, MODEL_NAME, VERSION_ID, VERSION_DESC,
         EXPE_NAME, DETAIL) 
        VALUES (%s, %s, %s, %s, %s, 
        '01', %s, '', 'Y', %s,
         now(), %s, now(), '新增应用', '', '', %s, %s, %s, %s, %s, '')'''
        _cur.execute(sql, [app_id, app_name, desc, proj_name, '11',
                           tag, user_name, user_name, model_id,
                           model_name, version_id, version_desc, expe_name])
        return _cur.rowcount > 0

    @staticmethod
    @dbfunc1
    def check_app_info(_db, _cur, user_name, app_name):
        '''查询app_name是否重复, 一个用户不可有同样的app_name存在(有效app)

        返回: None-- 无重复名称, 否则返回 app_id'''
        # tag = f"{expe_name}^{node_id}"
        _cur.execute("select app_id from dev_application where CREATE_ID=%s and is_valid='Y' and APP_NAME=%s ",
                     [user_name, app_name, ])
        rows = _cur.fetchall()
        if len(rows) == 0:
            return None
        return rows[0][0]

    @staticmethod
    @dbfunc1
    def get_apps(_db, _cur, user_name, app_name, pipeline_id):
        '''查询app_name是否重复, 一个用户不可有同样的app_name存在(有效app)

        返回: None-- 无重复名称, 否则返回 app_id'''
        # tag = f"{expe_name}^{node_id}"
        _cur.execute(
            # """select APP_IP, APP_PORT,APP_NAME,APP_ID from dev_application where CREATE_ID=%s
            # and is_valid='Y' and APP_NAME like %s"%%" """,  # and locate("schedule",APP_NAME)=0
            """select APP_IP, APP_PORT,APP_NAME,APP_ID,b.EXPE_NAME from dev_application a,dev_experiment b 
            where a.CREATE_ID=%s and a.is_valid='Y' and a.APP_NAME like %s"%%" and
            b.EXPE_NAME like "%%" %s "%%" """
            [user_name, app_name, pipeline_id])
        rows = _cur.fetchall()
        if len(rows) == 0:
            return None
        return rows

    @staticmethod
    @dbfunc1
    def new_deploy_logs(_db, _cur, user_name, app_name, deploy_name, app_type, app_status, note, percent, is_valid=1):
        '''部署日志记载，app_type: web or stream，app_status: 0(部署失败) or 1(部署成功) or -1(部署中)'''
        log_id = f"{int(time.time())}-{util._gen_rand_str(4)}"
        create_time = datetime.datetime.now()
        sql = '''INSERT INTO dev_deploy_logs (USER_ID, APP_NAME, DEPLOY_NAME, APP_TYPE, APP_STATUS, 
        IS_VALID, CREATE_TIME, NOTE, PERCENT, LOG_ID) 
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)'''
        _cur.execute(sql, [user_name, app_name, deploy_name, app_type,
                           app_status, is_valid, create_time, note, percent, log_id])
        return _cur.rowcount > 0

    @staticmethod
    @dbfunc1
    def update_deploy_logs(_db, _cur, app_name, user_name, valid=0):
        '''更新app部署信息的状态'''
        _cur.execute(
            "update dev_deploy_logs set IS_VALID=%s  where USER_ID=%s and APP_NAME=%s and IS_VALID=1",
            [valid, user_name, app_name])
        return _cur.rowcount > 0

    @staticmethod
    @dbfunc1
    def del_deploy_logs(_db, _cur, app_name, user_name, is_new=False):
        '''删除app部署信息的状态'''
        if is_new:
            _cur.execute(
                # "delete from dev_deploy_logs where USER_ID=%s and APP_NAME=%s",
                "delete from dev_deploy_logs where USER_ID=%s and APP_NAME=%s > 0",
                [user_name, app_name])
        else:
            _cur.execute(
                # "delete from dev_deploy_logs where USER_ID=%s and APP_NAME=%s",
                "delete from dev_deploy_logs where USER_ID=%s and locate(APP_NAME, %s) > 0",
                [user_name, app_name])
        return _cur.rowcount > 0

    @staticmethod
    @dbfunc1
    def get_deploy_logs(_db, _cur, user_name, app_name, is_new=False):
        '''部署日志获取，app_type: web or stream，app_status: 0(部署失败) or 1(部署成功) or -1(部署中)'''
        if is_new:
            _cur.execute(
                # "select NOTE, PERCENT, APP_STATUS, APP_NAME, CREATE_TIME from dev_deploy_logs where USER_ID=%s and IS_VALID=1 and SUBSTRING_INDEX(APP_NAME, '-', 1)=%s order by CREATE_TIME",[user_name, app_name])
                """select NOTE, PERCENT, APP_STATUS, APP_NAME, CREATE_TIME from dev_deploy_logs where USER_ID=%s and IS_VALID=1 and APP_NAME=%s order by CREATE_TIME""",
                [user_name, app_name])
        else:
            _cur.execute(
                # "select NOTE, PERCENT, APP_STATUS, APP_NAME, CREATE_TIME from dev_deploy_logs where USER_ID=%s and IS_VALID=1 and SUBSTRING_INDEX(APP_NAME, '-', 1)=%s order by CREATE_TIME",[user_name, app_name])
                """select NOTE, PERCENT, APP_STATUS, APP_NAME, CREATE_TIME from dev_deploy_logs where USER_ID=%s and IS_VALID=1 and APP_NAME like %s"%%" order by CREATE_TIME""",
                [user_name, app_name])
        rows = _cur.fetchall()
        if len(rows) == 0:
            return None
        return rows

    @staticmethod
    @dbfunc1
    def get_run_status(_db, _cur, expe_name, run_id):
        '''获取流水线任务运行状态

        返回: None-- 无有效部署app, 否则返回 第一条数据'''
        _cur.execute(
            "select run_status, OP_NOTE from dev_run where EXPE_NAME=%s and RUN_ID=%s order by OP_TIME",
            [
                expe_name, run_id])
        rows = _cur.fetchall()
        if len(rows) == 0:
            return None
        r = rows[0]
        ret = {n[0]: r[i] for i, n in enumerate(_cur.description)}
        return ret

    @staticmethod
    @dbfunc1
    def get_app_ip_port(_db, _cur, op_id, app_name):
        """
        获取部署的app的ip和port，有则返回对应的内容，无则返回None,None
        :param _db:
        :param _cur:
        :param op_id:
        :param app_name:
        :return:
            :ip
            :port
        """
        _cur.execute("select app_ip, app_port from dev_application where OP_ID=%s and is_valid='Y' and APP_NAME=%s",
                     [op_id, app_name])
        result = _cur.fetchone()
        return result

    @staticmethod
    @dbfunc1
    def get_user_id(_db, _cur, user_no, tenant_id):
        """
        获取部署的app的ip和port，有则返回对应的内容，无则返回None,None
        :param _db:
        :param _cur:
        :param op_id:
        :param app_name:
        :return:
            :ip
            :port
        """
        _cur.execute("select user_id from sys_user where user_no=%s and tenant_id=%s",
                     [user_no, tenant_id])
        result = _cur.fetchone()
        if result:
            return result[0]
        else:
            return None


# Experiment db operates
class EP:
    @staticmethod
    @dbfunc1
    def get_experi_num(_db, _cur):
        '''获取实验数, 获取各种类型试验的个数

        返回: experi_data '''
        type_dic = {'01': 'AutoML', '03': 'CodeML', '04': 'ModelOps'}
        _cur.execute(
            """select count(*), expe_type from dev_experiment group by dev_experiment.expe_type;""", )

        rows = _cur.fetchall()
        experi_data = {
            'AutoML': 0,
            'CodeML': 0,
            'ModelOps': 0
        }
        for _row in rows:
            experi_type = _row[1]
            if experi_type in type_dic:
                experi_data[type_dic[experi_type]] = _row[0]
        return experi_data

    @staticmethod
    @dbfunc1
    def get_train_times(_db, _cur):
        '''获取实验数, 获取各种类型试验的个数

        返回: experi_data '''
        type_dic = {'01': 'AutoML', '03': 'CodeML', '04': 'ModelOps'}
        _cur.execute(
            """select expe_type, train_num from dev_experiment_data group by expe_type;""", )

        rows = _cur.fetchall()
        experi_data = {
            'AutoML': 0,
            'CodeML': 0,
            'ModelOps': 0
        }
        for _row in rows:
            experi_type = _row[0]
            if experi_type in type_dic:
                experi_data[type_dic[experi_type]] = _row[1]
        return experi_data


if __name__ == '__main__':
    pool_init("""eyJkYl90eXBlIjogIm15c3FsIiwgInVzZXIiOiAiaWFzcG9wciIsICJwYXNzd29yZCI6ICJJYXNw
    ITIzJCIsICJob3N0IjogIjE3Mi4yMC41OS4xMDkiLCAicG9ydCI6IDYwMTIwLCAiZGF0YWJhc2Ui
    OiAiaWFzcGRiIn0=""")

    apps_obj = MD.get_apps('wangkun_csd', 'e0pzxz7l-DE1663206827803726310001')
    app_data = []
    for _obj in apps_obj:
        app_data.append(
            {
                "app_ip": _obj[0],
                "app_port": _obj[1],
                "app_name": _obj[2],
                "pod_name": _obj[3]
            }
        )
    print(app_data)
