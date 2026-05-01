# app/routers/tenants.py
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta
from app.database import get_db
from app.models import Tenant, User, SystemLog, Branch, Product, Sale, Loan, TenantSubscription, SubscriptionPlan, Payment
from app.schemas import (
    TenantCreate, TenantUpdate, TenantResponse, TenantStatus,
    TenantSubscriptionResponse, SubscriptionCheckResponse,
    PaymentStatus, SubscriptionStatus
)
from app.services import AuthService
from app.utils.auth import get_current_user, require_role

router = APIRouter(prefix="/tenants", tags=["Tenants"])


@router.post("/", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    tenant_data: TenantCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["super_admin"]))
):
    """
    Create a new tenant with trial period (Super Admin only).
    
    Creates a new business tenant with its own data isolation.
    Trial period defaults to 14 days unless specified.
    """
    try:
        # Check if tenant name exists
        existing = db.query(Tenant).filter(Tenant.name == tenant_data.name).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Tenant with name '{tenant_data.name}' already exists"
            )
        
        # Check subdomain uniqueness
        if tenant_data.subdomain:
            existing = db.query(Tenant).filter(Tenant.subdomain == tenant_data.subdomain).first()
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Subdomain '{tenant_data.subdomain}' already taken"
                )
        
        # Calculate trial period
        trial_days = getattr(tenant_data, 'trial_days', 14)
        trial_start = datetime.now()
        trial_end = trial_start + timedelta(days=trial_days)
        
        tenant = Tenant(
            name=tenant_data.name,
            subdomain=tenant_data.subdomain,
            business_type=tenant_data.business_type,
            email=tenant_data.email,
            phone=tenant_data.phone,
            address=tenant_data.address,
            logo_url=tenant_data.logo_url,
            status=TenantStatus.TRIAL,
            trial_start=trial_start,
            trial_end=trial_end,
            settings=tenant_data.settings if hasattr(tenant_data, 'settings') else None,
            created_by=current_user.id
        )
        
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        
        # Create default head office branch
        branch = Branch(
            tenant_id=tenant.id,
            name="Main Branch",
            business_type=tenant_data.business_type,
            is_head_office=True
        )
        db.add(branch)
        db.commit()
        db.refresh(branch)
        
        # Create default admin user for tenant
        if tenant_data.admin_email and tenant_data.admin_password:
            admin_user = User(
                tenant_id=tenant.id,
                branch_id=branch.id,
                name=tenant_data.admin_name or "Tenant Administrator",
                email=tenant_data.admin_email,
                password_hash=AuthService.get_password_hash(tenant_data.admin_password),
                role="tenant_admin",
                active=True
            )
            db.add(admin_user)
            db.commit()
        
        # Log tenant creation
        log = SystemLog(
            tenant_id=tenant.id,
            log_type="tenant_created",
            message=f"Tenant created: {tenant.name}",
            details=f"Trial period: {trial_days} days. Created by super admin: {current_user.email}",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        return tenant
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create tenant: {str(e)}"
        )


@router.get("/", response_model=List[TenantResponse])
async def get_tenants(
    status: Optional[TenantStatus] = Query(None, description="Filter by status"),
    subscription_plan: Optional[str] = Query(None, description="Filter by subscription plan"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["super_admin"]))
):
    """Get all tenants (Super Admin only)"""
    try:
        query = db.query(Tenant)
        
        if status:
            query = query.filter(Tenant.status == status)
        
        if subscription_plan:
            query = query.filter(Tenant.subscription_plan == subscription_plan)
        
        tenants = query.order_by(Tenant.created_at.desc()).offset(skip).limit(limit).all()
        
        return tenants
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve tenants: {str(e)}"
        )


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["super_admin", "tenant_admin"]))
):
    """Get tenant details with subscription information"""
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant with id {tenant_id} not found"
            )
        
        # If tenant admin, ensure they can only see their own tenant
        if current_user.role == "tenant_admin" and current_user.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only view your own tenant"
            )
        
        # Add subscription information
        response = TenantResponse.model_validate(tenant)
        
        # Get current active subscription
        current_sub = db.query(TenantSubscription).join(
            SubscriptionPlan
        ).filter(
            TenantSubscription.tenant_id == tenant.id,
            TenantSubscription.status == SubscriptionStatus.ACTIVE,
            TenantSubscription.payment_status == PaymentStatus.COMPLETED,
            TenantSubscription.end_date > datetime.now()
        ).first()
        
        if current_sub:
            response.current_subscription = TenantSubscriptionResponse.model_validate(current_sub)
            response.has_valid_subscription = True
            response.days_until_expiry = (current_sub.end_date - datetime.now()).days
        elif tenant.status == TenantStatus.TRIAL and tenant.trial_end:
            response.has_valid_subscription = True
            response.days_until_expiry = (tenant.trial_end - datetime.now()).days
        else:
            response.has_valid_subscription = False
            response.days_until_expiry = None
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve tenant: {str(e)}"
        )


