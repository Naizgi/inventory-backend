from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import List, Optional
from datetime import datetime, timedelta
from decimal import Decimal
from app.database import get_db
from app.services import BranchService
from app.schemas import Branch, BranchCreate, BranchUpdate, BusinessType
from app.utils.auth import get_current_user, require_role, get_current_tenant, verify_branch_access
from app.models import User, Stock, Sale, Branch as BranchModel, Loan, PurchaseOrder, UserRole

router = APIRouter(prefix="/branches", tags=["Branches"])


# ==================== BRANCH CRUD OPERATIONS ====================

@router.post("/", response_model=Branch, status_code=status.HTTP_201_CREATED)
async def create_branch(
    branch: BranchCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Create a new branch (Super Admin or Tenant Admin only).
    
    - **name**: Branch name (required)
    - **business_type**: Type of business (shop, pharmacy, mini_market, supermarket)
    - **address**: Branch address (optional)
    - **phone**: Branch phone number (optional)
    - **is_head_office**: Whether this is the head office (optional)
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check if branch with same name exists in this tenant
        existing = db.query(BranchModel).filter(
            BranchModel.tenant_id == tenant_id,
            BranchModel.name == branch.name
        ).first()
        
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Branch with name '{branch.name}' already exists in this tenant"
            )
        
        return BranchService.create_branch(db, branch, tenant_id)
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create branch: {str(e)}"
        )


@router.get("/", response_model=List[Branch])
async def get_branches(
    request: Request,
    business_type: Optional[BusinessType] = Query(None, description="Filter by business type"),
    active_only: bool = Query(True, description="Show only active branches (with users)"),
    search: Optional[str] = Query(None, description="Search by name"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all branches with optional filters.
    
    - **business_type**: Filter by business type
    - **active_only**: Show only branches with at least one user
    - **search**: Search by branch name
    
    Super Admin can see all branches across all tenants.
    Tenant Admin can see all branches in their tenant.
    Managers and Salesmen see only their assigned branch.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # For non-tenant-admin users, only return their assigned branch
        if current_user.role not in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to any branch"
                )
            branch = BranchService.get_branch(db, current_user.branch_id, tenant_id)
            if not branch:
                return []
            return [branch]
        
        # Build query with tenant filtering
        query = db.query(BranchModel).filter(BranchModel.tenant_id == tenant_id)
        
        if business_type:
            query = query.filter(BranchModel.business_type == business_type)
        
        if search:
            query = query.filter(BranchModel.name.ilike(f"%{search}%"))
        
        branches = query.order_by(BranchModel.name).all()
        
        if active_only:
            # Filter branches with at least one user
            branches = [b for b in branches if b.users and len(b.users) > 0]
        
        return branches
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve branches: {str(e)}"
        )


@router.get("/{branch_id}", response_model=Branch)
async def get_branch(
    branch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get branch details by ID.
    
    - **branch_id**: The ID of the branch to retrieve
    
    Super Admin can view any branch.
    Tenant Admin can view any branch in their tenant.
    Non-admin can only view their assigned branch.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check access permission
        if not verify_branch_access(current_user, branch_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have access to this branch"
            )
        
        branch = BranchService.get_branch(db, branch_id, tenant_id)
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Branch with id {branch_id} not found"
            )
        
        return branch
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve branch: {str(e)}"
        )


@router.put("/{branch_id}", response_model=Branch)
async def update_branch(
    branch_id: int,
    branch: BranchUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Update branch (Super Admin or Tenant Admin only).
    
    - **branch_id**: The ID of the branch to update
    - **name**: Updated branch name (optional)
    - **business_type**: Updated business type (optional)
    - **address**: Updated address (optional)
    - **phone**: Updated phone (optional)
    - **is_head_office**: Updated head office status (optional)
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check if branch exists in this tenant
        existing = BranchService.get_branch(db, branch_id, tenant_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Branch with id {branch_id} not found in this tenant"
            )
        
        # Check name uniqueness if changing name
        if branch.name and branch.name != existing.name:
            name_exists = db.query(BranchModel).filter(
                BranchModel.tenant_id == tenant_id,
                BranchModel.name == branch.name,
                BranchModel.id != branch_id
            ).first()
            if name_exists:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Branch with name '{branch.name}' already exists in this tenant"
                )
        
        updated_branch = BranchService.update_branch(db, branch_id, tenant_id, branch)
        
        return updated_branch
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update branch: {str(e)}"
        )


