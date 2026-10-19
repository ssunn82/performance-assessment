"""
中层绩效考核系统 - 主应用
FastAPI + Jinja2 + SQLite
支持：多季度会话、Excel导入、二维码导出、扫码打分、自动计分、结果导出、登录认证
"""
import os
import io
import qrcode
import hashlib
import secrets
from io import BytesIO
from typing import List, Optional
from urllib.parse import quote
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form, Depends
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel

from database import get_db

# ==================== 配置 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 会话密钥：优先读取环境变量，未设置时回退到默认值（仅限本地开发）
SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "performance-assessment-2026-secret")
if SECRET_KEY == "performance-assessment-2026-secret":
    import warnings
    warnings.warn(
        "⚠️  正在使用默认 SESSION_SECRET_KEY，生产环境请通过环境变量设置强随机值："
        "export SESSION_SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
    )

for sub in ["static/css", "static/js", "static/images", "static/qrcodes", "data"]:
    os.makedirs(os.path.join(BASE_DIR, sub), exist_ok=True)

app = FastAPI(title="中层绩效考核系统")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.cache = None


# ==================== 数据模型 ====================
class SessionCreate(BaseModel):
    name: str
    quarter: str


class AssessorCreate(BaseModel):
    name: str
    employee_id: Optional[str] = None


class EvaluateeCreate(BaseModel):
    name: str
    employee_id: Optional[str] = None
    position: Optional[str] = None
    department: Optional[str] = None
    group_type: Optional[str] = None
    department_ref_score: Optional[str] = None


class RelationCreate(BaseModel):
    assessor_id: int
    evaluatee_id: int
    weight: float = 1.0


class IndicatorCreate(BaseModel):
    name: str
    weight: float = 1.0
    max_score: float = 100


class ScoreBatchSave(BaseModel):
    evaluatee_id: int
    scores: List[dict]


class SettingsUpdate(BaseModel):
    instruction: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    old_password: str
    new_password: str


class LifecycleRequest(BaseModel):
    action: str  # start_test | start | pause | resume | end
    clear_scores: bool = False


# ==================== 认证辅助 ====================
def is_logged_in(request: Request) -> bool:
    return request.session.get("logged_in") is True


def require_login(request: Request):
    if not is_logged_in(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


# ==================== 辅助函数 ====================
def _base_url(request: Request) -> str:
    env_url = os.getenv("BASE_URL")
    if env_url:
        return env_url.rstrip("/")
    return str(request.base_url).rstrip("/")


# ==================== 考核链接访问令牌 ====================
# 安全背景：原实现用自增主键 /assess/{id} 作为打分入口，任何人把 URL 数字
# 改一下就能进入他人打分页并覆盖分数（IDOR）。现改为不可猜测的随机令牌，
# 且令牌可用「重置二维码」作废旧链接。

def _verify_assessor_access(request: Request, db, assessor_id: int) -> bool:
    """校验请求方是否有权操作该考核人的打分。

    通过方式（二选一）：
      1. 携带该考核人的 access_token（扫码进入的正常路径）
      2. 已登录管理后台（管理员本人在后台预览/代操作，便于内部调试）
    令牌来自请求头 X-Access-Token 或查询参数 t。
    """
    if is_logged_in(request):
        return True
    token = request.headers.get("X-Access-Token") or request.query_params.get("t")
    if not token:
        return False
    row = db.get_assessor_by_token(token)
    # 令牌必须属于「该」考核人，否则视为越权
    return bool(row) and row["id"] == assessor_id


# ==================== 登录/登出 ====================
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {})


@app.post("/api/login", response_class=JSONResponse)
async def login_api(request: Request, req: LoginRequest):
    db = get_db()
    if db.verify_admin(req.username, req.password):
        request.session["logged_in"] = True
        request.session["username"] = req.username
        return {"success": True, "message": "登录成功"}
    return JSONResponse({"success": False, "message": "用户名或密码错误"}, status_code=401)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.post("/api/change-password", response_class=JSONResponse)
async def change_password_api(request: Request, req: PasswordChange):
    if not is_logged_in(request):
        return JSONResponse({"success": False, "message": "请先登录"}, status_code=401)
    db = get_db()
    username = request.session.get("username", "admin")
    if db.change_password(username, req.old_password, req.new_password):
        return {"success": True, "message": "密码修改成功"}
    return JSONResponse({"success": False, "message": "原密码错误"}, status_code=400)


