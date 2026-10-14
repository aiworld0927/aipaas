# -*- coding: utf-8 -*-
"""
业务模块：文件名不含 "log.py" 子串，模拟"普通业务代码"调用日志。

用于观察：
  - 场景1：直接调用 logger —— 配置A / 配置B 的 function/line 均正确且相同，仅 {name} 不同
  - 场景2：经 helper_log.py 包装层间接调用 —— 配置A 跳过包装层，配置B 停在包装层
  - 场景3：反向回调制造外层帧 —— 演示 fix_log_position 对文件名含 "log.py" 的调用方误伤
"""


def business_direct_call(lg, tag):
    # ↓ 本行是"真实调用位置"：配置A与配置B都应报告 function=business_direct_call 及本行行号
    lg.bind(tag=tag).info("业务模块内直接调用 logger.info()")


def business_via_wrapper(wrapper, lg, tag):
    # 通过包装层间接打日志：配置A应跳过包装层定位到本行，配置B会停在包装函数内部
    wrapper(lg, tag)


def call_into_test_file(fn, lg, tag):
    """反向回调：制造一个"外层业务帧"。

    调用栈（内→外）：loguru内部 -> 测试文件的 fn 帧(会被fix_log_position跳过,
    因为 09-test-log.py 文件名含 "log.py" 子串) -> 本函数帧(不会被跳过)。
    于是配置A会把日志位置错误归因到本模块；配置B始终正确。
    """
    fn(lg, tag)
