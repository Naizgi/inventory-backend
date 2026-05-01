from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from typing import Optional, List
from datetime import datetime, date, timedelta
from decimal import Decimal

from app.database import get_db
from app.services import LoanService, ProductService, StockService
from app.schemas import (
    LoanCreate, LoanResponse, LoanUpdate, LoanPaymentCreate,
    LoanPaymentResponse, LoanSettleRequest, LoanStatus, LoanPaymentMethod
)
from app.utils.auth import get_current_user, require_role, get_current_tenant, verify_branch_access
from app.models import User, Loan, LoanPayment, LoanItem, StockMovement, MovementType, Branch, UserRole

router = APIRouter(prefix="/loans", tags=["Loans"])


@router.post("/", response_model=LoanResponse, status_code=status.HTTP_201_CREATED)
async def create_loan(
    loan_data: LoanCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value, UserRole.SALESMAN.value]))
):
    """
    Create a new loan - deducts stock and records stock movement.
    
    - **customer_name**: Name of the customer
    - **customer_phone**: Customer phone number (optional)
    - **customer_email**: Customer email (optional)
    - **due_date**: Loan due date
    - **interest_rate**: Interest rate percentage (0-100)
    - **items**: List of products with quantities and unit prices
    - **notes**: Additional notes (optional)
    
    Both Admin, Manager, and Salesman can create loans for their branch.
    """
    try:
        tenant_id = get_current_tenant(request)
        branch_id = current_user.branch_id
        
        if not branch_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User not assigned to a branch"
            )
        
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
        
        # Salesman can only create loans for their branch
        if current_user.role == UserRole.SALESMAN.value and current_user.branch_id != branch_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to create loans for this branch"
            )
        
        # Validate stock before creating loan
        for item in loan_data.items:
            product = ProductService.get_product(db, item.product_id, tenant_id)
            if not product:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Product with id {item.product_id} not found in this tenant"
                )
            
            stock = StockService.get_stock(db, branch_id, item.product_id, tenant_id)
            if not stock or stock.quantity < item.quantity:
                available = stock.quantity if stock else Decimal(0)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Insufficient stock for {product.name}. Available: {available}, Requested: {item.quantity}"
                )
        
        # Create loan using service
        loan = LoanService.create_loan(db, loan_data, current_user.id, branch_id, tenant_id)
        
        return loan
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
            detail=f"Failed to create loan: {str(e)}"
        )


@router.get("/", response_model=List[LoanResponse])
async def get_loans(
    request: Request,
    customer_name: Optional[str] = Query(None, description="Filter by customer name"),
    customer_phone: Optional[str] = Query(None, description="Filter by customer phone"),
    status: Optional[LoanStatus] = Query(None, description="Filter by loan status"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    start_date: Optional[date] = Query(None, description="Start date filter"),
    end_date: Optional[date] = Query(None, description="End date filter"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all loans with optional filters.
    
    - **customer_name**: Filter by customer name (partial match)
    - **customer_phone**: Filter by customer phone
    - **status**: Filter by loan status (active, partially_paid, settled, overdue, cancelled)
    - **branch_id**: Filter by branch (admin only)
    - **start_date**: Filter by loan date start
    - **end_date**: Filter by loan date end
    
    Admin sees all loans, Managers see their branch, Salesmen see only their branch.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(Loan).filter(Loan.tenant_id == tenant_id)
        
        # Apply branch filter based on role
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
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
                query = query.filter(Loan.branch_id == branch_id)
        else:
            # Managers and salesmen can only see their branch
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            query = query.filter(Loan.branch_id == current_user.branch_id)
        
        # Apply filters
        if customer_name:
            query = query.filter(Loan.customer_name.ilike(f"%{customer_name}%"))
        
        if customer_phone:
            query = query.filter(Loan.customer_phone.contains(customer_phone))
        
        if status:
            query = query.filter(Loan.status == status)
        
        if start_date:
            query = query.filter(Loan.loan_date >= start_date)
        
        if end_date:
            query = query.filter(Loan.loan_date <= end_date + timedelta(days=1))
        
        # Update overdue status before returning
        loans = query.order_by(Loan.created_at.desc()).offset(skip).limit(limit).all()
        
        # Check and update overdue loans
        now = datetime.now()
        for loan in loans:
            if loan.status in [LoanStatus.ACTIVE, LoanStatus.PARTIALLY_PAID]:
                if loan.due_date < now and loan.remaining_amount > 0:
                    loan.status = LoanStatus.OVERDUE
                    db.commit()
        
        return loans
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve loans: {str(e)}"
        )


@router.get("/overdue", response_model=List[LoanResponse])
async def get_overdue_loans(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get all overdue loans.
    
    - **branch_id**: Optional branch filter
    
    Returns loans that are past due date with remaining balance.
    Only admin, tenant admin, and manager can view overdue loans.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(Loan).filter(
            Loan.tenant_id == tenant_id,
            Loan.due_date < datetime.now(),
            Loan.remaining_amount > 0,
            Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.PARTIALLY_PAID])
        )
        
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
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
                query = query.filter(Loan.branch_id == branch_id)
        else:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            query = query.filter(Loan.branch_id == current_user.branch_id)
        
        loans = query.order_by(Loan.due_date.asc()).all()
        
        # Update status to overdue
        for loan in loans:
            if loan.status != LoanStatus.OVERDUE:
                loan.status = LoanStatus.OVERDUE
                db.commit()
        
        return loans
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve overdue loans: {str(e)}"
        )