# ==================== 页面路由 ====================
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not is_logged_in(request):
        return RedirectResponse("/login", status_code=303)
    db = get_db()
    session = db.get_current_session()
    return templates.TemplateResponse(request, "index.html", {
        "sessions": db.get_all_sessions(),
        "current_session": session,
        "assessors": db.get_all_assessors(),
        "evaluatees": db.get_all_evaluatees(),
        "indicators": db.get_all_indicators(),
        "relations": db.get_all_relations(),
        "instruction": db.get_setting("assess_instruction", ""),
        "username": request.session.get("username", "admin"),
    })


@app.get("/assess/{assessor_id}", response_class=HTMLResponse)
async def assess_page(request: Request, assessor_id: int):
    """兼容入口：仍用 id 定位，但必须提供有效令牌，否则拒绝。

    仅管理员登录态可无令牌访问（后台预览用）。
    """
    db = get_db()
    token = request.headers.get("X-Access-Token") or request.query_params.get("t")
    if not is_logged_in(request):
        if not token:
            return templates.TemplateResponse(request, "error.html", {
                "message": "链接无效或已失效，请使用最新的二维码重新扫码"
            })
        row = db.get_assessor_by_token(token)
        if not row or row["id"] != assessor_id:
            # 令牌无效，或令牌与 URL 中的 id 不匹配（试图改 id 访问他人）
            return templates.TemplateResponse(request, "error.html", {
                "message": "链接无效或已失效，请使用最新的二维码重新扫码"
            })
    return await _render_assess_page(request, assessor_id, token)


@app.get("/s/{token}", response_class=HTMLResponse)
async def assess_by_token(request: Request, token: str):
    """令牌版打分入口（二维码实际指向的地址）：不可猜测、可重置作废"""
    db = get_db()
    row = db.get_assessor_by_token(token)
    if not row:
        return templates.TemplateResponse(request, "error.html", {
            "message": "链接无效或已失效，请联系管理员重新获取二维码"
        })
    return await _render_assess_page(request, row["id"], token)


async def _render_assess_page(request: Request, assessor_id: int, token: str):
    db = get_db()
    session_id = db.get_current_session_id()
    assessor = db.get_assessor(assessor_id)

    if not assessor:
        return templates.TemplateResponse(request, "error.html", {
            "message": "考核人不存在或二维码已失效"
        })

    lifecycle = db.get_session_lifecycle(session_id)
    session = db.get_current_session()

    # 未启动/暂停/结束：完全不可进入打分，仅显示状态提示页
    lock_message = LIFECYCLE_LOCK_MESSAGES.get(lifecycle)
    if lock_message:
        return templates.TemplateResponse(request, "assess.html", {
            "assessor": assessor,
            "lock_message": lock_message,
            "lock_label": LIFECYCLE_LABELS.get(lifecycle, lifecycle),
            "is_testing": False,
            "session_name": session['name'] if session else "考核",
            "relations": [],
            "indicators": [],
            "score_map": {},
            "evaluatee_scores": {},
            "completed": False,
            "instruction": "",
            "access_token": token,
        })

    completed = db.is_assessor_completed(assessor_id, session_id)
    relations = db.get_assessor_relations(assessor_id, session_id)
    indicators = db.get_all_indicators()
    score_map = db.get_score_map(assessor_id, session_id)

    evaluatee_scores = {}
    for rel in relations:
        eid = rel['evaluatee_id']
        total = 0
        for ind in indicators:
            val = score_map.get(eid, {}).get(ind['id'])
            if val is not None:
                total += val * ind['weight']
        evaluatee_scores[eid] = round(total, 2)

    return templates.TemplateResponse(request, "assess.html", {
        "assessor": assessor,
        "relations": relations,
        "indicators": indicators,
        "score_map": score_map,
        "evaluatee_scores": evaluatee_scores,
        "completed": completed,
        "instruction": db.get_setting("assess_instruction", ""),
        "session_name": session['name'] if session else "考核",
        "is_testing": lifecycle == 'testing',
        "access_token": token,
    })


