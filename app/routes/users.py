from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func
from typing import List, Optional
from datetime import datetime
from app.database import get_db
from app.services import AuthService
from app.models import User as UserModel, Branch, SystemLog, UserRole
from app.schemas import User, UserCreate, UserUpdate, UserProfileUpdate, ChangePasswordRequest
from app.utils.auth import get_current_user, require_role, get_current_active_user, get_current_tenant, verify_branch_access

router = APIRouter(prefix="/users", tags=["Users"])


# ==================== ADMIN USER MANAGEMENT ====================

@router.post("/", response_model=User, status_code=status.HTTP_201_CREATED)
async def create_user(
    user: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Create a new user (Super Admin or Tenant Admin only).
    
    - **name**: Full name of the user
    - **email**: Email address (must be unique within tenant)
    - **password**: Password (min 6 characters)
    - **role**: User role (tenant_admin, manager, salesman)
    - **branch_id**: Branch ID (required for non-tenant_admin users)
    - **active**: Whether the user is active
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check if email already exists in this tenant
        existing_user = db.query(UserModel).filter(
            UserModel.tenant_id == tenant_id,
            UserModel.email == user.email
        ).first()
        
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered in this tenant"
            )
        
        # Only super admin can create super admin users
        if user.role == UserRole.SUPER_ADMIN.value and current_user.role != UserRole.SUPER_ADMIN.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only super admin can create super admin users"
            )
        
        # For tenant admin, ensure user belongs to their tenant
        if current_user.role == UserRole.TENANT_ADMIN.value:
            if user.role == UserRole.SUPER_ADMIN.value:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Tenant admin cannot create super admin users"
                )
            tenant_id = current_user.tenant_id
        
        # Validate branch assignment for non-tenant_admin users
        if user.role != UserRole.TENANT_ADMIN.value and not user.branch_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Branch ID is required for non-tenant_admin users"
            )
        
        # Validate branch exists in this tenant
        if user.branch_id:
            branch = db.query(Branch).filter(
                Branch.id == user.branch_id,
                Branch.tenant_id == tenant_id
            ).first()
            if not branch:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Branch with id {user.branch_id} not found in this tenant"
                )
        
        # Create new user
        db_user = UserModel(
            tenant_id=tenant_id,
            name=user.name,
            email=user.email,
            password_hash=AuthService.get_password_hash(user.password),
            role=user.role,
            branch_id=user.branch_id,
            active=user.active
        )
        
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        
        # Log user creation
        log = SystemLog(
            tenant_id=tenant_id,
            log_type="info",
            message=f"User created: {user.email}",
            details=f"Role: {user.role}, Created by: {current_user.email}",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        return db_user
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create user: {str(e)}"
        )


@router.get("/", response_model=List[User])
async def get_users(
    request: Request,
    role: Optional[str] = Query(None, description="Filter by role (tenant_admin, manager, salesman)"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    active_only: bool = Query(True, description="Show only active users"),
    search: Optional[str] = Query(None, description="Search by name or email"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records"),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Get all users with optional filters (Super Admin or Tenant Admin only).
    
    - **role**: Filter by user role
    - **branch_id**: Filter by branch
    - **active_only**: Show only active users
    - **search**: Search by name or email
    - **skip**: Pagination offset
    - **limit**: Maximum records to return
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Super admin can see all users, tenant admin sees only their tenant
        if current_user.role == UserRole.SUPER_ADMIN.value:
            query = db.query(UserModel)
            if tenant_id:
                query = query.filter(UserModel.tenant_id == tenant_id)
        else:
            query = db.query(UserModel).filter(UserModel.tenant_id == current_user.tenant_id)
        
        # Apply filters
        if role:
            query = query.filter(UserModel.role == role)
        
        if branch_id:
            # Verify branch belongs to tenant
            branch = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.tenant_id == tenant_id
            ).first()
            if not branch:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Branch with id {branch_id} not found in this tenant"
                )
            query = query.filter(UserModel.branch_id == branch_id)
        
        if active_only:
            query = query.filter(UserModel.active == True)
        
        if search:
            query = query.filter(
                or_(
                    UserModel.name.ilike(f"%{search}%"),
                    UserModel.email.ilike(f"%{search}%")
                )
            )
        
        users = query.order_by(UserModel.created_at.desc()).offset(skip).limit(limit).all()
        
        return users
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve users: {str(e)}"
        )


