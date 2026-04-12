from fastapi import APIRouter

from book_agent.app.api.routes import actions, documents, health, run_cost, run_stream, runs

api_router = APIRouter()
api_router.include_router(health.router, tags=["system"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(actions.router, prefix="/actions", tags=["actions"])
api_router.include_router(runs.router, prefix="/runs", tags=["runs"])
api_router.include_router(run_stream.router, prefix="/runs", tags=["runs"])
api_router.include_router(run_cost.router, prefix="/runs", tags=["runs"])
