# app/routers/auth.py
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional
import logging
import random
import string
import jwt

from app.database import get_db
from app.models import User, Tenant, SystemLog, TenantSubscription, SubscriptionPlan, Payment, OTP
from app.schemas import (
    Token, TokenData, LoginRequest, UserResponse, UserCreate,
    ChangePasswordRequest, ForgotPasswordRequest, ResetPasswordRequest,
    UserProfileUpdate, TenantStatus, PaymentStatus, SubscriptionStatus,
    SubscriptionCheckResponse, PaymentCreate, PaymentResponse,
    TenantSubscriptionCreate, TenantSubscriptionResponse
)
from app.services import AuthService, EmailService
from app.utils.auth import (
    get_current_user, get_current_active_user, get_current_user_with_subscription,
    require_super_admin, require_tenant_admin, get_subscription_status,
    check_subscription_valid
)
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Authentication"])


# ==================== HELPER FUNCTIONS ====================

def generate_otp() -> str:
    """Generate a 6-digit OTP"""
    return ''.join(random.choices(string.digits, k=6))


# ==================== AUTHENTICATION ENDPOINTS ====================

@router.post("/token", response_model=Token)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """
    Login endpoint - returns JWT token.
    
    Supports multi-tenant login. Super admin can login without tenant context.
    Tenant users must login through their tenant subdomain or provide tenant_id.
    """
    try:
        # Get tenant from request state if available
        tenant_id = getattr(request.state, 'tenant_id', None)
        
        # Authenticate user
        user = db.query(User).filter(User.email == form_data.username).first()
        
        if not user:
            logger.warning(f"Login attempt with non-existent user: {form_data.username}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Verify password
        if not AuthService.verify_password(form_data.password, user.password_hash):
            logger.warning(f"Failed login attempt for user: {form_data.username}")
            
            # Log failed attempt
            log = SystemLog(
                tenant_id=tenant_id,
                log_type="warning",
                message=f"Failed login attempt for: {form_data.username}",
                details=f"IP: {request.client.host if request.client else 'Unknown'}",
                user_id=user.id
            )
            db.add(log)
            db.commit()
            
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Check if user is active
        if not user.active:
            logger.warning(f"Inactive user login attempt: {form_data.username}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is disabled. Please contact administrator.",
            )
        
        # For non-super admin, validate tenant access
        if user.role != "super_admin":
            if not user.tenant_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User is not associated with any tenant"
                )
            
            if tenant_id and user.tenant_id != tenant_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied for this tenant"
                )
            
            # Check tenant status
            tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
            if tenant:
                if tenant.status == TenantStatus.SUSPENDED:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Account is suspended. Please contact support."
                    )
        
        # Update last login
        user.last_login = datetime.now()
        db.commit()
        
        # Create access token
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = AuthService.create_access_token(
            data={
                "sub": user.email,
                "user_id": user.id,
                "role": user.role,
                "tenant_id": user.tenant_id,
                "branch_id": user.branch_id
            },
            expires_delta=access_token_expires
        )
        
        # Log successful login
        log = SystemLog(
            tenant_id=user.tenant_id,
            log_type="info",
            message=f"User logged in: {user.email}",
            details=f"IP: {request.client.host if request.client else 'Unknown'}, Role: {user.role}",
            user_id=user.id
        )
        db.add(log)
        db.commit()
        
        # Get subscription info for tenant users
        subscription_info = None
        if user.tenant_id:
            subscription_info = get_subscription_status(user.tenant_id, db)
        
        return Token(
            access_token=access_token,
            token_type="bearer",
            user=UserResponse.model_validate(user),
            subscription=subscription_info
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during login"
        )


