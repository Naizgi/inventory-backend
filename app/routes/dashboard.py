from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from datetime import datetime, timedelta, date
from decimal import Decimal
from typing import Optional, List, Dict, Any
from app.database import get_db
from app.models import Product, Stock, Sale, Alert, Branch, User, Loan, LoanPayment, PurchaseOrder, SaleItem, StockMovement, UserRole
from app.utils.auth import get_current_user, verify_branch_access, get_user_branch_id, get_current_tenant
from app.services import AlertService, SettingsService

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# ==================== MAIN DASHBOARD ====================

@router.get("/")
async def get_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get main dashboard statistics.
    
    Returns key metrics for the dashboard including:
    - Product counts
    - Today's sales
    - Low stock alerts
    - Active alerts
    - Branch information
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Determine branch filter
        branch_id = None
        if current_user.role not in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]:
            branch_id = current_user.branch_id
            if not branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
        
        # Get total products count within tenant
        products_count = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.active == True
        ).count()
        
        # Get branches count within tenant
        branches_count = db.query(Branch).filter(Branch.tenant_id == tenant_id).count()
        
        # Get low stock products
        low_stock_query = db.query(Stock).join(Product).filter(
            Product.tenant_id == tenant_id,
            Stock.quantity <= Stock.reorder_level,
            Stock.quantity > 0
        )
        if branch_id:
            low_stock_query = low_stock_query.filter(Stock.branch_id == branch_id)
        
        low_stock_products = []
        for stock in low_stock_query.limit(10).all():
            product = stock.product
            branch = db.query(Branch).filter(
                Branch.id == stock.branch_id,
                Branch.tenant_id == tenant_id
            ).first()
            low_stock_products.append({
                "product_id": product.id,
                "product_name": product.name,
                "sku": product.sku,
                "current_stock": float(stock.quantity),
                "reorder_level": float(stock.reorder_level),
                "shortage": float(stock.reorder_level - stock.quantity),
                "branch_id": stock.branch_id,
                "branch_name": branch.name if branch else "Unknown"
            })
        
        # Get out of stock products
        out_of_stock_query = db.query(Stock).join(Product).filter(
            Product.tenant_id == tenant_id,
            Stock.quantity == 0
        )
        if branch_id:
            out_of_stock_query = out_of_stock_query.filter(Stock.branch_id == branch_id)
        out_of_stock_count = out_of_stock_query.count()
        
        # Get today's sales
        today = date.today()
        today_start = datetime.combine(today, datetime.min.time())
        today_end = datetime.combine(today, datetime.max.time())
        
        sales_query = db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.created_at >= today_start,
            Sale.created_at <= today_end
        )
        if branch_id:
            sales_query = sales_query.filter(Sale.branch_id == branch_id)
        
        today_sales = sales_query.all()
        today_revenue = sum(sale.total_amount for sale in today_sales)
        today_profit = sum(sale.total_amount - sale.total_cost for sale in today_sales)
        
        # Get today's loan repayments
        loan_payments_query = db.query(LoanPayment).join(Loan).filter(
            Loan.tenant_id == tenant_id,
            LoanPayment.payment_date >= today_start,
            LoanPayment.payment_date <= today_end
        )
        if branch_id:
            loan_payments_query = loan_payments_query.filter(Loan.branch_id == branch_id)
        
        today_loan_payments = loan_payments_query.all()
        today_loan_repayments = sum(payment.amount for payment in today_loan_payments)
        
        # Get active alerts count
        alerts_query = db.query(Alert).filter(
            Alert.tenant_id == tenant_id,
            Alert.resolved == False
        )
        if branch_id:
            alerts_query = alerts_query.filter(Alert.branch_id == branch_id)
        alerts_count = alerts_query.count()
        
        # Get recent alerts
        recent_alerts = []
        for alert in alerts_query.order_by(Alert.created_at.desc()).limit(5).all():
            product = alert.product
            branch = db.query(Branch).filter(
                Branch.id == alert.branch_id,
                Branch.tenant_id == tenant_id
            ).first()
            recent_alerts.append({
                "id": alert.id,
                "alert_type": alert.alert_type,
                "message": alert.message,
                "product_name": product.name if product else "Unknown",
                "branch_name": branch.name if branch else "Unknown",
                "created_at": alert.created_at.isoformat()
            })
        
        # Get active loans summary
        loans_query = db.query(Loan).filter(
            Loan.tenant_id == tenant_id,
            Loan.remaining_amount > 0,
            Loan.status != 'settled'
        )
        if branch_id:
            loans_query = loans_query.filter(Loan.branch_id == branch_id)
        
        active_loans_count = loans_query.count()
        active_loans_value = loans_query.with_entities(
            func.sum(Loan.remaining_amount)
        ).scalar() or Decimal(0)
        
        # Get overdue loans count
        now = datetime.now()
        overdue_loans_query = db.query(Loan).filter(
            Loan.tenant_id == tenant_id,
            Loan.due_date < now,
            Loan.remaining_amount > 0,
            Loan.status != 'settled'
        )
        if branch_id:
            overdue_loans_query = overdue_loans_query.filter(Loan.branch_id == branch_id)
        overdue_loans_count = overdue_loans_query.count()
        
        # Get pending purchase orders
        po_query = db.query(PurchaseOrder).filter(
            PurchaseOrder.tenant_id == tenant_id,
            PurchaseOrder.status.in_(['pending', 'partially_received'])
        )
        if branch_id:
            po_query = po_query.filter(PurchaseOrder.branch_id == branch_id)
        pending_pos_count = po_query.count()
        
        return {
            "summary": {
                "total_products": products_count,
                "total_branches": branches_count,
                "low_stock_alerts": len(low_stock_products),
                "out_of_stock_items": out_of_stock_count,
                "active_alerts": alerts_count,
                "active_loans": active_loans_count,
                "active_loans_value": float(active_loans_value),
                "overdue_loans": overdue_loans_count,
                "pending_purchase_orders": pending_pos_count
            },
            "today": {
                "sales_count": len(today_sales),
                "sales_revenue": float(today_revenue),
                "sales_profit": float(today_profit),
                "profit_margin": float((today_profit / today_revenue * 100) if today_revenue > 0 else 0),
                "loan_repayments": float(today_loan_repayments),
                "total_income": float(today_revenue + today_loan_repayments)
            },
            "low_stock_products": low_stock_products,
            "recent_alerts": recent_alerts
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve dashboard data: {str(e)}"
        )


