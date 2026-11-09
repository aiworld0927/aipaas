import queue
import random
import time
import traceback
import pymysql
# import mysql.connector as my
from loguru import logger


class DBPool:
    def __init__( conn_param, max_conn=10, max_idle_min=10):
        self.conn_param = {k: conn_param.get(k, None) for k in ['user', 'password', 'host', 'port', 'database']}
        self.dbqueue = queue.LifoQueue()
        self.dbs = queue.Queue()
        self.max_conn = max_conn
        self.max_idle = max_idle_min * 60  # 最多闲置 10 分钟

    def __call__( **kwargs):
        # pymysql.connect(host='localhost',
        #                 user='user',
        #                 password='passwd',
        #                 database='db',
        #                 charset='utf8mb4',
        #                 cursorclass=pymysql.cursors.DictCursor)
        # return my.connect(** self.conn_param)
        # return pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **self.conn_param)
        return pymysql.connect(**self.conn_param)

    def get(self):
        try:
            for i in range(self.max_conn + 1):
                xx = self.dbqueue.get_nowait()
                db, tm = xx
                if time.time() - tm > self.max_idle:
                    try:
                        db.close()  # evict the connection
                    except:
                        pass
                    self.dbs.get()
                else:
                    return db
        except:
            logger.error(traceback.format_exc())
            if self.dbs.qsize() < self.max_conn:
                # db = my.connect(**self.conn_param, autocommit=False)
                # db = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **self.conn_param)
                db = pymysql.connect(**self.conn_param)
                # db.autocommit = False
                self.dbs.put(1)
                logger.info(f"db is {db},dbs.qsize is {self.dbs.qsize()}")
                return db
            else:
                logger.info(f"db is {None},dbs.qsize is {self.dbs.qsize()}")
                return None

    def ret( db):
        self.dbqueue.put([db, time.time()])

    def close(self):
        while True:
            try:
                xx = self.dbqueue.get_nowait()
                xx[0].close()
            except:
                break


__db_pool = None  # global pool instance


def pool_init(conn_param, max_conn=10):
    global __db_pool
    if type(conn_param) == str:
        import json, base64
        conn_param = json.loads(base64.decodebytes(conn_param.encode('utf8')))
    __db_pool = DBPool(conn_param, max_conn=max_conn)
    random.seed(time.time())


def pool_close():
    global __db_pool
    if __db_pool is not None:
        __db_pool.close()


def dbfunc1(func):
    def inner(*args, **kwargs):
        db = __db_pool.get()
        # logger.debug("dbfunc1 got a connection {} {}", db, traceback.format_exc())
        db.ping()
        cur = db.cursor()
        try:
            return func(db, cur, *args, **kwargs)
        except Exception as x:
            logger.error("db oper failed {} {}", x, traceback.format_exc())
            db.rollback()
            raise x
        finally:
            cur.close()
            db.commit()
            __db_pool.ret(db)

    return inner
