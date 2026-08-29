"""
PyInstaller runtime hook: 确保 glibc 的 NSS 模块能正确加载。

问题背景：
    PyInstaller 的 bootloader 会将 _internal 目录添加到动态链接库搜索路径，
    这可能覆盖系统路径，导致 glibc 无法加载 DNS 解析所需的 NSS 模块
    (libnss_dns.so.2, libresolv.so.2)，socket.getaddrinfo() 返回
    [Errno -2] Name or service not known。

修复方式：
    在 Python 初始化后将系统库路径重新加入动态链接库搜索路径。
"""
import ctypes
import os


def _fix_nss_path() -> None:
    """将系统库路径追加到 LD_LIBRARY_PATH，确保 NSS 模块可被 glibc 发现。"""
    system_lib_paths: list[str] = [
        "/usr/lib/x86_64-linux-gnu",
        "/lib/x86_64-linux-gnu",
        "/usr/lib",
        "/lib",
    ]
    current_ld_path: str = os.environ.get("LD_LIBRARY_PATH", "")
    existing: set[str] = set(current_ld_path.split(":")) if current_ld_path else set()

    new_paths: list[str] = [p for p in system_lib_paths if p not in existing and os.path.isdir(p)]
    if new_paths:
        updated: str = ":".join(new_paths + ([current_ld_path] if current_ld_path else []))
        os.environ["LD_LIBRARY_PATH"] = updated


_fix_nss_path()
