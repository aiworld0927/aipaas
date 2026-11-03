# _*_ coding:utf-8_*_
# from rediscluster import StrictRedisCluster

import time
import json
import base64
import traceback

import redis
from loguru import logger

from svc.util import db
from svc.util.k8s import K8S

# reference：
# https://stackoverflow.com/questions/11931556/redis-publishing-when-no-subscriber
# https://kb.objectrocket.com/redis/basic-redis-usage-example-part-1-exploring-pub-sub-with-redis-python-583
# https://redis.io/commands/setex

class RedisHelper_(object):
    """
    host: redis ip
    port: redis port
    channel: 发送接送消息的频道
    """

    def __init__(self,
                 redis_nodes,
                 channel,
                 password=None):
        self.redis_nodes = redis_nodes
        self.password = password
        self.channel = channel
        # self.__conn = StrictRedisCluster(startup_nodes=self.redis_nodes,
        #                                  password=self.password,
        #                                  decode_responses=True)
        # self.__conn = redis.Redis("172.18.231.76")

    def ping(self):
        try:
            self.__conn.ping()
            return True
        except Exception as e:
            print(e)
            return False

    # 发送消息
    def public(self,key,value):
        if self.ping():
            self.__conn.rpush(key,value)
            return True
        else:
            return False

    def set(self,key,value):
        self.__conn.set(key,value)

    def get(self,key):
        return self.__conn.get(key)

    def exists(self,key):
        return self.__conn.exists(key)


class RedisHelper(object):
    """
    Redis 操作封装
    """

    def __init__(self, host, port=None, passwd=None):
        if port is None:  # host 为单个连接串: 1.1.1.1:3679:passwd
            f = host.split(':', maxsplit=2)
            host = f[0]
            port = int(f[1])
            passwd = None if len(f)<3 else f[2]
        self.__conn = redis.Redis(host=host, port=port, password=passwd, db=0)
        rd = redis.Redis(host=host, port=port, password=passwd, db=0)

    def close(self):
        try:
            self.__conn.close()
            self.__conn = None
        except Exception as x:
            logger.error("close redis failed {}, {}", str(x), traceback.format_exc())

    def put_message(self, key, message):
        n = self.__conn.lpush(key, message)
        if n == 0:
            logger.warning("redis lpush to queue failed")

    def get_message(self, key, timeout=5):
        t_end = time.time() + timeout
        while time.time() < t_end:
            m = self.__conn.rpop(key)
            if m is None:
                time.sleep(0.2)
                continue
            return m.decode('utf8')

    def get_message_nowait(self, key):
        return self.__conn.rpop(key)


class DelayTask:
    def __init__(self, task_name, params, delay_sec=10, timeout_secs=60, timeout_task_name=None):
        t = time.time()
        self.task_name = task_name
        self.params = params
        self.next_time = t + delay_sec
        self.max_time = t + timeout_secs
        self.timeout_task_name = timeout_task_name
    def to_json(self):
        return json.dumps([self.next_time, self.task_name, self.max_time, self.timeout_task_name, self.params])
    def to_list(self):
        return [self.next_time, self.task_name, self.max_time, self.timeout_task_name, self.params]
    def delay(self, secs):
        self.next_time = time.time()+secs

    @staticmethod
    def from_json( lt_str):
        lt = json.loads(lt_str)
        next_time, task_name, max_time, timeout_task_name, params = lt
        dt = DelayTask(task_name, params, timeout_task_name=timeout_task_name)
        dt.next_time = next_time
        dt.max_time = max_time
        return dt
    @staticmethod
    def tmstr(s):
        # if s.find(':') > 0:
        #     return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S")) * 1000
        # else:
        #     return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(s) / 1000))
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s) )

class Worker:
    # 子进程类, 需要在进程中创建数据库连接池和redis连接,
    def __init__(self, _conf):
        self.conf = _conf # 完整的pyserv config 对象
        # self.k8s = None #
        # self.db = None  # 数据库 DAO 对象
        logger.info("Enter worker_init ...")
        self.__rd = RedisHelper(host=self.conf['local_redis_addr'])

        conn_param = json.loads(base64.decodebytes(self.conf["db_conn_str"].encode('utf8')))
        db.pool_init(conn_param)  # 初始化连接池

        self.k8s = K8S(self.conf['k8s_config_file'])

    def add_delayed_task(self, dt: DelayTask):
        # print("put delayed message!!!", dt.task_name)
        self.__rd.put_message("delayed", dt.to_json())

def __test():
    import redis
    bob_r = redis.Redis(host='172.18.231.76', port=6379, db=0, password="abc123")
    bob_p = bob_r.pubsub()
    bob_p.subscribe("cc")
    alice_r = redis.Redis(host='localhost', port=6379, db=0, password="abc123")
    alice_r.publish("cc", "hello")

    m = bob_p.get_message()