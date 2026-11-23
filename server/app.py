# -*- coding: utf-8 -*-
"""FastAPI 应用：路由 + 静态挂载 + 统一错误格式（docs/INTERFACES.md §4）。

- CORS 全开（本地演示）；
- ``/static`` → ``server/static``；``/output`` → ``output``；``/web`` 与 ``/`` → ``web/``；
- ``/api/*`` 未命中返回 JSON 404，错误统一 ``{"detail": "人话"}``；
- **engine/ 一律惰性 import**（在 service.py 内部），engine 未就绪时本模块照常可 import；
- ``web/`` 目录可能暂时为空/不存在，挂载必须容错（不存在时 ``/`` 给占位页）。
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from server import __version__, service, store

ROOT = store.ROOT
WEB_DIR = ROOT / "web"
SERVER_STATIC_DIR = service.STATIC_DIR
OUTPUT_DIR = store.OUTPUT_DIR

_PLACEHOLDER_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>取景助手 · 前端未就绪</title>
<style>body{background:#111;color:#eee;font-family:system-ui,"Microsoft YaHei";display:grid;
place-items:center;height:100vh;margin:0}main{text-align:center;line-height:1.9}
code{background:#222;padding:2px 6px;border-radius:4px}</style></head>
<body><main><h1>取景助手后端已启动</h1>
<p>前端文件 <code>web/index.html</code> 还没生成（由 web 队友并行编写中）。</p>
<p>可以先看接口：<a style="color:#e8752c" href="/api/health">/api/health</a> ·
<a style="color:#e8752c" href="/api/demo">/api/demo</a> ·
<a style="color:#e8752c" href="/docs">/docs</a></p></main></body></html>"""


def _ensure_runtime_dirs() -> None:
    """启动时确保静态/输出目录存在（挂载与落盘都依赖它们）。"""
    store.ensure_dirs()
    SERVER_STATIC_DIR.mkdir(parents=True, exist_ok=True)
    (SERVER_STATIC_DIR / "demo").mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _ensure_runtime_dirs()
    try:
        service.demo_service()  # scenarios.json 缺失时生成一次（失败不阻塞启动）
    except Exception:  # pragma: no cover - demo 生成失败不影响服务
        pass
    yield


_ensure_runtime_dirs()

app = FastAPI(
    title="AI 现场拍摄指导助手",
    version=__version__,
    description="浏览器取景 → 抓帧 → 服务端提特征 → 引擎给 3 条可执行机位动作；拍完自动粗剪 EDL。",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# 请求体模型
# ---------------------------------------------------------------------------
class SuggestBody(BaseModel):
    session_id: Optional[str] = None
    intent: Optional[str] = ""
    frame_features: Optional[dict[str, Any]] = None
    frame_base64: Optional[str] = None


class FeedbackBody(BaseModel):
    session_id: Optional[str] = None
    action_id: Optional[str] = None
    rank: Optional[int] = None
    useful: Optional[bool] = None
    intent_id: Optional[str] = None


class ShotBody(BaseModel):
    session_id: Optional[str] = None
    intent: Optional[str] = None
    intent_id: Optional[str] = None
    quality_score: Optional[float] = None
    followed_advice: Optional[Any] = None
    advice_action_ids: Optional[list[str]] = None
    duration_sec: Optional[float] = None
    stability: Optional[float] = None
    focus_ok: Optional[Any] = None
    has_mistake: Optional[Any] = None
    scene_type: Optional[str] = None
    frame_features: Optional[dict[str, Any]] = None
    before_image: Optional[str] = None
    after_image: Optional[str] = None
    filename: Optional[str] = None
    notes: Optional[str] = None
    duplicate_of: Optional[str] = None
    in_point: Optional[float] = None
    out_point: Optional[float] = None


class EditBody(BaseModel):
    source: Optional[str] = "materials"


def _dump(model: BaseModel) -> dict[str, Any]:
    """pydantic v2 / v1 通用的 dict 转换。"""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return dict(model)  # pragma: no cover - pydantic v1


# ---------------------------------------------------------------------------
# 统一错误响应（契约：{"detail": "人话"}）
# ---------------------------------------------------------------------------
def _compact_validation_errors(exc: RequestValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors()[:3]:
        location = ".".join(str(item) for item in (error.get("loc") or []) if item != "body")
        message = error.get("msg") or "格式错误"
        parts.append(f"{location or 'body'}：{message}")
    return "；".join(parts) or "请求体格式不正确"


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):  # 允许自定义结构化错误
        content: dict[str, Any] = detail
    else:
        text = str(detail or "")
        if exc.status_code == 404 and text.strip().lower() in ("not found", "not found."):
            text = f"找不到接口或资源：{request.url.path}"
        content = {"detail": text}
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": f"请求参数格式不对：{_compact_validation_errors(exc)}"},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": f"服务器内部错误：{type(exc).__name__}: {exc}"},
    )


