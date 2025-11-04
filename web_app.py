"""小智 AI Web 控制台入口."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, status
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.webapp import WebRuntime
from src.webapp.audio import web_audio_bridge

app = FastAPI(title="XiaoZhi AI Web Console", version="2.0.0")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "web" / "templates"))
STATIC_DIR = BASE_DIR / "web" / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_runtime = WebRuntime()


async def get_runtime() -> WebRuntime:
    return _runtime


@app.on_event("startup")
async def startup_event() -> None:
    await _runtime.ensure_started()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await _runtime.shutdown()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse("index.html", {"request": request})


@app.websocket("/ws/audio")
async def audio_socket(websocket: WebSocket) -> None:
    await web_audio_bridge.handle_client(websocket)


@app.get("/api/status")
async def status_endpoint(runtime: WebRuntime = Depends(get_runtime)):
    return await runtime.get_status()


@app.get("/api/logs")
async def logs_endpoint(
    since: int | None = Query(default=None, ge=0),
    runtime: WebRuntime = Depends(get_runtime),
):
    logs = await runtime.get_logs(since)
    return {"logs": logs}


@app.post("/api/logs/reset")
async def reset_logs(runtime: WebRuntime = Depends(get_runtime)) -> JSONResponse:
    await runtime.reset_logs()
    return JSONResponse({"ok": True})


@app.post("/api/conversation/manual/start")
async def start_manual(runtime: WebRuntime = Depends(get_runtime)) -> JSONResponse:
    await runtime.start_manual_listening()
    return JSONResponse({"ok": True})


@app.post("/api/conversation/manual/stop")
async def stop_manual(runtime: WebRuntime = Depends(get_runtime)) -> JSONResponse:
    await runtime.stop_manual_listening()
    return JSONResponse({"ok": True})


@app.post("/api/conversation/auto/start")
async def start_auto(runtime: WebRuntime = Depends(get_runtime)) -> JSONResponse:
    await runtime.start_auto_conversation()
    return JSONResponse({"ok": True})


@app.post("/api/conversation/auto/stop")
async def stop_auto(runtime: WebRuntime = Depends(get_runtime)) -> JSONResponse:
    await runtime.stop_conversation()
    return JSONResponse({"ok": True})


@app.post("/api/conversation/send-text")
async def send_text(request: Request, runtime: WebRuntime = Depends(get_runtime)) -> JSONResponse:
    payload = await request.json()
    text = str(payload.get("text", ""))
    await runtime.send_text(text)
    return JSONResponse({"ok": True})


@app.post("/api/conversation/wake")
async def trigger_wake(request: Request, runtime: WebRuntime = Depends(get_runtime)) -> JSONResponse:
    payload = await request.json()
    text = str(payload.get("text") or "小智小智")
    await runtime.send_wake_word(text)
    return JSONResponse({"ok": True})


@app.post("/api/conversation/abort")
async def abort_speaking(runtime: WebRuntime = Depends(get_runtime)) -> JSONResponse:
    await runtime.abort_speaking()
    return JSONResponse({"ok": True})


@app.get("/api/camera/status")
async def camera_status(runtime: WebRuntime = Depends(get_runtime)) -> JSONResponse:
    status_payload = await runtime.get_status()
    camera = status_payload.get("camera", {})
    return JSONResponse({"status": camera.get("status"), "active": camera.get("active")})


@app.post("/api/camera/open")
async def open_camera(runtime: WebRuntime = Depends(get_runtime)) -> JSONResponse:
    await runtime.open_camera(force=True)
    return JSONResponse({"ok": True})


@app.post("/api/camera/close")
async def close_camera(runtime: WebRuntime = Depends(get_runtime)) -> JSONResponse:
    await runtime.close_camera()
    return JSONResponse({"ok": True})


@app.get("/api/camera/preview")
async def camera_preview(runtime: WebRuntime = Depends(get_runtime)) -> Response:
    data = await runtime.get_camera_preview()
    if not data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "无法获取预览画面")
    return Response(content=data, media_type="image/jpeg")


@app.post("/api/camera/capture")
async def camera_capture(request: Request, runtime: WebRuntime = Depends(get_runtime)) -> JSONResponse:
    payload = await request.json()
    question = str(payload.get("question", ""))
    result = await runtime.capture_photo(question)
    return JSONResponse({"result": result})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web_app:app", host="0.0.0.0", port=8000, reload=False)
