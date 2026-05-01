from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import Optional, List
from datetime import datetime, date, timedelta
from decimal import Decimal

from app.database import get_db
from app.services import PurchaseOrderService, ProductService, StockService
from app.schemas import (
    PurchaseCreate, 
    Purchase as PurchaseSchema, 
    PurchaseOrderCreate, 
    PurchaseOrderResponse, 
    PurchaseOrderUpdate, 
    ReceivePurchaseOrder,
    PurchaseStatus
)
from app.utils.auth import get_current_user, require_role, get_current_tenant, verify_branch_access
from app.models import User, Purchase, PurchaseItem, PurchaseOrder, PurchaseOrderItem, Product, Stock, StockMovement, MovementType, Branch, UserRole

router = APIRouter(prefix="/purchases", tags=["Purchases"])


# ==================== LEGACY PURCHASE ROUTES ====================

@router.post("/legacy", response_model=PurchaseSchema, status_code=status.HTTP_201_CREATED)
async def create_legacy_purchase(
    purchase_data: PurchaseCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Create a new legacy purchase (for backward compatibility).
    
    - **branch_id**: Branch ID for the purchase
    - **supplier_name**: Name of the supplier
    - **items**: List of items with quantities and costs
    
    Only admin, tenant admin, and manager can create purchases.
    """
    try:
        tenant_id = get_current_tenant(request)
        branch_id = purchase_data.branch_id or current_user.branch_id
        
        if not branch_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Branch ID required or user must be assigned to a branch"
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
        
        total_amount = Decimal('0')
        
        # Create purchase
        purchase = Purchase(
            tenant_id=tenant_id,
            branch_id=branch_id,
            supplier_name=purchase_data.supplier_name,
            total_amount=Decimal('0'),
        )
        db.add(purchase)
        db.flush()
        
        # Add items and calculate total
        for item_data in purchase_data.items:
            product = ProductService.get_product(db, item_data.product_id, tenant_id)
            if not product:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Product {item_data.product_id} not found in this tenant"
                )
            
            item_total = item_data.quantity * item_data.unit_cost
            total_amount += item_total
            
            purchase_item = PurchaseItem(
                purchase_id=purchase.id,
                product_id=item_data.product_id,
                quantity=item_data.quantity,
                unit_cost=item_data.unit_cost
            )
            db.add(purchase_item)
            
            # Update stock
            stock = StockService.get_stock(db, branch_id, item_data.product_id, tenant_id)
            
            if stock:
                stock.quantity += item_data.quantity
            else:
                stock = Stock(
                    branch_id=branch_id,
                    product_id=item_data.product_id,
                    quantity=item_data.quantity,
                    reorder_level=0
                )
                db.add(stock)
            
            # Record stock movement
            stock_movement = StockMovement(
                branch_id=branch_id,
                product_id=item_data.product_id,
                user_id=current_user.id,
                change_qty=item_data.quantity,
                movement_type=MovementType.PURCHASE.value,
                reference_id=purchase.id,
                notes=f"Legacy purchase from {purchase_data.supplier_name}"
            )
            db.add(stock_movement)
        
        purchase.total_amount = total_amount
        db.commit()
        db.refresh(purchase)
        
        return purchase
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create purchase: {str(e)}"
        )


@router.get("/legacy", response_model=List[PurchaseSchema])
async def get_legacy_purchases(
    request: Request,
    supplier: Optional[str] = Query(None, description="Filter by supplier name"),
    from_date: Optional[date] = Query(None, description="Start date"),
    to_date: Optional[date] = Query(None, description="End date"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get all legacy purchases with optional filters.
    
    - **supplier**: Filter by supplier name (partial match)
    - **from_date**: Start date filter
    - **to_date**: End date filter
    - **branch_id**: Filter by branch
    
    Only admin, tenant admin, and manager can view purchases.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(Purchase).filter(Purchase.tenant_id == tenant_id)
        
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
                query = query.filter(Purchase.branch_id == branch_id)
        else:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            query = query.filter(Purchase.branch_id == current_user.branch_id)
        
        if supplier:
            query = query.filter(Purchase.supplier_name.ilike(f"%{supplier}%"))
        
        if from_date:
            start_date = datetime.combine(from_date, datetime.min.time())
            query = query.filter(Purchase.created_at >= start_date)
        
        if to_date:
            end_date = datetime.combine(to_date, datetime.max.time())
            query = query.filter(Purchase.created_at <= end_date)
        
        purchases = query.order_by(Purchase.created_at.desc()).offset(skip).limit(limit).all()
        
        return purchases
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve purchases: {str(e)}"
        )


# ==================== PURCHASE ORDER ROUTES ====================

@router.post("/orders", response_model=PurchaseOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_purchase_order(
    purchase_data: PurchaseOrderCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Create a new purchase order.
    
    - **supplier**: Supplier name
    - **expected_delivery_date**: Expected delivery date
    - **tax_amount**: Tax amount
    - **shipping_cost**: Shipping cost
    - **discount_amount**: Discount amount
    - **items**: List of items with quantities and costs
    - **notes**: Additional notes
    
    Only admin, tenant admin, and manager can create purchase orders.
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
        
        # Validate products exist in this tenant
        for item in purchase_data.items:
            product = ProductService.get_product(db, item.product_id, tenant_id)
            if not product:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Product with id {item.product_id} not found in this tenant"
                )
        
        purchase_order = PurchaseOrderService.create_purchase_order(
            db, purchase_data, current_user.id, branch_id, tenant_id
        )
        
        return purchase_order
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
            detail=f"Failed to create purchase order: {str(e)}"
        )


@router.get("/orders", response_model=List[PurchaseOrderResponse])
async def get_purchase_orders(
    request: Request,
    supplier: Optional[str] = Query(None, description="Filter by supplier name"),
    status_filter: Optional[PurchaseStatus] = Query(None, description="Filter by status"),
    from_date: Optional[date] = Query(None, description="Start date"),
    to_date: Optional[date] = Query(None, description="End date"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get all purchase orders with optional filters.
    
    - **supplier**: Filter by supplier name (partial match)
    - **status**: Filter by status (pending, completed, cancelled, partially_received)
    - **from_date**: Start date filter
    - **to_date**: End date filter
    - **branch_id**: Filter by branch (admin only)
    
    Only admin, tenant admin, and manager can view purchase orders.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(PurchaseOrder).filter(PurchaseOrder.tenant_id == tenant_id)
        
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
                query = query.filter(PurchaseOrder.branch_id == branch_id)
        else:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            query = query.filter(PurchaseOrder.branch_id == current_user.branch_id)
        
        if supplier:
            query = query.filter(PurchaseOrder.supplier.ilike(f"%{supplier}%"))
        
        if status_filter:
            query = query.filter(PurchaseOrder.status == status_filter)
        
        if from_date:
            start_date = datetime.combine(from_date, datetime.min.time())
            query = query.filter(PurchaseOrder.order_date >= start_date)
        
        if to_date:
            end_date = datetime.combine(to_date, datetime.max.time())
            query = query.filter(PurchaseOrder.order_date <= end_date)
        
        orders = query.order_by(PurchaseOrder.order_date.desc()).offset(skip).limit(limit).all()
        
        return orders
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve purchase orders: {str(e)}"
        )


@router.get("/orders/pending", response_model=List[PurchaseOrderResponse])
async def get_pending_purchase_orders(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get all pending purchase orders.
    
    - **branch_id**: Optional branch filter
    
    Returns purchase orders with status 'pending' or 'partially_received'.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(PurchaseOrder).filter(
            PurchaseOrder.tenant_id == tenant_id,
            PurchaseOrder.status.in_([PurchaseStatus.PENDING, PurchaseStatus.PARTIALLY_RECEIVED])
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
                query = query.filter(PurchaseOrder.branch_id == branch_id)
        else:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            query = query.filter(PurchaseOrder.branch_id == current_user.branch_id)
        
        orders = query.order_by(PurchaseOrder.expected_delivery_date.asc()).all()
        
        return orders
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve pending purchase orders: {str(e)}"
        )


@router.get("/orders/{order_id}", response_model=PurchaseOrderResponse)
async def get_purchase_order(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get a specific purchase order by ID.
    
    - **order_id**: The ID of the purchase order to retrieve
    
    Returns the purchase order details.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        order = db.query(PurchaseOrder).filter(
            PurchaseOrder.id == order_id,
            PurchaseOrder.tenant_id == tenant_id
        ).first()
        
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Purchase order with id {order_id} not found in this tenant"
            )
        
        # Check permission
        if current_user.role not in [UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]:
            if not current_user.branch_id or order.branch_id != current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to view this purchase order"
                )
        
        return order
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve purchase order: {str(e)}"
        )


@router.get("/orders/by-number/{order_number}", response_model=PurchaseOrderResponse)
async def get_purchase_order_by_number(
    order_number: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get a purchase order by its order number.
    
    - **order_number**: The order number to search for
    
    Returns the purchase order details.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        order = db.query(PurchaseOrder).filter(
            PurchaseOrder.tenant_id == tenant_id,
            PurchaseOrder.order_number == order_number
        ).first()
        
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Purchase order with number {order_number} not found in this tenant"
            )
        
        return order
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve purchase order: {str(e)}"
        )


@router.put("/orders/{order_id}", response_model=PurchaseOrderResponse)
async def update_purchase_order(
    order_id: int,
    update_data: PurchaseOrderUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Update a purchase order.
    
    - **order_id**: The ID of the purchase order to update
    - **status**: Update status (pending, completed, cancelled, partially_received)
    - **actual_delivery_date**: Update actual delivery date
    - **notes**: Update notes
    
    Only admin, tenant admin, and manager can update purchase orders.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        order = db.query(PurchaseOrder).filter(
            PurchaseOrder.id == order_id,
            PurchaseOrder.tenant_id == tenant_id
        ).first()
        
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Purchase order with id {order_id} not found in this tenant"
            )
        
        if update_data.status:
            order.status = update_data.status
        
        if update_data.actual_delivery_date:
            order.actual_delivery_date = datetime.combine(update_data.actual_delivery_date, datetime.min.time())
        
        if update_data.notes is not None:
            order.notes = update_data.notes
        
        order.updated_at = datetime.now()
        
        db.commit()
        db.refresh(order)
        
        return order
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update purchase order: {str(e)}"
        )


@router.post("/orders/{order_id}/receive", response_model=dict)
async def receive_purchase_order(
    order_id: int,
    receive_data: ReceivePurchaseOrder,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Receive items from a purchase order and update inventory.
    
    - **order_id**: The ID of the purchase order
    - **items**: List of received items with quantities
    - **actual_delivery_date**: Actual delivery date
    
    Only admin, tenant admin, and manager can receive purchase orders.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        order = db.query(PurchaseOrder).filter(
            PurchaseOrder.id == order_id,
            PurchaseOrder.tenant_id == tenant_id
        ).first()
        
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Purchase order with id {order_id} not found in this tenant"
            )
        
        if order.status == PurchaseStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Purchase order already completed"
            )
        
        received_items = []
        
        for receive_item in receive_data.items:
            purchase_item = db.query(PurchaseOrderItem).filter(
                and_(
                    PurchaseOrderItem.purchase_order_id == order_id,
                    PurchaseOrderItem.product_id == receive_item.product_id
                )
            ).first()
            
            if not purchase_item:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Product ID {receive_item.product_id} not found in purchase order"
                )
            
            quantity_received = receive_item.quantity_received
            new_received = purchase_item.quantity_received + quantity_received
            
            if new_received > purchase_item.quantity_ordered:
                remaining = purchase_item.quantity_ordered - purchase_item.quantity_received
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot receive {quantity_received} units. Only {remaining} units remaining for product ID {receive_item.product_id}."
                )
            
            purchase_item.quantity_received = new_received
            purchase_item.received_at = datetime.now()
            
            product = ProductService.get_product(db, purchase_item.product_id, tenant_id)
            
            # Update stock
            stock = StockService.get_stock(db, order.branch_id, purchase_item.product_id, tenant_id)
            
            if stock:
                stock.quantity += quantity_received
            else:
                stock = Stock(
                    branch_id=order.branch_id,
                    product_id=purchase_item.product_id,
                    quantity=quantity_received,
                    reorder_level=0
                )
                db.add(stock)
            
            # Record stock movement
            stock_movement = StockMovement(
                branch_id=order.branch_id,
                product_id=purchase_item.product_id,
                user_id=current_user.id,
                change_qty=quantity_received,
                movement_type=MovementType.PURCHASE.value,
                reference_id=order.id,
                notes=f"Received from PO: {order.order_number}"
            )
            db.add(stock_movement)
            
            received_items.append({
                "product_id": purchase_item.product_id,
                "product_name": product.name if product else "Unknown",
                "quantity_received": float(quantity_received),
                "unit_cost": float(purchase_item.unit_cost),
                "total_cost": float(purchase_item.unit_cost * quantity_received),
                "branch_id": order.branch_id
            })
        
        # Update order status
        all_items_received = all(
            item.quantity_received >= item.quantity_ordered 
            for item in order.items
        )
        
        order.status = PurchaseStatus.COMPLETED if all_items_received else PurchaseStatus.PARTIALLY_RECEIVED
        order.actual_delivery_date = datetime.combine(receive_data.actual_delivery_date, datetime.min.time())
        order.updated_at = datetime.now()
        
        db.commit()
        
        return {
            "success": True,
            "message": "Purchase order received successfully",
            "status": order.status.value,
            "order_number": order.order_number,
            "branch_id": order.branch_id,
            "received_items": received_items,
            "total_items_received": len(received_items)
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to receive purchase order: {str(e)}"
        )


@router.delete("/orders/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_purchase_order(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Delete a purchase order (Super Admin or Tenant Admin only).
    
    - **order_id**: The ID of the purchase order to delete
    
    Only super admin and tenant admin can delete purchase orders. Only pending orders can be deleted.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        order = db.query(PurchaseOrder).filter(
            PurchaseOrder.id == order_id,
            PurchaseOrder.tenant_id == tenant_id
        ).first()
        
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Purchase order with id {order_id} not found in this tenant"
            )
        
        if order.status != PurchaseStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete purchase order with status: {order.status.value}. Only pending orders can be deleted."
            )
        
        db.delete(order)
        db.commit()
        
        return None
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete purchase order: {str(e)}"
        )


@router.post("/orders/{order_id}/cancel", response_model=PurchaseOrderResponse)
async def cancel_purchase_order(
    order_id: int,
    request: Request,
    cancellation_reason: Optional[str] = Query(None, description="Reason for cancellation"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Cancel a purchase order.
    
    - **order_id**: The ID of the purchase order to cancel
    - **cancellation_reason**: Reason for cancellation
    
    Only admin, tenant admin, and manager can cancel purchase orders.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        order = db.query(PurchaseOrder).filter(
            PurchaseOrder.id == order_id,
            PurchaseOrder.tenant_id == tenant_id
        ).first()
        
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Purchase order with id {order_id} not found in this tenant"
            )
        
        if order.status == PurchaseStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot cancel a completed purchase order"
            )
        
        order.status = PurchaseStatus.CANCELLED
        if cancellation_reason:
            order.notes = f"{order.notes or ''}\nCancelled: {cancellation_reason}".strip()
        order.updated_at = datetime.now()
        
        db.commit()
        db.refresh(order)
        
        return order
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel purchase order: {str(e)}"
        )


# ==================== PURCHASE REPORTS ====================

@router.get("/reports/summary", response_model=dict)
async def get_purchase_report(
    request: Request,
    from_date: Optional[date] = Query(None, description="Start date"),
    to_date: Optional[date] = Query(None, description="End date"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get purchase report with summaries.
    
    - **from_date**: Start date (default: 30 days ago)
    - **to_date**: End date (default: today)
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
        
        query = db.query(PurchaseOrder).filter(
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
                query = query.filter(PurchaseOrder.branch_id == branch_id)
        else:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            query = query.filter(PurchaseOrder.branch_id == current_user.branch_id)
        
        purchase_orders = query.all()
        
        # Legacy purchases
        legacy_query = db.query(Purchase).filter(
            Purchase.tenant_id == tenant_id,
            Purchase.created_at.between(start_date, end_date)
        )
        
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
            if branch_id:
                legacy_query = legacy_query.filter(Purchase.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            legacy_query = legacy_query.filter(Purchase.branch_id == current_user.branch_id)
        
        legacy_purchases = legacy_query.all()
        
        # Calculate totals
        total_po_cost = sum(float(po.total_amount) for po in purchase_orders)
        total_legacy_cost = sum(float(p.total_amount) for p in legacy_purchases)
        
        # Supplier breakdown
        supplier_totals = {}
        for po in purchase_orders:
            supplier_totals[po.supplier] = supplier_totals.get(po.supplier, 0) + float(po.total_amount)
        
        # Top items
        top_items_query = db.query(
            PurchaseOrderItem.product_id,
            Product.name.label("product_name"),
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
        
        top_items = top_items_query.group_by(
            PurchaseOrderItem.product_id, Product.name
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
                "total_po_cost": round(total_po_cost, 2),
                "total_legacy_purchases": len(legacy_purchases),
                "total_legacy_cost": round(total_legacy_cost, 2),
                "total_all_purchases": round(total_po_cost + total_legacy_cost, 2),
                "average_order_value": round(total_po_cost / len(purchase_orders), 2) if purchase_orders else 0
            },
            "supplier_breakdown": [
                {"supplier": supplier, "total_amount": round(amount, 2)}
                for supplier, amount in sorted(supplier_totals.items(), key=lambda x: x[1], reverse=True)
            ],
            "top_items": [
                {
                    "product_id": item.product_id,
                    "product_name": item.product_name,
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


# ==================== HELPER FUNCTIONS ====================

def get_purchase_order_by_number(db: Session, tenant_id: int, order_number: str) -> Optional[PurchaseOrder]:
    """
    Get a purchase order by its order number within a tenant.
    """
    return db.query(PurchaseOrder).filter(
        PurchaseOrder.tenant_id == tenant_id,
        PurchaseOrder.order_number == order_number
    ).first()


def get_pending_orders_count(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> int:
    """
    Get count of pending purchase orders within a tenant.
    """
    query = db.query(PurchaseOrder).filter(
        PurchaseOrder.tenant_id == tenant_id,
        PurchaseOrder.status.in_([PurchaseStatus.PENDING, PurchaseStatus.PARTIALLY_RECEIVED])
    )
    if branch_id:
        query = query.filter(PurchaseOrder.branch_id == branch_id)
    return query.count()