@router.get("/by-subdomain/{subdomain}", response_model=TenantResponse)
async def get_tenant_by_subdomain(
    subdomain: str,
    db: Session = Depends(get_db)
):
    """Get tenant by subdomain (public endpoint for subdomain resolution)"""
    try:
        tenant = db.query(Tenant).filter(
            Tenant.subdomain == subdomain,
            Tenant.status.in_([TenantStatus.ACTIVE, TenantStatus.TRIAL])
        ).first()
        
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant with subdomain '{subdomain}' not found"
            )
        
        return tenant
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve tenant: {str(e)}"
        )


@router.put("/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: int,
    tenant_data: TenantUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["super_admin"]))
):
    """Update tenant (Super Admin only)"""
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant with id {tenant_id} not found"
            )
        
        update_data = tenant_data.model_dump(exclude_unset=True)
        
        # Check subdomain uniqueness if changing
        if "subdomain" in update_data and update_data["subdomain"] != tenant.subdomain:
            existing = db.query(Tenant).filter(
                Tenant.subdomain == update_data["subdomain"],
                Tenant.id != tenant_id
            ).first()
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Subdomain '{update_data['subdomain']}' already taken"
                )
        
        for key, value in update_data.items():
            setattr(tenant, key, value)
        
        tenant.updated_at = datetime.now()
        
        # Log tenant update
        log = SystemLog(
            tenant_id=tenant.id,
            log_type="tenant_updated",
            message=f"Tenant updated: {tenant.name}",
            details=f"Updated fields: {', '.join(update_data.keys())}",
            user_id=current_user.id
        )
        db.add(log)
        
        db.commit()
        db.refresh(tenant)
        
        return tenant
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update tenant: {str(e)}"
        )


@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(
    tenant_id: int,
    force: bool = Query(False, description="Force delete all tenant data"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["super_admin"]))
):
    """Delete a tenant (Super Admin only)"""
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant with id {tenant_id} not found"
            )
        
        if not force:
            # Check if tenant has data
            user_count = db.query(User).filter(User.tenant_id == tenant_id).count()
            if user_count > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Tenant has {user_count} users. Use force=True to delete anyway."
                )
        
        # Delete associated subscriptions and payments
        db.query(Payment).filter(Payment.tenant_id == tenant_id).delete()
        db.query(TenantSubscription).filter(TenantSubscription.tenant_id == tenant_id).delete()
        
        # Log before deletion
        log = SystemLog(
            log_type="tenant_deleted",
            message=f"Tenant deleted: {tenant.name}",
            details=f"Deleted by super admin: {current_user.email}",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        db.delete(tenant)
        db.commit()
        
        return None
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete tenant: {str(e)}"
        )


@router.post("/{tenant_id}/activate", response_model=TenantResponse)
async def activate_tenant(
    tenant_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["super_admin"]))
):
    """Activate a tenant - requires active paid subscription (Super Admin only)"""
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant with id {tenant_id} not found"
            )
        
        # Check if tenant has an active subscription
        active_sub = db.query(TenantSubscription).filter(
            TenantSubscription.tenant_id == tenant_id,
            TenantSubscription.status == SubscriptionStatus.ACTIVE,
            TenantSubscription.payment_status == PaymentStatus.COMPLETED,
            TenantSubscription.end_date > datetime.now()
        ).first()
        
        if not active_sub and tenant.status != TenantStatus.TRIAL:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot activate tenant without an active paid subscription"
            )
        
        tenant.status = TenantStatus.ACTIVE
        tenant.updated_at = datetime.now()
        
        # Log activation
        log = SystemLog(
            tenant_id=tenant.id,
            log_type="tenant_activated",
            message=f"Tenant activated: {tenant.name}",
            user_id=current_user.id
        )
        db.add(log)
        
        db.commit()
        db.refresh(tenant)
        
        return tenant
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to activate tenant: {str(e)}"
        )


@router.post("/{tenant_id}/suspend", response_model=TenantResponse)
async def suspend_tenant(
    tenant_id: int,
    reason: Optional[str] = Query(None, description="Reason for suspension"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["super_admin"]))
):
    """Suspend a tenant (Super Admin only)"""
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant with id {tenant_id} not found"
            )
        
        tenant.status = TenantStatus.SUSPENDED
        tenant.updated_at = datetime.now()
        
        # Log suspension
        log = SystemLog(
            tenant_id=tenant.id,
            log_type="tenant_suspended",
            message=f"Tenant suspended: {tenant.name}",
            details=reason or "No reason provided",
            user_id=current_user.id
        )
        db.add(log)
        
        db.commit()
        db.refresh(tenant)
        
        return tenant
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to suspend tenant: {str(e)}"
        )


