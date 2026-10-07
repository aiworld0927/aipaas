import uuid

from app.utils.sse_utils import create_sse_queue
from app.utils.task_utils import update_task_status, TASK_STATUS_PROCESSING


def test1():
    session_id = str(uuid.uuid4())
    # 创建一个字典 存储对一个session_id : queue 结果队列
    create_sse_queue(session_id)
    # 当前会话id作为key! 整体装填处于运行中！
    update_task_status(session_id, TASK_STATUS_PROCESSING,True)
    import sys
    print([k for k in sys.modules if 'sse_utils' in k or 'task_utils' in k])
if __name__ == '__main__':
    test1()