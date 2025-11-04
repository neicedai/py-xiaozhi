"""小智 AI Web 控制台入口."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.webapp import ProcessController

app = FastAPI(title="XiaoZhi AI Web Console", version="1.0.0")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "web" / "templates"))
STATIC_DIR = BASE_DIR / "web" / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_controller = ProcessController()


async def get_controller() -> ProcessController:
    return _controller


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse("index.html", {"request": request})


@app.get("/api/status")
async def status_endpoint(controller: ProcessController = Depends(get_controller)) -> Dict[str, Any]:
    status_obj = controller.current_status()
    return {
        "status": status_obj.status,
        "mode": status_obj.mode,
        "protocol": status_obj.protocol,
        "skipActivation": status_obj.skip_activation,
        "pid": status_obj.pid,
        "exitCode": status_obj.exit_code,
        "message": status_obj.message,
    }


@app.post("/api/start")
async def start_endpoint(
    request: Request,
    controller: ProcessController = Depends(get_controller),
) -> JSONResponse:
    data = await request.json()
    mode = data.get("mode", "cli")
    protocol = data.get("protocol", "websocket")
    skip_activation = bool(data.get("skipActivation", False))

    if mode not in {"cli", "gui"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的运行模式")
    if protocol not in {"websocket", "mqtt"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的通信协议")

    await controller.start(mode=mode, protocol=protocol, skip_activation=skip_activation)
    return JSONResponse({"ok": True})


@app.post("/api/stop")
async def stop_endpoint(controller: ProcessController = Depends(get_controller)) -> JSONResponse:
    await controller.stop()
    return JSONResponse({"ok": True})


@app.get("/api/logs")
async def logs_endpoint(
    since: int | None = Query(default=None, ge=0),
    controller: ProcessController = Depends(get_controller),
) -> Dict[str, Any]:
    return {"logs": controller.get_logs(since)}


@app.post("/api/logs/reset")
async def reset_logs_endpoint(controller: ProcessController = Depends(get_controller)) -> JSONResponse:
    controller.reset_logs()
    return JSONResponse({"ok": True})


@app.on_event("shutdown")
async def _cleanup() -> None:
    if _controller.is_running():
        await _controller.stop()
        # 等待所有后台任务完成
        await asyncio.sleep(0.1)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web_app:app", host="0.0.0.0", port=8000, reload=False)