@router.get("/{user_id}", response_model=User)
async def get_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Get user details by ID (Super Admin or Tenant Admin only).
    
    - **user_id**: The ID of the user to retrieve
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(UserModel).filter(UserModel.id == user_id)
        
        if current_user.role == UserRole.TENANT_ADMIN.value:
            query = query.filter(UserModel.tenant_id == current_user.tenant_id)
        elif tenant_id:
            query = query.filter(UserModel.tenant_id == tenant_id)
        
        user = query.first()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with id {user_id} not found in this tenant"
            )
        
        return user
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve user: {str(e)}"
        )


@router.put("/{user_id}", response_model=User)
async def update_user(
    user_id: int,
    user_update: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Update user (Super Admin or Tenant Admin only).
    
    - **user_id**: The ID of the user to update
    - **name**: Updated name (optional)
    - **email**: Updated email (optional)
    - **role**: Updated role (optional)
    - **branch_id**: Updated branch ID (optional)
    - **active**: Updated active status (optional)
    - **password**: Updated password (optional)
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(UserModel).filter(UserModel.id == user_id)
        
        if current_user.role == UserRole.TENANT_ADMIN.value:
            query = query.filter(UserModel.tenant_id == current_user.tenant_id)
        elif tenant_id:
            query = query.filter(UserModel.tenant_id == tenant_id)
        
        user = query.first()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with id {user_id} not found in this tenant"
            )
        
        # Prevent admin from changing their own role or deactivating themselves
        if user.id == current_user.id:
            if user_update.role is not None and user_update.role != user.role:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot change your own role"
                )
            if user_update.active is not None and user_update.active != user.active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot deactivate your own account"
                )
        
        # Check email uniqueness if changing email
        if user_update.email and user_update.email != user.email:
            email_query = db.query(UserModel).filter(
                UserModel.tenant_id == user.tenant_id,
                UserModel.email == user_update.email,
                UserModel.id != user_id
            )
            existing = email_query.first()
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already registered in this tenant"
                )
        
        # Validate branch exists if branch_id provided
        if user_update.branch_id:
            branch = db.query(Branch).filter(
                Branch.id == user_update.branch_id,
                Branch.tenant_id == user.tenant_id
            ).first()
            if not branch:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Branch with id {user_update.branch_id} not found in this tenant"
                )
        
        # Update fields
        update_data = user_update.model_dump(exclude_unset=True)
        
        if "password" in update_data and update_data["password"]:
            update_data["password_hash"] = AuthService.get_password_hash(update_data.pop("password"))
        elif "password" in update_data:
            update_data.pop("password")
        
        for key, value in update_data.items():
            setattr(user, key, value)
        
        db.commit()
        db.refresh(user)
        
        # Log user update
        log = SystemLog(
            tenant_id=user.tenant_id,
            log_type="info",
            message=f"User updated: {user.email}",
            details=f"Updated by: {current_user.email}",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        return user
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update user: {str(e)}"
        )


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Delete user (Super Admin or Tenant Admin only).
    
    - **user_id**: The ID of the user to delete
    
    Cannot delete your own account or users with associated data.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(UserModel).filter(UserModel.id == user_id)
        
        if current_user.role == UserRole.TENANT_ADMIN.value:
            query = query.filter(UserModel.tenant_id == current_user.tenant_id)
        elif tenant_id:
            query = query.filter(UserModel.tenant_id == tenant_id)
        
        user = query.first()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with id {user_id} not found in this tenant"
            )
        
        # Prevent deleting own account
        if user.id == current_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete your own account"
            )
        
        # Check if user has associated data
        from app.models import Sale, Loan, StockMovement
        
        sale_count = db.query(Sale).filter(
            Sale.user_id == user_id,
            Sale.tenant_id == user.tenant_id
        ).count()
        
        loan_count = db.query(Loan).filter(
            Loan.created_by == user_id,
            Loan.tenant_id == user.tenant_id
        ).count()
        
        movement_count = db.query(StockMovement).filter(
            StockMovement.user_id == user_id,
            StockMovement.branch.has(Branch.tenant_id == user.tenant_id)
        ).count()
        
        if sale_count > 0 or loan_count > 0 or movement_count > 0:
            # Soft delete by deactivating instead
            user.active = False
            user.email = f"deleted_{user.id}_{user.email}"
            db.commit()
            
            log = SystemLog(
                tenant_id=user.tenant_id,
                log_type="warning",
                message=f"User deactivated (had associated data): {user.email}",
                details=f"Sales: {sale_count}, Loans: {loan_count}, Movements: {movement_count}",
                user_id=current_user.id
            )
            db.add(log)
            db.commit()
            
            return None
        
        # Hard delete if no associated data
        db.delete(user)
        db.commit()
        
        # Log user deletion
        log = SystemLog(
            tenant_id=user.tenant_id,
            log_type="info",
            message=f"User deleted: {user.email}",
            details=f"Deleted by: {current_user.email}",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        return None
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete user: {str(e)}"
        )


