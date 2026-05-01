# app/routers/__init__.py
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
from app.routers.purchases import router as purchases_router
from app.routers.loans import router as loans_router
from app.routers.alerts import router as alerts_router
from app.routers.reports import router as reports_router
from app.routers.dashboard import router as dashboard_router
from app.routers.settings import router as settings_router
from app.routers.temp_items import router as temp_items_router

# Subscription routers are imported directly in main.py


# Export all routers
__all__ = [
    'auth_router',
    'branches_router', 
    'products_router',
    'users_router',
    'stock_router',
    'sales_router',
    'purchase_router',
    'reports_router',
    'alerts_router',
    'dashboard_router',
    'loan_router',
    'temp_items_router'
]