@router.post("/login", response_model=Token)
async def login_json(
    request: Request,
    login_data: LoginRequest,
    db: Session = Depends(get_db)
):
    """
    Login with JSON body (alternative to form data).
    
    Request body:
        {"username": "email@example.com", "password": "your_password"}
    """
    # Convert to form data for reuse
    form_data = OAuth2PasswordRequestForm(
        username=login_data.username,
        password=login_data.password,
        scope=""
    )
    return await login(request, form_data, db)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    user_data: UserCreate,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Register a new user (public registration).
    """
    try:
        # Prevent super admin registration through public API
        if user_data.role == "super_admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admin registration not allowed through this endpoint"
            )
        
        # Check if email already exists
        existing = db.query(User).filter(User.email == user_data.email).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        
        tenant_id = getattr(request.state, 'tenant_id', None)
        
        # If no tenant context, create a new tenant for this admin
        if not tenant_id:
            logger.info(f"No tenant context - creating new tenant for: {user_data.email}")
            
            # Create a new tenant
            tenant_name = f"{user_data.name}'s Business"
            subdomain = f"{user_data.email.split('@')[0].lower()}_{int(datetime.now().timestamp())}"
            
            new_tenant = Tenant(
                name=tenant_name,
                subdomain=subdomain,
                business_type="shop",
                status=TenantStatus.ACTIVE.value,
                created_at=datetime.now()
            )
            db.add(new_tenant)
            db.commit()
            db.refresh(new_tenant)
            tenant_id = new_tenant.id
            logger.info(f"✅ Created new tenant {tenant_id}: {tenant_name}")
        
        # Validate tenant exists
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid tenant"
            )
        
        # Check tenant status
        if tenant.status == TenantStatus.SUSPENDED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant is suspended. Cannot register new users."
            )
        
        # Create user
        user = User(
            tenant_id=tenant_id,
            name=user_data.name,
            email=user_data.email,
            password_hash=AuthService.get_password_hash(user_data.password),
            role=user_data.role,
            branch_id=user_data.branch_id,
            active=True,
            created_at=datetime.now()
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        
        # Log user creation
        log = SystemLog(
            tenant_id=tenant_id,
            log_type="info",
            message=f"New user registered: {user.email}",
            details=f"Role: {user.role}",
            user_id=user.id,
            ip_address=request.client.host if request.client else None
        )
        db.add(log)
        db.commit()
        
        logger.info(f"✅ User registered: {user.email}, Tenant: {tenant_id}")
        return UserResponse.model_validate(user)
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Registration error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred during registration: {str(e)}"
        )


@router.post("/logout")
async def logout(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Logout endpoint (token invalidation handled client-side).
    
    Logs the logout event for audit purposes.
    """
    tenant_id = getattr(request.state, 'tenant_id', None)
    
    # Log logout
    log = SystemLog(
        tenant_id=tenant_id,
        log_type="info",
        message=f"User logged out: {current_user.email}",
        user_id=current_user.id,
        ip_address=request.client.host if request.client else None
    )
    db.add(log)
    db.commit()
    
    return {"message": "Logged out successfully"}


# ==================== PROFILE ENDPOINTS ====================

@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Get current user profile with subscription info.
    """
    user_data = UserResponse.model_validate(current_user)
    
    # Add subscription info for tenant users
    if current_user.tenant_id:
        subscription = get_subscription_status(current_user.tenant_id, db)
        user_data.subscription = subscription
    
    return user_data


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Get current user profile with subscription info.
    """
    user_data = UserResponse.model_validate(current_user)
    
    # Add subscription info for tenant users
    if current_user.tenant_id:
        subscription = get_subscription_status(current_user.tenant_id, db)
        # Convert to dict if it's a Pydantic model
        if hasattr(subscription, 'model_dump'):
            user_data.subscription = subscription.model_dump()
        elif hasattr(subscription, 'dict'):
            user_data.subscription = subscription.dict()
        elif isinstance(subscription, dict):
            user_data.subscription = subscription
        else:
            # Fallback: try to convert to dict
            user_data.subscription = dict(subscription) if subscription else None
    
    return user_data