@router.get("/{tenant_id}/subscription", response_model=TenantSubscriptionResponse)
async def get_tenant_subscription(
    tenant_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["super_admin", "tenant_admin"]))
):
    """Get current subscription for a tenant"""
    try:
        # If tenant admin, ensure they can only see their own
        if current_user.role == "tenant_admin" and current_user.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only view your own subscription"
            )
        
        subscription = db.query(TenantSubscription).filter(
            TenantSubscription.tenant_id == tenant_id,
            TenantSubscription.is_current == True,
            TenantSubscription.status.in_([
                SubscriptionStatus.ACTIVE,
                SubscriptionStatus.PENDING_PAYMENT
            ])
        ).first()
        
        if not subscription:
            # Check for trial
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if tenant and tenant.status == TenantStatus.TRIAL:
                return {
                    "message": "Tenant is in trial period",
                    "trial_end": tenant.trial_end,
                    "days_remaining": (tenant.trial_end - datetime.now()).days if tenant.trial_end else 0
                }
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active subscription found"
            )
        
        return TenantSubscriptionResponse.model_validate(subscription)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve subscription: {str(e)}"
        )


@router.post("/{tenant_id}/extend-trial")
async def extend_trial(
    tenant_id: int,
    days: int = Query(14, ge=1, le=90, description="Number of days to extend"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["super_admin"]))
):
    """Extend trial period for a tenant (Super Admin only)"""
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant with id {tenant_id} not found"
            )
        
        if tenant.status != TenantStatus.TRIAL:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Can only extend trial for tenants in trial status"
            )
        
        # Extend trial
        if tenant.trial_end and tenant.trial_end > datetime.now():
            tenant.trial_end = tenant.trial_end + timedelta(days=days)
        else:
            tenant.trial_end = datetime.now() + timedelta(days=days)
        
        tenant.updated_at = datetime.now()
        
        # Log trial extension
        log = SystemLog(
            tenant_id=tenant.id,
            log_type="trial_extended",
            message=f"Trial extended for: {tenant.name}",
            details=f"Extended by {days} days. New end date: {tenant.trial_end}",
            user_id=current_user.id
        )
        db.add(log)
        
        db.commit()
        db.refresh(tenant)
        
        return {
            "message": f"Trial extended by {days} days",
            "trial_end": tenant.trial_end,
            "days_remaining": (tenant.trial_end - datetime.now()).days
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to extend trial: {str(e)}"
        )


@router.get("/{tenant_id}/stats")
async def get_tenant_stats(
    tenant_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["super_admin", "tenant_admin"]))
):
    """Get tenant statistics"""
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant with id {tenant_id} not found"
            )
        
        # If tenant admin, ensure they can only see their own tenant
        if current_user.role == "tenant_admin" and current_user.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only view your own tenant stats"
            )
        
        # Count tenant data
        user_count = db.query(User).filter(User.tenant_id == tenant_id).count()
        branch_count = db.query(Branch).filter(Branch.tenant_id == tenant_id).count()
        product_count = db.query(Product).filter(Product.tenant_id == tenant_id).count()
        sale_count = db.query(Sale).filter(Sale.tenant_id == tenant_id).count()
        loan_count = db.query(Loan).filter(Loan.tenant_id == tenant_id).count()
        
        # Get subscription info
        subscription_status = "No subscription"
        subscription_end = None
        
        if tenant.status == TenantStatus.TRIAL:
            subscription_status = "Trial"
            subscription_end = tenant.trial_end
        else:
            active_sub = db.query(TenantSubscription).filter(
                TenantSubscription.tenant_id == tenant_id,
                TenantSubscription.is_current == True
            ).first()
            if active_sub:
                subscription_status = f"Active - {active_sub.plan.plan_name if active_sub.plan else 'Unknown'}"
                subscription_end = active_sub.end_date
        
        return {
            "tenant_id": tenant.id,
            "tenant_name": tenant.name,
            "status": tenant.status,
            "user_count": user_count,
            "branch_count": branch_count,
            "product_count": product_count,
            "sale_count": sale_count,
            "loan_count": loan_count,
            "subscription_status": subscription_status,
            "subscription_end": subscription_end,
            "trial_start": tenant.trial_start,
            "trial_end": tenant.trial_end,
            "created_at": tenant.created_at,
            "days_active": (datetime.now() - tenant.created_at).days if tenant.created_at else 0
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve tenant stats: {str(e)}"
        )


@router.get("/{tenant_id}/subscription-history")
async def get_subscription_history(
    tenant_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["super_admin", "tenant_admin"]))
):
    """Get subscription history for a tenant"""
    try:
        # If tenant admin, ensure they can only see their own
        if current_user.role == "tenant_admin" and current_user.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only view your own subscription history"
            )
        
        subscriptions = db.query(TenantSubscription).filter(
            TenantSubscription.tenant_id == tenant_id
        ).order_by(TenantSubscription.created_at.desc()).all()
        
        payments = db.query(Payment).filter(
            Payment.tenant_id == tenant_id
        ).order_by(Payment.created_at.desc()).all()
        
        return {
            "subscriptions": [TenantSubscriptionResponse.model_validate(s) for s in subscriptions],
            "payments": payments
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve subscription history: {str(e)}"
        )