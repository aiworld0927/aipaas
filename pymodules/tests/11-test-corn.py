import uvicorn
from fastapi import FastAPI

# 创建FastAPI实例
app = FastAPI(title="测试参数")

"""
在 OpenAPI 中，每一个 HTTP 方法都被称为「操作」

路径操作装饰器
@app.get()  创建数据。
@app.post() 读取数据。
@app.put()  更新数据。
@app.delete() 删除数据。
少见的：
@app.options()
@app.head()
@app.patch()
@app.trace()

路径:也被称为「端点」或「路由」
FastAPI底层维护了一个字典
键K 请求路径+请求方法
值V 对应的处理函数
当请求发送给uvicorn服务器 将根据请求路径(键)找到对应的值 (函数) 进行调用 并且返回结果

JSON属于键值对格式的字符串 键或者值都是字符串  是一种轻量级的 前后端交互的数据格式
"""


@app.get("/",summary="跟路径方法")
async def read_root():
    return {"Hello": "World"}


"""参数顺序
出现: /users/me VS /users/{user_id} 两重冲突路径情况。
结局：/users/me 要在 /users/{user_id} 之前声明 
"""


@app.get("/items/hello")
async def read_item(item_id: int = 0, q: str | None = None):
    return {"readme": "我的路径与路径参数情况重合", "q": q}


"""
1、路径参数：item_id: int 声明路径参数的类型，请求时会自动校验
2、查询参数设置默认值，即为可选参数：查询参数q是可选的，默认值为 None

http://127.0.0.1:8000/items/1
http://127.0.0.1:8000/items/1?q=111

"""


@app.get("/items/{item_id}")
async def read_item(item_id: int, q: str | None = None, short: bool = False):
    item:dict[str, int | str | bool]= {"item_id": item_id}
    print(f"bool类型参数:{short}")
    if q:
        item.update({"q": q})
    if not short:
        item.update({"description": "这是描述信息"})
    else:
        item.update({"short": "True"})
    return item


"""
查询参数：声明的参数不是路径参数时,按照参数的名字进行匹配
http://127.0.0.1:8000/items/?start=0&limit=2
"""

@app.get("/users/{user_id}/items/{item_id}")
async def read_user_item(user_id: int, item_id: str, q: str | None = None, short: bool = False):
    item = {"item_id": item_id, "owner_id": user_id}
    if q:
        item.update({"q": q})
    if not short:
        item.update({"description": "这是描述信息"})
    return item

@app.get("/items", summary="分页")
async def read_item(start: int = 0, limit: int = 10):
    return {"skip": start, "limit": limit}


if __name__ == "__main__":
    # 直接在代码中启动uvicorn服务器
    uvicorn.run(
        app="main:app",  # 指定要运行的FastAPI应用实例
        host="0.0.0.0",  # 允许外部访问（本地可通过127.0.0.1或localhost访问）
        port=8000,  # 端口号
        reload=True  # 开发模式：代码修改后自动重启（生产环境需去掉）
    )

"""
函数前加async def（异步函数）：
本质：定义一个异步协程（coroutine），支持在函数内部使用 await 调用其他异步操作（如异步数据库查询、异步 HTTP 请求等）。
适用场景：函数内部包含I/O 密集型操作（如网络请求、文件读写、数据库操作等），且这些操作有对应的异步实现（如 asyncpg 异步数据库、aiohttp 异步 HTTP 库）。
执行机制：FastAPI 会通过 Python 的 asyncio 库处理这些异步请求，这种情况下，请求的处理是“真正的异步”。当遇到 await 时，会暂时让出 CPU 控制权，控制权会被交还给事件循环，允许 FastAPI 在当前请求的 I/O 操作（例如，异步数据库查询或 HTTP 请求）等待期间，继续处理其他请求，从而提高并发效率。

异步和同步函数的选择：
异步函数（async def）：FastAPI 会直接在事件循环中运行，遇到 await 时自动切换任务，充分利用异步性能。
同步函数（def）：FastAPI 会自动将其放入一个线程池中执行，避免阻塞事件循环（但本质仍是同步执行，无法并行处理）。
总结：
只要涉及 I/O 操作（如数据库、网络请求），优先用 async def 并配合异步库，发挥 FastAPI的异步优势。
纯计算逻辑或无异步库可用时，用 def 即可（FastAPI 会自动处理线程池，无需手动管理）。

"""