@router.post("/change-password")
async def change_password(
    password_data: ChangePasswordRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Change current user password.
    """
    try:
        # Verify current password
        if not AuthService.verify_password(password_data.current_password, current_user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect"
            )
        
        # Update password
        current_user.password_hash = AuthService.get_password_hash(password_data.new_password)
        db.commit()
        
        # Log password change
        log = SystemLog(
            tenant_id=current_user.tenant_id,
            log_type="warning",
            message=f"Password changed for user: {current_user.email}",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        return {"message": "Password changed successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Password change error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to change password"
        )


# ==================== OTP ENDPOINTS ====================

@router.post("/send-otp")
async def send_otp(
    email: str,
    purpose: str = "verification",
    db: Session = Depends(get_db)
):
    """
    Send OTP to email for verification
    """
    try:
        # Check rate limiting - prevent spam
        last_otp = db.query(OTP).filter(
            OTP.email == email,
            OTP.purpose == purpose,
            OTP.created_at > datetime.now() - timedelta(minutes=1)
        ).first()
        
        if last_otp:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Please wait 60 seconds before requesting another OTP"
            )
        
        # For registration, check if email already exists
        if purpose == "registration":
            existing_user = db.query(User).filter(User.email == email).first()
            if existing_user:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Email already registered"
                )
        
        # Generate OTP
        otp_code = generate_otp()
        expires_at = datetime.now() + timedelta(minutes=10)
        
        # Delete old unused OTPs for this email/purpose
        db.query(OTP).filter(
            OTP.email == email,
            OTP.purpose == purpose,
            OTP.is_used == False
        ).delete()
        
        # Save OTP to database
        otp_record = OTP(
            email=email,
            otp_code=otp_code,
            purpose=purpose,
            expires_at=expires_at,
            is_used=False
        )
        db.add(otp_record)
        db.commit()
        
        # Send email
        EmailService.send_otp_email(email, otp_code, purpose)
        
        return {
            "success": True,
            "message": "OTP sent successfully",
            "expires_in": 600
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Send OTP error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP"
        )


@router.post("/verify-otp")
async def verify_otp(
    email: str,
    otp: str,
    purpose: str = "verification",
    db: Session = Depends(get_db)
):
    """
    Verify OTP code
    """
    try:
        # Find valid OTP
        otp_record = db.query(OTP).filter(
            OTP.email == email,
            OTP.otp_code == otp,
            OTP.purpose == purpose,
            OTP.is_used == False,
            OTP.expires_at > datetime.now()
        ).first()
        
        if not otp_record:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired OTP"
            )
        
        # Mark OTP as used
        otp_record.is_used = True
        db.commit()
        
        # Generate a temporary token for registration completion
        temp_token = AuthService.create_access_token(
            data={"email": email, "purpose": purpose, "verified": True},
            expires_delta=timedelta(minutes=30)
        )
        
        return {
            "success": True,
            "message": "OTP verified successfully",
            "temp_token": temp_token
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Verify OTP error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify OTP"
        )


@router.post("/resend-otp")
async def resend_otp(
    email: str,
    purpose: str = "verification",
    db: Session = Depends(get_db)
):
    """
    Resend OTP code
    """
    return await send_otp(email, purpose, db)


@router.post("/complete-registration", response_model=UserResponse)
async def complete_registration(
    user_data: UserCreate,
    otp_token: str,
    db: Session = Depends(get_db)
):
    """
    Complete registration after OTP verification
    """
    try:
        # Verify OTP token
        try:
            payload = jwt.decode(
                otp_token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM]
            )
            verified_email = payload.get("email")
            purpose = payload.get("purpose")
            
            if purpose != "registration" or not payload.get("verified"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid verification token"
                )
            
            if verified_email != user_data.email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email mismatch"
                )
        except jwt.InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification token"
            )
        
        # Check if email already exists
        existing = db.query(User).filter(User.email == user_data.email).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        
        # Prevent super admin registration
        if user_data.role == "super_admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admin registration not allowed"
            )
        
        # Create a tenant for this user
        tenant_name = f"{user_data.name}'s Business"
        subdomain = f"{user_data.email.split('@')[0]}_{int(datetime.now().timestamp())}"
        
        new_tenant = Tenant(
            name=tenant_name,
            subdomain=subdomain,
            business_type="shop",
            status=TenantStatus.ACTIVE.value,
            created_at=datetime.now()
        )
        db.add(new_tenant)
        db.flush()
        tenant_id = new_tenant.id
        logger.info(f"✅ Created new tenant {tenant_id}: {tenant_name}")
        
        # Create user with tenant_id
        user = User(
            tenant_id=tenant_id,
            name=user_data.name,
            email=user_data.email,
            password_hash=AuthService.get_password_hash(user_data.password),
            role=user_data.role,
            active=True,
            created_at=datetime.now()
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        
        logger.info(f"✅ User registered successfully: {user.email} (Tenant: {tenant_id})")
        return UserResponse.model_validate(user)
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Complete registration error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to complete registration: {str(e)}"
        )


# ==================== SUBSCRIPTION ENDPOINTS ====================

@router.get("/subscription/status", response_model=SubscriptionCheckResponse)
async def get_my_subscription_status(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Get subscription status for current user's tenant.
    """
    if not current_user.tenant_id:
        return SubscriptionCheckResponse(
            is_valid=True,
            status="super_admin",
            plan_type=None,
            expires_in_days=None,
            features={"unlimited": True},
            message="Super admin - unlimited access"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    # Trial check
    if tenant.status == TenantStatus.TRIAL:
        if tenant.trial_end:
            days_left = (tenant.trial_end - datetime.now()).days
            return SubscriptionCheckResponse(
                is_valid=days_left > 0,
                status="trial",
                plan_type=None,
                expires_in_days=max(0, days_left),
                features={"trial": True},
                message=f"Trial period: {max(0, days_left)} days remaining"
            )
    
    # Active subscription check
    active_sub = db.query(TenantSubscription).join(SubscriptionPlan).filter(
        TenantSubscription.tenant_id == current_user.tenant_id,
        TenantSubscription.status == SubscriptionStatus.ACTIVE,
        TenantSubscription.payment_status == PaymentStatus.COMPLETED,
        TenantSubscription.end_date > datetime.now()
    ).first()
    
    if active_sub:
        days_left = (active_sub.end_date - datetime.now()).days
        plan = active_sub.plan
        features = {
            "has_loans": plan.has_loans if plan else False,
            "has_batch_tracking": plan.has_batch_tracking if plan else False,
            "has_pharmacy_features": plan.has_pharmacy_features if plan else False,
            "has_advanced_reports": plan.has_advanced_reports if plan else False,
            "has_api_access": plan.has_api_access if plan else False,
            "has_custom_branding": plan.has_custom_branding if plan else False,
            "max_users": plan.max_users if plan else 5,
            "max_branches": plan.max_branches if plan else 1,
            "max_products": plan.max_products if plan else 1000,
        }
        return SubscriptionCheckResponse(
            is_valid=True,
            status="active",
            plan_type=plan.plan_type if plan else None,
            expires_in_days=days_left,
            features=features,
            message=f"Active {plan.plan_name if plan else 'Unknown'} plan: {days_left} days remaining"
        )
    
    # Pending payment
    pending_sub = db.query(TenantSubscription).filter(
        TenantSubscription.tenant_id == current_user.tenant_id,
        TenantSubscription.payment_status == PaymentStatus.PENDING
    ).first()
    
    if pending_sub:
        return SubscriptionCheckResponse(
            is_valid=False,
            status="pending_payment",
            plan_type=None,
            expires_in_days=None,
            features=None,
            message="Payment pending. Please complete payment to activate your subscription."
        )
    
    return SubscriptionCheckResponse(
        is_valid=False,
        status="expired",
        plan_type=None,
        expires_in_days=None,
        features=None,
        message="No active subscription. Please subscribe to continue."
    )


@router.post("/subscription/subscribe", response_model=TenantSubscriptionResponse)
async def subscribe_to_plan(
    subscription_data: TenantSubscriptionCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_admin)
):
    """
    Subscribe to a plan (Tenant Admin only).
    
    Requires payment to be processed.
    """
    try:
        tenant_id = current_user.tenant_id if current_user.tenant_id else subscription_data.tenant_id
        
        if not tenant_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tenant ID is required"
            )
        
        # Validate tenant
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        
        # Validate plan
        plan = db.query(SubscriptionPlan).filter(
            SubscriptionPlan.id == subscription_data.plan_id,
            SubscriptionPlan.active == True
        ).first()
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found or inactive")
        
        # Check if already has active subscription
        existing_active = db.query(TenantSubscription).filter(
            TenantSubscription.tenant_id == tenant_id,
            TenantSubscription.status == SubscriptionStatus.ACTIVE,
            TenantSubscription.end_date > datetime.now()
        ).first()
        
        if existing_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You already have an active subscription. Please wait for it to expire or cancel it first."
            )
        
        # Create payment record
        payment_number = f"PAY-{datetime.now().strftime('%Y%m%d%H%M%S')}-{tenant_id}"
        payment = Payment(
            tenant_id=tenant_id,
            payment_number=payment_number,
            amount=plan.price,
            payment_method=subscription_data.payment_method,
            payment_status=PaymentStatus.PENDING,
            payment_type="subscription",
            notes=f"Subscription to {plan.plan_name}",
            created_at=datetime.now()
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)
        
        # Calculate subscription dates
        start_date = datetime.now()
        end_date = start_date + timedelta(days=plan.duration_months * 30)
        
        # Create subscription
        subscription = TenantSubscription(
            tenant_id=tenant_id,
            plan_id=plan.id,
            start_date=start_date,
            end_date=end_date,
            status=SubscriptionStatus.PENDING_PAYMENT,
            auto_renew=subscription_data.auto_renew,
            amount_paid=plan.price,
            payment_status=PaymentStatus.PENDING,
            payment_id=payment.id,
            created_at=datetime.now()
        )
        db.add(subscription)
        
        # Update tenant status
        tenant.status = TenantStatus.PENDING_PAYMENT
        
        db.commit()
        db.refresh(subscription)
        
        # Log subscription
        log = SystemLog(
            tenant_id=tenant_id,
            log_type="info",
            message=f"Subscription created: {plan.plan_name}",
            details=f"Amount: {plan.price}, Duration: {plan.duration_months} months, Payment: {payment_number}",
            user_id=current_user.id,
            ip_address=request.client.host if request.client else None
        )
        db.add(log)
        db.commit()
        
        return TenantSubscriptionResponse.model_validate(subscription)
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Subscription error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create subscription"
        )