@router.post("/{user_id}/activate", response_model=User)
async def activate_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Activate a deactivated user (Super Admin or Tenant Admin only).
    
    - **user_id**: The ID of the user to activate
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(UserModel).filter(UserModel.id == user_id)
        
        if current_user.role == UserRole.TENANT_ADMIN.value:
            query = query.filter(UserModel.tenant_id == current_user.tenant_id)
        elif tenant_id:
            query = query.filter(UserModel.tenant_id == tenant_id)
        
        user = query.first()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with id {user_id} not found in this tenant"
            )
        
        user.active = True
        db.commit()
        db.refresh(user)
        
        log = SystemLog(
            tenant_id=user.tenant_id,
            log_type="info",
            message=f"User activated: {user.email}",
            details=f"Activated by: {current_user.email}",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        return user
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to activate user: {str(e)}"
        )


@router.post("/{user_id}/deactivate", response_model=User)
async def deactivate_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Deactivate a user (Super Admin or Tenant Admin only).
    
    - **user_id**: The ID of the user to deactivate
    
    Cannot deactivate your own account.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(UserModel).filter(UserModel.id == user_id)
        
        if current_user.role == UserRole.TENANT_ADMIN.value:
            query = query.filter(UserModel.tenant_id == current_user.tenant_id)
        elif tenant_id:
            query = query.filter(UserModel.tenant_id == tenant_id)
        
        user = query.first()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with id {user_id} not found in this tenant"
            )
        
        if user.id == current_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot deactivate your own account"
            )
        
        user.active = False
        db.commit()
        db.refresh(user)
        
        log = SystemLog(
            tenant_id=user.tenant_id,
            log_type="info",
            message=f"User deactivated: {user.email}",
            details=f"Deactivated by: {current_user.email}",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        return user
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to deactivate user: {str(e)}"
        )


# ==================== CURRENT USER ENDPOINTS ====================