@router.get("/{loan_id}", response_model=LoanResponse)
async def get_loan(
    loan_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific loan by ID.
    
    - **loan_id**: The ID of the loan to retrieve
    
    Returns the loan details including items and payment history.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        loan = db.query(Loan).filter(
            Loan.id == loan_id,
            Loan.tenant_id == tenant_id
        ).first()
        
        if not loan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Loan with id {loan_id} not found in this tenant"
            )
        
        # Check permission
        if current_user.role not in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            if loan.branch_id != current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to view this loan"
                )
        
        # Check if loan should be marked as overdue
        now = datetime.now()
        if loan.status in [LoanStatus.ACTIVE, LoanStatus.PARTIALLY_PAID]:
            if loan.due_date < now and loan.remaining_amount > 0:
                loan.status = LoanStatus.OVERDUE
                db.commit()
        
        return loan
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve loan: {str(e)}"
        )


@router.put("/{loan_id}", response_model=LoanResponse)
async def update_loan(
    loan_id: int,
    loan_update: LoanUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Update loan details.
    
    - **loan_id**: The ID of the loan to update
    - **due_date**: Updated due date (optional)
    - **interest_rate**: Updated interest rate (optional)
    - **status**: Updated status (optional)
    - **notes**: Updated notes (optional)
    
    Only admin, tenant admin, and manager can update loans.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        loan = db.query(Loan).filter(
            Loan.id == loan_id,
            Loan.tenant_id == tenant_id
        ).first()
        
        if not loan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Loan with id {loan_id} not found in this tenant"
            )
        
        # Update fields
        if loan_update.due_date:
            loan.due_date = datetime.combine(loan_update.due_date, datetime.min.time())
        
        if loan_update.interest_rate is not None:
            old_interest_rate = loan.interest_rate
            loan.interest_rate = loan_update.interest_rate
            
            # Recalculate amounts
            principal = loan.total_amount - loan.interest_amount
            loan.interest_amount = principal * (loan_update.interest_rate / Decimal(100))
            loan.total_amount = principal + loan.interest_amount
            loan.remaining_amount = loan.total_amount - loan.paid_amount
        
        if loan_update.status:
            loan.status = loan_update.status
        
        if loan_update.notes is not None:
            loan.notes = loan_update.notes
        
        loan.updated_at = datetime.now()
        
        db.commit()
        db.refresh(loan)
        
        return loan
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update loan: {str(e)}"
        )


