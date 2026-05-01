from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import List, Optional
from datetime import datetime, date, timedelta
from decimal import Decimal
import traceback

from app.database import get_db
from app.models import User, Sale, SaleItem, Product, Stock, StockMovement, Branch, Batch, UserRole
from app.schemas import Sale as SaleSchema, SaleCreate, SaleItem as SaleItemSchema
from app.services import SaleService, ProductService, StockService, BatchService
from app.utils.auth import get_current_user, require_role, verify_branch_access, get_current_tenant

router = APIRouter(prefix="/sales", tags=["Sales"])


# ==================== SALE CREATION ====================

@router.post("/", response_model=SaleSchema, status_code=status.HTTP_201_CREATED)
async def create_sale(
    sale_data: SaleCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new sale transaction.
    
    - **branch_id**: Branch ID (optional, uses user's branch if not provided)
    - **customer_name**: Customer name (optional)
    - **customer_phone**: Customer phone number (optional)
    - **discount_amount**: Discount amount (optional)
    - **tax_amount**: Tax amount (optional)
    - **payment_method**: Payment method (cash, card, mixed)
    - **items**: List of items with product_id, quantity, unit_price, and optional batch_id
    
    Any authenticated user can create sales for their branch.
    """
    print(f"=== CREATE SALE ===")
    print(f"User: {current_user.id} - {current_user.name} - Role: {current_user.role}")
    
    try:
        tenant_id = get_current_tenant(request)
        
        # Determine branch
        branch_id = sale_data.branch_id or current_user.branch_id
        
        if not branch_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Branch ID is required"
            )
        
        # Check branch access
        if not verify_branch_access(current_user, branch_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to create sales for this branch"
            )
        
        # Check if branch exists in this tenant
        branch = db.query(Branch).filter(
            Branch.id == branch_id,
            Branch.tenant_id == tenant_id
        ).first()
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Branch with id {branch_id} not found in this tenant"
            )
        
        # Validate stock and get batch information
        for item in sale_data.items:
            product = ProductService.get_product(db, item.product_id, tenant_id)
            if not product:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Product with id {item.product_id} not found in this tenant"
                )
            
            # Check stock
            if item.batch_id:
                batch = BatchService.get_batch(db, item.batch_id, tenant_id)
                if not batch or batch.remaining_quantity < item.quantity:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Insufficient stock in batch for product {product.name}"
                    )
            else:
                stock = StockService.get_stock(db, branch_id, item.product_id, tenant_id)
                if not stock or stock.quantity < item.quantity:
                    available = float(stock.quantity) if stock else 0
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Insufficient stock for {product.name}. Available: {available}, Requested: {item.quantity}"
                    )
        
        # Create sale using service
        sale = SaleService.create_sale(db, sale_data, current_user.id, branch_id, tenant_id)
        
        print(f"Sale created successfully! ID: {sale.id}, Total: {float(sale.total_amount)}")
        
        return sale
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"Error creating sale: {str(e)}")
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create sale: {str(e)}"
        )


# ==================== SALE RETRIEVAL ====================

@router.get("/", response_model=List[SaleSchema])
async def get_sales(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    user_id: Optional[int] = Query(None, description="Filter by user ID"),
    customer_name: Optional[str] = Query(None, description="Filter by customer name"),
    start_date: Optional[date] = Query(None, description="Start date filter"),
    end_date: Optional[date] = Query(None, description="End date filter"),
    min_amount: Optional[float] = Query(None, description="Minimum sale amount"),
    max_amount: Optional[float] = Query(None, description="Maximum sale amount"),
    payment_method: Optional[str] = Query(None, description="Filter by payment method"),
    limit: int = Query(100, ge=1, le=500, description="Maximum records"),
    skip: int = Query(0, ge=0, description="Records to skip"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get sales with filters.
    
    Returns a list of sales matching the criteria.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(Sale).filter(Sale.tenant_id == tenant_id)
        
        # Apply branch filter based on role
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
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
                query = query.filter(Sale.branch_id == branch_id)
        else:
            # Non-admin users can only see their branch
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            query = query.filter(Sale.branch_id == current_user.branch_id)
            if branch_id and branch_id != current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to view sales from other branches"
                )
        
        # Apply filters
        if user_id:
            query = query.filter(Sale.user_id == user_id)
        
        if customer_name:
            query = query.filter(Sale.customer_name.ilike(f"%{customer_name}%"))
        
        if start_date:
            start_datetime = datetime.combine(start_date, datetime.min.time())
            query = query.filter(Sale.created_at >= start_datetime)
        
        if end_date:
            end_datetime = datetime.combine(end_date, datetime.max.time())
            query = query.filter(Sale.created_at <= end_datetime)
        
        if min_amount:
            query = query.filter(Sale.total_amount >= Decimal(str(min_amount)))
        
        if max_amount:
            query = query.filter(Sale.total_amount <= Decimal(str(max_amount)))
        
        if payment_method:
            query = query.filter(Sale.payment_method == payment_method)
        
        sales = query.order_by(Sale.created_at.desc()).offset(skip).limit(limit).all()
        
        # Load items for each sale
        result = []
        for sale in sales:
            items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
            
            # Get user name
            user = db.query(User).filter(User.id == sale.user_id).first()
            
            result.append({
                "id": sale.id,
                "tenant_id": sale.tenant_id,
                "branch_id": sale.branch_id,
                "user_id": sale.user_id,
                "user_name": user.name if user else "Unknown",
                "customer_name": sale.customer_name,
                "customer_phone": sale.customer_phone,
                "total_amount": float(sale.total_amount),
                "total_cost": float(sale.total_cost),
                "discount_amount": float(sale.discount_amount),
                "tax_amount": float(sale.tax_amount),
                "payment_method": sale.payment_method,
                "created_at": sale.created_at,
                "items": [
                    {
                        "id": item.id,
                        "sale_id": item.sale_id,
                        "product_id": item.product_id,
                        "product_name": item.product.name if item.product else "Unknown",
                        "batch_id": item.batch_id,
                        "quantity": float(item.quantity),
                        "unit_price": float(item.unit_price),
                        "total": float(item.total),
                        "cost": float(item.cost)
                    }
                    for item in items
                ]
            })
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve sales: {str(e)}"
        )


@router.get("/today", response_model=List[SaleSchema])
async def get_today_sales(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get today's sales.
    
    Returns all sales created today.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        today_start = datetime.combine(date.today(), datetime.min.time())
        today_end = datetime.combine(date.today(), datetime.max.time())
        
        query = db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.created_at.between(today_start, today_end)
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
        else:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            query = query.filter(Sale.branch_id == current_user.branch_id)
        
        sales = query.order_by(Sale.created_at.desc()).all()
        
        result = []
        for sale in sales:
            items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
            
            result.append({
                "id": sale.id,
                "tenant_id": sale.tenant_id,
                "branch_id": sale.branch_id,
                "user_id": sale.user_id,
                "customer_name": sale.customer_name,
                "customer_phone": sale.customer_phone,
                "total_amount": float(sale.total_amount),
                "total_cost": float(sale.total_cost),
                "discount_amount": float(sale.discount_amount),
                "tax_amount": float(sale.tax_amount),
                "payment_method": sale.payment_method,
                "created_at": sale.created_at,
                "items": [
                    {
                        "id": item.id,
                        "sale_id": item.sale_id,
                        "product_id": item.product_id,
                        "product_name": item.product.name if item.product else "Unknown",
                        "batch_id": item.batch_id,
                        "quantity": float(item.quantity),
                        "unit_price": float(item.unit_price),
                        "total": float(item.total),
                        "cost": float(item.cost)
                    }
                    for item in items
                ]
            })
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve today's sales: {str(e)}"
        )


@router.get("/{sale_id}", response_model=SaleSchema)
async def get_sale(
    sale_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a single sale by ID.
    
    - **sale_id**: The ID of the sale to retrieve
    """
    try:
        tenant_id = get_current_tenant(request)
        
        sale = db.query(Sale).filter(
            Sale.id == sale_id,
            Sale.tenant_id == tenant_id
        ).first()
        
        if not sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Sale with id {sale_id} not found in this tenant"
            )
        
        # Check permissions
        if not verify_branch_access(current_user, sale.branch_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this sale"
            )
        
        items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
        user = db.query(User).filter(User.id == sale.user_id).first()
        
        return {
            "id": sale.id,
            "tenant_id": sale.tenant_id,
            "branch_id": sale.branch_id,
            "user_id": sale.user_id,
            "user_name": user.name if user else "Unknown",
            "customer_name": sale.customer_name,
            "customer_phone": sale.customer_phone,
            "total_amount": float(sale.total_amount),
            "total_cost": float(sale.total_cost),
            "discount_amount": float(sale.discount_amount),
            "tax_amount": float(sale.tax_amount),
            "payment_method": sale.payment_method,
            "created_at": sale.created_at,
            "items": [
                {
                    "id": item.id,
                    "sale_id": item.sale_id,
                    "product_id": item.product_id,
                    "product_name": item.product.name if item.product else "Unknown",
                    "batch_id": item.batch_id,
                    "batch_number": item.batch.batch_number if item.batch else None,
                    "quantity": float(item.quantity),
                    "unit_price": float(item.unit_price),
                    "total": float(item.total),
                    "cost": float(item.cost)
                }
                for item in items
            ]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve sale: {str(e)}"
        )


# ==================== SALE STATISTICS ====================

@router.get("/stats/summary")
async def get_sale_statistics(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    days: int = Query(30, description="Number of days for statistics", ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get sale statistics (Admin/Manager only).
    
    Returns aggregated sale metrics for the specified period.
    """
    try:
        tenant_id = get_current_tenant(request)
        start_date = datetime.now() - timedelta(days=days)
        
        query = db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.created_at >= start_date
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
                if not verify_branch_access(current_user, branch_id):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Not authorized to view this branch"
                    )
                query = query.filter(Sale.branch_id == branch_id)
        else:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            query = query.filter(Sale.branch_id == current_user.branch_id)
        
        # Calculate statistics
        total_sales = query.count()
        total_revenue = query.with_entities(func.sum(Sale.total_amount)).scalar() or Decimal(0)
        total_profit = query.with_entities(func.sum(Sale.total_amount - Sale.total_cost)).scalar() or Decimal(0)
        average_sale_value = total_revenue / total_sales if total_sales > 0 else Decimal(0)
        
        # Sales by payment method
        payment_methods = {}
        for method in ["cash", "card", "mobile", "mixed"]:
            count = query.filter(Sale.payment_method == method).count()
            amount = query.filter(Sale.payment_method == method).with_entities(
                func.sum(Sale.total_amount)
            ).scalar() or Decimal(0)
            if count > 0:
                payment_methods[method] = {
                    "count": count,
                    "amount": float(amount)
                }
        
        # Daily breakdown
        daily_breakdown = []
        current_date = date.today() - timedelta(days=days - 1)
        for _ in range(days):
            day_start = datetime.combine(current_date, datetime.min.time())
            day_end = datetime.combine(current_date, datetime.max.time())
            
            day_query = query.filter(Sale.created_at.between(day_start, day_end))
            day_revenue = day_query.with_entities(func.sum(Sale.total_amount)).scalar() or Decimal(0)
            day_count = day_query.count()
            
            daily_breakdown.append({
                "date": current_date.isoformat(),
                "revenue": float(day_revenue),
                "count": day_count
            })
            
            current_date += timedelta(days=1)
        
        return {
            "period_days": days,
            "start_date": (datetime.now() - timedelta(days=days)).isoformat(),
            "end_date": datetime.now().isoformat(),
            "total_sales": total_sales,
            "total_revenue": float(total_revenue),
            "total_profit": float(total_profit),
            "profit_margin": float((total_profit / total_revenue * 100) if total_revenue > 0 else 0),
            "average_sale_value": float(average_sale_value),
            "payment_methods": payment_methods,
            "daily_breakdown": daily_breakdown
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve sale statistics: {str(e)}"
        )


@router.get("/stats/daily")
async def get_daily_sales_stats(
    request: Request,
    date: Optional[date] = Query(None, description="Date to get stats for (default: today)"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get daily sales statistics.
    
    Returns detailed statistics for a specific day.
    """
    try:
        tenant_id = get_current_tenant(request)
        target_date = date or date.today()
        day_start = datetime.combine(target_date, datetime.min.time())
        day_end = datetime.combine(target_date, datetime.max.time())
        
        query = db.query(Sale).filter(
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
                if not verify_branch_access(current_user, branch_id):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Not authorized to view this branch"
                    )
                query = query.filter(Sale.branch_id == branch_id)
        else:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            query = query.filter(Sale.branch_id == current_user.branch_id)
        
        sales = query.all()
        total_revenue = sum(s.total_amount for s in sales)
        total_profit = sum(s.total_amount - s.total_cost for s in sales)
        
        # Hourly breakdown
        hourly_breakdown = []
        for hour in range(24):
            hour_start = day_start.replace(hour=hour)
            hour_end = hour_start.replace(minute=59, second=59)
            hour_sales = [s for s in sales if hour_start <= s.created_at <= hour_end]
            hour_revenue = sum(s.total_amount for s in hour_sales)
            
            hourly_breakdown.append({
                "hour": hour,
                "revenue": float(hour_revenue),
                "count": len(hour_sales)
            })
        
        # Top selling products for the day
        top_products = db.query(
            Product.id,
            Product.name,
            func.sum(SaleItem.quantity).label('total_quantity'),
            func.sum(SaleItem.total).label('total_revenue')
        ).join(
            SaleItem, Product.id == SaleItem.product_id
        ).join(
            Sale, SaleItem.sale_id == Sale.id
        ).filter(
            Sale.tenant_id == tenant_id,
            Sale.created_at.between(day_start, day_end)
        )
        
        if branch_id:
            top_products = top_products.filter(Sale.branch_id == branch_id)
        elif current_user.role not in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value] and current_user.branch_id:
            top_products = top_products.filter(Sale.branch_id == current_user.branch_id)
        
        top_products = top_products.group_by(Product.id).order_by(
            func.sum(SaleItem.quantity).desc()
        ).limit(10).all()
        
        return {
            "date": target_date.isoformat(),
            "day_name": target_date.strftime("%A"),
            "total_sales": len(sales),
            "total_revenue": float(total_revenue),
            "total_profit": float(total_profit),
            "profit_margin": float((total_profit / total_revenue * 100) if total_revenue > 0 else 0),
            "average_transaction": float(total_revenue / len(sales)) if sales else 0,
            "hourly_breakdown": hourly_breakdown,
            "top_products": [
                {
                    "product_id": p.id,
                    "product_name": p.name,
                    "quantity_sold": int(p.total_quantity),
                    "revenue": float(p.total_revenue)
                }
                for p in top_products
            ]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve daily sales stats: {str(e)}"
        )


# ==================== SALE EXPORT ====================

@router.get("/export/csv")
async def export_sales_csv(
    request: Request,
    start_date: Optional[date] = Query(None, description="Start date"),
    end_date: Optional[date] = Query(None, description="End date"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Export sales data to CSV (Admin/Manager only).
    
    Returns CSV data for sales in the specified date range.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        if not end_date:
            end_date = date.today()
        if not start_date:
            start_date = end_date - timedelta(days=30)
        
        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())
        
        query = db.query(Sale).filter(
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
                query = query.filter(Sale.branch_id == branch_id)
        else:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            query = query.filter(Sale.branch_id == current_user.branch_id)
        
        sales = query.order_by(Sale.created_at.desc()).all()
        
        # Build CSV data
        csv_data = []
        headers = ["Sale ID", "Date", "Customer Name", "Customer Phone", "Total Amount", 
                   "Discount", "Tax", "Payment Method", "Items Count", "Created By"]
        csv_data.append(",".join(headers))
        
        for sale in sales:
            items_count = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).count()
            user = db.query(User).filter(User.id == sale.user_id).first()
            
            row = [
                str(sale.id),
                sale.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                sale.customer_name or "",
                sale.customer_phone or "",
                f"{float(sale.total_amount):.2f}",
                f"{float(sale.discount_amount):.2f}",
                f"{float(sale.tax_amount):.2f}",
                sale.payment_method or "",
                str(items_count),
                user.name if user else "Unknown"
            ]
            csv_data.append(",".join(f'"{item}"' for item in row))
        
        return {
            "csv_content": "\n".join(csv_data),
            "filename": f"sales_export_{start_date}_{end_date}.csv",
            "record_count": len(sales)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to export sales: {str(e)}"
        )


# ==================== HELPER FUNCTIONS ====================

def get_sales_by_customer(db: Session, tenant_id: int, customer_phone: str, branch_id: Optional[int] = None) -> List[Sale]:
    """Get all sales for a specific customer within a tenant"""
    query = db.query(Sale).filter(
        Sale.tenant_id == tenant_id,
        Sale.customer_phone == customer_phone
    )
    if branch_id:
        query = query.filter(Sale.branch_id == branch_id)
    return query.order_by(Sale.created_at.desc()).all()


def get_total_sales_today(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> Decimal:
    """Get total sales amount for today within a tenant"""
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = datetime.combine(date.today(), datetime.max.time())
    
    query = db.query(func.sum(Sale.total_amount)).filter(
        Sale.tenant_id == tenant_id,
        Sale.created_at.between(today_start, today_end)
    )
    if branch_id:
        query = query.filter(Sale.branch_id == branch_id)
    
    result = query.scalar()
    return result or Decimal(0)