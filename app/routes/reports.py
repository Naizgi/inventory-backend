from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, case
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, date
from decimal import Decimal

from app.database import get_db
from app.services import ReportService, SettingsService
from app.utils.auth import get_current_user, require_role, get_current_tenant, verify_branch_access
from app.models import (
    User, Purchase, PurchaseOrder, PurchaseOrderItem, Loan, LoanPayment, 
    Sale, SaleItem, Product, Stock, Branch, PurchaseItem, StockMovement,
    LoanStatus, PurchaseStatus, UserRole
)

router = APIRouter(prefix="/reports", tags=["Reports"])


# ==================== SALES REPORT ====================
@router.get("/sales")
async def sales_report(
    request: Request,
    report_type: str = Query(..., pattern="^(weekly|monthly|yearly|custom)$", description="Report type"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    from_date: Optional[date] = Query(None, description="Start date for custom report"),
    to_date: Optional[date] = Query(None, description="End date for custom report"),
    include_loan_repayments: bool = Query(True, description="Include loan repayments in revenue"),
    include_purchases: bool = Query(True, description="Include purchase costs for profit calculation"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Generate sales report with purchases and loan repayments.
    
    - **report_type**: weekly, monthly, yearly, or custom
    - **branch_id**: Filter by specific branch (admin only)
    - **from_date**: Start date for custom report
    - **to_date**: End date for custom report
    
    Returns comprehensive sales analytics including revenue, profit, and product performance.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        end_date = date.today()
        
        if report_type == "weekly":
            start_date = end_date - timedelta(days=7)
        elif report_type == "monthly":
            start_date = end_date - timedelta(days=30)
        elif report_type == "yearly":
            start_date = end_date - timedelta(days=365)
        else:  # custom
            if not from_date or not to_date:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="from_date and to_date required for custom report"
                )
            start_date = from_date
            end_date = to_date
        
        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())
        
        # Base query for sales
        sales_query = db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.created_at.between(start_datetime, end_datetime)
        )
        
        # Apply branch filter
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
                sales_query = sales_query.filter(Sale.branch_id == branch_id)
        else:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            sales_query = sales_query.filter(Sale.branch_id == current_user.branch_id)
        
        sales = sales_query.all()
        
        # Calculate sales metrics
        total_sales = len(sales)
        total_revenue = sum(sale.total_amount for sale in sales)
        average_sale_value = float(total_revenue / total_sales) if total_sales > 0 else 0
        
        # Calculate profit from sales
        total_profit = sum(sale.total_amount - sale.total_cost for sale in sales)
        
        # Get best selling products
        best_selling_query = db.query(
            Product.id,
            Product.name,
            Product.sku,
            func.sum(SaleItem.quantity).label('total_quantity'),
            func.sum(SaleItem.total).label('total_revenue'),
            func.sum(SaleItem.cost).label('total_cost')
        ).join(
            SaleItem, Product.id == SaleItem.product_id
        ).join(
            Sale, SaleItem.sale_id == Sale.id
        ).filter(
            Sale.tenant_id == tenant_id,
            Sale.created_at.between(start_datetime, end_datetime)
        )
        
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
            if branch_id:
                best_selling_query = best_selling_query.filter(Sale.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            best_selling_query = best_selling_query.filter(Sale.branch_id == current_user.branch_id)
        
        best_selling = best_selling_query.group_by(Product.id).order_by(
            func.sum(SaleItem.quantity).desc()
        ).limit(10).all()
        
        # Get slow moving products
        slow_moving_query = db.query(
            Product.id,
            Product.name,
            Product.sku,
            func.coalesce(func.sum(SaleItem.quantity), 0).label('total_quantity'),
            func.coalesce(func.sum(SaleItem.total), 0).label('total_revenue')
        ).outerjoin(
            SaleItem, Product.id == SaleItem.product_id
        ).outerjoin(
            Sale, and_(
                SaleItem.sale_id == Sale.id,
                Sale.tenant_id == tenant_id,
                Sale.created_at.between(start_datetime, end_datetime)
            )
        ).filter(Product.tenant_id == tenant_id)
        
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
            if branch_id:
                slow_moving_query = slow_moving_query.filter(Sale.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            slow_moving_query = slow_moving_query.filter(Sale.branch_id == current_user.branch_id)
        
        slow_moving = slow_moving_query.group_by(Product.id).having(
            func.coalesce(func.sum(SaleItem.quantity), 0) < 5
        ).order_by(
            func.coalesce(func.sum(SaleItem.quantity), 0).asc()
        ).limit(10).all()
        
        # Get loan repayments if requested
        loan_repayments_total = Decimal(0)
        if include_loan_repayments:
            loan_payments_query = db.query(LoanPayment).join(Loan).filter(
                Loan.tenant_id == tenant_id,
                LoanPayment.payment_date.between(start_datetime, end_datetime)
            )
            
            if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
                if branch_id:
                    loan_payments_query = loan_payments_query.filter(Loan.branch_id == branch_id)
            elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
                loan_payments_query = loan_payments_query.filter(Loan.branch_id == current_user.branch_id)
            
            loan_payments = loan_payments_query.all()
            loan_repayments_total = sum(payment.amount for payment in loan_payments)
        
        # Get purchase costs if requested
        purchase_costs_total = Decimal(0)
        if include_purchases:
            purchase_query = db.query(PurchaseOrder).filter(
                PurchaseOrder.tenant_id == tenant_id,
                PurchaseOrder.order_date.between(start_datetime, end_datetime),
                PurchaseOrder.status == PurchaseStatus.COMPLETED
            )
            
            if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
                if branch_id:
                    purchase_query = purchase_query.filter(PurchaseOrder.branch_id == branch_id)
            elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
                purchase_query = purchase_query.filter(PurchaseOrder.branch_id == current_user.branch_id)
            
            purchase_orders = purchase_query.all()
            purchase_costs_total = sum(po.total_amount for po in purchase_orders)
        
        # Daily breakdown
        daily_breakdown = []
        current_date = start_date
        while current_date <= end_date:
            day_start = datetime.combine(current_date, datetime.min.time())
            day_end = datetime.combine(current_date, datetime.max.time())
            
            day_sales = db.query(Sale).filter(
                Sale.tenant_id == tenant_id,
                Sale.created_at.between(day_start, day_end)
            )
            
            if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
                if branch_id:
                    day_sales = day_sales.filter(Sale.branch_id == branch_id)
            elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
                day_sales = day_sales.filter(Sale.branch_id == current_user.branch_id)
            
            day_sales_list = day_sales.all()
            day_revenue = sum(s.total_amount for s in day_sales_list)
            day_profit = sum(s.total_amount - s.total_cost for s in day_sales_list)
            
            daily_breakdown.append({
                "date": current_date.isoformat(),
                "revenue": float(day_revenue),
                "profit": float(day_profit),
                "transactions": len(day_sales_list)
            })
            
            current_date += timedelta(days=1)
        
        return {
            "report_type": report_type,
            "date_range": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat()
            },
            "summary": {
                "total_sales": total_sales,
                "total_revenue": float(total_revenue),
                "average_sale_value": float(average_sale_value),
                "total_profit": float(total_profit),
                "profit_margin": float((total_profit / total_revenue * 100) if total_revenue > 0 else 0),
                "loan_repayments": float(loan_repayments_total),
                "purchase_costs": float(purchase_costs_total),
                "net_income": float(total_revenue + loan_repayments_total - purchase_costs_total)
            },
            "best_selling_products": [
                {
                    "product_id": product.id,
                    "product_name": product.name,
                    "product_sku": product.sku,
                    "quantity_sold": int(product.total_quantity),
                    "revenue": float(product.total_revenue),
                    "profit": float(product.total_revenue - (product.total_cost if hasattr(product, 'total_cost') else 0))
                }
                for product in best_selling
            ],
            "slow_moving_products": [
                {
                    "product_id": product.id,
                    "product_name": product.name,
                    "product_sku": product.sku,
                    "quantity_sold": int(product.total_quantity),
                    "revenue": float(product.total_revenue)
                }
                for product in slow_moving
            ],
            "daily_breakdown": daily_breakdown
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate sales report: {str(e)}"
        )


# ==================== PURCHASE REPORT ====================
@router.get("/purchases")
async def purchase_report(
    request: Request,
    from_date: Optional[date] = Query(None, description="Start date"),
    to_date: Optional[date] = Query(None, description="End date"),
    supplier: Optional[str] = Query(None, description="Filter by supplier"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Generate purchase report.
    
    - **from_date**: Start date (default: 30 days ago)
    - **to_date**: End date (default: today)
    - **supplier**: Filter by supplier name
    - **branch_id**: Filter by branch (admin only)
    
    Returns purchase summary including totals, supplier breakdown, and top items.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        if not to_date:
            to_date = date.today()
        if not from_date:
            from_date = to_date - timedelta(days=30)
        
        start_date = datetime.combine(from_date, datetime.min.time())
        end_date = datetime.combine(to_date, datetime.max.time())
        
        # Get purchase orders
        po_query = db.query(PurchaseOrder).filter(
            PurchaseOrder.tenant_id == tenant_id,
            PurchaseOrder.order_date.between(start_date, end_date)
        )
        
        # Apply branch filter
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
                po_query = po_query.filter(PurchaseOrder.branch_id == branch_id)
        else:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            po_query = po_query.filter(PurchaseOrder.branch_id == current_user.branch_id)
        
        if supplier:
            po_query = po_query.filter(PurchaseOrder.supplier.ilike(f"%{supplier}%"))
        
        purchase_orders = po_query.all()
        
        # Get legacy purchases
        legacy_query = db.query(Purchase).filter(
            Purchase.tenant_id == tenant_id,
            Purchase.created_at.between(start_date, end_date)
        )
        
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
            if branch_id:
                legacy_query = legacy_query.filter(Purchase.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            legacy_query = legacy_query.filter(Purchase.branch_id == current_user.branch_id)
        
        if supplier:
            legacy_query = legacy_query.filter(Purchase.supplier_name.ilike(f"%{supplier}%"))
        
        legacy_purchases = legacy_query.all()
        
        # Calculate totals
        total_po_cost = sum(po.total_amount for po in purchase_orders)
        total_legacy_cost = sum(p.total_amount for p in legacy_purchases)
        
        # Group by supplier
        supplier_totals = {}
        for po in purchase_orders:
            supplier_totals[po.supplier] = supplier_totals.get(po.supplier, Decimal(0)) + po.total_amount
        
        for p in legacy_purchases:
            if p.supplier_name:
                supplier_totals[p.supplier_name] = supplier_totals.get(p.supplier_name, Decimal(0)) + p.total_amount
        
        # Get top purchased products
        top_items_query = db.query(
            PurchaseOrderItem.product_id,
            Product.name.label('product_name'),
            Product.sku,
            func.sum(PurchaseOrderItem.quantity_received).label('total_quantity'),
            func.sum(PurchaseOrderItem.total_cost).label('total_cost')
        ).join(
            Product, PurchaseOrderItem.product_id == Product.id
        ).join(
            PurchaseOrder, PurchaseOrderItem.purchase_order_id == PurchaseOrder.id
        ).filter(
            PurchaseOrder.tenant_id == tenant_id,
            PurchaseOrder.order_date.between(start_date, end_date),
            PurchaseOrder.status == PurchaseStatus.COMPLETED
        )
        
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
            if branch_id:
                top_items_query = top_items_query.filter(PurchaseOrder.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            top_items_query = top_items_query.filter(PurchaseOrder.branch_id == current_user.branch_id)
        
        if supplier:
            top_items_query = top_items_query.filter(PurchaseOrder.supplier.ilike(f"%{supplier}%"))
        
        top_items = top_items_query.group_by(
            PurchaseOrderItem.product_id, Product.name, Product.sku
        ).order_by(
            func.sum(PurchaseOrderItem.total_cost).desc()
        ).limit(10).all()
        
        return {
            "date_range": {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat()
            },
            "summary": {
                "total_purchase_orders": len(purchase_orders),
                "total_po_cost": float(total_po_cost),
                "total_legacy_purchases": len(legacy_purchases),
                "total_legacy_cost": float(total_legacy_cost),
                "total_all_purchases": float(total_po_cost + total_legacy_cost),
                "average_order_value": float(total_po_cost / len(purchase_orders)) if purchase_orders else 0
            },
            "supplier_breakdown": [
                {"supplier": supplier_name, "total_amount": float(amount)}
                for supplier_name, amount in sorted(supplier_totals.items(), key=lambda x: x[1], reverse=True)
            ],
            "top_items": [
                {
                    "product_id": item.product_id,
                    "product_name": item.product_name,
                    "product_sku": item.sku,
                    "quantity": float(item.total_quantity) if item.total_quantity else 0,
                    "total_cost": float(item.total_cost) if item.total_cost else 0,
                    "average_cost": float(item.total_cost / item.total_quantity) if item.total_quantity and item.total_quantity > 0 else 0
                }
                for item in top_items
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate purchase report: {str(e)}"
        )


# ==================== LOAN REPORT ====================
@router.get("/loans")
async def loan_report(
    request: Request,
    from_date: Optional[date] = Query(None, description="Start date"),
    to_date: Optional[date] = Query(None, description="End date"),
    status: Optional[str] = Query(None, description="Filter by loan status"),
    customer_name: Optional[str] = Query(None, description="Filter by customer name"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Generate loan report.
    
    - **from_date**: Start date (default: 30 days ago)
    - **to_date**: End date (default: today)
    - **status**: Filter by loan status
    - **customer_name**: Filter by customer name
    - **branch_id**: Filter by branch (admin only)
    
    Returns loan analytics including totals, overdue loans, and payment breakdown.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        if not to_date:
            to_date = date.today()
        if not from_date:
            from_date = to_date - timedelta(days=30)
        
        start_date = datetime.combine(from_date, datetime.min.time())
        end_date = datetime.combine(to_date, datetime.max.time())
        
        # Get loans created in period
        loan_query = db.query(Loan).filter(
            Loan.tenant_id == tenant_id,
            Loan.created_at.between(start_date, end_date)
        )
        
        # Apply branch filter
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
                loan_query = loan_query.filter(Loan.branch_id == branch_id)
        else:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            loan_query = loan_query.filter(Loan.branch_id == current_user.branch_id)
        
        if status:
            loan_query = loan_query.filter(Loan.status == status)
        if customer_name:
            loan_query = loan_query.filter(Loan.customer_name.ilike(f"%{customer_name}%"))
        
        loans = loan_query.all()
        
        # Get payments in period
        payment_query = db.query(LoanPayment).join(Loan).filter(
            Loan.tenant_id == tenant_id,
            LoanPayment.payment_date.between(start_date, end_date)
        )
        
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
            if branch_id:
                payment_query = payment_query.filter(Loan.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            payment_query = payment_query.filter(Loan.branch_id == current_user.branch_id)
        
        payments = payment_query.all()
        
        # Calculate totals
        total_loans_amount = sum(loan.total_amount for loan in loans)
        total_payments = sum(payment.amount for payment in payments)
        
        # Get overdue loans
        now = datetime.now()
        overdue_query = db.query(Loan).filter(
            Loan.tenant_id == tenant_id,
            Loan.due_date < now,
            Loan.remaining_amount > 0,
            Loan.status != LoanStatus.SETTLED
        )
        
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
            if branch_id:
                overdue_query = overdue_query.filter(Loan.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            overdue_query = overdue_query.filter(Loan.branch_id == current_user.branch_id)
        
        overdue_loans = overdue_query.all()
        
        # Payment method breakdown
        payment_methods = {}
        for payment in payments:
            method = payment.payment_method
            payment_methods[method] = payment_methods.get(method, Decimal(0)) + payment.amount
        
        # Loans by status
        loans_by_status = {}
        for status_value in LoanStatus:
            status_count = db.query(Loan).filter(
                Loan.tenant_id == tenant_id,
                Loan.status == status_value
            )
            if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
                if branch_id:
                    status_count = status_count.filter(Loan.branch_id == branch_id)
            elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
                status_count = status_count.filter(Loan.branch_id == current_user.branch_id)
            loans_by_status[status_value.value] = status_count.count()
        
        # Outstanding total
        outstanding_total = db.query(func.sum(Loan.remaining_amount)).filter(
            Loan.tenant_id == tenant_id,
            Loan.remaining_amount > 0,
            Loan.status != LoanStatus.SETTLED
        )
        
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
            if branch_id:
                outstanding_total = outstanding_total.filter(Loan.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            outstanding_total = outstanding_total.filter(Loan.branch_id == current_user.branch_id)
        
        total_outstanding = outstanding_total.scalar() or Decimal(0)
        
        return {
            "date_range": {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat()
            },
            "summary": {
                "total_loans_issued": len(loans),
                "total_loan_amount": float(total_loans_amount),
                "total_repayments": float(total_payments),
                "net_outstanding_change": float(total_loans_amount - total_payments),
                "total_outstanding_loans": db.query(Loan).filter(
                    Loan.tenant_id == tenant_id,
                    Loan.remaining_amount > 0,
                    Loan.status != LoanStatus.SETTLED
                ).count(),
                "total_outstanding_amount": float(total_outstanding),
                "overdue_loans_count": len(overdue_loans),
                "overdue_amount": float(sum(loan.remaining_amount for loan in overdue_loans)),
                "repayment_rate": float((total_payments / total_loans_amount * 100)) if total_loans_amount > 0 else 0
            },
            "payment_method_breakdown": [
                {"method": method, "amount": float(amount)}
                for method, amount in payment_methods.items()
            ],
            "loans_by_status": loans_by_status,
            "recent_loans": [
                {
                    "loan_number": loan.loan_number,
                    "customer_name": loan.customer_name,
                    "total_amount": float(loan.total_amount),
                    "paid_amount": float(loan.paid_amount),
                    "remaining_amount": float(loan.remaining_amount),
                    "due_date": loan.due_date.isoformat(),
                    "status": loan.status.value,
                    "days_overdue": max(0, (now - loan.due_date).days) if loan.remaining_amount > 0 else 0
                }
                for loan in loans[:20]
            ],
            "overdue_loans": [
                {
                    "loan_number": loan.loan_number,
                    "customer_name": loan.customer_name,
                    "remaining_amount": float(loan.remaining_amount),
                    "due_date": loan.due_date.isoformat(),
                    "days_overdue": (now - loan.due_date).days
                }
                for loan in overdue_loans[:20]
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate loan report: {str(e)}"
        )


# ==================== PROFIT & LOSS REPORT ====================
@router.get("/profit-loss")
async def profit_loss_report(
    request: Request,
    from_date: Optional[date] = Query(None, description="Start date"),
    to_date: Optional[date] = Query(None, description="End date"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Generate Profit & Loss statement.
    
    - **from_date**: Start date (default: 30 days ago)
    - **to_date**: End date (default: today)
    - **branch_id**: Filter by branch (admin only)
    
    Returns comprehensive P&L statement with revenue, COGS, and profit calculations.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        if not to_date:
            to_date = date.today()
        if not from_date:
            from_date = to_date - timedelta(days=30)
        
        start_date = datetime.combine(from_date, datetime.min.time())
        end_date = datetime.combine(to_date, datetime.max.time())
        
        # Apply branch filter for all queries
        def apply_branch_filter(query, model, branch_field='branch_id'):
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
                    return query.filter(getattr(model, branch_field) == branch_id)
            elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
                return query.filter(getattr(model, branch_field) == current_user.branch_id)
            return query
        
        # === REVENUE ===
        # Sales revenue
        sales_query = db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.created_at.between(start_date, end_date)
        )
        sales_query = apply_branch_filter(sales_query, Sale)
        sales = sales_query.all()
        sales_revenue = sum(sale.total_amount for sale in sales)
        
        # Loan repayments revenue
        loan_payments_query = db.query(LoanPayment).join(Loan).filter(
            Loan.tenant_id == tenant_id,
            LoanPayment.payment_date.between(start_date, end_date)
        )
        loan_payments_query = apply_branch_filter(loan_payments_query, Loan)
        loan_payments = loan_payments_query.all()
        loan_repayment_revenue = sum(payment.amount for payment in loan_payments)
        
        total_revenue = sales_revenue + loan_repayment_revenue
        
        # === COST OF GOODS SOLD ===
        # Purchase order costs
        po_query = db.query(PurchaseOrder).filter(
            PurchaseOrder.tenant_id == tenant_id,
            PurchaseOrder.order_date.between(start_date, end_date),
            PurchaseOrder.status == PurchaseStatus.COMPLETED
        )
        po_query = apply_branch_filter(po_query, PurchaseOrder)
        purchase_orders = po_query.all()
        purchase_cost = sum(po.total_amount for po in purchase_orders)
        
        # Legacy purchase costs
        legacy_query = db.query(Purchase).filter(
            Purchase.tenant_id == tenant_id,
            Purchase.created_at.between(start_date, end_date)
        )
        legacy_query = apply_branch_filter(legacy_query, Purchase)
        legacy_purchases = legacy_query.all()
        legacy_purchase_cost = sum(p.total_amount for p in legacy_purchases)
        
        total_cogs = purchase_cost + legacy_purchase_cost
        
        # === PROFIT CALCULATIONS ===
        gross_profit = total_revenue - total_cogs
        gross_margin = (gross_profit / total_revenue * 100) if total_revenue > 0 else 0
        
        # === DAILY BREAKDOWN ===
        daily_breakdown = []
        current_date = from_date
        while current_date <= to_date:
            day_start = datetime.combine(current_date, datetime.min.time())
            day_end = datetime.combine(current_date, datetime.max.time())
            
            # Day sales
            day_sales_query = db.query(Sale).filter(
                Sale.tenant_id == tenant_id,
                Sale.created_at.between(day_start, day_end)
            )
            day_sales_query = apply_branch_filter(day_sales_query, Sale)
            day_sales = day_sales_query.all()
            day_sales_revenue = sum(s.total_amount for s in day_sales)
            
            # Day loan payments
            day_loans_query = db.query(LoanPayment).join(Loan).filter(
                Loan.tenant_id == tenant_id,
                LoanPayment.payment_date.between(day_start, day_end)
            )
            day_loans_query = apply_branch_filter(day_loans_query, Loan)
            day_loan_payments = day_loans_query.all()
            day_loan_revenue = sum(p.amount for p in day_loan_payments)
            
            # Day purchases
            day_purchases_query = db.query(Purchase).filter(
                Purchase.tenant_id == tenant_id,
                Purchase.created_at.between(day_start, day_end)
            )
            day_purchases_query = apply_branch_filter(day_purchases_query, Purchase)
            day_purchases = day_purchases_query.all()
            day_purchase_cost = sum(p.total_amount for p in day_purchases)
            
            daily_breakdown.append({
                "date": current_date.isoformat(),
                "sales_revenue": float(day_sales_revenue),
                "loan_repayments": float(day_loan_revenue),
                "total_revenue": float(day_sales_revenue + day_loan_revenue),
                "purchase_cost": float(day_purchase_cost),
                "gross_profit": float((day_sales_revenue + day_loan_revenue) - day_purchase_cost),
                "transactions_count": len(day_sales)
            })
            
            current_date += timedelta(days=1)
        
        return {
            "date_range": {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat()
            },
            "revenue": {
                "sales_revenue": float(sales_revenue),
                "loan_repayments": float(loan_repayment_revenue),
                "total_revenue": float(total_revenue)
            },
            "cost_of_goods_sold": {
                "purchase_orders": float(purchase_cost),
                "legacy_purchases": float(legacy_purchase_cost),
                "total_cogs": float(total_cogs)
            },
            "profit": {
                "gross_profit": float(gross_profit),
                "gross_margin_percentage": float(gross_margin)
            },
            "summary": {
                "total_sales_transactions": len(sales),
                "total_loan_payments": len(loan_payments),
                "total_purchases": len(purchase_orders) + len(legacy_purchases),
                "average_transaction_value": float(sales_revenue / len(sales)) if sales else 0,
                "average_loan_payment": float(loan_repayment_revenue / len(loan_payments)) if loan_payments else 0
            },
            "daily_breakdown": daily_breakdown
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate P&L report: {str(e)}"
        )


# ==================== INVENTORY VALUATION REPORT ====================
@router.get("/inventory-valuation")
async def inventory_valuation_report(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    low_stock_only: bool = Query(False, description="Show only low stock items"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get current inventory valuation based on purchase costs.
    
    - **branch_id**: Filter by branch (admin only)
    - **low_stock_only**: Show only items below reorder level
    
    Returns detailed inventory valuation including total value and low stock alerts.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        stock_query = db.query(Stock).join(Product).filter(Product.tenant_id == tenant_id)
        
        # Apply branch filter
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
                stock_query = stock_query.filter(Stock.branch_id == branch_id)
        else:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            stock_query = stock_query.filter(Stock.branch_id == current_user.branch_id)
        
        stock_items = stock_query.all()
        
        total_value = Decimal(0)
        items_detail = []
        
        for stock in stock_items:
            # Use latest purchase cost or product cost
            latest_purchase = db.query(PurchaseItem).join(Purchase).filter(
                Purchase.tenant_id == tenant_id,
                PurchaseItem.product_id == stock.product_id
            ).order_by(Purchase.created_at.desc()).first()
            
            unit_cost = latest_purchase.unit_cost if latest_purchase else stock.product.cost
            item_value = stock.quantity * unit_cost
            total_value += item_value
            
            is_low_stock = stock.quantity <= stock.reorder_level
            
            if not low_stock_only or is_low_stock:
                items_detail.append({
                    "product_id": stock.product_id,
                    "product_name": stock.product.name,
                    "sku": stock.product.sku,
                    "quantity": float(stock.quantity),
                    "unit_cost": float(unit_cost),
                    "total_value": float(item_value),
                    "reorder_level": float(stock.reorder_level),
                    "status": "Low Stock" if is_low_stock else "OK",
                    "has_expiry": stock.product.has_expiry,
                    "track_batch": stock.product.track_batch
                })
        
        return {
            "total_inventory_value": float(total_value),
            "total_products_count": len(stock_items),
            "low_stock_items_count": len([i for i in items_detail if i["status"] == "Low Stock"]),
            "low_stock_items": [i for i in items_detail if i["status"] == "Low Stock"],
            "items": items_detail
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate inventory valuation: {str(e)}"
        )


# ==================== DASHBOARD SUMMARY ====================
@router.get("/dashboard-summary")
async def dashboard_summary(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get quick dashboard summary for today.
    
    - **branch_id**: Filter by branch (admin only)
    
    Returns real-time dashboard metrics including today's sales, active loans, and alerts.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        today_start = datetime.combine(date.today(), datetime.min.time())
        today_end = datetime.combine(date.today(), datetime.max.time())
        
        # Helper for branch filtering
        def apply_filter(query, model):
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
                    return query.filter(getattr(model, 'branch_id') == branch_id)
            elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
                return query.filter(getattr(model, 'branch_id') == current_user.branch_id)
            return query
        
        # Today's sales
        sales_query = db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.created_at.between(today_start, today_end)
        )
        sales_query = apply_filter(sales_query, Sale)
        today_sales = sales_query.all()
        today_sales_revenue = sum(s.total_amount for s in today_sales)
        today_sales_count = len(today_sales)
        
        # Today's loan repayments
        loan_payments_query = db.query(LoanPayment).join(Loan).filter(
            Loan.tenant_id == tenant_id,
            LoanPayment.payment_date.between(today_start, today_end)
        )
        loan_payments_query = apply_filter(loan_payments_query, Loan)
        today_loan_payments = loan_payments_query.all()
        today_loan_repayments = sum(p.amount for p in today_loan_payments)
        
        # Today's purchases
        purchases_query = db.query(Purchase).filter(
            Purchase.tenant_id == tenant_id,
            Purchase.created_at.between(today_start, today_end)
        )
        purchases_query = apply_filter(purchases_query, Purchase)
        today_purchases = purchases_query.all()
        today_purchase_cost = sum(p.total_amount for p in today_purchases)
        
        # Active loans
        active_loans_query = db.query(Loan).filter(
            Loan.tenant_id == tenant_id,
            Loan.status.in_([LoanStatus.ACTIVE, LoanStatus.PARTIALLY_PAID])
        )
        active_loans_query = apply_filter(active_loans_query, Loan)
        active_loans = active_loans_query.all()
        
        # Overdue loans
        now = datetime.now()
        overdue_loans_query = db.query(Loan).filter(
            Loan.tenant_id == tenant_id,
            Loan.due_date < now,
            Loan.remaining_amount > 0,
            Loan.status != LoanStatus.SETTLED
        )
        overdue_loans_query = apply_filter(overdue_loans_query, Loan)
        overdue_loans = overdue_loans_query.all()
        
        # Low stock items
        stock_query = db.query(Stock).join(Product).filter(
            Product.tenant_id == tenant_id,
            Stock.quantity <= Stock.reorder_level
        )
        stock_query = apply_filter(stock_query, Stock)
        low_stock_items = stock_query.count()
        
        # Pending purchase orders
        pending_pos_query = db.query(PurchaseOrder).filter(
            PurchaseOrder.tenant_id == tenant_id,
            PurchaseOrder.status.in_([PurchaseStatus.PENDING, PurchaseStatus.PARTIALLY_RECEIVED])
        )
        pending_pos_query = apply_filter(pending_pos_query, PurchaseOrder)
        pending_purchase_orders = pending_pos_query.count()
        
        return {
            "today": {
                "sales_revenue": float(today_sales_revenue),
                "sales_count": today_sales_count,
                "loan_repayments": float(today_loan_repayments),
                "purchase_cost": float(today_purchase_cost),
                "total_income": float(today_sales_revenue + today_loan_repayments),
                "net_cash_flow": float((today_sales_revenue + today_loan_repayments) - today_purchase_cost),
                "average_transaction": float(today_sales_revenue / today_sales_count) if today_sales_count > 0 else 0
            },
            "current_status": {
                "active_loans_count": len(active_loans),
                "active_loans_value": float(sum(loan.remaining_amount for loan in active_loans)),
                "overdue_loans_count": len(overdue_loans),
                "overdue_loans_value": float(sum(loan.remaining_amount for loan in overdue_loans)),
                "low_stock_items_count": low_stock_items,
                "pending_purchase_orders": pending_purchase_orders
            },
            "quick_actions": [
                "Process pending purchase orders" if pending_purchase_orders > 0 else None,
                "Review overdue loans" if len(overdue_loans) > 0 else None,
                "Reorder low stock items" if low_stock_items > 0 else None,
                "Generate end-of-day report"
            ],
            "alerts": {
                "overdue_loans": len(overdue_loans),
                "low_stock": low_stock_items,
                "pending_orders": pending_purchase_orders
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate dashboard summary: {str(e)}"
        )


# ==================== FINANCIAL SUMMARY ====================
@router.get("/financial-summary")
async def get_financial_summary(
    request: Request,
    from_date: Optional[date] = Query(None, description="Start date"),
    to_date: Optional[date] = Query(None, description="End date"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get financial summary for the period.
    
    - **from_date**: Start date (default: 30 days ago)
    - **to_date**: End date (default: today)
    - **branch_id**: Filter by branch (admin only)
    
    Returns key financial metrics including revenue, expenses, and profit.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        if not to_date:
            to_date = date.today()
        if not from_date:
            from_date = to_date - timedelta(days=30)
        
        start_date = datetime.combine(from_date, datetime.min.time())
        end_date = datetime.combine(to_date, datetime.max.time())
        
        def apply_filter(query, model, date_field='created_at'):
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
                    return query.filter(getattr(model, 'branch_id') == branch_id).filter(date_field.between(start_date, end_date))
            elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
                return query.filter(getattr(model, 'branch_id') == current_user.branch_id).filter(date_field.between(start_date, end_date))
            return query.filter(date_field.between(start_date, end_date))
        
        # Sales revenue
        sales_query = apply_filter(db.query(Sale).filter(Sale.tenant_id == tenant_id), Sale, Sale.created_at)
        sales = sales_query.all()
        total_revenue = sum(s.total_amount for s in sales)
        
        # Purchase expenses
        purchase_query = apply_filter(db.query(Purchase).filter(Purchase.tenant_id == tenant_id), Purchase, Purchase.created_at)
        purchases = purchase_query.all()
        total_expenses = sum(p.total_amount for p in purchases)
        
        # Purchase orders
        po_query = apply_filter(db.query(PurchaseOrder).filter(
            PurchaseOrder.tenant_id == tenant_id,
            PurchaseOrder.status == PurchaseStatus.COMPLETED
        ), PurchaseOrder, PurchaseOrder.order_date)
        purchase_orders = po_query.all()
        total_expenses += sum(po.total_amount for po in purchase_orders)
        
        # Loan repayments
        loan_payment_query = db.query(LoanPayment).join(Loan).filter(
            Loan.tenant_id == tenant_id,
            LoanPayment.payment_date.between(start_date, end_date)
        )
        
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
            if branch_id:
                loan_payment_query = loan_payment_query.filter(Loan.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            loan_payment_query = loan_payment_query.filter(Loan.branch_id == current_user.branch_id)
        
        loan_payments = loan_payment_query.all()
        loan_repayments = sum(p.amount for p in loan_payments)
        
        # Outstanding loans
        outstanding_query = db.query(func.sum(Loan.remaining_amount)).filter(
            Loan.tenant_id == tenant_id,
            Loan.remaining_amount > 0,
            Loan.status != LoanStatus.SETTLED
        )
        
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
            if branch_id:
                outstanding_query = outstanding_query.filter(Loan.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            outstanding_query = outstanding_query.filter(Loan.branch_id == current_user.branch_id)
        
        outstanding_loans = outstanding_query.scalar() or Decimal(0)
        
        net_profit = total_revenue - total_expenses
        profit_margin = (net_profit / total_revenue * 100) if total_revenue > 0 else 0
        
        # Calculate previous period for trend
        period_days = (to_date - from_date).days
        prev_from_date = from_date - timedelta(days=period_days)
        prev_to_date = to_date - timedelta(days=period_days)
        
        prev_start = datetime.combine(prev_from_date, datetime.min.time())
        prev_end = datetime.combine(prev_to_date, datetime.max.time())
        
        prev_sales = apply_filter(db.query(Sale).filter(Sale.tenant_id == tenant_id), Sale, Sale.created_at).filter(
            Sale.created_at.between(prev_start, prev_end)
        ).all()
        prev_revenue = sum(s.total_amount for s in prev_sales)
        
        revenue_trend = ((total_revenue - prev_revenue) / prev_revenue * 100) if prev_revenue > 0 else 0
        
        return {
            "period": {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "days": period_days
            },
            "total_revenue": float(total_revenue),
            "total_expenses": float(total_expenses),
            "net_profit": float(net_profit),
            "loan_repayments": float(loan_repayments),
            "outstanding_loans": float(outstanding_loans),
            "profit_margin": round(profit_margin, 2),
            "revenue_trend": round(float(revenue_trend), 2),
            "transaction_counts": {
                "sales": len(sales),
                "purchases": len(purchases) + len(purchase_orders),
                "loan_payments": len(loan_payments)
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate financial summary: {str(e)}"
        )


# ==================== DAILY REVENUE ====================
@router.get("/daily-revenue")
async def get_daily_revenue(
    request: Request,
    days: int = Query(7, description="Number of days to show", ge=1, le=90),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get daily revenue for the specified number of days.
    
    - **days**: Number of days to show (default: 7, max: 90)
    - **branch_id**: Filter by branch (admin only)
    
    Returns daily revenue breakdown.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        end_date = date.today()
        start_date = end_date - timedelta(days=days - 1)
        
        results = []
        current = start_date
        
        while current <= end_date:
            day_start = datetime.combine(current, datetime.min.time())
            day_end = datetime.combine(current, datetime.max.time())
            
            revenue_query = db.query(func.sum(Sale.total_amount)).filter(
                Sale.tenant_id == tenant_id,
                Sale.created_at.between(day_start, day_end)
            )
            
            # Apply branch filter
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
                    revenue_query = revenue_query.filter(Sale.branch_id == branch_id)
            elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
                revenue_query = revenue_query.filter(Sale.branch_id == current_user.branch_id)
            
            revenue = revenue_query.scalar() or Decimal(0)
            
            # Also get loan repayments for the day
            loan_query = db.query(func.sum(LoanPayment.amount)).join(Loan).filter(
                Loan.tenant_id == tenant_id,
                LoanPayment.payment_date.between(day_start, day_end)
            )
            
            if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
                if branch_id:
                    loan_query = loan_query.filter(Loan.branch_id == branch_id)
            elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
                loan_query = loan_query.filter(Loan.branch_id == current_user.branch_id)
            
            loan_revenue = loan_query.scalar() or Decimal(0)
            
            results.append({
                "date": current.isoformat(),
                "day_name": current.strftime("%A"),
                "sales_revenue": float(revenue),
                "loan_repayments": float(loan_revenue),
                "total_revenue": float(revenue + loan_revenue)
            })
            current += timedelta(days=1)
        
        return {
            "days": days,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily_breakdown": results,
            "total_revenue": float(sum(r["total_revenue"] for r in results))
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate daily revenue: {str(e)}"
        )


# ==================== TOP PRODUCTS ====================
@router.get("/top-products")
async def get_top_products(
    request: Request,
    from_date: Optional[date] = Query(None, description="Start date"),
    to_date: Optional[date] = Query(None, description="End date"),
    limit: int = Query(10, description="Number of products to return", ge=1, le=50),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    sort_by: str = Query("quantity", description="Sort by: quantity, revenue, profit", pattern="^(quantity|revenue|profit)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get top selling products.
    
    - **from_date**: Start date (default: 30 days ago)
    - **to_date**: End date (default: today)
    - **limit**: Number of products to return
    - **branch_id**: Filter by branch (admin only)
    - **sort_by**: Sort by quantity, revenue, or profit
    
    Returns top performing products based on sales.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        if not to_date:
            to_date = date.today()
        if not from_date:
            from_date = to_date - timedelta(days=30)
        
        start_date = datetime.combine(from_date, datetime.min.time())
        end_date = datetime.combine(to_date, datetime.max.time())
        
        query = db.query(
            Product.id,
            Product.name,
            Product.sku,
            Product.price,
            Product.cost,
            func.sum(SaleItem.quantity).label('total_quantity'),
            func.sum(SaleItem.total).label('total_revenue'),
            func.sum(SaleItem.cost).label('total_cost')
        ).join(
            SaleItem, Product.id == SaleItem.product_id
        ).join(
            Sale, SaleItem.sale_id == Sale.id
        ).filter(
            Product.tenant_id == tenant_id,
            Sale.tenant_id == tenant_id,
            Sale.created_at.between(start_date, end_date)
        )
        
        # Apply branch filter
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
                query = query.filter(Sale.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            query = query.filter(Sale.branch_id == current_user.branch_id)
        
        # Apply sorting
        if sort_by == "quantity":
            query = query.order_by(func.sum(SaleItem.quantity).desc())
        elif sort_by == "revenue":
            query = query.order_by(func.sum(SaleItem.total).desc())
        elif sort_by == "profit":
            query = query.order_by((func.sum(SaleItem.total) - func.sum(SaleItem.cost)).desc())
        
        top_products = query.group_by(Product.id).limit(limit).all()
        
        return [
            {
                "id": p.id,
                "name": p.name,
                "sku": p.sku,
                "price": float(p.price),
                "quantity_sold": int(p.total_quantity),
                "revenue": float(p.total_revenue),
                "cost": float(p.total_cost) if p.total_cost else 0,
                "profit": float(p.total_revenue - (p.total_cost or 0)),
                "profit_margin": float(((p.total_revenue - (p.total_cost or 0)) / p.total_revenue * 100) if p.total_revenue > 0 else 0)
            }
            for p in top_products
        ]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate top products: {str(e)}"
        )