@router.delete("/{loan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_loan(
    loan_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Delete a loan (Super Admin or Tenant Admin only).
    
    - **loan_id**: The ID of the loan to delete
    
    Only super admin and tenant admin can delete loans. Loan must have no payments or be settled.
    Deletion restores stock for all items in the loan.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        loan = db.query(Loan).filter(
            Loan.id == loan_id,
            Loan.tenant_id == tenant_id
        ).first()
        
        if not loan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Loan with id {loan_id} not found in this tenant"
            )
        
        # Only allow deletion of loans with no payments or settled status
        if loan.paid_amount > 0 and loan.status != LoanStatus.SETTLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete loan with existing payments"
            )
        
        # Restore stock for items
        for item in loan.items:
            stock = StockService.get_stock(db, loan.branch_id, item.product_id, tenant_id)
            if stock:
                stock.quantity += item.quantity
            else:
                stock = Stock(
                    branch_id=loan.branch_id,
                    product_id=item.product_id,
                    quantity=item.quantity,
                    reorder_level=0
                )
                db.add(stock)
            
            # Record stock movement for restoration
            stock_movement = StockMovement(
                branch_id=loan.branch_id,
                product_id=item.product_id,
                user_id=current_user.id,
                change_qty=item.quantity,
                movement_type=MovementType.ADJUSTMENT.value,
                reference_id=loan.id,
                notes=f"Loan #{loan.loan_number} deleted - Stock restored"
            )
            db.add(stock_movement)
        
        db.delete(loan)
        db.commit()
        
        return None
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete loan: {str(e)}"
        )


@router.post("/{loan_id}/payments", response_model=LoanPaymentResponse)
async def add_loan_payment(
    loan_id: int,
    payment_data: LoanPaymentCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value, UserRole.SALESMAN.value]))
):
    """
    Add a payment to a loan.
    
    - **loan_id**: The ID of the loan
    - **amount**: Payment amount
    - **payment_method**: Payment method (cash, ticket, coupon, mixed)
    - **reference_number**: Optional reference number
    - **notes**: Optional payment notes
    - **sale_id**: Optional associated sale ID
    
    Both Admin, Manager, and Salesman can record payments for their branch.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        loan = db.query(Loan).filter(
            Loan.id == loan_id,
            Loan.tenant_id == tenant_id
        ).first()
        
        if not loan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Loan with id {loan_id} not found in this tenant"
            )
        
        # Check permission
        if current_user.role not in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            if loan.branch_id != current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to record payment for this loan"
                )
        
        if loan.status == LoanStatus.SETTLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Loan already settled"
            )
        
        if payment_data.amount > loan.remaining_amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Payment amount exceeds remaining balance. Remaining: {loan.remaining_amount}"
            )
        
        payment = LoanService.make_payment(db, loan_id, payment_data, current_user.id, tenant_id)
        
        return payment
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
            detail=f"Failed to add payment: {str(e)}"
        )


@router.post("/{loan_id}/settle", response_model=dict)
async def settle_loan(
    loan_id: int,
    settle_data: LoanSettleRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value, UserRole.SALESMAN.value]))
):
    """
    Settle a loan completely.
    
    - **loan_id**: The ID of the loan to settle
    - **amount**: Payment amount (must be >= remaining balance)
    - **payment_method**: Payment method
    - **reference_number**: Optional reference number
    - **notes**: Optional notes
    
    Both Admin, Manager, and Salesman can settle loans for their branch.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        loan = db.query(Loan).filter(
            Loan.id == loan_id,
            Loan.tenant_id == tenant_id
        ).first()
        
        if not loan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Loan with id {loan_id} not found in this tenant"
            )
        
        # Check permission
        if current_user.role not in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            if loan.branch_id != current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to settle this loan"
                )
        
        if loan.status == LoanStatus.SETTLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Loan already settled"
            )
        
        if settle_data.amount < loan.remaining_amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Amount must be at least {loan.remaining_amount} to settle"
            )
        
        # Create payment for remaining amount
        payment = LoanPayment(
            loan_id=loan_id,
            payment_number=f"PMT-{datetime.now().strftime('%Y%m%d')}-{loan.loan_number[-6:]}",
            amount=loan.remaining_amount,
            payment_method=settle_data.payment_method.value if hasattr(settle_data.payment_method, 'value') else settle_data.payment_method,
            reference_number=settle_data.reference_number,
            notes=settle_data.notes,
            recorded_by=current_user.id
        )
        
        db.add(payment)
        
        # Update loan
        loan.paid_amount = loan.total_amount
        loan.remaining_amount = Decimal(0)
        loan.status = LoanStatus.SETTLED
        loan.updated_at = datetime.now()
        
        db.commit()
        
        return {
            "message": "Loan settled successfully",
            "payment_id": payment.id,
            "payment_number": payment.payment_number,
            "settled_amount": float(loan.total_amount)
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to settle loan: {str(e)}"
        )


