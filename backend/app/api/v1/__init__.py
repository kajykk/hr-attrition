"""API v1 路由聚合."""
from fastapi import APIRouter

from app.api.v1 import admin, advise, auth, employees, kb, risk, warnings, ws

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(employees.router, prefix="/employees", tags=["employees"])
api_router.include_router(risk.router, prefix="/risk", tags=["risk"])
api_router.include_router(warnings.router, prefix="/warnings", tags=["warnings"])
api_router.include_router(advise.router, prefix="/advise", tags=["advise"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
# RAG 知识库（feat/rag-kb）：路由常驻注册，未启用时端点返回 503
api_router.include_router(kb.router, prefix="/kb", tags=["knowledge-base"])
# WebSocket 路由（无 prefix，路径已含 /ws/）
api_router.include_router(ws.router, tags=["websocket"])
