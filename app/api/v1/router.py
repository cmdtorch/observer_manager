from fastapi import APIRouter

from app.api.v1.endpoints import api_keys, applications, health, organizations

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(organizations.router)
api_router.include_router(api_keys.router)
api_router.include_router(applications.router)
