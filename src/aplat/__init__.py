"""
agent-platform 0.1.0：一组可离线重跑的教学模块，不是生产控制面。

离线入口只用 Python 标准库和 SQLite。
``m1 --live`` / ``m3b --live`` 会访问本机 ollama。
M4b / M6 需要 Docker；本仓库不拉取镜像，也不创建 PostgreSQL 容器。
入口在 ``src/m*.py``，共用代码在 ``src/aplat/``。

数字以已提交的 ``results/*.json`` 为准。墙钟和加速比每次重跑都会变，
不能把某一次的倍数写成承诺。
"""

__version__ = "0.1.0"
LAB = "agent-platform"