# ==================== QUICK STATS ====================

@router.get("/quick-stats")
async def get_quick_stats(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get quick statistics for dashboard widgets.
    
    Returns lightweight stats for dashboard cards.
    """
    try:
        tenant_id = get_current_tenant(request)
        branch_id = get_user_branch_id(current_user)
        
        # Today's sales
        today = date.today()
        today_start = datetime.combine(today, datetime.min.time())
        today_end = datetime.combine(today, datetime.max.time())
        
        sales_query = db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.created_at >= today_start,
            Sale.created_at <= today_end
        )
        if branch_id:
            sales_query = sales_query.filter(Sale.branch_id == branch_id)
        
        today_sales_count = sales_query.count()
        today_revenue = sales_query.with_entities(func.sum(Sale.total_amount)).scalar() or Decimal(0)
        
        # Low stock count
        stock_query = db.query(Stock).join(Product).filter(
            Product.tenant_id == tenant_id,
            Stock.quantity <= Stock.reorder_level,
            Stock.quantity > 0
        )
        if branch_id:
            stock_query = stock_query.filter(Stock.branch_id == branch_id)
        low_stock_count = stock_query.count()
        
        # Out of stock count
        out_of_stock_query = db.query(Stock).join(Product).filter(
            Product.tenant_id == tenant_id,
            Stock.quantity == 0
        )
        if branch_id:
            out_of_stock_query = out_of_stock_query.filter(Stock.branch_id == branch_id)
        out_of_stock_count = out_of_stock_query.count()
        
        # Active alerts
        alert_query = db.query(Alert).filter(
            Alert.tenant_id == tenant_id,
            Alert.resolved == False
        )
        if branch_id:
            alert_query = alert_query.filter(Alert.branch_id == branch_id)
        active_alerts = alert_query.count()
        
        # Active loans
        loan_query = db.query(Loan).filter(
            Loan.tenant_id == tenant_id,
            Loan.remaining_amount > 0,
            Loan.status != 'settled'
        )
        if branch_id:
            loan_query = loan_query.filter(Loan.branch_id == branch_id)
        active_loans = loan_query.count()
        
        return {
            "today_sales": {
                "count": today_sales_count,
                "revenue": float(today_revenue)
            },
            "inventory": {
                "low_stock": low_stock_count,
                "out_of_stock": out_of_stock_count
            },
            "alerts": active_alerts,
            "active_loans": active_loans
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve quick stats: {str(e)}"
        )


# ==================== SALES CHARTS ====================

@router.get("/sales-chart")
async def get_sales_chart_data(
    request: Request,
    days: int = Query(7, description="Number of days to show", ge=1, le=90),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get sales chart data for dashboard charts.
    
    Returns daily sales data for the specified number of days.
    """
    try:
        tenant_id = get_current_tenant(request)
        
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
                if not verify_branch_access(current_user, branch_id):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Not authorized to view this branch"
                    )
        else:
            branch_id = current_user.branch_id
            if not branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
        
        end_date = date.today()
        start_date = end_date - timedelta(days=days - 1)
        
        sales_data = []
        
        current_date = start_date
        while current_date <= end_date:
            day_start = datetime.combine(current_date, datetime.min.time())
            day_end = datetime.combine(current_date, datetime.max.time())
            
            # Sales for the day
            sales_query = db.query(
                func.count(Sale.id).label('count'),
                func.sum(Sale.total_amount).label('revenue'),
                func.sum(Sale.total_amount - Sale.total_cost).label('profit')
            ).filter(
                Sale.tenant_id == tenant_id,
                Sale.created_at.between(day_start, day_end)
            )
            if branch_id:
                sales_query = sales_query.filter(Sale.branch_id == branch_id)
            
            sales_result = sales_query.first()
            
            # Loan repayments for the day
            loan_query = db.query(func.sum(LoanPayment.amount)).filter(
                LoanPayment.payment_date.between(day_start, day_end)
            ).join(Loan).filter(Loan.tenant_id == tenant_id)
            if branch_id:
                loan_query = loan_query.filter(Loan.branch_id == branch_id)
            
            loan_repayment = loan_query.scalar() or Decimal(0)
            
            sales_data.append({
                "date": current_date.isoformat(),
                "day_name": current_date.strftime("%A"),
                "sales_count": sales_result.count or 0,
                "revenue": float(sales_result.revenue or 0),
                "profit": float(sales_result.profit or 0),
                "loan_repayments": float(loan_repayment)
            })
            
            current_date += timedelta(days=1)
        
        # Calculate totals
        total_revenue = sum(d["revenue"] for d in sales_data)
        total_profit = sum(d["profit"] for d in sales_data)
        total_loan_repayments = sum(d["loan_repayments"] for d in sales_data)
        
        return {
            "days": days,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "data": sales_data,
            "totals": {
                "revenue": total_revenue,
                "profit": total_profit,
                "loan_repayments": total_loan_repayments,
                "combined_income": total_revenue + total_loan_repayments
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve sales chart data: {str(e)}"
        )


# ==================== TOP PRODUCTS ====================

@router.get("/top-products")
async def get_top_products_dashboard(
    request: Request,
    days: int = Query(30, description="Number of days to analyze", ge=1, le=365),
    limit: int = Query(5, description="Number of products to return", ge=1, le=20),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get top selling products for dashboard.
    
    Returns the best-selling products for the specified period.
    """
    try:
        tenant_id = get_current_tenant(request)
        
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
                if not verify_branch_access(current_user, branch_id):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Not authorized to view this branch"
                    )
        else:
            branch_id = current_user.branch_id
            if not branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
        
        start_date = datetime.now() - timedelta(days=days)
        
        query = db.query(
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
            Product.tenant_id == tenant_id,
            Sale.tenant_id == tenant_id,
            Sale.created_at >= start_date
        )
        
        if branch_id:
            query = query.filter(Sale.branch_id == branch_id)
        
        top_products = query.group_by(Product.id).order_by(
            func.sum(SaleItem.quantity).desc()
        ).limit(limit).all()
        
        return [
            {
                "product_id": p.id,
                "product_name": p.name,
                "sku": p.sku,
                "quantity_sold": int(p.total_quantity),
                "revenue": float(p.total_revenue),
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
            detail=f"Failed to retrieve top products: {str(e)}"
        )


# ==================== RECENT ACTIVITY ====================

@router.get("/recent-activity")
async def get_recent_activity(
    request: Request,
    limit: int = Query(10, description="Number of activities to return", ge=1, le=50),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get recent system activity.
    
    Returns recent sales, stock movements, and loan payments.
    """
    try:
        tenant_id = get_current_tenant(request)
        
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
                if not verify_branch_access(current_user, branch_id):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Not authorized to view this branch"
                    )
        else:
            branch_id = current_user.branch_id
            if not branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
        
        activities = []
        
        # Recent sales
        sales_query = db.query(Sale).filter(Sale.tenant_id == tenant_id).order_by(Sale.created_at.desc()).limit(limit)
        if branch_id:
            sales_query = sales_query.filter(Sale.branch_id == branch_id)
        
        for sale in sales_query.all():
            user = db.query(User).filter(User.id == sale.user_id).first()
            activities.append({
                "id": sale.id,
                "type": "sale",
                "description": f"Sale #{sale.id} - {sale.customer_name or 'Walk-in Customer'}",
                "amount": float(sale.total_amount),
                "user": user.name if user else "Unknown",
                "timestamp": sale.created_at.isoformat()
            })
        
        # Recent stock movements
        movement_query = db.query(StockMovement).join(Product).filter(
            Product.tenant_id == tenant_id
        ).order_by(StockMovement.created_at.desc()).limit(limit)
        if branch_id:
            movement_query = movement_query.filter(StockMovement.branch_id == branch_id)
        
        for movement in movement_query.all():
            product = movement.product
            user = db.query(User).filter(User.id == movement.user_id).first()
            activities.append({
                "id": movement.id,
                "type": "stock",
                "description": f"Stock {movement.movement_type}: {product.name if product else 'Unknown'} - {abs(float(movement.change_qty))} units",
                "amount": float(movement.change_qty),
                "user": user.name if user else "Unknown",
                "timestamp": movement.created_at.isoformat()
            })
        
        # Recent loan payments
        payment_query = db.query(LoanPayment).join(Loan).filter(
            Loan.tenant_id == tenant_id
        ).order_by(LoanPayment.created_at.desc()).limit(limit)
        if branch_id:
            payment_query = payment_query.filter(Loan.branch_id == branch_id)
        
        for payment in payment_query.all():
            loan = payment.loan
            user = db.query(User).filter(User.id == payment.recorded_by).first()
            activities.append({
                "id": payment.id,
                "type": "loan_payment",
                "description": f"Loan payment for {loan.customer_name}",
                "amount": float(payment.amount),
                "user": user.name if user else "Unknown",
                "timestamp": payment.created_at.isoformat()
            })
        
        # Sort by timestamp and limit
        activities.sort(key=lambda x: x["timestamp"], reverse=True)
        
        return activities[:limit]
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve recent activity: {str(e)}"
        )


