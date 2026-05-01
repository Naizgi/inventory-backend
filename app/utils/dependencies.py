# app/utils/auth.py
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime, timedelta
from app.database import get_db
from app.models import User, SystemLog, UserRole, Tenant, TenantSubscription, Payment
from app.config import settings
import jwt
import logging

logger = logging.getLogger(__name__)

# OAuth2 scheme for token extraction
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/token",
    auto_error=False  # Don't auto-raise error, handle manually
)


def check_subscription_valid(
    tenant_id: int,
    db: Session
) -> bool:
    """
    Check if tenant has valid subscription or trial.
    
    Returns:
        True if subscription is valid or in trial period
        False if subscription expired or not paid
    """
    if not tenant_id:
        return False
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        return False
    
    # Check trial period
    if tenant.status == "trial":
        if tenant.trial_end and tenant.trial_end > datetime.now():
            return True
        else:
            # Trial expired
            tenant.status = "expired"
            db.commit()
            return False
    
    # Check if suspended
    if tenant.status == "suspended":
        return False
    
    # Check active subscription
    active_sub = db.query(TenantSubscription).filter(
        TenantSubscription.tenant_id == tenant_id,
        TenantSubscription.status == "active",
        TenantSubscription.payment_status == "completed",
        TenantSubscription.end_date > datetime.now()
    ).first()
    
    if active_sub:
        return True
    
    # Check grace period (7 days after subscription expiry)
    latest_sub = db.query(TenantSubscription).filter(
        TenantSubscription.tenant_id == tenant_id
    ).order_by(TenantSubscription.end_date.desc()).first()
    
    if latest_sub:
        grace_end = latest_sub.end_date + timedelta(days=7)
        if datetime.now() < grace_end:
            # In grace period
            tenant.status = "pending_payment"
            db.commit()
            return True
        else:
            # Grace period expired
            tenant.status = "suspended"
            db.commit()
            return False
    
    # No subscription or trial - check if pending payment
    pending_sub = db.query(TenantSubscription).filter(
        TenantSubscription.tenant_id == tenant_id,
        TenantSubscription.payment_status == "pending"
    ).first()
    
    if pending_sub:
        tenant.status = "pending_payment"
        db.commit()
        return False
    
    # Completely expired
    tenant.status = "expired"
    db.commit()
    return False


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    token: Optional[str] = Depends(oauth2_scheme),
) -> User:
    """
    Get current authenticated user from JWT token with tenant validation.
    
    This dependency extracts the user from the JWT token.
    Returns the User object if valid, raises 401 otherwise.
    
    Usage:
        @router.get("/protected")
        def protected_route(current_user: User = Depends(get_current_user)):
            return {"user": current_user.email}
    """
    if not token:
        logger.warning("No token provided in request")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Get tenant from request state
    tenant_id = getattr(request.state, 'tenant_id', None)
    
    try:
        # Decode JWT token
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        
        logger.debug(f"Token payload: {payload}")
        
        # Extract user information
        user_id = payload.get("user_id")
        email = payload.get("sub")
        role = payload.get("role")
        token_tenant_id = payload.get("tenant_id")
        
        if not user_id:
            logger.warning("Token missing user_id")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user_id",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Super admin can access any tenant, others must match
        if role != UserRole.SUPER_ADMIN.value and tenant_id and token_tenant_id != tenant_id:
            logger.warning(f"Tenant mismatch: token tenant {token_tenant_id} vs request tenant {tenant_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant access denied",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Get user from database
        query = db.query(User).filter(User.id == user_id)
        
        # For non-super admin, filter by tenant
        if role != UserRole.SUPER_ADMIN.value and tenant_id:
            query = query.filter(User.tenant_id == tenant_id)
        
        user = query.first()
        
        if not user:
            logger.warning(f"User not found for id: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Verify token matches user
        if user.email != email:
            logger.warning(f"Email mismatch: token email {email} vs user email {user.email}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token user mismatch",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Check if user is active
        if not user.active:
            logger.warning(f"User {user.email} is inactive")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is disabled. Please contact administrator.",
            )
        
        # Check if role in token matches database (security)
        if user.role != role:
            logger.warning(f"Role mismatch: token role {role} vs user role {user.role}")
            # Log this as it might indicate token tampering
            log_security_event(db, user, "Role mismatch in token", request)
        
        # ========== SUBSCRIPTION CHECK ==========
        # Check subscription for non-super admin users
        if user.role != UserRole.SUPER_ADMIN.value and user.tenant_id:
            tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
            
            if tenant:
                # Check if tenant is suspended
                if tenant.status == "suspended":
                    logger.warning(f"Tenant {tenant.id} is suspended, access denied for {user.email}")
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Account is suspended. Please contact support or renew your subscription.",
                    )
                
                # Check if subscription is valid (not super admin)
                if not check_subscription_valid(user.tenant_id, db):
                    logger.warning(f"Tenant {tenant.id} has no valid subscription, access denied for {user.email}")
                    
                    # Allow access if in grace period or pending payment for tenant admins
                    if user.role == UserRole.TENANT_ADMIN.value:
                        if tenant.status == "pending_payment":
                            raise HTTPException(
                                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                                detail="Subscription payment pending. Please complete payment to continue.",
                            )
                        elif tenant.status == "expired":
                            # Allow access but warn
                            logger.info(f"Allowing expired tenant admin access: {user.email}")
                        else:
                            raise HTTPException(
                                status_code=status.HTTP_403_FORBIDDEN,
                                detail="No active subscription. Please subscribe to continue.",
                            )
                    else:
                        # Non-admin users get generic message
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied. Please contact your administrator.",
                        )
        
        logger.debug(f"User authenticated: {user.email} (ID: {user.id}, Role: {user.role}, Tenant: {user.tenant_id})")
        return user
        
    except jwt.ExpiredSignatureError:
        logger.warning("Token has expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Please login again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_current_user: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Get current active user with subscription check.
    
    Same as get_current_user but also ensures user is active.
    Use this for most protected routes.
    
    Usage:
        @router.get("/protected")
        def protected_route(current_user: User = Depends(get_current_active_user)):
            return {"user": current_user.email}
    """
    if not current_user.active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled"
        )
    return current_user


def get_current_user_with_subscription(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> User:
    """
    Get current user with strict subscription requirement.
    
    This dependency ensures the user has an active paid subscription.
    Use for routes that require an active subscription (not trial or grace period).
    
    Usage:
        @router.post("/sales/")
        def create_sale(current_user: User = Depends(get_current_user_with_subscription)):
            return {"message": "Sale created"}
    """
    # Super admin always has access
    if current_user.role == UserRole.SUPER_ADMIN.value:
        return current_user
    
    # Check for active subscription (not just trial or grace period)
    if current_user.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
        
        if tenant:
            # Trial is acceptable
            if tenant.status == "trial" and tenant.trial_end and tenant.trial_end > datetime.now():
                return current_user
            
            # Need active paid subscription
            active_paid_sub = db.query(TenantSubscription).filter(
                TenantSubscription.tenant_id == current_user.tenant_id,
                TenantSubscription.status == "active",
                TenantSubscription.payment_status == "completed",
                TenantSubscription.end_date > datetime.now()
            ).first()
            
            if not active_paid_sub:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail="Active paid subscription required. Please subscribe or renew.",
                )
    
    return current_user


def require_role(required_roles: List[str]):
    """
    Dependency factory to require specific user roles.
    
    Args:
        required_roles: List of allowed roles (e.g., ["super_admin"], ["tenant_admin", "manager"])
    
    Usage:
        @router.get("/super-admin-only")
        def super_admin_route(current_user: User = Depends(require_role(["super_admin"]))):
            return {"message": "Super admin access granted"}
        
        @router.get("/manager-or-admin")
        def manager_route(current_user: User = Depends(require_role(["tenant_admin", "manager"]))):
            return {"message": "Access granted"}
    """
    def role_checker(current_user: User = Depends(get_current_active_user)):
        if current_user.role not in required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {', '.join(required_roles)}. Your role: {current_user.role}"
            )
        return current_user
    return role_checker


def require_super_admin(current_user: User = Depends(get_current_active_user)) -> User:
    """
    Dependency to require super admin role.
    
    Usage:
        @router.get("/super-admin-only")
        def super_admin_route(current_user: User = Depends(require_super_admin)):
            return {"message": "Super admin access granted"}
    """
    if current_user.role != UserRole.SUPER_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin privileges required"
        )
    return current_user


def require_tenant_admin(current_user: User = Depends(get_current_active_user)) -> User:
    """
    Dependency to require tenant admin or super admin role.
    
    Usage:
        @router.get("/tenant-admin-only")
        def tenant_admin_route(current_user: User = Depends(require_tenant_admin)):
            return {"message": "Tenant admin access granted"}
    """
    if current_user.role not in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant admin privileges required"
        )
    return current_user


def require_manager(current_user: User = Depends(get_current_active_user)) -> User:
    """
    Dependency to require manager, tenant admin, or super admin role.
    
    Usage:
        @router.get("/manager-only")
        def manager_route(current_user: User = Depends(require_manager)):
            return {"message": "Manager access granted"}
    """
    if current_user.role not in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager or higher privileges required"
        )
    return current_user


def require_salesman(current_user: User = Depends(get_current_active_user)) -> User:
    """
    Dependency for salesman role (allows all authenticated users within tenant).
    
    Usage:
        @router.get("/salesman-access")
        def salesman_route(current_user: User = Depends(require_salesman)):
            return {"message": "Access granted"}
    """
    # All authenticated users within a tenant have access
    return current_user


def get_current_user_optional(
    request: Request,
    db: Session = Depends(get_db),
    token: Optional[str] = Depends(oauth2_scheme),
) -> Optional[User]:
    """
    Get current user if authenticated, otherwise return None.
    
    Useful for routes that work for both authenticated and unauthenticated users.
    
    Usage:
        @router.get("/public-or-private")
        def mixed_route(current_user: Optional[User] = Depends(get_current_user_optional)):
            if current_user:
                return {"user": current_user.email, "authenticated": True}
            return {"message": "Public content", "authenticated": False}
    """
    if not token:
        return None
    
    tenant_id = getattr(request.state, 'tenant_id', None)
    
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        
        user_id = payload.get("user_id")
        role = payload.get("role")
        token_tenant_id = payload.get("tenant_id")
        
        if not user_id:
            return None
        
        # Validate tenant for non-super admin
        if role != UserRole.SUPER_ADMIN.value and tenant_id and token_tenant_id != tenant_id:
            return None
        
        query = db.query(User).filter(User.id == user_id)
        
        if role != UserRole.SUPER_ADMIN.value and tenant_id:
            query = query.filter(User.tenant_id == tenant_id)
        
        user = query.first()
        
        if not user or not user.active:
            return None
        
        # Don't check subscription for optional auth
        return user
        
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
    except Exception:
        return None


def get_token_from_request(request: Request) -> Optional[str]:
    """
    Extract JWT token from Authorization header.
    
    Usage:
        token = get_token_from_request(request)
        if token:
            # Process token
    """
    authorization = request.headers.get("Authorization")
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return None


def get_current_tenant(request: Request) -> Optional[int]:
    """
    Get current tenant ID from request state.
    
    Usage:
        tenant_id = get_current_tenant(request)
        if tenant_id:
            # Filter by tenant
    """
    return getattr(request.state, 'tenant_id', None)


def verify_branch_access(
    current_user: User,
    target_branch_id: Optional[int]
) -> bool:
    """
    Verify if current user has access to a specific branch.
    
    - Super admin has access to all branches
    - Tenant admin has access to all branches in their tenant
    - Managers and salesmen only have access to their assigned branch
    
    Usage:
        if not verify_branch_access(current_user, sale.branch_id):
            raise HTTPException(status_code=403, detail="Access denied")
    """
    if current_user.role in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]:
        return True
    
    if target_branch_id is None:
        return True
    
    return current_user.branch_id == target_branch_id


def get_user_branch_id(current_user: User) -> Optional[int]:
    """
    Get the branch ID for the current user.
    
    For super admin and tenant admin, returns None (no branch restriction).
    For managers and salesmen, returns their assigned branch ID.
    """
    if current_user.role in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]:
        return None
    return current_user.branch_id


def log_security_event(
    db: Session,
    user: Optional[User],
    message: str,
    request: Optional[Request] = None
):
    """
    Log security-related events (token tampering, unauthorized access attempts, etc.)
    """
    try:
        tenant_id = getattr(request.state, 'tenant_id', None) if request else None
        
        log = SystemLog(
            tenant_id=tenant_id,
            log_type="warning",
            message=f"Security: {message}",
            details=f"User: {user.email if user else 'Unknown'}",
            user_id=user.id if user else None,
            ip_address=request.client.host if request and hasattr(request, 'client') else None
        )
        db.add(log)
        db.commit()
    except Exception as e:
        logger.error(f"Failed to log security event: {str(e)}")


# ==================== DECORATORS FOR ROUTE PROTECTION ====================

def super_admin_required(func):
    """
    Decorator to mark a route as requiring super admin access.
    
    Usage:
        @router.get("/super-admin-only")
        @super_admin_required
        def super_admin_route():
            return {"message": "Super admin access"}
    """
    from functools import wraps
    
    @wraps(func)
    def wrapper(*args, current_user: User = Depends(require_super_admin), **kwargs):
        return func(*args, current_user=current_user, **kwargs)
    return wrapper


def tenant_admin_required(func):
    """
    Decorator to mark a route as requiring tenant admin access.
    """
    from functools import wraps
    
    @wraps(func)
    def wrapper(*args, current_user: User = Depends(require_tenant_admin), **kwargs):
        return func(*args, current_user=current_user, **kwargs)
    return wrapper


def manager_required(func):
    """
    Decorator to mark a route as requiring manager or higher access.
    """
    from functools import wraps
    
    @wraps(func)
    def wrapper(*args, current_user: User = Depends(require_manager), **kwargs):
        return func(*args, current_user=current_user, **kwargs)
    return wrapper


# ==================== HELPER FUNCTIONS ====================

def is_super_admin(user: User) -> bool:
    """Check if user has super admin role"""
    return user.role == UserRole.SUPER_ADMIN.value


def is_tenant_admin(user: User) -> bool:
    """Check if user has tenant admin role"""
    return user.role == UserRole.TENANT_ADMIN.value


def is_manager(user: User) -> bool:
    """Check if user has manager role"""
    return user.role == UserRole.MANAGER.value


def is_salesman(user: User) -> bool:
    """Check if user has salesman role"""
    return user.role == UserRole.SALESMAN.value


def is_admin_or_manager(user: User) -> bool:
    """Check if user is tenant admin or manager"""
    return user.role in [UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]


def can_manage_users(user: User) -> bool:
    """Check if user can manage other users"""
    return user.role in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]


def can_manage_tenant(user: User) -> bool:
    """Check if user can manage tenant settings"""
    return user.role in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]


def can_manage_branch(user: User, branch_id: int) -> bool:
    """
    Check if user can manage a specific branch.
    Super admin and tenant admin can manage any branch in their scope.
    Managers can manage their assigned branch.
    """
    if user.role in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]:
        return True
    return user.branch_id == branch_id and user.role == UserRole.MANAGER.value


def can_create_sales(user: User) -> bool:
    """Check if user can create sales (all authenticated users)"""
    return True


def can_create_loans(user: User) -> bool:
    """Check if user can create loans"""
    return user.role in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value, UserRole.SALESMAN.value]


def can_approve_loans(user: User) -> bool:
    """Check if user can approve loans"""
    return user.role in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]


def can_view_reports(user: User) -> bool:
    """Check if user can view reports"""
    return user.role in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]


def can_manage_settings(user: User) -> bool:
    """Check if user can manage system settings"""
    return user.role in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]


def can_manage_subscription(user: User, db: Session = None) -> bool:
    """Check if user can manage subscription"""
    if user.role == UserRole.SUPER_ADMIN.value:
        return True
    
    if user.role == UserRole.TENANT_ADMIN.value:
        return True
    
    return False


def get_subscription_status(tenant_id: int, db: Session) -> dict:
    """
    Get detailed subscription status for a tenant.
    
    Returns:
        dict with subscription status information
    """
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        return {"status": "not_found", "message": "Tenant not found"}
    
    if tenant.status == "trial":
        days_left = (tenant.trial_end - datetime.now()).days if tenant.trial_end else 0
        return {
            "status": "trial",
            "days_left": max(0, days_left),
            "trial_end": tenant.trial_end,
            "message": f"Trial period: {max(0, days_left)} days remaining"
        }
    
    active_sub = db.query(TenantSubscription).filter(
        TenantSubscription.tenant_id == tenant_id,
        TenantSubscription.status == "active",
        TenantSubscription.payment_status == "completed",
        TenantSubscription.end_date > datetime.now()
    ).first()
    
    if active_sub:
        days_left = (active_sub.end_date - datetime.now()).days
        return {
            "status": "active",
            "days_left": days_left,
            "end_date": active_sub.end_date,
            "plan_name": active_sub.plan.plan_name if active_sub.plan else "Unknown",
            "message": f"Active subscription: {days_left} days remaining"
        }
    
    pending_sub = db.query(TenantSubscription).filter(
        TenantSubscription.tenant_id == tenant_id,
        TenantSubscription.payment_status == "pending"
    ).first()
    
    if pending_sub:
        return {
            "status": "pending_payment",
            "message": "Payment pending. Please complete payment to activate."
        }
    
    return {
        "status": "expired",
        "message": "No active subscription. Please subscribe to continue."
    }