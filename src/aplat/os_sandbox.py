#!/usr/bin/env python3
"""M4b OS 级隔离：把工具真正关进容器里执行。

M4 的沙箱是**进程内策略层**——它拦得住「调了不该调的工具」，拦不住
「工具本身失控或恶意」。这一层补上 OS 级隔离，用 docker 做真隔离。

每条隔离都用**攻击性用例**验证，而不是声明（断言写在 m4b_os_sandbox.py）：

    I1 网络      --network none              容器内连不出去
    I2 文件系统  --read-only + tmpfs        根文件系统不可写
    I3 非 root   --user 65534:65534         进程不是 root
    I4 权限      --cap-drop ALL             有效 capability 为空
                 --security-opt no-new-privileges
    I5 内存      --memory 64m               内存炸弹被 OOM，不拖垮宿主
    I6 进程数    --pids-limit 32            fork 炸弹被限流
    I7 时间      外部 timeout + docker kill  死循环被外部终止

不挂载任何宿主目录，不用 --privileged。
"""
from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass

# 本机已有缓存的镜像。alpine 在本机无缓存且网络拉取不稳定，
# 所以固定用 python:3.12-slim（与模块自身解释器同大版本）。
IMAGE = "python:3.12-slim"

BASE_ARGS = [
    "--rm", "-i",
    "--network", "none",
    "--memory", "64m", "--memory-swap", "64m",
    "--pids-limit", "32",
    "--read-only",
    "--tmpfs", "/tmp:rw,size=8m,noexec,nosuid",
    "--user", "65534:65534",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
    "--workdir", "/tmp",
]


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    oom_killed: bool
    duration_ms: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class DockerSandbox:
    """把一段 Python 代码放进受限容器里跑，返回真实退出码与输出。

    这是 M4 策略层之下的**第二道防线**：策略层判「该不该执行」，
    这一层保证「执行了也跑不出去」。
    """

    def __init__(self, image: str = IMAGE, extra_args: list[str] | None = None):
        self.image = image
        self.extra = list(extra_args or [])

    def available(self) -> tuple[bool, str]:
        try:
            r = subprocess.run(["docker", "image", "inspect", self.image],
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"docker 不可用：{e}"
        if r.returncode != 0:
            return False, f"镜像 {self.image} 不在本机：{r.stderr.strip()[:120]}"
        return True, "ok"

    @staticmethod
    def _await_gone(name: str, timeout_s: float = 10.0) -> bool:
        """轮询等待容器从 `docker ps -a` 消失。返回是否真的消失。"""
        import time
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            r = subprocess.run(
                ["docker", "ps", "-a", "--filter", f"name=^{name}$",
                 "--format", "{{.Names}}"], capture_output=True, text=True)
            if not r.stdout.strip():
                return True
            time.sleep(0.25)
        return False

    def run(self, code: str, timeout_s: int = 20) -> ExecResult:
        import time
        name = f"aplat-sbx-{uuid.uuid4().hex[:10]}"
        cmd = (["docker", "run", "--name", name] + BASE_ARGS + self.extra
               + [self.image, "python3", "-c", code])
        t0 = time.monotonic()
        timed_out = False
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout_s)
            rc, out, err = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired as e:
            # 客户端超时后必须真的清掉容器。踩坑：`docker kill` 是异步的，
            # `--rm` 的清理由 dockerd 在稍后完成，所以 kill 之后立刻 `docker ps -a`
            # 仍能看到残留容器（本仓的「无残留容器」断言就是这么抓出来的）。
            # 正解是 `docker rm -f`（kill + remove 一步），再轮询确认消失。
            timed_out = True
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            self._await_gone(name)
            rc, out, err = 124, (e.stdout or b"").decode(errors="replace") \
                if isinstance(e.stdout, bytes) else (e.stdout or ""), "timeout"
        dur = int((time.monotonic() - t0) * 1000)
        blob = (err or "") + (out or "")
        oom = ("Killed" in blob or rc == 137
               or "MemoryError" in blob or "Cannot allocate memory" in blob)
        return ExecResult(rc, out or "", err or "", timed_out, oom, dur)
