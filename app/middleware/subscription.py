from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.database import get_db
from app.models import Tenant, TenantSubscription, Payment
from app.schemas import TenantStatus, SubscriptionStatus, PaymentStatus

security = HTTPBearer()

async def check_subscription(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Middleware to check if tenant has valid subscription
    """
    # Get tenant_id from request state (set by auth middleware)
    tenant_id = getattr(request.state, "tenant_id", None)
    
    if not tenant_id:
        # Super admin access - no subscription check needed
        return True
    
    # Get tenant
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    # Check trial period
    if tenant.status == TenantStatus.TRIAL:
        if tenant.trial_end and tenant.trial_end > datetime.now():
            # Trial still valid
            return True
        else:
            # Trial expired
            tenant.status = TenantStatus.EXPIRED
            db.commit()
            raise HTTPException(
                status_code=403,
                detail="Trial period has expired. Please subscribe to continue."
            )
    
    # Check active subscription
    active_subscription = db.query(TenantSubscription).filter(
        TenantSubscription.tenant_id == tenant_id,
        TenantSubscription.status == SubscriptionStatus.ACTIVE,
        TenantSubscription.payment_status == PaymentStatus.COMPLETED,
        TenantSubscription.end_date > datetime.now()
    ).first()
    
    if active_subscription:
        # Has valid paid subscription
        return True
    
    # Check grace period (7 days after expiry)
    latest_subscription = db.query(TenantSubscription).filter(
        TenantSubscription.tenant_id == tenant_id
    ).order_by(TenantSubscription.end_date.desc()).first()
    
    if latest_subscription:
        grace_end = latest_subscription.end_date + timedelta(days=7)
        if datetime.now() < grace_end:
            # In grace period
            tenant.status = TenantStatus.PENDING_PAYMENT
            db.commit()
            return True
        else:
            # Grace period expired
            tenant.status = TenantStatus.SUSPENDED
            db.commit()
            raise HTTPException(
                status_code=403,
                detail="Subscription has expired. Please renew to continue."
            )
    
    # No subscription at all
    tenant.status = TenantStatus.SUSPENDED
    db.commit()
    raise HTTPException(
        status_code=403,
        detail="No active subscription found. Please subscribe to continue."
    )

def require_active_subscription():
    """Dependency that requires active subscription"""
    return Depends(check_subscription)