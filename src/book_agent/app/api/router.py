from fastapi import APIRouter, Depends

from book_agent.app.api.access import enforce_access
from book_agent.app.api.routes import (
    actions,
    api_keys,
    documents,
    harness,
    issues,
    health,
    orgs,
    providers,
    run_cost,
    run_stream,
    runs,
)

api_router = APIRouter()
# /health and /meta stay public for load balancers and the UI's connection check.
api_router.include_router(health.router, tags=["system"])

# Everything else authenticates, checks the role and scopes path resources to the caller's org.
_protected = [Depends(enforce_access)]
api_router.include_router(documents.router, prefix="/documents", tags=["documents"], dependencies=_protected)
api_router.include_router(harness.documents_router, prefix="/documents", tags=["harness"], dependencies=_protected)
api_router.include_router(harness.approvals_router, prefix="/approvals", tags=["harness"], dependencies=_protected)
api_router.include_router(actions.router, prefix="/actions", tags=["actions"], dependencies=_protected)
api_router.include_router(issues.documents_router, prefix="/documents", tags=["issues"], dependencies=_protected)
api_router.include_router(issues.issues_router, prefix="/issues", tags=["issues"], dependencies=_protected)
api_router.include_router(runs.router, prefix="/runs", tags=["runs"], dependencies=_protected)
api_router.include_router(run_stream.router, prefix="/runs", tags=["runs"], dependencies=_protected)
api_router.include_router(run_cost.router, prefix="/runs", tags=["runs"], dependencies=_protected)
api_router.include_router(providers.router, prefix="/providers", tags=["providers"], dependencies=_protected)
api_router.include_router(api_keys.router, prefix="/api-keys", tags=["auth"], dependencies=_protected)
api_router.include_router(orgs.router, prefix="/orgs", tags=["auth"], dependencies=_protected)
