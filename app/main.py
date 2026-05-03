# app/main.py - Updated with multi-tenant and subscription support
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.database import engine, Base, SessionLocal
from app.config import settings
from app.middleware.tenant import TenantMiddleware
from app.utils.auth import get_current_user
from app.utils.subscription_seed import seed_subscription_plans
import logging
import os
from sqlalchemy import text


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize database tables and seed data
def initialize_database():
    """Initialize database tables and seed initial data"""
    try:
        # Import all models to ensure they're registered with Base
        from app.models import (
            Tenant, User, Branch, Category, Unit, Product,
            Stock, Batch, Sale, SaleItem, SaleReturn, SaleReturnItem,
            PurchaseOrder, PurchaseOrderItem, Purchase, PurchaseItem,
            Loan, LoanItem, LoanPayment, LoanSummary,
            StockMovement, Alert, TempItem,
            SystemSetting, BackupRecord, SystemLog,
            SubscriptionPlan, TenantSubscription, Payment, Invoice, InvoiceItem,
            OTP  # Make sure OTP is imported
        )
        
        # Create all tables
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Database tables created/verified")
        
        # Seed subscription plans
        db = SessionLocal()
        try:
            seed_subscription_plans(db)
            logger.info("✅ Subscription plans seeded successfully")
            
            # Create default super admin if not exists
            from app.services import AuthService
            
            admin = db.query(User).filter(
                User.email == "admin@example.com",
                User.role == "super_admin"
            ).first()
            
            if not admin:
                admin = User(
                    name="Super Admin",
                    email="admin@example.com",
                    password_hash=AuthService.get_password_hash("admin123"),
                    role="super_admin",
                    active=True
                )
                db.add(admin)
                db.commit()
                logger.info("✅ Default super admin created (admin@example.com / admin123)")
            else:
                logger.info("✅ Super admin already exists")
                
        except Exception as e:
            logger.error(f"❌ Error during seeding: {e}")
        finally:
            db.close()
            
        return True
        
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {str(e)}")
        return False

# Initialize database
initialize_database()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    debug=settings.DEBUG
)

# Add tenant middleware FIRST (before CORS)
app.add_middleware(TenantMiddleware)

# CORS - Allow all origins for testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Temporarily allow all for testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== DEBUG ENDPOINTS ====================
@app.get("/debug-ping")
async def debug_ping():
    return {"message": "pong", "status": "ok"}

@app.get("/debug-routes")
async def debug_routes():
    routes = []
    for route in app.routes:
        routes.append({
            "path": route.path,
            "methods": list(route.methods) if hasattr(route, 'methods') else ["GET"]
        })
    return {"routes": routes, "total": len(routes)}


# ==================== DIRECT ROUTER IMPORTS ====================
# Import routers directly (not from __init__.py)
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
from app.routers.loan_routes import router as loans_router
from app.routers.alerts import router as alerts_router
from app.routers.reports import router as reports_router
from app.routers.dashboard import router as dashboard_router
from app.routers.settings_router import router as settings_router
from app.routers.temp_items_routes import router as temp_items_router
from app.routers.subscription_plans import router as subscription_plans_router
from app.routers.tenant_subscriptions import router as tenant_subscriptions_router

logger.info("✅ All routers imported successfully")

# Register routers
app.include_router(auth_router, prefix="/api/auth", tags=["Authentication"])
app.include_router(users_router, prefix="/api/users", tags=["Users"])
app.include_router(branches_router, prefix="/api/branches", tags=["Branches"])
app.include_router(tenants_router, prefix="/api/tenants", tags=["Tenants"])
app.include_router(categories_router, prefix="/api/categories", tags=["Categories"])
app.include_router(units_router, prefix="/api/units", tags=["Units"])
app.include_router(products_router, prefix="/api/products", tags=["Products"])
app.include_router(batches_router, prefix="/api/batches", tags=["Batches"])
app.include_router(stock_router, prefix="/api/stock", tags=["Stock"])
app.include_router(sales_router, prefix="/api/sales", tags=["Sales"])
app.include_router(returns_router, prefix="/api/returns", tags=["Returns"])
app.include_router(purchases_router, prefix="/api/purchases", tags=["Purchases"])
app.include_router(loans_router, prefix="/api/loans", tags=["Loans"])
app.include_router(alerts_router, prefix="/api/alerts", tags=["Alerts"])
app.include_router(reports_router, prefix="/api/reports", tags=["Reports"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(settings_router, prefix="/api/settings", tags=["Settings"])
app.include_router(temp_items_router, prefix="/api/temp-items", tags=["Temporary Items"])
app.include_router(subscription_plans_router, prefix="/api/subscription-plans", tags=["Subscription Plans"])
app.include_router(tenant_subscriptions_router, prefix="/api/subscriptions", tags=["Tenant Subscriptions"])

logger.info("✅ All routers registered")


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Multi-tenant mode: {settings.ENABLE_MULTI_TENANT}")
    logger.info(f"Trial days: {settings.TRIAL_DAYS}")
    logger.info(f"Grace period: {settings.GRACE_PERIOD_DAYS} days")
    logger.info(f"Payment verification required: {settings.PAYMENT_VERIFICATION_REQUIRED}")
    
    # Verify database connection
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        logger.info("✅ Database connection verified")
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info(f"Shutting down {settings.APP_NAME}")


@app.get("/")
async def root(request: Request):
    tenant_id = getattr(request.state, 'tenant_id', None)
    
    subscription_info = None
    if tenant_id:
        try:
            db = SessionLocal()
            from app.services import SubscriptionService
            subscription_info = SubscriptionService.get_subscription_status(db, tenant_id)
            db.close()
        except Exception as e:
            logger.error(f"Failed to get subscription info: {str(e)}")
    
    return {
        "message": f"Welcome to {settings.APP_NAME}",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "tenant_id": tenant_id,
        "subscription": subscription_info,
        "docs": "/docs",
        "api_prefix": "/api"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    db_healthy = False
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        db_healthy = True
    except Exception as e:
        logger.error(f"Health check DB error: {e}")
    
    return {
        "status": "healthy" if db_healthy else "degraded",
        "database": "connected" if db_healthy else "disconnected",
        "multi_tenant": settings.ENABLE_MULTI_TENANT,
        "subscription_system": True,
        "version": settings.APP_VERSION
    }


@app.get("/api/plans/public")
async def get_public_plans():
    """Public endpoint to view available subscription plans"""
    try:
        db = SessionLocal()
        from app.services import SubscriptionService
        plans = SubscriptionService.get_available_plans(db)
        db.close()
        return {
            "plans": plans,
            "trial_days": settings.TRIAL_DAYS,
            "currency": settings.SUBSCRIPTION_CURRENCY
        }
    except Exception as e:
        logger.error(f"Failed to get plans: {str(e)}")
        return {"error": "Failed to load plans", "plans": []}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)