# ---------------------------------------------------------------------------
# API 路由（docs/INTERFACES.md §4）
# ---------------------------------------------------------------------------
@app.get("/api/health")
def api_health() -> dict[str, Any]:
    return service.health_service()


@app.get("/api/demo")
def api_demo() -> dict[str, Any]:
    return service.demo_service()


@app.post("/api/suggest")
def api_suggest(body: SuggestBody) -> dict[str, Any]:
    payload = _dump(body)
    try:
        return service.suggest_service(
            intent=payload.get("intent") or "",
            frame_features=payload.get("frame_features"),
            frame_base64=payload.get("frame_base64"),
            session_id=payload.get("session_id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/feedback")
def api_feedback(body: FeedbackBody) -> dict[str, Any]:
    return service.feedback_service(_dump(body))


@app.post("/api/shots")
def api_record_shot(body: ShotBody) -> dict[str, Any]:
    return service.record_shot_service(_dump(body))


@app.get("/api/shots")
def api_list_shots() -> dict[str, Any]:
    return service.list_shots_service()


@app.post("/api/shots/reset")
def api_reset_shots() -> dict[str, Any]:
    return service.reset_shots_service()


@app.get("/api/materials")
def api_materials() -> dict[str, Any]:
    return service.materials_service()


@app.post("/api/edit")
def api_edit(body: EditBody) -> dict[str, Any]:
    payload = _dump(body)
    try:
        return service.edit_service(payload.get("source") or "materials")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/edit/export/rough_cut.json")
def api_export_edl_json() -> Response:
    data = service.export_edit_payload()
    body = {
        "edl": data.get("edl"),
        "rough_cut": data.get("rough_cut"),
        "organization": data.get("organization"),
    }
    return Response(
        content=json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="rough_cut.json"'},
    )


@app.get("/api/edit/export/rough_cut.bat")
def api_export_bat() -> Response:
    data = service.export_edit_payload()
    ffmpeg = data.get("ffmpeg") or {}
    script = str(ffmpeg.get("batch_script") or "")
    if not script.strip():
        script = "@echo off\r\nrem 没有可用的粗剪结果，请先调用 POST /api/edit\r\n"
    if not script.lstrip().lower().startswith("@echo off"):
        script = "@echo off\r\n" + script
    return Response(
        content=script.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="rough_cut.bat"'},
    )


@app.get("/api/dev/ca.pem", include_in_schema=False)
def dev_ca_pem() -> Response:
    """给手机下载开发 CA 证书（只暴露 ca.pem，绝不暴露 ca-key.pem / server-key.pem）。

    手机安装这个 CA 后，https://<电脑IP>:8443 不再报证书错误（相机/PWA 安装更稳），
    安装步骤见 README「手机真机」一节。
    注意：必须定义在 /api/{rest:path} 兜底路由之前，否则会被兜底 404 吃掉。
    """
    ca_path = ROOT / "certs" / "ca.pem"
    if not ca_path.exists():
        raise HTTPException(
            status_code=404,
            detail="开发证书还没生成：先在电脑上运行 python tools/make_dev_cert.py",
        )
    return FileResponse(
        str(ca_path),
        media_type="application/x-pem-file",
        filename="learnbuddy-dev-ca.pem",
    )


@app.api_route(
    "/api/{rest:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def api_not_found(rest: str) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": f"找不到接口：/api/{rest}"})


# ---------------------------------------------------------------------------
# 首页 + 静态挂载（注意顺序：先 API，后 mount；mount "/" 放最后）
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def index() -> Response:
    index_path = WEB_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), media_type="text/html; charset=utf-8")
    return HTMLResponse(content=_PLACEHOLDER_HTML, status_code=200)


def _safe_mount(path: str, directory: Path, *, name: str, html: bool = False) -> None:
    """容错挂载：目录不存在时也不要在 import 阶段崩。"""
    try:
        app.mount(
            path,
            StaticFiles(directory=str(directory), html=html, check_dir=False),
            name=name,
        )
    except Exception:  # pragma: no cover - 挂载失败不影响 API
        pass


_safe_mount("/static", SERVER_STATIC_DIR, name="static")
_safe_mount("/output", OUTPUT_DIR, name="output")
_safe_mount("/web", WEB_DIR, name="web-dir")
_safe_mount("/", WEB_DIR, name="web-root", html=True)
