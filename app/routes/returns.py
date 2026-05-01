from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from typing import List, Optional
from datetime import datetime, date, timedelta
from decimal import Decimal
from app.database import get_db
from app.services import SaleReturnService, SaleService, ProductService, StockService
from app.schemas import (
    SaleReturnCreate, SaleReturnResponse, SaleReturnItemResponse,
    ReturnStatus
)
from app.utils.auth import get_current_user, require_role, get_current_tenant, verify_branch_access
from app.models import User, SaleReturn, SaleReturnItem, Sale, SaleItem, Product, StockMovement, MovementType, Branch, UserRole

router = APIRouter(prefix="/returns", tags=["Returns"])


@router.post("/", response_model=SaleReturnResponse, status_code=status.HTTP_201_CREATED)
async def create_return(
    return_data: SaleReturnCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value, UserRole.SALESMAN.value]))
):
    """
    Create a new sale return/refund request.
    
    - **sale_id**: ID of the original sale
    - **items**: List of items to return with quantities
    - **reason**: Overall reason for return (optional)
    - **notes**: Additional notes (optional)
    
    Returns the created return record with pending status.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check if sale exists in this tenant
        sale = SaleService.get_sale(db, return_data.sale_id, tenant_id)
        if not sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Sale with id {return_data.sale_id} not found in this tenant"
            )
        
        # Check if sale is recent (within 30 days) - configurable
        days_limit = 30
        if sale.created_at < datetime.now() - timedelta(days=days_limit):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot return items from sales older than {days_limit} days"
            )
        
        # Check if there's already a pending return for this sale
        existing_return = db.query(SaleReturn).filter(
            SaleReturn.tenant_id == tenant_id,
            SaleReturn.sale_id == return_data.sale_id,
            SaleReturn.status.in_([ReturnStatus.PENDING, ReturnStatus.APPROVED])
        ).first()
        
        if existing_return:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"A return request already exists for this sale with status: {existing_return.status.value}"
            )
        
        # Create return
        sale_return = SaleReturnService.create_return(
            db, return_data, current_user.id, sale.branch_id, tenant_id
        )
        
        return sale_return
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create return: {str(e)}"
        )


@router.get("/", response_model=List[SaleReturnResponse])
async def get_returns(
    request: Request,
    status_filter: Optional[ReturnStatus] = Query(None, description="Filter by status"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    sale_id: Optional[int] = Query(None, description="Filter by sale ID"),
    start_date: Optional[date] = Query(None, description="Start date filter"),
    end_date: Optional[date] = Query(None, description="End date filter"),
    limit: int = Query(100, description="Maximum number of records", ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all sale returns with optional filters.
    
    - **status_filter**: Filter by return status (pending, approved, rejected, completed)
    - **branch_id**: Filter by branch
    - **sale_id**: Filter by sale ID
    - **start_date**: Filter by creation date start
    - **end_date**: Filter by creation date end
    - **limit**: Maximum number of records
    
    Returns a list of sale returns.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(SaleReturn).filter(SaleReturn.tenant_id == tenant_id)
        
        if status_filter:
            query = query.filter(SaleReturn.status == status_filter)
        
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
            query = query.filter(SaleReturn.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            # Non-admin users can only see returns from their branch
            query = query.filter(SaleReturn.branch_id == current_user.branch_id)
        
        if sale_id:
            query = query.filter(SaleReturn.sale_id == sale_id)
        
        if start_date:
            query = query.filter(SaleReturn.created_at >= start_date)
        
        if end_date:
            query = query.filter(SaleReturn.created_at <= end_date + timedelta(days=1))
        
        returns = query.order_by(SaleReturn.created_at.desc()).limit(limit).all()
        
        # Enrich with user names
        for return_item in returns:
            user = db.query(User).filter(User.id == return_item.user_id).first()
            return_item.user_name = user.name if user else "Unknown"
            
            if return_item.approved_by:
                approver = db.query(User).filter(User.id == return_item.approved_by).first()
                return_item.approver_name = approver.name if approver else "Unknown"
        
        return returns
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve returns: {str(e)}"
        )


@router.get("/pending", response_model=List[SaleReturnResponse])
async def get_pending_returns(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get all pending return requests.
    
    - **branch_id**: Optional branch filter
    
    Returns all returns with status 'pending'.
    Only admin, tenant admin, and manager can view pending returns.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(SaleReturn).filter(
            SaleReturn.tenant_id == tenant_id,
            SaleReturn.status == ReturnStatus.PENDING
        )
        
        if branch_id:
            branch = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.tenant_id == tenant_id
            ).first()
            if not branch:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Branch with id {branch_id} not found in this tenant"
                )
            query = query.filter(SaleReturn.branch_id == branch_id)
        
        returns = query.order_by(SaleReturn.created_at.asc()).all()
        
        # Enrich with user names
        for return_item in returns:
            user = db.query(User).filter(User.id == return_item.user_id).first()
            return_item.user_name = user.name if user else "Unknown"
        
        return returns
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve pending returns: {str(e)}"
        )


@router.get("/{return_id}", response_model=SaleReturnResponse)
async def get_return(
    return_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific return by ID.
    
    - **return_id**: The ID of the return to retrieve
    
    Returns the return details.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        return_item = db.query(SaleReturn).filter(
            SaleReturn.id == return_id,
            SaleReturn.tenant_id == tenant_id
        ).first()
        
        if not return_item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Return with id {return_id} not found in this tenant"
            )
        
        # Check permission
        if current_user.role not in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value] and current_user.branch_id != return_item.branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to view this return"
            )
        
        # Enrich with user names
        user = db.query(User).filter(User.id == return_item.user_id).first()
        return_item.user_name = user.name if user else "Unknown"
        
        if return_item.approved_by:
            approver = db.query(User).filter(User.id == return_item.approved_by).first()
            return_item.approver_name = approver.name if approver else "Unknown"
        
        return return_item
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve return: {str(e)}"
        )


@router.put("/{return_id}/approve", response_model=SaleReturnResponse)
async def approve_return(
    return_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Approve a pending return request.
    
    - **return_id**: The ID of the return to approve
    
    This will process the refund and restore inventory.
    Only admin, tenant admin, and manager can approve returns.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        return_item = db.query(SaleReturn).filter(
            SaleReturn.id == return_id,
            SaleReturn.tenant_id == tenant_id
        ).first()
        
        if not return_item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Return with id {return_id} not found in this tenant"
            )
        
        if return_item.status != ReturnStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot approve return with status: {return_item.status.value}"
            )
        
        # Approve the return
        approved_return = SaleReturnService.approve_return(db, return_id, current_user.id, tenant_id)
        
        return approved_return
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to approve return: {str(e)}"
        )


@router.put("/{return_id}/reject", response_model=SaleReturnResponse)
async def reject_return(
    return_id: int,
    request: Request,
    reason: Optional[str] = Query(None, description="Rejection reason"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Reject a pending return request.
    
    - **return_id**: The ID of the return to reject
    - **reason**: Optional rejection reason
    
    Only admin, tenant admin, and manager can reject returns.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        return_item = db.query(SaleReturn).filter(
            SaleReturn.id == return_id,
            SaleReturn.tenant_id == tenant_id
        ).first()
        
        if not return_item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Return with id {return_id} not found in this tenant"
            )
        
        if return_item.status != ReturnStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot reject return with status: {return_item.status.value}"
            )
        
        return_item.status = ReturnStatus.REJECTED
        return_item.notes = f"{return_item.notes or ''}\nRejected: {reason}" if reason else return_item.notes
        return_item.approved_by = current_user.id
        return_item.approved_at = datetime.now()
        
        db.commit()
        db.refresh(return_item)
        
        # Enrich with user names
        user = db.query(User).filter(User.id == return_item.user_id).first()
        return_item.user_name = user.name if user else "Unknown"
        
        approver = db.query(User).filter(User.id == return_item.approved_by).first()
        return_item.approver_name = approver.name if approver else "Unknown"
        
        return return_item
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reject return: {str(e)}"
        )


@router.get("/sale/{sale_id}", response_model=List[SaleReturnResponse])
async def get_returns_by_sale(
    sale_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all returns for a specific sale.
    
    - **sale_id**: The sale ID
    
    Returns all return requests associated with the sale.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        sale = SaleService.get_sale(db, sale_id, tenant_id)
        if not sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Sale with id {sale_id} not found in this tenant"
            )
        
        # Check permission
        if current_user.role not in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value] and current_user.branch_id != sale.branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to view returns for this sale"
            )
        
        returns = db.query(SaleReturn).filter(
            SaleReturn.tenant_id == tenant_id,
            SaleReturn.sale_id == sale_id
        ).all()
        
        # Enrich with user names
        for return_item in returns:
            user = db.query(User).filter(User.id == return_item.user_id).first()
            return_item.user_name = user.name if user else "Unknown"
        
        return returns
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve returns: {str(e)}"
        )


@router.get("/summary/daily", response_model=List[dict])
async def get_daily_return_summary(
    request: Request,
    start_date: date = Query(..., description="Start date"),
    end_date: date = Query(..., description="End date"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get daily return summary for a date range.
    
    - **start_date**: Start date
    - **end_date**: End date
    - **branch_id**: Optional branch filter
    
    Returns daily totals of return amounts and counts.
    Only admin, tenant admin, and manager can view return summaries.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(
            func.date(SaleReturn.created_at).label("return_date"),
            func.count(SaleReturn.id).label("return_count"),
            func.sum(SaleReturn.total_return_amount).label("total_return_amount"),
            func.sum(func.if_(SaleReturn.status == ReturnStatus.APPROVED, SaleReturn.total_return_amount, 0)).label("approved_amount"),
            func.sum(func.if_(SaleReturn.status == ReturnStatus.PENDING, SaleReturn.total_return_amount, 0)).label("pending_amount")
        ).filter(
            SaleReturn.tenant_id == tenant_id,
            SaleReturn.created_at >= start_date,
            SaleReturn.created_at <= end_date + timedelta(days=1)
        )
        
        if branch_id:
            branch = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.tenant_id == tenant_id
            ).first()
            if not branch:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Branch with id {branch_id} not found in this tenant"
                )
            query = query.filter(SaleReturn.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            query = query.filter(SaleReturn.branch_id == current_user.branch_id)
        
        results = query.group_by(func.date(SaleReturn.created_at)).order_by(func.date(SaleReturn.created_at)).all()
        
        return [
            {
                "date": str(r.return_date),
                "return_count": r.return_count,
                "total_return_amount": float(r.total_return_amount or 0),
                "approved_amount": float(r.approved_amount or 0),
                "pending_amount": float(r.pending_amount or 0)
            }
            for r in results
        ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve daily return summary: {str(e)}"
        )


@router.get("/summary/total", response_model=dict)
async def get_total_return_summary(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get total return summary.
    
    - **branch_id**: Optional branch filter
    
    Returns overall return statistics.
    Only admin, tenant admin, and manager can view return summaries.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(SaleReturn).filter(SaleReturn.tenant_id == tenant_id)
        
        if branch_id:
            branch = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.tenant_id == tenant_id
            ).first()
            if not branch:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Branch with id {branch_id} not found in this tenant"
                )
            query = query.filter(SaleReturn.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            query = query.filter(SaleReturn.branch_id == current_user.branch_id)
        
        total_returns = query.count()
        total_amount = query.filter(SaleReturn.status == ReturnStatus.APPROVED).with_entities(
            func.sum(SaleReturn.total_return_amount)
        ).scalar() or 0
        
        pending_count = query.filter(SaleReturn.status == ReturnStatus.PENDING).count()
        pending_amount = query.filter(SaleReturn.status == ReturnStatus.PENDING).with_entities(
            func.sum(SaleReturn.total_return_amount)
        ).scalar() or 0
        
        approved_count = query.filter(SaleReturn.status == ReturnStatus.APPROVED).count()
        approved_amount = query.filter(SaleReturn.status == ReturnStatus.APPROVED).with_entities(
            func.sum(SaleReturn.total_return_amount)
        ).scalar() or 0
        
        rejected_count = query.filter(SaleReturn.status == ReturnStatus.REJECTED).count()
        
        # Get this month's returns
        first_day_of_month = datetime.now().replace(day=1)
        this_month_amount = query.filter(
            SaleReturn.status == ReturnStatus.APPROVED,
            SaleReturn.created_at >= first_day_of_month
        ).with_entities(func.sum(SaleReturn.total_return_amount)).scalar() or 0
        
        return {
            "total_returns": total_returns,
            "total_return_amount": float(total_amount),
            "pending_count": pending_count,
            "pending_amount": float(pending_amount),
            "approved_count": approved_count,
            "approved_amount": float(approved_amount),
            "rejected_count": rejected_count,
            "this_month_amount": float(this_month_amount)
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve return summary: {str(e)}"
        )


@router.get("/top-returned-products", response_model=List[dict])
async def get_top_returned_products(
    request: Request,
    limit: int = Query(10, description="Number of products to return", ge=1, le=50),
    start_date: Optional[date] = Query(None, description="Start date filter"),
    end_date: Optional[date] = Query(None, description="End date filter"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get most frequently returned products.
    
    - **limit**: Maximum number of products to return
    - **start_date**: Optional start date filter
    - **end_date**: Optional end date filter
    - **branch_id**: Optional branch filter
    
    Returns top returned products with counts and amounts.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(
            SaleReturnItem.product_id,
            Product.name.label("product_name"),
            Product.sku.label("product_sku"),
            func.sum(SaleReturnItem.quantity).label("total_quantity_returned"),
            func.sum(SaleReturnItem.refund_amount).label("total_refund_amount"),
            func.count(SaleReturnItem.return_id).label("return_count")
        ).join(
            SaleReturn, SaleReturnItem.return_id == SaleReturn.id
        ).join(
            Product, SaleReturnItem.product_id == Product.id
        ).filter(
            SaleReturn.tenant_id == tenant_id,
            SaleReturn.status == ReturnStatus.APPROVED
        )
        
        if start_date:
            query = query.filter(SaleReturn.created_at >= start_date)
        
        if end_date:
            query = query.filter(SaleReturn.created_at <= end_date + timedelta(days=1))
        
        if branch_id:
            branch = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.tenant_id == tenant_id
            ).first()
            if not branch:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Branch with id {branch_id} not found in this tenant"
                )
            query = query.filter(SaleReturn.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            query = query.filter(SaleReturn.branch_id == current_user.branch_id)
        
        results = query.group_by(
            SaleReturnItem.product_id, Product.name, Product.sku
        ).order_by(
            func.sum(SaleReturnItem.quantity).desc()
        ).limit(limit).all()
        
        return [
            {
                "product_id": r.product_id,
                "product_name": r.product_name,
                "product_sku": r.product_sku,
                "total_quantity_returned": float(r.total_quantity_returned),
                "total_refund_amount": float(r.total_refund_amount),
                "return_count": r.return_count
            }
            for r in results
        ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve top returned products: {str(e)}"
        )


# ==================== HELPER FUNCTIONS ====================

def get_return_by_number(db: Session, tenant_id: int, return_number: str) -> Optional[SaleReturn]:
    """
    Get a return by its return number within a tenant.
    """
    return db.query(SaleReturn).filter(
        SaleReturn.tenant_id == tenant_id,
        SaleReturn.return_number == return_number
    ).first()


def get_pending_returns_count(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> int:
    """
    Get count of pending return requests within a tenant.
    """
    query = db.query(SaleReturn).filter(
        SaleReturn.tenant_id == tenant_id,
        SaleReturn.status == ReturnStatus.PENDING
    )
    if branch_id:
        query = query.filter(SaleReturn.branch_id == branch_id)
    return query.count()


def can_return_item(db: Session, tenant_id: int, sale_item_id: int, quantity: Decimal) -> bool:
    """
    Check if an item can be returned within a tenant.
    """
    # Get all returns for this sale item
    returns = db.query(SaleReturnItem).filter(
        SaleReturnItem.sale_item_id == sale_item_id
    ).join(
        SaleReturn, SaleReturnItem.return_id == SaleReturn.id
    ).filter(
        SaleReturn.tenant_id == tenant_id,
        SaleReturn.status == ReturnStatus.APPROVED
    ).all()
    
    already_returned = sum(r.quantity for r in returns)
    
    # Get original sale item
    sale_item = db.query(SaleItem).filter(SaleItem.id == sale_item_id).first()
    
    if not sale_item:
        return False
    
    return (sale_item.quantity - already_returned) >= quantity