@router.delete("/{branch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_branch(
    branch_id: int,
    request: Request,
    force: bool = Query(False, description="Force delete even if branch has associated data"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Delete a branch (Super Admin or Tenant Admin only).
    
    - **branch_id**: The ID of the branch to delete
    - **force**: If True, reassign or delete associated data
    
    Cannot delete branch with associated users, stock, sales, or loans unless force=True.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        branch = BranchService.get_branch(db, branch_id, tenant_id)
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Branch with id {branch_id} not found"
            )
        
        # Check for associated data
        users_count = db.query(User).filter(User.branch_id == branch_id).count()
        stock_count = db.query(Stock).filter(Stock.branch_id == branch_id).count()
        sales_count = db.query(Sale).filter(Sale.branch_id == branch_id).count()
        loans_count = db.query(Loan).filter(Loan.branch_id == branch_id).count()
        purchase_orders_count = db.query(PurchaseOrder).filter(PurchaseOrder.branch_id == branch_id).count()
        
        has_associated_data = users_count > 0 or stock_count > 0 or sales_count > 0 or loans_count > 0 or purchase_orders_count > 0
        
        if has_associated_data and not force:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete branch with associated data. Users: {users_count}, Stock: {stock_count}, Sales: {sales_count}, Loans: {loans_count}. Use force=True to delete anyway."
            )
        
        if force and has_associated_data:
            # Reassign users to NULL or delete them
            if users_count > 0:
                db.query(User).filter(User.branch_id == branch_id).update({User.branch_id: None})
            
            # Delete associated stock
            if stock_count > 0:
                db.query(Stock).filter(Stock.branch_id == branch_id).delete()
            
            # Delete associated data (sales, loans, purchase orders)
            if sales_count > 0:
                db.query(Sale).filter(Sale.branch_id == branch_id).delete()
            
            if loans_count > 0:
                db.query(Loan).filter(Loan.branch_id == branch_id).delete()
            
            if purchase_orders_count > 0:
                db.query(PurchaseOrder).filter(PurchaseOrder.branch_id == branch_id).delete()
        
        db.delete(branch)
        db.commit()
        
        return None
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete branch: {str(e)}"
        )


# ==================== BRANCH STATISTICS ====================