@router.post("/subscription/cancel")
async def cancel_my_subscription(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_admin)
):
    """
    Cancel current subscription (Tenant Admin only).
    """
    try:
        if not current_user.tenant_id:
            raise HTTPException(status_code=400, detail="No tenant associated")
        
        active_sub = db.query(TenantSubscription).filter(
            TenantSubscription.tenant_id == current_user.tenant_id,
            TenantSubscription.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.PENDING_PAYMENT])
        ).first()
        
        if not active_sub:
            raise HTTPException(status_code=404, detail="No active subscription found")
        
        active_sub.status = SubscriptionStatus.CANCELLED
        active_sub.cancelled_at = datetime.now()
        active_sub.is_current = False
        
        # Log cancellation
        log = SystemLog(
            tenant_id=current_user.tenant_id,
            log_type="warning",
            message=f"Subscription cancelled by: {current_user.email}",
            details=f"Plan: {active_sub.plan.plan_name if active_sub.plan else 'Unknown'}",
            user_id=current_user.id,
            ip_address=request.client.host if request.client else None
        )
        db.add(log)
        db.commit()
        
        return {"message": "Subscription cancelled successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Subscription cancellation error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cancel subscription"
        )


@router.get("/subscription/plans")
async def get_available_plans(
    db: Session = Depends(get_db)
):
    """
    Get all available subscription plans (public endpoint).
    """
    plans = db.query(SubscriptionPlan).filter(
        SubscriptionPlan.active == True
    ).order_by(SubscriptionPlan.price).all()
    
    return {
        "plans": [
            {
                "id": plan.id,
                "plan_code": plan.plan_code,
                "plan_name": plan.plan_name,
                "plan_type": plan.plan_type,
                "duration_months": plan.duration_months,
                "price": float(plan.price),
                "max_users": plan.max_users,
                "max_branches": plan.max_branches,
                "max_products": plan.max_products,
                "features": {
                    "loans": plan.has_loans,
                    "batch_tracking": plan.has_batch_tracking,
                    "pharmacy": plan.has_pharmacy_features,
                    "advanced_reports": plan.has_advanced_reports,
                    "api_access": plan.has_api_access,
                    "custom_branding": plan.has_custom_branding,
                    "multi_branch": plan.has_multi_branch,
                    "priority_support": plan.has_priority_support,
                },
                "discount_percentage": float(plan.discount_percentage),
                "is_popular": plan.is_popular
            }
            for plan in plans
        ]
    }


