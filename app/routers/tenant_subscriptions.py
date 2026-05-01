from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from typing import List
from app.database import get_db
from app.models import Tenant, TenantSubscription, SubscriptionPlan, Payment
from app.schemas import (
    TenantSubscriptionCreate,
    TenantSubscriptionUpdate,
    TenantSubscriptionResponse,
    SubscriptionCheckResponse,
    PaymentCreate,
    PaymentResponse,
    PaymentStatus,
    SubscriptionStatus,
    PaymentMethod
)
from app.utils.auth import get_current_user, get_current_super_admin

router = APIRouter(prefix="/api/subscriptions", tags=["Tenant Subscriptions"])

@router.get("/check/{tenant_id}", response_model=SubscriptionCheckResponse)
async def check_subscription_status(
    tenant_id: int,
    db: Session = Depends(get_db)
):
    """Check subscription status for a tenant"""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    # Check trial
    if tenant.status == "trial" and tenant.trial_end and tenant.trial_end > datetime.now():
        days_left = (tenant.trial_end - datetime.now()).days
        return SubscriptionCheckResponse(
            is_valid=True,
            status="trial",
            plan_type=None,
            expires_in_days=days_left,
            features={"trial": True},
            message=f"Trial period active. {days_left} days remaining."
        )
    
    # Check active subscription
    active_sub = db.query(TenantSubscription).join(
        SubscriptionPlan
    ).filter(
        TenantSubscription.tenant_id == tenant_id,
        TenantSubscription.status == SubscriptionStatus.ACTIVE,
        TenantSubscription.payment_status == PaymentStatus.COMPLETED,
        TenantSubscription.end_date > datetime.now()
    ).first()
    
    if active_sub:
        days_left = (active_sub.end_date - datetime.now()).days
        plan = active_sub.plan
        features = {
            "has_loans": plan.has_loans,
            "has_batch_tracking": plan.has_batch_tracking,
            "has_pharmacy_features": plan.has_pharmacy_features,
            "has_advanced_reports": plan.has_advanced_reports,
            "has_api_access": plan.has_api_access,
            "max_users": plan.max_users,
            "max_branches": plan.max_branches,
            "max_products": plan.max_products
        }
        return SubscriptionCheckResponse(
            is_valid=True,
            status="active",
            plan_type=plan.plan_type,
            expires_in_days=days_left,
            features=features,
            message=f"Active {plan.plan_name} subscription. {days_left} days remaining."
        )
    
    return SubscriptionCheckResponse(
        is_valid=False,
        status="expired",
        plan_type=None,
        expires_in_days=None,
        features=None,
        message="No active subscription. Please subscribe to continue."
    )