@router.get("/summary/statistics", response_model=dict)
async def get_loan_statistics(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get loan statistics summary.
    
    - **branch_id**: Optional branch filter
    
    Returns statistics about loans including totals, active loans, overdue loans.
    Only admin, tenant admin, and manager can view loan statistics.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(Loan).filter(Loan.tenant_id == tenant_id)
        
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
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
                query = query.filter(Loan.branch_id == branch_id)
        else:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            query = query.filter(Loan.branch_id == current_user.branch_id)
        
        # Calculate statistics
        total_loans = query.count()
        total_amount = query.with_entities(func.sum(Loan.total_amount)).scalar() or Decimal(0)
        total_paid = query.with_entities(func.sum(Loan.paid_amount)).scalar() or Decimal(0)
        total_remaining = query.with_entities(func.sum(Loan.remaining_amount)).scalar() or Decimal(0)
        
        active_loans = query.filter(Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.PARTIALLY_PAID])).count()
        overdue_loans = query.filter(Loan.status == LoanStatus.OVERDUE).count()
        settled_loans = query.filter(Loan.status == LoanStatus.SETTLED).count()
        
        # Calculate this month's loans
        first_day_of_month = datetime.now().replace(day=1)
        this_month_amount = query.filter(
            Loan.loan_date >= first_day_of_month
        ).with_entities(func.sum(Loan.total_amount)).scalar() or Decimal(0)
        
        # Calculate repayment rate
        repayment_rate = float(total_paid / total_amount * 100) if total_amount > 0 else 0
        
        return {
            "total_loans": total_loans,
            "total_amount": float(total_amount),
            "total_paid": float(total_paid),
            "total_remaining": float(total_remaining),
            "active_loans": active_loans,
            "overdue_loans": overdue_loans,
            "settled_loans": settled_loans,
            "this_month_amount": float(this_month_amount),
            "repayment_rate": round(repayment_rate, 2)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve loan statistics: {str(e)}"
        )


@router.get("/customer/{customer_phone}", response_model=List[LoanResponse])
async def get_customer_loans(
    customer_phone: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all loans for a specific customer by phone number.
    
    - **customer_phone**: Customer phone number
    
    Returns all loans associated with the customer.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(Loan).filter(
            Loan.tenant_id == tenant_id,
            Loan.customer_phone == customer_phone
        )
        
        # Apply branch filter based on role
        if current_user.role not in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            query = query.filter(Loan.branch_id == current_user.branch_id)
        
        loans = query.order_by(Loan.created_at.desc()).all()
        
        return loans
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve customer loans: {str(e)}"
        )


# ==================== HELPER FUNCTIONS ====================

def get_loan_by_number(db: Session, tenant_id: int, loan_number: str) -> Optional[Loan]:
    """
    Get a loan by its loan number within a tenant.
    """
    return db.query(Loan).filter(
        Loan.tenant_id == tenant_id,
        Loan.loan_number == loan_number
    ).first()


def get_customer_total_debt(db: Session, tenant_id: int, customer_phone: str, branch_id: Optional[int] = None) -> Decimal:
    """
    Get total outstanding debt for a customer within a tenant.
    """
    query = db.query(Loan).filter(
        Loan.tenant_id == tenant_id,
        Loan.customer_phone == customer_phone,
        Loan.remaining_amount > 0,
        Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.PARTIALLY_PAID, LoanStatus.OVERDUE])
    )
    
    if branch_id:
        query = query.filter(Loan.branch_id == branch_id)
    
    total = query.with_entities(func.sum(Loan.remaining_amount)).scalar() or Decimal(0)
    return total