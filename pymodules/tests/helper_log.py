# -*- coding: utf-8 -*-
"""
包装层模块：文件名故意包含 "log.py" 子串（helper_**log.py**）。

作用：
  1. 模拟 core/log.py 这类"日志工具层"——fix_log_position 的设计目标就是跳过这一层，
     定位到真正的业务调用方；
  2. 同时演示其过滤规则的真实实现是【文件名子串匹配】：任何路径中包含 "log.py" 的帧
     都会被跳过（catalog.py / dialog.py / xxx_log.py / 09-test-log.py 均会被误伤）。

注意：本模块不 import 日志，只把传入的 logger 转发调用，保证调用栈干净。
"""


def log_via_wrapper(lg, tag):
    # 配置B（loguru 原生探测）会把位置停在本行（包装函数内部）
    # 配置A（fix_log_position）会跳过本帧，定位到外层业务调用行
    lg.bind(tag=tag).info("经 helper_log.py 包装函数间接调用 logger.info()")
