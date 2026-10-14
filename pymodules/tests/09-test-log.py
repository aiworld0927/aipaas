# -*- coding: utf-8 -*-
"""
tests/09-test-log.py —— 对比两种日志补丁配置的行为差异
================================================================
配置A（app/core/log.py 现行实现，第 124-126 行）:
    logger_temp = base_logger.patch(fix_log_position)
    logger      = logger_temp.patch(inject_request_id)

配置B（省略位置修复补丁）:
    logger = base_logger.patch(inject_request_id)

验证点:
    场景1  业务代码直接调用          -> 对比 {name}/{function}/{line} 字段
    场景2  经 log.py 同类工具层包装  -> 验证 fix_log_position 的"穿透包装层"能力
    场景3  文件名含 "log.py" 子串    -> 验证 fix_log_position 的子串误伤缺陷
    场景4  request_id 注入           -> 验证两种配置均生效（均含 inject_request_id）
    场景5  缺少 inject 补丁的后果    -> 纯函数演示（不实际写日志）
    场景6  fix_log_position 性能开销 -> inspect.stack() 单次成本基准

运行方式:
    cd pymodules && uv run python tests/09-test-log.py
"""
import inspect
import sys
import time
from pathlib import Path

# ---------- 导入路径准备：保证能导入 core.* 与同目录辅助模块 ----------
TESTS_DIR = Path(__file__).resolve().parent
APP_DIR = TESTS_DIR.parent / "app"
for _p in (str(APP_DIR), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.context import request_id_ctx_var  # noqa: E402
from core.log import base_logger, fix_log_position, inject_request_id  # noqa: E402

from business_module import (  # noqa: E402
    business_direct_call,
    business_via_wrapper,
    call_into_test_file,
)
from helper_log import log_via_wrapper  # noqa: E402

# ============================================================================
# 1. 构造三种 logger 实例
# ============================================================================
# 配置A：先修位置，再注入 request_id（core/log.py 现行写法）
logger_a = base_logger.patch(fix_log_position).patch(inject_request_id)
# 配置B：只注入 request_id，不做位置修复
logger_b = base_logger.patch(inject_request_id)
# 配置A'：补丁顺序对调（用于验证两个补丁互不干扰、顺序无关）
logger_ab = base_logger.patch(inject_request_id).patch(fix_log_position)

# ============================================================================
# 2. 安装捕获 sink：抓取 record 字段做机器级对比（不依赖肉眼观察控制台）
#    说明：loguru 的 handler 是全局的，patched logger 与 base_logger 共享 sink，
#    因此一个捕获 sink 能同时收到 A/B 两个 logger 的记录。
# ============================================================================
captured = []


def capture_sink(message):
    """从 loguru Message 对象中提取 record 的关键字段"""
    rec = message.record
    captured.append({
        "tag": rec["extra"].get("tag", ""),
        "name": rec["name"],
        "function": rec["function"],
        "line": rec["line"],
        "request_id": rec["extra"].get("request_id", "<缺失>"),
    })


capture_handler = base_logger.add(capture_sink, level="INFO")

_printed = 0


def dump_rows(title):
    """打印自上次调用以来新捕获的日志字段对比表"""
    global _printed
    print("\n" + "=" * 108)
    print(title)
    print("=" * 108)
    print(f"{'标签':<22}{'name':<26}{'function':<24}{'line':<7}{'request_id'}")
    print("-" * 108)
    for c in captured[_printed:]:
        print(f"{c['tag']:<22}{c['name']:<26}{c['function']:<24}{c['line']:<7}{c['request_id']}")
    _printed = len(captured)


# ============================================================================
# 场景1：业务代码直接调用（business_module.py，文件名不含 "log.py" 子串）
# ============================================================================
def scenario_1_direct_call():
    """预期：
    - A 与 B 的 function/line 均正确且指向 business_module.py 中同一行
    - 差异仅在 {name}：A=文件名 "business_module.py"，B=模块名 "business_module"
    - A'（补丁顺序对调）输出应与 A 完全一致 -> 补丁互不干扰、顺序无关
    """
    business_direct_call(logger_a, "A1-direct")
    business_direct_call(logger_b, "B1-direct")
    business_direct_call(logger_ab, "A1-order-swapped")
    dump_rows("场景1：业务代码直接调用（文件名不含 'log.py'，两种配置均能正确定位）")


# ============================================================================
# 场景2：经 helper_log.py 包装层间接调用（文件名含 "log.py" 子串）
# ============================================================================
def scenario_2_wrapper_call():
    """预期：
    - A 跳过 helper_log.py 的包装帧 -> 定位到 business_module.py 的真实业务调用行
    - B 停在包装函数内部 -> name="helper_log", function="log_via_wrapper"
    本场景是 fix_log_position 的设计价值所在（穿透 core/log.py 这类工具层）。
    """
    business_via_wrapper(log_via_wrapper, logger_a, "A2-wrapper")
    business_via_wrapper(log_via_wrapper, logger_b, "B2-wrapper")
    dump_rows("场景2：经 helper_log.py 包装层间接调用（模拟 core/log.py 工具层）")


# ============================================================================
# 场景3：调用方文件名含 "log.py" 子串 -> fix_log_position 误伤演示
# ============================================================================
def _log_inside_test_file(lg, tag):
    """真实调用点位于 09-test-log.py 内 —— 但本文件名恰好包含 "log.py" 子串！"""
    lg.bind(tag=tag).info("真实调用点在 09-test-log.py 内（文件名含 'log.py' 子串）")


def scenario_3_name_collision():
    """预期（3a 回调版，结果确定）：
    - A 把 09-test-log.py 的所有帧全部过滤 -> 位置错误归因到外层 business_module 帧
    - B 始终正确：function=_log_inside_test_file，指向本文件真实行号
    预期（3b 直调版，取决于启动器）：
    - A 过滤掉本文件全部帧后循环走完未命中 -> record 保持 loguru 默认值（碰巧正确），
      或在 pytest/-m 启动时拾取 runpy 等外部帧（错误归因）
    - B 始终正确
    """
    # 3a 反向回调：外层存在 business_module 帧，A 的错误归因是确定性的
    call_into_test_file(_log_inside_test_file, logger_a, "A3-callback")
    call_into_test_file(_log_inside_test_file, logger_b, "B3-callback")
    # 3b 普通直调：从测试文件内部直接调用
    _log_inside_test_file(logger_a, "A3-plain")
    _log_inside_test_file(logger_b, "B3-plain")
    dump_rows("场景3：调用方文件名含 'log.py' 子串（演示 fix_log_position 子串误伤）")


# ============================================================================
# 场景4：request_id 注入验证
# ============================================================================
def scenario_4_request_id():
    """预期：A/B 均含 inject_request_id 补丁，request_id 字段均正确；
    未设置 contextvar 时使用默认值 "backend_script"（见 core/context.py）。
    """
    token = request_id_ctx_var.set("req-1001")
    business_direct_call(logger_a, "A4-req-1001")
    business_direct_call(logger_b, "B4-req-1001")
    request_id_ctx_var.reset(token)

    token = request_id_ctx_var.set("req-1002")
    business_direct_call(logger_a, "A4-req-1002")
    business_direct_call(logger_b, "B4-req-1002")
    request_id_ctx_var.reset(token)

    # 未设置 contextvar -> 默认值
    business_direct_call(logger_a, "A4-default")
    business_direct_call(logger_b, "B4-default")
    dump_rows("场景4：request_id 注入（两种配置均应携带正确的 request_id）")


# ============================================================================
# 场景5：缺少 inject_request_id 补丁的后果（纯函数演示，不实际写日志）
# ============================================================================
def scenario_5_no_inject_patch():
    """控制台 sink 的格式 LOG_FORMAT_REQUEST_ID 含 {extra[request_id]} 占位符，
    若 logger 未挂 inject_request_id 补丁，格式化时将因 extra 缺键而报错。
    这里直接构造 record 调用补丁函数演示字段注入过程（避免真实触发渲染错误）。
    """
    fake_record = {"extra": {}}
    print("\n" + "=" * 108)
    print("场景5：缺少 inject_request_id 补丁时（构造 record 直接演示，不实际写日志）")
    print("=" * 108)
    print(f"补丁执行前 record['extra'] : {fake_record['extra']}")
    inject_request_id(fake_record)
    print(f"补丁执行后 record['extra'] : {fake_record['extra']}  <-- 控制台格式依赖该字段")


# ============================================================================
# 场景6：fix_log_position 的性能开销
# ============================================================================
def scenario_6_perf():
    """fix_log_position 在【调用方线程】内同步执行，每条日志都会做一次
    inspect.stack()（构造全部帧的 FrameInfo 列表，代价远高于裸取帧）。
    """
    n = 2000
    t0 = time.perf_counter()
    for _ in range(n):
        inspect.stack()
    stack_us = (time.perf_counter() - t0) / n * 1e6

    t0 = time.perf_counter()
    for _ in range(n):
        sys._getframe(1)
    frame_us = (time.perf_counter() - t0) / n * 1e6

    print("\n" + "=" * 108)
    print("场景6：fix_log_position 性能开销（配置A每条日志的固定额外成本）")
    print("=" * 108)
    print(f"inspect.stack()   单次耗时 : {stack_us:10.1f} us  <-- 配置A：每条日志同步执行一次")
    print(f"sys._getframe(1)  单次耗时 : {frame_us:10.2f} us  <-- 参考基准（loguru原生探测近似成本）")
    print(f"倍率: {stack_us / max(frame_us, 1e-9):.0f}x")


# ============================================================================
# 主流程
# ============================================================================
if __name__ == "__main__":
    print("#" * 108)
    print("# 日志配置差异对比测试：配置A(双patch) vs 配置B(单patch)")
    print("#" * 108)

    scenario_1_direct_call()
    scenario_2_wrapper_call()
    scenario_3_name_collision()
    scenario_4_request_id()
    scenario_5_no_inject_patch()
    scenario_6_perf()

    # 清理捕获 sink，恢复原始日志环境
    base_logger.remove(capture_handler)

    print("\n" + "#" * 108)
    print("# 结论摘要")
    print("#" * 108)
    print("""
1. fix_log_position（仅配置A有）:
   - 作用: 用 inspect.stack() 手工定位调用方，并改写 name/function/line 三个字段
   - 收益: {name} 显示文件名而非模块名；可穿透"文件名含 log.py 的工具层"定位真实业务调用行(场景2)
   - 代价: 每条日志同步做一次 inspect.stack()，开销为原生探测的数百倍(场景6)
   - 缺陷: 按"log.py"子串过滤帧，会误伤 09-test-log.py / catalog.py / dialog.py 等文件，
           导致位置错误归因或退化为默认值(场景3)；还会跳过名为 _log 的业务函数

2. inject_request_id（两种配置均有）:
   - 作用: 从 contextvar 读取 request_id 写入 record["extra"]，供控制台格式渲染
   - 两种配置下均正常工作(场景4)，是配置B仍能避免格式渲染报错的必要补丁

3. 配置B省略 fix_log_position 后:
   - 直接调用时 function/line 依旧正确（loguru 原生探测），仅 {name} 由文件名变为模块名
   - 经工具层包装调用时，位置停在包装函数内部（无法穿透，场景2的B行）
   - 换来的是: 零额外性能开销 + 不会被文件名子串规则误伤（场景3的B行始终正确）
""")
