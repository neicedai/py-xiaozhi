"""管理 main.py 子进程的控制器."""

from __future__ import annotations

import asyncio
import os
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, List, Optional

from fastapi import HTTPException, status

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAIN_FILE = PROJECT_ROOT / "main.py"


@dataclass
class ProcessStatus:
    """描述子进程运行状态."""

    status: str = "idle"
    mode: Optional[str] = None
    protocol: Optional[str] = None
    skip_activation: bool = False
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    message: str = "服务尚未启动"


class ProcessController:
    """通过子进程运行 main.py 并收集日志."""

    def __init__(self, max_logs: int = 2000) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._status = ProcessStatus()
        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._monitor_task: asyncio.Task | None = None
        self._log_entries: Deque[Dict[str, object]] = deque(maxlen=max_logs)
        self._log_counter: int = 0
        self._lock = asyncio.Lock()
        self._max_logs = max_logs

    # ------------------------------------------------------------------
    # 状态访问
    # ------------------------------------------------------------------
    def current_status(self) -> ProcessStatus:
        return self._status

    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def get_logs(self, since: Optional[int] = None) -> List[Dict[str, object]]:
        if since is None:
            return list(self._log_entries)
        return [entry for entry in self._log_entries if entry["id"] > since]

    # ------------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------------
    async def start(self, *, mode: str, protocol: str, skip_activation: bool) -> None:
        async with self._lock:
            if self.is_running():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="应用已在运行中，请先停止后再启动",
                )

            if not MAIN_FILE.exists():
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"未找到 main.py ({MAIN_FILE})",
                )

            self._append_log("SYSTEM", "启动子进程...")
            self._status = ProcessStatus(
                status="starting",
                mode=mode,
                protocol=protocol,
                skip_activation=skip_activation,
                message="正在启动小智 AI 应用...",
            )
            self._status.exit_code = None

            cmd = [sys.executable, str(MAIN_FILE), "--mode", mode, "--protocol", protocol]
            if skip_activation:
                cmd.append("--skip-activation")

            env = os.environ.copy()
            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    cwd=str(PROJECT_ROOT),
                )
            except FileNotFoundError:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="无法启动 Python 解释器",
                ) from None

            self._process = process
            self._status.status = "running"
            self._status.pid = process.pid
            self._status.message = "应用正在运行"
            self._append_log(
                "SYSTEM",
                f"子进程已启动 (PID: {process.pid})，模式: {mode}, 协议: {protocol}",
            )

            # 启动日志读取与进程监控
            assert process.stdout and process.stderr
            self._stdout_task = asyncio.create_task(
                self._read_stream(process.stdout, label="STDOUT")
            )
            self._stderr_task = asyncio.create_task(
                self._read_stream(process.stderr, label="STDERR")
            )
            self._monitor_task = asyncio.create_task(self._monitor_process())

    async def stop(self) -> None:
        async with self._lock:
            if not self.is_running():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="应用未在运行，无需停止",
                )
            assert self._process is not None
            process = self._process
            self._status.status = "stopping"
            self._status.message = "正在请求应用停止..."
            self._append_log("SYSTEM", "发送终止信号")

        try:
            process.terminate()
        except ProcessLookupError:
            pass

        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            self._append_log("SYSTEM", "终止超时，强制结束进程")
            process.kill()
            await process.wait()

        if self._monitor_task:
            await self._monitor_task

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    async def _monitor_process(self) -> None:
        if not self._process:
            return
        process = self._process
        returncode = await process.wait()

        if self._stdout_task:
            await self._stdout_task
        if self._stderr_task:
            await self._stderr_task

        async with self._lock:
            self._status.pid = None
            self._status.exit_code = returncode
            if returncode == 0:
                self._status.status = "stopped"
                self._status.message = "应用已正常退出"
            else:
                self._status.status = "failed"
                self._status.message = f"应用异常退出（代码 {returncode}）"
            self._process = None
            self._stdout_task = None
            self._stderr_task = None
            self._monitor_task = None

        self._append_log("SYSTEM", f"子进程结束，退出码 {returncode}")

    async def _read_stream(
        self, stream: asyncio.StreamReader, *, label: str
    ) -> None:
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    self._append_log(label, text)
        except Exception as exc:  # pragma: no cover - 防御性日志
            self._append_log("SYSTEM", f"读取{label}失败: {exc}")

    def _append_log(self, source: str, message: str) -> None:
        self._log_counter += 1
        entry = {
            "id": self._log_counter,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "source": source,
            "message": message,
        }
        self._log_entries.append(entry)

    def reset_logs(self) -> None:
        self._log_entries.clear()
        self._log_counter = 0
        self._append_log("SYSTEM", "日志已清空")

