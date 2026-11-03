import psutil
import socket

def get_all_ips_psutil():
    """
    uv add psutil
    优点：能拿到所有网卡（含虚拟网卡、Docker、VPN），跨 Windows/Linux/macOS，还能配合 psutil.net_if_stats() 拿到网卡是否启用
    """
    """返回 {网卡名: [IPv4/IPv6 地址列表]}，含 MAC 和状态"""
    result = {}
    for name, addrs in psutil.net_if_addrs().items():
        ips = []
        for addr in addrs:
            if addr.family == socket.AF_INET:      # IPv4
                ips.append(addr.address)
            elif addr.family == socket.AF_INET6:   # IPv6
                ips.append(addr.address)
        result[name] = ips
    return result

def get_ips_socket():
    """
    局限：gethostname 解析在多网卡、容器、没配 hosts 的环境下经常只返回 127.0.1.1 或一个地址；UDP connect 只能拿到默认路由那张网卡的 IP。无法枚举全部网卡
    """

    import socket
    ips = set()
    # 1) 主机名解析（依赖 /etc/hosts 和 DNS，可能只返回一个）
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ips.add(info[4][0])
    except socket.gaierror:
        pass

    # 2) 通过 UDP "连接"外部地址拿默认出口 IP（不会真的发包）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass

    return ips




if __name__ == '__main__':
    # for iface, ips in get_all_ips_psutil().items():
    #     print(iface, ips)
    print(get_ips_socket())