@app.get("/progress", response_class=HTMLResponse)
async def progress_page(request: Request):
    if not is_logged_in(request):
        return RedirectResponse("/login", status_code=303)
    db = get_db()
    session = db.get_current_session()
    session_id = session['id'] if session else db.get_current_session_id()
    progress = db.get_all_progress(session_id)
    stats = db.get_session_stats(session_id)
    return templates.TemplateResponse(request, "progress.html", {
        "progress": progress,
        "stats": stats,
        "session": session,
    })


@app.get("/results", response_class=HTMLResponse)
async def results_page(request: Request):
    if not is_logged_in(request):
        return RedirectResponse("/login", status_code=303)
    db = get_db()
    session_id = db.get_current_session_id()
    session = db.get_current_session()
    final_scores = db.get_final_scores(session_id)
    progress = db.get_all_progress(session_id)
    completed_count = sum(1 for p in progress if p['status'] == '已完成')
    total_assessors = len(progress)
    total_evaluatees = len(db.get_all_evaluatees())

    return templates.TemplateResponse(request, "results.html", {
        "final_scores": final_scores,
        "completed_count": completed_count,
        "total_assessors": total_assessors,
        "total_evaluatees": total_evaluatees,
        "session": session,
    })


# ==================== 会话 API ====================
@app.get("/api/sessions", response_class=JSONResponse)
async def list_sessions(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    return {"sessions": db.get_all_sessions()}


@app.post("/api/sessions", response_class=JSONResponse)
async def create_session(request: Request, req: SessionCreate):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    sid = db.create_session(req.name, req.quarter)
    return {"id": sid, "name": req.name, "quarter": req.quarter}


@app.post("/api/sessions/{session_id}/activate", response_class=JSONResponse)
async def activate_session(request: Request, session_id: int):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    db.set_current_session(session_id)
    return {"message": "已切换考核任务"}


@app.delete("/api/sessions/{session_id}", response_class=JSONResponse)
async def delete_session(request: Request, session_id: int):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    db.delete_session(session_id)
    return {"message": "已删除考核任务"}


@app.post("/api/sessions/{session_id}/clear")
async def clear_session_data(request: Request, session_id: int):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    db.clear_session_relations(session_id)
    return {"message": "已清空当前考核任务的关系和打分"}


# ==================== 考核任务生命周期 API ====================
LIFECYCLE_LABELS = {
    "draft": "未启动",
    "testing": "测试模式",
    "running": "正式考核",
    "paused": "已暂停",
    "ended": "已结束",
}
LIFECYCLE_LOCK_MESSAGES = {
    "draft": "考核尚未启动，请稍后再试。",
    "paused": "考核已暂停，请等待管理员恢复后继续。",
    "ended": "考核已结束，感谢您的参与。",
}


@app.get("/api/sessions/current/lifecycle", response_class=JSONResponse)
async def get_current_lifecycle(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    lifecycle = db.get_session_lifecycle()
    return {"lifecycle": lifecycle, "label": LIFECYCLE_LABELS.get(lifecycle, lifecycle)}


@app.post("/api/sessions/{session_id}/lifecycle", response_class=JSONResponse)
async def set_session_lifecycle(request: Request, session_id: int, req: LifecycleRequest):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    result = db.set_lifecycle(session_id, req.action, clear_scores=req.clear_scores)
    if not result.get("success"):
        return JSONResponse(result, status_code=400)
    result["label"] = LIFECYCLE_LABELS.get(result["lifecycle"], result["lifecycle"])
    return result


# ==================== 考核人 API ====================
@app.post("/api/assessors", response_class=JSONResponse)
async def create_assessor(request: Request, req: AssessorCreate):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    session_id = db.get_current_session_id()
    aid = db.add_assessor(req.name, req.employee_id, session_id)
    return {"id": aid, "name": req.name, "employee_id": req.employee_id}


@app.delete("/api/assessors/{assessor_id}", response_class=JSONResponse)
async def delete_assessor(request: Request, assessor_id: int):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    db.delete_assessor(assessor_id)
    return {"message": "删除成功"}


# ==================== 被考核人 API ====================
@app.post("/api/evaluatees", response_class=JSONResponse)
async def create_evaluatee(request: Request, req: EvaluateeCreate):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    session_id = db.get_current_session_id()
    eid = db.add_evaluatee(
        req.name, req.employee_id, req.position,
        req.department, req.group_type, req.department_ref_score,
        session_id
    )
    return {"id": eid, "name": req.name}


@app.delete("/api/evaluatees/{evaluatee_id}", response_class=JSONResponse)
async def delete_evaluatee(request: Request, evaluatee_id: int):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    db.delete_evaluatee(evaluatee_id)
    return {"message": "删除成功"}


# ==================== 考核关系 API ====================
@app.post("/api/relations", response_class=JSONResponse)
async def create_relation(request: Request, req: RelationCreate):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    rid = db.add_relation(req.assessor_id, req.evaluatee_id, req.weight)
    return {"id": rid}


@app.delete("/api/relations/{relation_id}", response_class=JSONResponse)
async def delete_relation(request: Request, relation_id: int):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    db.delete_relation(relation_id)
    return {"message": "删除成功"}


# ==================== 指标 API ====================
@app.post("/api/indicators", response_class=JSONResponse)
async def create_indicator(request: Request, req: IndicatorCreate):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    iid = db.add_indicator(req.name, req.weight, req.max_score)
    return {"id": iid, "name": req.name}


@app.delete("/api/indicators/{indicator_id}", response_class=JSONResponse)
async def delete_indicator(request: Request, indicator_id: int):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    db.delete_indicator(indicator_id)
    return {"message": "删除成功"}


# ==================== 打分 API ====================
@app.post("/api/scores/{assessor_id}/batch", response_class=JSONResponse)
async def batch_save_scores(request: Request, assessor_id: int, req: ScoreBatchSave):
    db = get_db()
    # 校验访问令牌：防止他人改 URL 中的 id 冒充他人打分（IDOR）
    if not _verify_assessor_access(request, db, assessor_id):
        return JSONResponse({"error": "链接无效或已失效，请重新扫码"}, status_code=403)
    session_id = db.get_current_session_id()
    lifecycle = db.get_session_lifecycle(session_id)
    if lifecycle not in ('testing', 'running'):
        return JSONResponse({
            "success": False,
            "message": f"当前考核状态（{LIFECYCLE_LABELS.get(lifecycle, lifecycle)}）不允许打分"
        }, status_code=403)
    if db.is_assessor_completed(assessor_id, session_id):
        raise HTTPException(status_code=400, detail="考核已完成，无法修改")

    indicators = db.get_all_indicators()
    ind_map = {i['id']: i for i in indicators}

    saved = 0
    for item in req.scores:
        ind_id = item['indicator_id']
        score = item['score']
        ind = ind_map.get(ind_id)
        if not ind:
            raise HTTPException(status_code=404, detail=f"指标不存在: {ind_id}")
        if score < 0 or score > ind['max_score']:
            raise HTTPException(status_code=400,
                                detail=f"分数应在 0-{ind['max_score']} 之间")
        db.save_score(assessor_id, req.evaluatee_id, ind_id, score, session_id)
        saved += 1

    return {"success": True, "message": f"已暂存 {saved} 条分数", "saved": saved}


@app.post("/api/scores/{assessor_id}/submit", response_class=JSONResponse)
async def submit_scores(request: Request, assessor_id: int):
    db = get_db()
    # 校验访问令牌：防止他人越权提交他人考核结果
    if not _verify_assessor_access(request, db, assessor_id):
        return JSONResponse({"error": "链接无效或已失效，请重新扫码"}, status_code=403)
    session_id = db.get_current_session_id()
    lifecycle = db.get_session_lifecycle(session_id)
    if lifecycle not in ('testing', 'running'):
        return JSONResponse({
            "success": False,
            "message": f"当前考核状态（{LIFECYCLE_LABELS.get(lifecycle, lifecycle)}）不允许提交"
        }, status_code=403)

    if db.is_assessor_completed(assessor_id, session_id):
        return {"success": True, "message": "考核已完成"}

    relations = db.get_assessor_relations(assessor_id, session_id)
    if not relations:
        raise HTTPException(status_code=400, detail="未配置考核关系")

    check = db.check_all_scores_complete(assessor_id, session_id)
    if not check["complete"]:
        missing_names = ", ".join(
            f"{m['evaluatee_name']}({m['indicator_name']})" for m in check["missing"]
        )
        return JSONResponse({
            "success": False,
            "message": f"以下人员尚未完成评分：{missing_names}"
        }, status_code=400)

    db.mark_all_submitted(assessor_id, session_id)
    summary = db.get_assessment_summary(assessor_id, session_id)

    return {"success": True, "message": "考核提交成功！", "summary": summary}


@app.post("/api/scores/{assessor_id}/reset", response_class=JSONResponse)
async def reset_assessor_scores(request: Request, assessor_id: int):
    """管理员重置考核人打分状态：归档原打分记录到历史表，解锁重新打分（旧分数保留为预填）"""
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    assessors = db.get_all_assessors()
    assessor = next((a for a in assessors if a['id'] == assessor_id), None)
    if not assessor:
        return JSONResponse({"success": False, "message": "考核人不存在"}, status_code=404)

    archived = db.reset_assessor(assessor_id)
    return {
        "success": True,
        "message": f"已重置 {assessor['name']} 的考核状态，原打分记录（{archived} 条）已归档，可重新扫码打分提交",
        "archived": archived
    }


@app.get("/api/scores/{assessor_id}/history", response_class=JSONResponse)
async def get_score_history(request: Request, assessor_id: int):
    """查看某考核人的历史归档打分记录（每次重置为一批，仅管理员）"""
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    assessors = db.get_all_assessors()
    assessor = next((a for a in assessors if a['id'] == assessor_id), None)
    if not assessor:
        return JSONResponse({"success": False, "message": "考核人不存在"}, status_code=404)

    batches = db.get_score_history(assessor_id)
    return {
        "success": True,
        "assessor_name": assessor['name'],
        "reset_count": db.get_assessor_reset_count(assessor_id),
        "batches": batches
    }


# ==================== 设置 API ====================
@app.get("/api/settings", response_class=JSONResponse)
async def get_settings(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    return {"instruction": db.get_setting("assess_instruction", "")}


@app.post("/api/settings", response_class=JSONResponse)
async def update_settings(request: Request, req: SettingsUpdate):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    db.set_setting("assess_instruction", req.instruction)
    return {"message": "设置已保存"}


# ==================== 二维码 ====================
@app.get("/api/qrcode/{assessor_id}", response_class=Response)
async def generate_qrcode(request: Request, assessor_id: int):
    db = get_db()
    assessors = db.get_all_assessors()
    assessor = next((a for a in assessors if a['id'] == assessor_id), None)
    if not assessor:
        raise HTTPException(status_code=404, detail="考核人不存在")

    # 二维码内容为不可猜测的令牌地址（/s/<token>），而非自增 id
    token = db.ensure_assessor_token(assessor_id)
    qr_url = f"{_base_url(request)}/s/{token}"
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H,
                       box_size=10, border=4)
    qr.add_data(qr_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png")


@app.post("/api/qrcode/{assessor_id}/reset", response_class=JSONResponse)
async def reset_qrcode_token(request: Request, assessor_id: int):
    """重置某考核人的二维码令牌：旧链接（含已泄露/已拍照的）立即失效"""
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    new_token = db.reset_assessor_token(assessor_id)
    if not new_token:
        return JSONResponse({"error": "考核人不存在"}, status_code=404)
    return {
        "success": True,
        "message": "二维码已重置，旧链接立即失效，请重新导出并分发",
        "url": f"{_base_url(request)}/s/{new_token}",
    }


# ==================== Excel 导入 ====================
@app.post("/api/import/assessors", response_class=JSONResponse)
async def import_assessors(request: Request, file: UploadFile = File(...)):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    import openpyxl
    content = await file.read()
    wb = openpyxl.load_workbook(BytesIO(content))
    ws = wb.active

    data = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[0]:
            continue
        data.append({
            'employee_id': str(row[0]).strip(),
            'name': str(row[1]).strip() if row[1] else '',
        })

    db = get_db()
    session_id = db.get_current_session_id()
    result = db.import_assessors(data, session_id)
    return {"success": True, "result": result}


@app.post("/api/import/evaluatees", response_class=JSONResponse)
async def import_evaluatees(request: Request, file: UploadFile = File(...)):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    import openpyxl
    content = await file.read()
    wb = openpyxl.load_workbook(BytesIO(content))
    ws = wb.active

    data = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[0]:
            continue
        ref_score = None
        if len(row) > 5 and row[5] is not None:
            ref_score = str(row[5]).strip()
        data.append({
            'employee_id': str(row[0]).strip(),
            'name': str(row[1]).strip() if row[1] else '',
            'department': str(row[2]).strip() if len(row) > 2 and row[2] else None,
            'position': str(row[3]).strip() if len(row) > 3 and row[3] else None,
            'group_type': str(row[4]).strip() if len(row) > 4 and row[4] else None,
            'department_ref_score': ref_score
        })

    db = get_db()
    session_id = db.get_current_session_id()
    result = db.import_evaluatees(data, session_id)
    return {"success": True, "result": result}


@app.post("/api/import/relations", response_class=JSONResponse)
async def import_relations(request: Request, file: UploadFile = File(...)):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    import openpyxl
    content = await file.read()
    wb = openpyxl.load_workbook(BytesIO(content))
    ws = wb.active

    data = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[0]:
            continue
        data.append({
            'assessor_employee_id': str(row[0]).strip(),
            'evaluatee_employee_id': str(row[1]).strip() if len(row) > 1 and row[1] else '',
            'weight': row[2] if len(row) > 2 and row[2] is not None else 1.0
        })

    db = get_db()
    session_id = db.get_current_session_id()
    result = db.import_relations(data, session_id)
    return {"success": True, "result": result}


# ==================== Excel 模板下载 ====================
def _styled_header(ws, headers, col_widths=None):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")
    border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin")
    )
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
        cell.border = border
    if col_widths:
        for i, w in enumerate(col_widths, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w


@app.get("/api/templates/assessors")
async def download_assessor_template(request: Request):
    if not is_logged_in(request):
        return RedirectResponse("/login", status_code=303)
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "考核人导入"
    _styled_header(ws, ["工号", "姓名"], [15, 15])
    ws.cell(row=2, column=1, value="1001")
    ws.cell(row=2, column=2, value="张三")
    ws.cell(row=3, column=1, value="1002")
    ws.cell(row=3, column=2, value="李四")

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = quote("考核人导入模板.xlsx")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
    )


@app.get("/api/templates/evaluatees")
async def download_evaluatee_template(request: Request):
    if not is_logged_in(request):
        return RedirectResponse("/login", status_code=303)
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "被考核人导入"
    _styled_header(ws, ["工号", "姓名", "部门", "职务", "组别", "部门考核得分(参考值)"],
                   [15, 15, 20, 15, 15, 20])
    ws.cell(row=2, column=1, value="2001")
    ws.cell(row=2, column=2, value="王五")
    ws.cell(row=2, column=3, value="财务部")
    ws.cell(row=2, column=4, value="部长")
    ws.cell(row=2, column=5, value="业务前端")
    ws.cell(row=2, column=6, value="88.5")

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = quote("被考核人导入模板.xlsx")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
    )


@app.get("/api/templates/relations")
async def download_relation_template(request: Request):
    if not is_logged_in(request):
        return RedirectResponse("/login", status_code=303)
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "考核关系导入"
    _styled_header(ws, ["考核人工号", "被考核人工号", "权重"], [15, 15, 10])
    ws.cell(row=2, column=1, value="1001")
    ws.cell(row=2, column=2, value="2001")
    ws.cell(row=2, column=3, value=0.35)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = quote("考核关系导入模板.xlsx")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
    )