@router.get("/stats/summary")
async def get_branch_stats_summary(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get overall branch statistics.
    
    Returns aggregate statistics across all accessible branches.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Determine branch filter
        if current_user.role == UserRole.SUPER_ADMIN.value:
            branch_filter = None
        elif current_user.role == UserRole.TENANT_ADMIN.value:
            branch_filter = None  # Tenant admin sees all branches in tenant
        else:
            branch_filter = current_user.branch_id
        
        # Base query for branches
        branch_query = db.query(BranchModel).filter(BranchModel.tenant_id == tenant_id)
        if branch_filter:
            branch_query = branch_query.filter(BranchModel.id == branch_filter)
        
        total_branches = branch_query.count()
        
        # Total staff (users assigned to branches in this tenant)
        staff_query = db.query(User).filter(
            User.tenant_id == tenant_id,
            User.branch_id.isnot(None)
        )
        if branch_filter:
            staff_query = staff_query.filter(User.branch_id == branch_filter)
        total_staff = staff_query.count()
        
        # Total stock across branches
        stock_query = db.query(func.sum(Stock.quantity)).join(Branch).filter(Branch.tenant_id == tenant_id)
        if branch_filter:
            stock_query = stock_query.filter(Stock.branch_id == branch_filter)
        total_stock_result = stock_query.scalar()
        total_stock = float(total_stock_result) if total_stock_result else 0
        
        # Total revenue from sales (last 30 days)
        thirty_days_ago = datetime.now() - timedelta(days=30)
        revenue_query = db.query(func.sum(Sale.total_amount)).join(Branch).filter(
            Branch.tenant_id == tenant_id,
            Sale.created_at >= thirty_days_ago
        )
        if branch_filter:
            revenue_query = revenue_query.filter(Sale.branch_id == branch_filter)
        total_revenue_result = revenue_query.scalar()
        total_revenue = float(total_revenue_result) if total_revenue_result else 0
        
        # Total profit (last 30 days)
        profit_query = db.query(func.sum(Sale.total_amount - Sale.total_cost)).join(Branch).filter(
            Branch.tenant_id == tenant_id,
            Sale.created_at >= thirty_days_ago
        )
        if branch_filter:
            profit_query = profit_query.filter(Sale.branch_id == branch_filter)
        total_profit_result = profit_query.scalar()
        total_profit = float(total_profit_result) if total_profit_result else 0
        
        # Active loans
        loans_query = db.query(Loan).join(Branch).filter(
            Branch.tenant_id == tenant_id,
            Loan.remaining_amount > 0,
            Loan.status != 'settled'
        )
        if branch_filter:
            loans_query = loans_query.filter(Loan.branch_id == branch_filter)
        active_loans = loans_query.count()
        
        # Outstanding loan amount
        outstanding_query = db.query(func.sum(Loan.remaining_amount)).join(Branch).filter(
            Branch.tenant_id == tenant_id,
            Loan.remaining_amount > 0,
            Loan.status != 'settled'
        )
        if branch_filter:
            outstanding_query = outstanding_query.filter(Loan.branch_id == branch_filter)
        outstanding_amount_result = outstanding_query.scalar()
        outstanding_amount = float(outstanding_amount_result) if outstanding_amount_result else 0
        
        return {
            "total_branches": total_branches,
            "total_staff": total_staff,
            "total_stock": round(total_stock, 2),
            "total_revenue_30d": round(total_revenue, 2),
            "total_profit_30d": round(total_profit, 2),
            "profit_margin_30d": round((total_profit / total_revenue * 100) if total_revenue > 0 else 0, 2),
            "active_loans": active_loans,
            "outstanding_loans_amount": round(outstanding_amount, 2)
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve branch statistics: {str(e)}"
        )


@router.get("/{branch_id}/stats")
async def get_branch_stats(
    branch_id: int,
    request: Request,
    days: int = Query(30, description="Number of days for stats", ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get detailed statistics for a specific branch.
    
    - **branch_id**: The branch ID
    - **days**: Number of days for time-based statistics
    
    Returns comprehensive branch performance metrics.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check access permission
        if not verify_branch_access(current_user, branch_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have access to this branch"
            )
        
        branch = BranchService.get_branch(db, branch_id, tenant_id)
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Branch with id {branch_id} not found"
            )
        
        start_date = datetime.now() - timedelta(days=days)
        
        # Staff count
        staff_count = db.query(User).filter(
            User.tenant_id == tenant_id,
            User.branch_id == branch_id,
            User.active == True
        ).count()
        
        # Sales statistics
        sales_query = db.query(Sale).filter(Sale.branch_id == branch_id)
        total_sales = sales_query.count()
        total_revenue = sales_query.with_entities(func.sum(Sale.total_amount)).scalar() or Decimal(0)
        total_profit = sales_query.with_entities(func.sum(Sale.total_amount - Sale.total_cost)).scalar() or Decimal(0)
        
        # Recent sales (last 'days' days)
        recent_sales_query = sales_query.filter(Sale.created_at >= start_date)
        recent_sales_count = recent_sales_query.count()
        recent_revenue = recent_sales_query.with_entities(func.sum(Sale.total_amount)).scalar() or Decimal(0)
        
        # Stock statistics
        stock_query = db.query(Stock).filter(Stock.branch_id == branch_id)
        total_products_in_stock = stock_query.filter(Stock.quantity > 0).count()
        low_stock_items = stock_query.filter(Stock.quantity <= Stock.reorder_level).count()
        out_of_stock_items = stock_query.filter(Stock.quantity == 0).count()
        total_stock_value_query = stock_query.with_entities(
            func.sum(Stock.quantity * Product.cost)
        ).join(Product).scalar() or Decimal(0)
        
        # Loan statistics
        loans_query = db.query(Loan).filter(Loan.branch_id == branch_id)
        active_loans = loans_query.filter(Loan.remaining_amount > 0).count()
        total_loaned = loans_query.with_entities(func.sum(Loan.total_amount)).scalar() or Decimal(0)
        total_repaid = loans_query.with_entities(func.sum(Loan.paid_amount)).scalar() or Decimal(0)
        outstanding = loans_query.with_entities(func.sum(Loan.remaining_amount)).scalar() or Decimal(0)
        
        # Overdue loans
        now = datetime.now()
        overdue_loans = loans_query.filter(
            Loan.due_date < now,
            Loan.remaining_amount > 0,
            Loan.status != 'settled'
        ).count()
        
        # Purchase orders
        po_query = db.query(PurchaseOrder).filter(PurchaseOrder.branch_id == branch_id)
        pending_pos = po_query.filter(
            PurchaseOrder.status.in_(['pending', 'partially_received'])
        ).count()
        total_purchases = po_query.filter(
            PurchaseOrder.status == 'completed'
        ).with_entities(func.sum(PurchaseOrder.total_amount)).scalar() or Decimal(0)
        
        return {
            "branch": {
                "id": branch.id,
                "name": branch.name,
                "business_type": branch.business_type,
                "address": branch.address,
                "phone": branch.phone,
                "created_at": branch.created_at
            },
            "staff": {
                "total_staff": staff_count
            },
            "sales": {
                "total_sales": total_sales,
                "total_revenue": float(total_revenue),
                "total_profit": float(total_profit),
                "profit_margin": float((total_profit / total_revenue * 100) if total_revenue > 0 else 0),
                "recent_sales_count": recent_sales_count,
                "recent_revenue": float(recent_revenue),
                "recent_days": days
            },
            "inventory": {
                "total_products_in_stock": total_products_in_stock,
                "low_stock_items": low_stock_items,
                "out_of_stock_items": out_of_stock_items,
                "total_stock_value": float(total_stock_value)
            },
            "loans": {
                "active_loans": active_loans,
                "total_loaned": float(total_loaned),
                "total_repaid": float(total_repaid),
                "outstanding": float(outstanding),
                "overdue_loans": overdue_loans,
                "repayment_rate": float((total_repaid / total_loaned * 100) if total_loaned > 0 else 0)
            },
            "purchases": {
                "pending_orders": pending_pos,
                "total_purchases_30d": float(total_purchases)
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve branch statistics: {str(e)}"
        )


# ==================== BRANCH PERFORMANCE ====================

@router.get("/performance/top")
async def get_top_performing_branches(
    request: Request,
    metric: str = Query("revenue", description="Metric to rank by (revenue, profit, sales)"),
    days: int = Query(30, description="Number of days for calculation", ge=1, le=365),
    limit: int = Query(5, description="Number of branches to return", ge=1, le=20),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Get top performing branches (Super Admin or Tenant Admin only).
    
    - **metric**: Ranking metric (revenue, profit, sales)
    - **days**: Time period in days
    - **limit**: Number of branches to return
    
    Returns branches ranked by performance within the tenant.
    """
    try:
        tenant_id = get_current_tenant(request)
        start_date = datetime.now() - timedelta(days=days)
        
        branches = db.query(BranchModel).filter(BranchModel.tenant_id == tenant_id).all()
        
        performance_data = []
        for branch in branches:
            sales_query = db.query(Sale).filter(
                Sale.branch_id == branch.id,
                Sale.created_at >= start_date
            )
            
            total_sales = sales_query.count()
            total_revenue = sales_query.with_entities(func.sum(Sale.total_amount)).scalar() or Decimal(0)
            total_profit = sales_query.with_entities(func.sum(Sale.total_amount - Sale.total_cost)).scalar() or Decimal(0)
            
            performance_data.append({
                "branch_id": branch.id,
                "branch_name": branch.name,
                "business_type": branch.business_type,
                "total_sales": total_sales,
                "total_revenue": float(total_revenue),
                "total_profit": float(total_profit),
                "average_transaction": float(total_revenue / total_sales) if total_sales > 0 else 0
            })
        
        # Sort by selected metric
        if metric == "revenue":
            performance_data.sort(key=lambda x: x["total_revenue"], reverse=True)
        elif metric == "profit":
            performance_data.sort(key=lambda x: x["total_profit"], reverse=True)
        elif metric == "sales":
            performance_data.sort(key=lambda x: x["total_sales"], reverse=True)
        
        return performance_data[:limit]
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve branch performance: {str(e)}"
        )


# ==================== BUSINESS TYPE ENDPOINTS ====================

@router.get("/types", response_model=List[str])
async def get_business_types(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all available business types.
    
    Returns list of supported business types for branch configuration.
    """
    return [bt.value for bt in BusinessType]


@router.get("/by-type/{business_type}", response_model=List[Branch])
async def get_branches_by_type(
    business_type: BusinessType,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get branches by business type.
    
    - **business_type**: Filter by business type (shop, pharmacy, mini_market, supermarket)
    """
    try:
        tenant_id = get_current_tenant(request)
        query = db.query(BranchModel).filter(
            BranchModel.tenant_id == tenant_id,
            BranchModel.business_type == business_type
        )
        
        if current_user.role == UserRole.MANAGER.value and current_user.branch_id:
            query = query.filter(BranchModel.id == current_user.branch_id)
        
        return query.all()
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve branches by type: {str(e)}"
        )


# ==================== HELPER FUNCTIONS ====================

def get_branch_by_name(db: Session, tenant_id: int, name: str) -> Optional[BranchModel]:
    """Get a branch by its name within a tenant"""
    return db.query(BranchModel).filter(
        BranchModel.tenant_id == tenant_id,
        BranchModel.name == name
    ).first()


def get_branch_count(db: Session, tenant_id: int) -> int:
    """Get total number of branches in a tenant"""
    return db.query(BranchModel).filter(BranchModel.tenant_id == tenant_id).count()


def get_branch_with_users(db: Session, branch_id: int, tenant_id: int) -> Optional[BranchModel]:
    """Get branch with its users preloaded within a tenant"""
    return db.query(BranchModel).filter(
        BranchModel.id == branch_id,
        BranchModel.tenant_id == tenant_id
    ).first()