@router.post("/subscribe", response_model=TenantSubscriptionResponse)
async def create_subscription(
    subscription: TenantSubscriptionCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Subscribe to a plan"""
    # Only tenant admin or super admin can subscribe
    if current_user.role not in ["tenant_admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    tenant_id = subscription.tenant_id
    
    # Validate tenant
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    # Validate plan
    plan = db.query(SubscriptionPlan).filter(
        SubscriptionPlan.id == subscription.plan_id,
        SubscriptionPlan.active == True
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    
    # Create payment record first (pending)
    payment_number = f"PAY-{datetime.now().strftime('%Y%m%d')}-{tenant_id}-{plan.id}"
    payment = Payment(
        tenant_id=tenant_id,
        payment_number=payment_number,
        amount=plan.price,
        payment_method=subscription.payment_method,
        payment_status=PaymentStatus.PENDING,
        payment_type="subscription"
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    
    # Calculate subscription dates
    start_date = datetime.now()
    end_date = start_date + relativedelta(months=plan.duration_months)
    
    # Create subscription (pending payment)
    db_subscription = TenantSubscription(
        tenant_id=tenant_id,
        plan_id=plan.id,
        start_date=start_date,
        end_date=end_date,
        status=SubscriptionStatus.PENDING_PAYMENT,
        auto_renew=subscription.auto_renew,
        amount_paid=plan.price,
        payment_status=PaymentStatus.PENDING,
        payment_id=payment.id
    )
    db.add(db_subscription)
    
    # Update tenant status
    tenant.status = "pending_payment"
    
    db.commit()
    db.refresh(db_subscription)
    
    return TenantSubscriptionResponse.model_validate(db_subscription)

@router.post("/payments/verify/{payment_id}", response_model=PaymentResponse)
async def verify_payment(
    payment_id: int,
    verified: bool = Query(...),
    rejection_reason: str = Query(None),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_super_admin)
):
    """Verify a payment (Super Admin only)"""
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    if verified:
        payment.payment_status = PaymentStatus.COMPLETED
        payment.verified_by = current_user.id
        payment.verified_at = datetime.now()
        payment.payment_date = datetime.now()
        
        # Activate the associated subscription
        subscription = db.query(TenantSubscription).filter(
            TenantSubscription.payment_id == payment.id
        ).first()
        
        if subscription:
            subscription.payment_status = PaymentStatus.COMPLETED
            subscription.status = SubscriptionStatus.ACTIVE
            subscription.is_current = True
            subscription.activated_at = datetime.now()
            
            # Deactivate other subscriptions for this tenant
            db.query(TenantSubscription).filter(
                TenantSubscription.tenant_id == subscription.tenant_id,
                TenantSubscription.id != subscription.id,
                TenantSubscription.status == SubscriptionStatus.ACTIVE
            ).update({"status": SubscriptionStatus.EXPIRED, "is_current": False})
            
            # Update tenant status
            tenant = db.query(Tenant).filter(Tenant.id == subscription.tenant_id).first()
            if tenant:
                tenant.status = "active"
    else:
        payment.payment_status = PaymentStatus.FAILED
        payment.rejection_reason = rejection_reason
        payment.verified_by = current_user.id
        payment.verified_at = datetime.now()
        
        # Cancel the associated subscription
        subscription = db.query(TenantSubscription).filter(
            TenantSubscription.payment_id == payment.id
        ).first()
        if subscription:
            subscription.status = SubscriptionStatus.CANCELLED
    
    db.commit()
    db.refresh(payment)
    return PaymentResponse.model_validate(payment)

@router.get("/my-subscription", response_model=TenantSubscriptionResponse)
async def get_my_subscription(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Get current user's tenant subscription"""
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Not associated with a tenant")
    
    subscription = db.query(TenantSubscription).filter(
        TenantSubscription.tenant_id == current_user.tenant_id,
        TenantSubscription.is_current == True,
        TenantSubscription.status == SubscriptionStatus.ACTIVE
    ).first()
    
    if not subscription:
        raise HTTPException(status_code=404, detail="No active subscription found")
    
    return TenantSubscriptionResponse.model_validate(subscription)

@router.get("/tenant/{tenant_id}", response_model=List[TenantSubscriptionResponse])
async def get_tenant_subscriptions(
    tenant_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_super_admin)
):
    """Get all subscriptions for a tenant (Super Admin only)"""
    subscriptions = db.query(TenantSubscription).filter(
        TenantSubscription.tenant_id == tenant_id
    ).order_by(TenantSubscription.created_at.desc()).all()
    
    return [TenantSubscriptionResponse.model_validate(s) for s in subscriptions]

@router.put("/{subscription_id}/cancel")
async def cancel_subscription(
    subscription_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Cancel a subscription"""
    subscription = db.query(TenantSubscription).filter(
        TenantSubscription.id == subscription_id
    ).first()
    
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")
    
    # Only tenant admin of the same tenant or super admin can cancel
    if current_user.role != "super_admin" and current_user.tenant_id != subscription.tenant_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    subscription.status = SubscriptionStatus.CANCELLED
    subscription.cancelled_at = datetime.now()
    subscription.is_current = False
    
    db.commit()
    return {"message": "Subscription cancelled successfully"}