# ==================== Excel 导出 ====================
@app.get("/api/export/excel")
async def export_excel(request: Request):
    if not is_logged_in(request):
        return RedirectResponse("/login", status_code=303)
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    db = get_db()
    session_id = db.get_current_session_id()
    final_scores = db.get_final_scores(session_id)
    indicators = db.get_all_indicators()
    progress = db.get_all_progress(session_id)

    wb = openpyxl.Workbook()

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")
    border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin")
    )

    # Sheet 1: 汇总排名
    ws1 = wb.active
    ws1.title = "考核结果汇总"
    headers = ["排名", "工号", "姓名", "职务", "部门", "组别", "最终得分"]
    for col, h in enumerate(headers, 1):
        cell = ws1.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
        cell.border = border

    for row_idx, fs in enumerate(final_scores, 2):
        vals = [
            row_idx - 1,
            fs.get('evaluatee_employee_id') or "",
            fs['evaluatee_name'],
            fs.get('position') or "",
            fs.get('department') or "",
            fs.get('group_type') or "",
            fs['final_score']
        ]
        for col, v in enumerate(vals, 1):
            ws1.cell(row=row_idx, column=col, value=v).border = border

    for col in range(1, 8):
        ws1.column_dimensions[openpyxl.utils.get_column_letter(col)].width = 15

    # Sheet 2: 各考核人详细打分
    ws2 = wb.create_sheet("各考核人打分明细")
    detail_headers = ["被考核人工号", "被考核人", "职务", "部门", "考核人", "考核人工号", "考核人权重"]
    for ind in indicators:
        detail_headers.append(f"{ind['name']}({ind['weight']*100:.0f}%)")
    detail_headers.append("该考核人加权得分")

    for col, h in enumerate(detail_headers, 1):
        cell = ws2.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border = border

    row = 2
    for fs in final_scores:
        for aid, adata in fs.get('assessors', {}).items():
            ws2.cell(row=row, column=1, value=fs.get('evaluatee_employee_id') or "").border = border
            ws2.cell(row=row, column=2, value=fs['evaluatee_name']).border = border
            ws2.cell(row=row, column=3, value=fs.get('position') or "").border = border
            ws2.cell(row=row, column=4, value=fs.get('department') or "").border = border
            ws2.cell(row=row, column=5, value=adata['assessor_name']).border = border
            ws2.cell(row=row, column=6, value=adata.get('assessor_employee_id') or "").border = border
            ws2.cell(row=row, column=7, value=adata['assessor_weight']).border = border
            col = 8
            for ind in indicators:
                ind_data = adata['indicator_scores'].get(ind['name'])
                val = ind_data['score'] if ind_data else None
                ws2.cell(row=row, column=col, value=val).border = border
                col += 1
            ws2.cell(row=row, column=col, value=round(adata['weighted_total'], 2)).border = border
            row += 1

    # Sheet 3: 考核进度
    ws3 = wb.create_sheet("考核进度")
    prog_headers = ["考核人", "工号", "状态", "待考核人数", "已打分人数"]
    for col, h in enumerate(prog_headers, 1):
        cell = ws3.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border

    for row_idx, p in enumerate(progress, 2):
        vals = [p['assessor_name'], p.get('employee_id') or "",
                p['status'], p['total_evaluatees'], p['scored_evaluatees']]
        for col, v in enumerate(vals, 1):
            ws3.cell(row=row_idx, column=col, value=v).border = border

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = quote("绩效考核结果.xlsx")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
    )