# ==================== ADVANCED AUTH ENDPOINTS ====================

@router.post("/switch-tenant/{tenant_id}")
async def switch_tenant(
    tenant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Switch to a different tenant (for users with multiple tenant access).
    Currently only super admins can switch.
    """
    if current_user.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admins can switch tenants"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    # Create new token with updated tenant context
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = AuthService.create_access_token(
        data={
            "sub": current_user.email,
            "user_id": current_user.id,
            "role": current_user.role,
            "tenant_id": tenant_id,
            "branch_id": None
        },
        expires_delta=access_token_expires
    )
    
    # Log tenant switch
    log = SystemLog(
        tenant_id=tenant_id,
        log_type="info",
        message=f"Super admin switched to tenant: {tenant.name}",
        user_id=current_user.id,
        ip_address=request.client.host if request.client else None
    )
    db.add(log)
    db.commit()
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "tenant": {
            "id": tenant.id,
            "name": tenant.name,
            "subdomain": tenant.subdomain
        }
    }


@router.post("/refresh-token", response_model=Token)
async def refresh_token(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Refresh JWT token.
    """
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = AuthService.create_access_token(
        data={
            "sub": current_user.email,
            "user_id": current_user.id,
            "role": current_user.role,
            "tenant_id": current_user.tenant_id,
            "branch_id": current_user.branch_id
        },
        expires_delta=access_token_expires
    )
    
    return Token(
        access_token=access_token,
        token_type="bearer",
        user=UserResponse.model_validate(current_user)
    )


@router.get("/permissions")
async def get_user_permissions(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Get current user's permissions based on role and subscription.
    """
    permissions = {
        "can_manage_users": current_user.role in ["super_admin", "tenant_admin"],
        "can_manage_tenant": current_user.role in ["super_admin", "tenant_admin"],
        "can_create_sales": True,
        "can_create_loans": True,
        "can_approve_loans": current_user.role in ["super_admin", "tenant_admin", "manager"],
        "can_view_reports": current_user.role in ["super_admin", "tenant_admin", "manager"],
        "can_manage_settings": current_user.role in ["super_admin", "tenant_admin"],
        "can_manage_subscription": current_user.role in ["super_admin", "tenant_admin"],
    }
    
    # Add subscription-based permissions for tenant users
    if current_user.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
        if tenant and tenant.status != TenantStatus.TRIAL:
            active_sub = db.query(TenantSubscription).join(SubscriptionPlan).filter(
                TenantSubscription.tenant_id == current_user.tenant_id,
                TenantSubscription.status == SubscriptionStatus.ACTIVE,
                TenantSubscription.payment_status == PaymentStatus.COMPLETED
            ).first()
            
            if active_sub and active_sub.plan:
                permissions["has_loans"] = active_sub.plan.has_loans
                permissions["has_batch_tracking"] = active_sub.plan.has_batch_tracking
                permissions["has_pharmacy_features"] = active_sub.plan.has_pharmacy_features
                permissions["has_advanced_reports"] = active_sub.plan.has_advanced_reports
                permissions["has_api_access"] = active_sub.plan.has_api_access
            else:
                permissions["has_loans"] = False
                permissions["has_batch_tracking"] = False
                permissions["has_pharmacy_features"] = False
                permissions["has_advanced_reports"] = False
                permissions["has_api_access"] = False
        else:
            # Trial - give all features
            permissions["has_loans"] = True
            permissions["has_batch_tracking"] = True
            permissions["has_pharmacy_features"] = True
            permissions["has_advanced_reports"] = True
            permissions["has_api_access"] = False
    
    return permissions


@router.get("/session-info")
async def get_session_info(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Get detailed session information including subscription details.
    """
    session_info = {
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "name": current_user.name,
            "role": current_user.role,
            "tenant_id": current_user.tenant_id,
            "branch_id": current_user.branch_id
        },
        "ip": request.client.host if request.client else None,
        "subscription": None
    }
    
    if current_user.tenant_id:
        session_info["subscription"] = get_subscription_status(current_user.tenant_id, db)
    
    return session_info