#!/usr/bin/env bash
#
# kill-port.sh - 查找并终止占用指定端口的进程
#
# 用法:
#   ./kill-port.sh <端口> [选项]
#
# 参数:
#   <端口>          要检查的端口号（1-65535）
#
# 选项:
#   -f, --force     跳过确认，直接发送信号
#   -9, --sigkill   使用 SIGKILL（强制终止），默认为 SIGTERM
#   -h, --help      显示帮助信息
#
# 示例:
#   ./kill-port.sh 8080              # 交互式确认后发送 SIGTERM
#   ./kill-port.sh 8080 --force      # 不确认，直接发送 SIGTERM
#   ./kill-port.sh 8080 -f -9        # 不确认，直接 SIGKILL
#
# 退出码:
#   0  成功（包括端口空闲、用户取消）
#   1  参数错误、缺少依赖、操作失败
#   2  端口非法
#

# 启用未定义变量检查，减少拼写错误
set -u

# ---------- 帮助信息 ----------
usage() {
  cat <<'EOF'
用法: kill-port.sh <端口> [选项]

查找并终止占用指定端口的进程。

参数:
  <端口>          要检查的端口号（1-65535）

选项:
  -f, --force     跳过确认，直接发送信号
  -9, --sigkill   使用 SIGKILL（强制终止）而不是默认的 SIGTERM
  -h, --help      显示此帮助信息

示例:
  kill-port.sh 8080              # 交互式确认后发送 SIGTERM
  kill-port.sh 8080 --force      # 不确认，直接发送 SIGTERM
  kill-port.sh 8080 -f -9        # 不确认，直接 SIGKILL

说明:
  默认发送 SIGTERM，等待 3 秒后检查进程是否退出。
  如果进程未退出，可再次执行并加上 --sigkill。
  查找范围包括 TCP 监听端口和 UDP 端口。
  如果提示端口空闲但确认有占用，请尝试用 sudo 运行。
EOF
}

# ---------- 默认参数 ----------
PORT=""          # 目标端口，由命令行参数填充
FORCE=0          # 1 表示跳过确认
SIGKILL=0        # 1 表示使用 SIGKILL
WAIT_SECONDS=3   # 发送 SIGTERM 后等待进程退出的秒数

# ---------- 解析命令行参数 ----------
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -f|--force)
      FORCE=1
      shift
      ;;
    -9|--kill|--sigkill)
      SIGKILL=1
      shift
      ;;
    --)
      # 结束选项解析，后面的参数按位置参数处理
      shift
      break
      ;;
    -*)
      echo "错误：未知选项 $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      # 第一个非选项参数作为端口
      if [ -z "$PORT" ]; then
        PORT="$1"
      else
        echo "错误：多余的参数 $1" >&2
        exit 1
      fi
      shift
      ;;
  esac
done

# 处理 "--" 之后的位置参数
if [ -z "$PORT" ] && [ $# -gt 0 ]; then
  PORT="$1"
  shift
fi

# ---------- 校验端口 ----------
if [ -z "$PORT" ]; then
  echo "错误：请指定端口号。" >&2
  usage >&2
  exit 1
fi

# 必须是纯数字
if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
  echo "错误：端口必须是数字，当前为 '$PORT'。" >&2
  exit 2
fi

# 使用 10# 强制按十进制解析，避免 "080" 被当作八进制
port_num=$((10#$PORT))

# 端口合法范围 1-65535
if [ "$port_num" -lt 1 ] || [ "$port_num" -gt 65535 ]; then
  echo "错误：端口号必须在 1-65535 之间，当前为 $port_num。" >&2
  exit 2
fi

# 规范化端口，去掉可能的前导零
PORT=$port_num

# ---------- 检查依赖 ----------
if ! command -v lsof >/dev/null 2>&1; then
  echo "错误：未找到 lsof 命令。" >&2
  echo "请先安装 lsof，例如：" >&2
  echo "  Debian/Ubuntu: sudo apt install lsof" >&2
  echo "  RHEL/CentOS:   sudo yum install lsof" >&2
  echo "  macOS:         brew install lsof" >&2
  exit 1
fi

# ---------- 查找占用端口的进程 ----------
# 同时检查：
#   1. TCP 监听端口（端口冲突最常见的情况）
#   2. UDP 端口
# -t 只输出 PID，sort -u 去重
#
# 注意：UDP 查询会匹配使用该端口的 UDP 套接字，
# 包括远程端口恰好为该值的连接。如不需要可删掉第二行。
pids=$(
  {
    lsof -t -iTCP:"$PORT" -sTCP:LISTEN
    lsof -t -iUDP:"$PORT"
  } | sort -u
)

# 没有找到占用进程
if [ -z "$pids" ]; then
  echo "端口 $PORT 当前没有被占用。"
  echo "提示：如果确认有进程占用但此处为空，可能是权限不足，请尝试用 sudo 运行本脚本。" >&2
  exit 0
fi

# ---------- 展示进程信息 ----------
echo "占用端口 $PORT 的 PID："
echo "$pids"
echo
echo "进程信息："

# 把多行 PID 合并成逗号分隔，供 ps -p 使用
pid_csv=$(echo "$pids" | paste -sd, -)
ps -p "$pid_csv" -o pid,user,comm,args || true
echo

# ---------- 确认 ----------
if [ "$FORCE" -eq 0 ]; then
  # 非交互式环境（例如管道、CI）下无法询问，必须显式加 --force
  if [ ! -t 0 ]; then
    echo "错误：当前不是交互式终端，无法确认。" >&2
    echo "请在交互式终端中运行，或加 --force 跳过确认。" >&2
    exit 1
  fi

  read -r -p "确认终止以上进程？(y/N): " answer
  case "$answer" in
    y|Y)
      # 继续执行
      ;;
    *)
      echo "已取消。"
      exit 0
      ;;
  esac
fi

# ---------- 发送信号 ----------
if [ "$SIGKILL" -eq 1 ]; then
  SIG="KILL"
else
  SIG="TERM"
fi

echo "准备向以下 PID 发送 SIG$SIG：$pids"
failed=0

# 逐个发送信号，便于记录失败项
for pid in $pids; do
  if kill -"$SIG" "$pid" 2>/dev/null; then
    echo "  已发送 SIG$SIG -> PID $pid"
  else
    echo "  警告：无法向 PID $pid 发送信号（可能权限不足或进程已退出）" >&2
    failed=1
  fi
done

# ---------- SIGTERM 后等待并检查 ----------
# SIGKILL 是立即生效的，不需要等待检查
if [ "$SIG" = "TERM" ]; then
  echo "等待 ${WAIT_SECONDS}s 让进程退出..."
  sleep "$WAIT_SECONDS"

  remaining=""
  for pid in $pids; do
    # kill -0 只检查进程是否存在，不发送信号
    if kill -0 "$pid" 2>/dev/null; then
      remaining="$remaining $pid"
    fi
  done

  if [ -n "$remaining" ]; then
    echo "以下进程仍在运行：$remaining" >&2
    echo "可再次执行并加上 -9/--sigkill 强制终止：" >&2
    echo "  $0 $PORT -f -9" >&2
    failed=1
  else
    echo "所有目标进程已退出。"
  fi
fi

# ---------- 退出码 ----------
if [ "$failed" -ne 0 ]; then
  exit 1
fi
exit 0