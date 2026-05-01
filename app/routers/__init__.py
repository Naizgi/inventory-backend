# app/routers/__init__.py
from app.routers.auth import router as auth_router
from app.routers.users import router as users_router
from app.routers.branches import router as branches_router
from app.routers.tenants import router as tenants_router
from app.routers.categories import router as categories_router
from app.routers.units import router as units_router
from app.routers.products import router as products_router
from app.routers.batches import router as batches_router
from app.routers.stock import router as stock_router
from app.routers.sales import router as sales_router
from app.routers.returns import router as returns_router
from app.routers.purchase_routes import router as purchases_router
from app.routers.loans import router as loans_router
from app.routers.alerts import router as alerts_router
from app.routers.reports import router as reports_router
from app.routers.dashboard import router as dashboard_router
from app.routers.settings import router as settings_router
from app.routers.temp_items import router as temp_items_router

__all__ = [
    'auth_router',
    'users_router',
    'branches_router',
    'tenants_router',
    'categories_router',
    'units_router',
    'products_router',
    'batches_router',
    'stock_router',
    'sales_router',
    'returns_router',
    'purchases_router',
    'loans_router',
    'alerts_router',
    'reports_router',
    'dashboard_router',
    'settings_router',
    'temp_items_router'
]