# ==================== BRANCH COMPARISON (Admin only) ====================

@router.get("/branch-comparison")
async def get_branch_comparison(
    request: Request,
    metric: str = Query("revenue", description="Metric to compare (revenue, profit, sales)"),
    days: int = Query(30, description="Number of days to analyze", ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get branch performance comparison (Super Admin or Tenant Admin only).
    
    Compares performance metrics across all branches within the tenant.
    """
    try:
        if current_user.role not in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Branch comparison is only available for administrators"
            )
        
        tenant_id = get_current_tenant(request)
        start_date = datetime.now() - timedelta(days=days)
        branches = db.query(Branch).filter(Branch.tenant_id == tenant_id).all()
        
        comparison_data = []
        for branch in branches:
            sales_query = db.query(Sale).filter(
                Sale.tenant_id == tenant_id,
                Sale.branch_id == branch.id,
                Sale.created_at >= start_date
            )
            
            total_sales = sales_query.count()
            total_revenue = sales_query.with_entities(func.sum(Sale.total_amount)).scalar() or Decimal(0)
            total_profit = sales_query.with_entities(func.sum(Sale.total_amount - Sale.total_cost)).scalar() or Decimal(0)
            
            # Loan repayments
            loan_payments = db.query(func.sum(LoanPayment.amount)).filter(
                LoanPayment.payment_date >= start_date
            ).join(Loan).filter(
                Loan.tenant_id == tenant_id,
                Loan.branch_id == branch.id
            ).scalar() or Decimal(0)
            
            comparison_data.append({
                "branch_id": branch.id,
                "branch_name": branch.name,
                "business_type": branch.business_type,
                "total_sales": total_sales,
                "total_revenue": float(total_revenue),
                "total_profit": float(total_profit),
                "loan_repayments": float(loan_payments),
                "combined_income": float(total_revenue + loan_payments),
                "profit_margin": float((total_profit / total_revenue * 100) if total_revenue > 0 else 0)
            })
        
        # Sort by selected metric
        if metric == "revenue":
            comparison_data.sort(key=lambda x: x["total_revenue"], reverse=True)
        elif metric == "profit":
            comparison_data.sort(key=lambda x: x["total_profit"], reverse=True)
        elif metric == "sales":
            comparison_data.sort(key=lambda x: x["total_sales"], reverse=True)
        elif metric == "combined":
            comparison_data.sort(key=lambda x: x["combined_income"], reverse=True)
        
        return {
            "metric": metric,
            "days": days,
            "start_date": start_date.isoformat(),
            "end_date": datetime.now().isoformat(),
            "branches": comparison_data
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve branch comparison: {str(e)}"
        )


# ==================== HELPER FUNCTIONS ====================

def get_dashboard_widgets(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> Dict[str, Any]:
    """Get all dashboard widget data within a tenant"""
    return {
        "sales_today": get_today_sales_summary(db, tenant_id, branch_id),
        "inventory_status": get_inventory_summary(db, tenant_id, branch_id),
        "alert_summary": get_alert_summary(db, tenant_id, branch_id),
        "loan_summary": get_loan_summary(db, tenant_id, branch_id)
    }


def get_today_sales_summary(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> Dict[str, Any]:
    """Get today's sales summary within a tenant"""
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = datetime.combine(date.today(), datetime.max.time())
    
    query = db.query(Sale).filter(
        Sale.tenant_id == tenant_id,
        Sale.created_at.between(today_start, today_end)
    )
    if branch_id:
        query = query.filter(Sale.branch_id == branch_id)
    
    sales = query.all()
    return {
        "count": len(sales),
        "revenue": float(sum(s.total_amount for s in sales)),
        "profit": float(sum(s.total_amount - s.total_cost for s in sales))
    }


def get_inventory_summary(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> Dict[str, Any]:
    """Get inventory summary within a tenant"""
    query = db.query(Stock).join(Product).filter(Product.tenant_id == tenant_id)
    if branch_id:
        query = query.filter(Stock.branch_id == branch_id)
    
    stocks = query.all()
    low_stock = [s for s in stocks if s.quantity <= s.reorder_level and s.quantity > 0]
    out_of_stock = [s for s in stocks if s.quantity == 0]
    
    return {
        "total_products": len(stocks),
        "low_stock": len(low_stock),
        "out_of_stock": len(out_of_stock)
    }


def get_alert_summary(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> Dict[str, Any]:
    """Get alert summary within a tenant"""
    query = db.query(Alert).filter(
        Alert.tenant_id == tenant_id,
        Alert.resolved == False
    )
    if branch_id:
        query = query.filter(Alert.branch_id == branch_id)
    
    alerts = query.all()
    return {
        "total": len(alerts),
        "low_stock": len([a for a in alerts if a.alert_type == "low_stock"]),
        "out_of_stock": len([a for a in alerts if a.alert_type == "out_of_stock"]),
        "expiry": len([a for a in alerts if a.alert_type == "expiry"])
    }


def get_loan_summary(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> Dict[str, Any]:
    """Get loan summary within a tenant"""
    query = db.query(Loan).filter(
        Loan.tenant_id == tenant_id,
        Loan.remaining_amount > 0,
        Loan.status != 'settled'
    )
    if branch_id:
        query = query.filter(Loan.branch_id == branch_id)
    
    loans = query.all()
    total_outstanding = sum(l.remaining_amount for l in loans)
    
    return {
        "active_loans": len(loans),
        "outstanding_amount": float(total_outstanding)
    }