@router.get("/me", response_model=User)
async def get_current_user_profile(
    request: Request,
    current_user: UserModel = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Get current user profile (Any authenticated user).
    
    Returns the profile of the currently logged-in user.
    """
    try:
        # Get branch name if branch exists
        branch_name = None
        if current_user.branch_id:
            branch = db.query(Branch).filter(
                Branch.id == current_user.branch_id,
                Branch.tenant_id == current_user.tenant_id
            ).first()
            branch_name = branch.name if branch else None
        
        # Create response with additional fields
        user_dict = {
            "id": current_user.id,
            "tenant_id": current_user.tenant_id,
            "name": current_user.name,
            "email": current_user.email,
            "role": current_user.role,
            "branch_id": current_user.branch_id,
            "branch_name": branch_name,
            "active": current_user.active,
            "created_at": current_user.created_at
        }
        
        return user_dict
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve profile: {str(e)}"
        )


@router.put("/me", response_model=User)
async def update_current_user_profile(
    user_update: UserProfileUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_active_user)
):
    """
    Update current user profile (Any authenticated user).
    
    - **name**: Updated name (optional)
    - **email**: Updated email (optional)
    - **password**: Updated password (optional)
    
    Users can update their own profile.
    """
    try:
        update_data = user_update.model_dump(exclude_unset=True)
        
        # Check email uniqueness if changing email
        if "email" in update_data and update_data["email"] != current_user.email:
            existing = db.query(UserModel).filter(
                UserModel.tenant_id == current_user.tenant_id,
                UserModel.email == update_data["email"],
                UserModel.id != current_user.id
            ).first()
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already registered in this tenant"
                )
        
        # Hash password if provided
        if "password" in update_data and update_data["password"]:
            update_data["password_hash"] = AuthService.get_password_hash(update_data.pop("password"))
        elif "password" in update_data:
            update_data.pop("password")
        
        for key, value in update_data.items():
            if value is not None:
                setattr(current_user, key, value)
        
        db.commit()
        db.refresh(current_user)
        
        return current_user
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update profile: {str(e)}"
        )


@router.post("/me/change-password")
async def change_password(
    password_data: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_active_user)
):
    """
    Change current user password.
    
    - **current_password**: Current password
    - **new_password**: New password (min 6 characters)
    """
    try:
        # Verify current password
        if not AuthService.verify_password(password_data.current_password, current_user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect"
            )
        
        # Check new password length
        if len(password_data.new_password) < 6:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New password must be at least 6 characters long"
            )
        
        # Update password
        current_user.password_hash = AuthService.get_password_hash(password_data.new_password)
        db.commit()
        
        # Log password change
        log = SystemLog(
            tenant_id=current_user.tenant_id,
            log_type="info",
            message=f"Password changed for user {current_user.email}",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        return {"message": "Password changed successfully", "success": True}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to change password: {str(e)}"
        )


# ==================== STATISTICS ENDPOINTS ====================

@router.get("/stats/summary")
async def get_user_statistics(
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Get user statistics (Super Admin or Tenant Admin only).
    
    Returns counts of users by role and status within the tenant.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(UserModel)
        
        if current_user.role == UserRole.SUPER_ADMIN.value:
            if tenant_id:
                query = query.filter(UserModel.tenant_id == tenant_id)
        else:
            query = query.filter(UserModel.tenant_id == current_user.tenant_id)
        
        total_users = query.count()
        active_users = query.filter(UserModel.active == True).count()
        
        admin_count = query.filter(UserModel.role == UserRole.TENANT_ADMIN.value).count()
        manager_count = query.filter(UserModel.role == UserRole.MANAGER.value).count()
        salesman_count = query.filter(UserModel.role == UserRole.SALESMAN.value).count()
        
        users_by_branch = db.query(
            Branch.name,
            func.count(UserModel.id).label('user_count')
        ).outerjoin(
            UserModel, Branch.id == UserModel.branch_id
        ).filter(
            Branch.tenant_id == tenant_id
        ).group_by(Branch.id).all()
        
        return {
            "total_users": total_users,
            "active_users": active_users,
            "inactive_users": total_users - active_users,
            "by_role": {
                "tenant_admin": admin_count,
                "manager": manager_count,
                "salesman": salesman_count
            },
            "by_branch": [
                {"branch": branch_name, "user_count": count}
                for branch_name, count in users_by_branch
            ]
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve user statistics: {str(e)}"
        )


# ==================== BULK OPERATIONS ====================

@router.post("/bulk", response_model=dict, status_code=status.HTTP_201_CREATED)
async def bulk_create_users(
    users_data: List[UserCreate],
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Bulk create multiple users (Super Admin or Tenant Admin only).
    
    - **users_data**: List of user creation data
    
    Creates multiple users in a single request.
    """
    try:
        tenant_id = get_current_tenant(request)
        created_users = []
        errors = []
        
        for user_data in users_data:
            try:
                # Check if email already exists in tenant
                existing = db.query(UserModel).filter(
                    UserModel.tenant_id == tenant_id,
                    UserModel.email == user_data.email
                ).first()
                if existing:
                    errors.append(f"Email {user_data.email} already exists in this tenant")
                    continue
                
                # Validate branch for non-tenant_admin users
                if user_data.role != UserRole.TENANT_ADMIN.value and not user_data.branch_id:
                    errors.append(f"Branch ID required for user {user_data.email}")
                    continue
                
                # Validate branch exists in tenant
                if user_data.branch_id:
                    branch = db.query(Branch).filter(
                        Branch.id == user_data.branch_id,
                        Branch.tenant_id == tenant_id
                    ).first()
                    if not branch:
                        errors.append(f"Branch {user_data.branch_id} not found in tenant")
                        continue
                
                db_user = UserModel(
                    tenant_id=tenant_id,
                    name=user_data.name,
                    email=user_data.email,
                    password_hash=AuthService.get_password_hash(user_data.password),
                    role=user_data.role,
                    branch_id=user_data.branch_id,
                    active=user_data.active
                )
                db.add(db_user)
                created_users.append(db_user)
                
            except Exception as e:
                errors.append(f"Failed to create user {user_data.email}: {str(e)}")
        
        db.commit()
        
        # Refresh created users
        for user in created_users:
            db.refresh(user)
        
        return {
            "created": created_users,
            "created_count": len(created_users),
            "errors": errors
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to bulk create users: {str(e)}"
        )


# ==================== HELPER FUNCTIONS ====================

def get_users_by_branch(db: Session, tenant_id: int, branch_id: int) -> List[UserModel]:
    """Get all users belonging to a specific branch within a tenant"""
    return db.query(UserModel).filter(
        UserModel.tenant_id == tenant_id,
        UserModel.branch_id == branch_id,
        UserModel.active == True
    ).all()


def get_salesman_count(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> int:
    """Get count of salesman users within a tenant"""
    query = db.query(UserModel).filter(
        UserModel.tenant_id == tenant_id,
        UserModel.role == UserRole.SALESMAN.value,
        UserModel.active == True
    )
    if branch_id:
        query = query.filter(UserModel.branch_id == branch_id)
    return query.count()