@app.get("/api/export/qrcodes")
async def export_qrcodes(request: Request):
    """将所有考核人二维码导出为 Excel（含二维码图片）"""
    if not is_logged_in(request):
        return RedirectResponse("/login", status_code=303)
    import openpyxl
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    db = get_db()
    assessors = db.get_all_assessors()
    base = _base_url(request)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "考核二维码"

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")
    border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin")
    )

    headers = ["姓名", "工号", "二维码", "扫码链接"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
        cell.border = border

    ws.column_dimensions['A'].width = 12
    ws.column_dimensions['B'].width = 12
    ws.column_dimensions['C'].width = 30
    ws.column_dimensions['D'].width = 50

    for idx, a in enumerate(assessors, 2):
        ws.cell(row=idx, column=1, value=a['name']).border = border
        ws.cell(row=idx, column=2, value=a.get('employee_id') or "").border = border

        token = db.ensure_assessor_token(a['id'])
        qr_url = f"{base}/s/{token}"
        ws.cell(row=idx, column=4, value=qr_url).border = border

        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H,
                           box_size=10, border=2)
        qr.add_data(qr_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img_buf = BytesIO()
        img.save(img_buf, format='PNG')
        img_buf.seek(0)

        xl_img = XLImage(img_buf)
        xl_img.width = 180
        xl_img.height = 180
        ws.add_image(xl_img, f"C{idx}")
        ws.row_dimensions[idx].height = 140

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = quote("考核二维码.xlsx")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
    )


@app.get("/api/results", response_class=JSONResponse)
async def get_results_api(request: Request):
    if not is_logged_in(request):
        return JSONResponse({"error": "未登录"}, status_code=401)
    db = get_db()
    session_id = db.get_current_session_id()
    return {
        "final_scores": db.get_final_scores(session_id),
        "progress": db.get_all_progress(session_id),
        "evaluatees": db.get_all_evaluatees(),
    }


# ==================== 运行入口 ====================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
