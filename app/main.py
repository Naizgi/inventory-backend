# app/main.py - Updated with multi-tenant and subscription support
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine, Base, SessionLocal
from app.config import settings
from app.middleware.tenant import TenantMiddleware
from app.utils.dependencies import get_current_user
from app.utils.subscription_seed import seed_subscription_plans
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create tables
Base.metadata.create_all(bind=engine)

# Seed subscription plans on startup
try:
    db = SessionLocal()
    seed_subscription_plans(db)
    db.close()
    logger.info("Subscription plans seeded successfully")
except Exception as e:
    logger.error(f"Failed to seed subscription plans: {str(e)}")

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    debug=settings.DEBUG
)

# Add tenant middleware FIRST (before CORS)
app.add_middleware(TenantMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import routers
from app.routers import (
    auth_router, users_router, branches_router, tenants_router,
    categories_router, units_router, products_router, batches_router,
    stock_router, sales_router, returns_router, purchases_router,
    loans_router, alerts_router, reports_router, dashboard_router,
    settings_router, temp_items_router
)

# Import subscription routers
from app.routers.subscription_plans import router as subscription_plans_router
from app.routers.tenant_subscriptions import router as tenant_subscriptions_router

# Register routers
app.include_router(tenants_router, prefix="/api/tenants", tags=["Tenants"])
app.include_router(auth_router, prefix="/api/auth", tags=["Authentication"])
app.include_router(users_router, prefix="/api/users", tags=["Users"])
app.include_router(branches_router, prefix="/api/branches", tags=["Branches"])
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

# Register subscription routers
app.include_router(subscription_plans_router, prefix="/api/subscription-plans", tags=["Subscription Plans"])
app.include_router(tenant_subscriptions_router, prefix="/api/subscriptions", tags=["Tenant Subscriptions"])


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Multi-tenant mode: {settings.ENABLE_MULTI_TENANT}")
    logger.info(f"Trial days: {settings.TRIAL_DAYS}")
    logger.info(f"Grace period: {settings.GRACE_PERIOD_DAYS} days")
    logger.info(f"Payment verification required: {settings.PAYMENT_VERIFICATION_REQUIRED}")
    
    # You could add scheduled tasks here for:
    # - Checking expired subscriptions
    # - Sending subscription expiry reminders
    # - Auto-suspending expired tenants


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info(f"Shutting down {settings.APP_NAME}")


@app.get("/")
async def root(request: Request):
    tenant_id = getattr(request.state, 'tenant_id', None)
    
    # Get subscription info if tenant
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
    return {
        "status": "healthy",
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


# Optional: Add middleware to check subscription on each request
# Uncomment if you want to enforce subscription checks on all routes
"""
@app.middleware("http")
async def subscription_check_middleware(request: Request, call_next):
    # Check subscription for API routes
    if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/auth/"):
        tenant_id = getattr(request.state, 'tenant_id', None)
        
        if tenant_id:
            try:
                db = SessionLocal()
                from app.services import SubscriptionService
                
                # Skip check for subscription-related endpoints
                skip_paths = [
                    "/api/subscription-plans/",
                    "/api/plans/public",
                    "/api/health"
                ]
                
                if not any(request.url.path.startswith(path) for path in skip_paths):
                    if not SubscriptionService.check_subscription_valid(db, tenant_id):
                        db.close()
                        return JSONResponse(
                            status_code=402,
                            content={"detail": "Subscription required. Please subscribe to continue."}
                        )
                
                db.close()
            except Exception as e:
                logger.error(f"Subscription middleware error: {str(e)}")
    
    response = await call_next(request)
    return response
"""