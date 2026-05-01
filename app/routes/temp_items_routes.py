from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func
from typing import Optional, List
from datetime import datetime, date, timedelta
import uuid

from app.database import get_db
from app.models import User, TempItem, TempItemStatus, SystemLog, Branch, UserRole
from app.schemas import TempItemCreate, TempItemUpdate, TempItemResponse
from app.utils.auth import get_current_user, require_role, verify_branch_access, get_current_tenant
from app.services import ProductService

router = APIRouter(prefix="/temp-items", tags=["Temporary Items"])


def generate_item_number(tenant_id: int = None):
    """Generate a unique temporary item number with optional tenant prefix"""
    tenant_prefix = f"T{tenant_id}-" if tenant_id else ""
    return f"{tenant_prefix}TMP-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


# ==================== CREATE TEMP ITEM ====================

@router.post("/", response_model=TempItemResponse, status_code=status.HTTP_201_CREATED)
async def register_temp_item(
    item_data: TempItemCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Register a temporary item.
    
    Salesmen can register items that need approval or special processing.
    - **item_name**: Name of the item
    - **description**: Optional description
    - **quantity**: Quantity (default: 1)
    - **unit_price**: Optional unit price
    - **customer_name**: Optional customer name
    - **customer_phone**: Optional customer phone
    - **notes**: Optional notes
    """
    try:
        tenant_id = get_current_tenant(request)
        
        temp_item = TempItem(
            tenant_id=tenant_id,
            item_number=generate_item_number(tenant_id),
            item_name=item_data.item_name,
            description=item_data.description,
            quantity=item_data.quantity,
            unit_price=item_data.unit_price,
            customer_name=item_data.customer_name,
            customer_phone=item_data.customer_phone,
            notes=item_data.notes,
            registered_by=current_user.id,
            status=TempItemStatus.PENDING.value
        )
        
        db.add(temp_item)
        db.commit()
        db.refresh(temp_item)
        
        # Get registrar info
        registrar = db.query(User).filter(User.id == temp_item.registered_by).first()
        
        # Log creation
        log = SystemLog(
            tenant_id=tenant_id,
            log_type="info",
            message=f"Temp item registered: {temp_item.item_number}",
            details=f"Item: {temp_item.item_name}, Quantity: {temp_item.quantity}",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        return {
            "id": temp_item.id,
            "tenant_id": temp_item.tenant_id,
            "item_number": temp_item.item_number,
            "item_name": temp_item.item_name,
            "description": temp_item.description,
            "quantity": temp_item.quantity,
            "unit_price": float(temp_item.unit_price) if temp_item.unit_price else None,
            "customer_name": temp_item.customer_name,
            "customer_phone": temp_item.customer_phone,
            "notes": temp_item.notes,
            "status": temp_item.status,
            "registered_by": registrar.name if registrar else "System",
            "registered_at": temp_item.registered_at,
            "received_by": None,
            "received_at": None
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to register temporary item: {str(e)}"
        )


# ==================== GET TEMP ITEMS ====================

@router.get("/", response_model=List[TempItemResponse])
async def get_temp_items(
    request: Request,
    status_filter: Optional[TempItemStatus] = Query(None, description="Filter by status"),
    search: Optional[str] = Query(None, description="Search by item name, number, or customer"),
    registered_by: Optional[int] = Query(None, description="Filter by registrar ID"),
    start_date: Optional[date] = Query(None, description="Start date filter"),
    end_date: Optional[date] = Query(None, description="End date filter"),
    skip: int = Query(0, ge=0, description="Records to skip"),
    limit: int = Query(100, ge=1, le=500, description="Maximum records"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get temporary items.
    
    - Salesmen see only their own items
    - Admin/Manager see all items within their tenant
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(TempItem).filter(TempItem.tenant_id == tenant_id)
        
        # Apply role-based filter
        if current_user.role == UserRole.SALESMAN.value:
            query = query.filter(TempItem.registered_by == current_user.id)
        elif registered_by:
            query = query.filter(TempItem.registered_by == registered_by)
        
        # Apply filters
        if status_filter:
            query = query.filter(TempItem.status == status_filter)
        
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    TempItem.item_name.ilike(search_term),
                    TempItem.item_number.ilike(search_term),
                    TempItem.customer_name.ilike(search_term),
                    TempItem.customer_phone.ilike(search_term)
                )
            )
        
        if start_date:
            start_datetime = datetime.combine(start_date, datetime.min.time())
            query = query.filter(TempItem.registered_at >= start_datetime)
        
        if end_date:
            end_datetime = datetime.combine(end_date, datetime.max.time())
            query = query.filter(TempItem.registered_at <= end_datetime)
        
        items = query.order_by(TempItem.registered_at.desc()).offset(skip).limit(limit).all()
        
        result = []
        for item in items:
            registrar = db.query(User).filter(User.id == item.registered_by).first()
            receiver = db.query(User).filter(User.id == item.received_by).first() if item.received_by else None
            
            result.append({
                "id": item.id,
                "tenant_id": item.tenant_id,
                "item_number": item.item_number,
                "item_name": item.item_name,
                "description": item.description,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price) if item.unit_price else None,
                "customer_name": item.customer_name,
                "customer_phone": item.customer_phone,
                "notes": item.notes,
                "status": item.status,
                "registered_by": registrar.name if registrar else "System",
                "registered_at": item.registered_at,
                "received_by": receiver.name if receiver else None,
                "received_at": item.received_at
            })
        
        return result
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve temp items: {str(e)}"
        )


@router.get("/pending", response_model=List[TempItemResponse])
async def get_pending_temp_items(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get all pending temporary items (Admin, Tenant Admin, or Manager only).
    
    Returns items that need approval/receiving within the tenant.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(TempItem).filter(
            TempItem.tenant_id == tenant_id,
            TempItem.status == TempItemStatus.PENDING
        )
        
        items = query.order_by(TempItem.registered_at.asc()).all()
        
        result = []
        for item in items:
            registrar = db.query(User).filter(User.id == item.registered_by).first()
            
            result.append({
                "id": item.id,
                "tenant_id": item.tenant_id,
                "item_number": item.item_number,
                "item_name": item.item_name,
                "description": item.description,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price) if item.unit_price else None,
                "customer_name": item.customer_name,
                "customer_phone": item.customer_phone,
                "notes": item.notes,
                "status": item.status,
                "registered_by": registrar.name if registrar else "System",
                "registered_at": item.registered_at,
                "received_by": None,
                "received_at": None
            })
        
        return result
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve pending temp items: {str(e)}"
        )


@router.get("/{item_id}", response_model=TempItemResponse)
async def get_temp_item(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific temporary item by ID.
    
    - **item_id**: The ID of the temp item to retrieve
    """
    try:
        tenant_id = get_current_tenant(request)
        
        item = db.query(TempItem).filter(
            TempItem.id == item_id,
            TempItem.tenant_id == tenant_id
        ).first()
        
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Temp item with id {item_id} not found in this tenant"
            )
        
        # Check permissions
        if current_user.role == UserRole.SALESMAN.value and item.registered_by != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this item"
            )
        
        registrar = db.query(User).filter(User.id == item.registered_by).first()
        receiver = db.query(User).filter(User.id == item.received_by).first() if item.received_by else None
        
        return {
            "id": item.id,
            "tenant_id": item.tenant_id,
            "item_number": item.item_number,
            "item_name": item.item_name,
            "description": item.description,
            "quantity": item.quantity,
            "unit_price": float(item.unit_price) if item.unit_price else None,
            "customer_name": item.customer_name,
            "customer_phone": item.customer_phone,
            "notes": item.notes,
            "status": item.status,
            "registered_by": registrar.name if registrar else "System",
            "registered_at": item.registered_at,
            "received_by": receiver.name if receiver else None,
            "received_at": item.received_at
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve temp item: {str(e)}"
        )


@router.get("/by-number/{item_number}", response_model=TempItemResponse)
async def get_temp_item_by_number(
    item_number: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a temporary item by its item number.
    
    - **item_number**: The item number to search for
    """
    try:
        tenant_id = get_current_tenant(request)
        
        item = db.query(TempItem).filter(
            TempItem.tenant_id == tenant_id,
            TempItem.item_number == item_number
        ).first()
        
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Temp item with number {item_number} not found in this tenant"
            )
        
        # Check permissions
        if current_user.role == UserRole.SALESMAN.value and item.registered_by != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this item"
            )
        
        registrar = db.query(User).filter(User.id == item.registered_by).first()
        receiver = db.query(User).filter(User.id == item.received_by).first() if item.received_by else None
        
        return {
            "id": item.id,
            "tenant_id": item.tenant_id,
            "item_number": item.item_number,
            "item_name": item.item_name,
            "description": item.description,
            "quantity": item.quantity,
            "unit_price": float(item.unit_price) if item.unit_price else None,
            "customer_name": item.customer_name,
            "customer_phone": item.customer_phone,
            "notes": item.notes,
            "status": item.status,
            "registered_by": registrar.name if registrar else "System",
            "registered_at": item.registered_at,
            "received_by": receiver.name if receiver else None,
            "received_at": item.received_at
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve temp item: {str(e)}"
        )


# ==================== UPDATE TEMP ITEMS ====================

@router.put("/{item_id}", response_model=TempItemResponse)
async def update_temp_item(
    item_id: int,
    item_update: TempItemUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update a temporary item.
    
    - **item_id**: The ID of the item to update
    - **status**: Update status (pending, received, cancelled)
    - **notes**: Update notes
    
    Salesmen can only update their own pending items.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        item = db.query(TempItem).filter(
            TempItem.id == item_id,
            TempItem.tenant_id == tenant_id
        ).first()
        
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Temp item with id {item_id} not found in this tenant"
            )
        
        # Check permissions
        if current_user.role == UserRole.SALESMAN.value:
            if item.registered_by != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to update this item"
                )
            
            # Salesmen can only update pending items
            if item.status != TempItemStatus.PENDING:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot update item with status: {item.status}"
                )
        
        update_data = item_update.model_dump(exclude_unset=True)
        
        for key, value in update_data.items():
            setattr(item, key, value)
        
        db.commit()
        db.refresh(item)
        
        registrar = db.query(User).filter(User.id == item.registered_by).first()
        receiver = db.query(User).filter(User.id == item.received_by).first() if item.received_by else None
        
        return {
            "id": item.id,
            "tenant_id": item.tenant_id,
            "item_number": item.item_number,
            "item_name": item.item_name,
            "description": item.description,
            "quantity": item.quantity,
            "unit_price": float(item.unit_price) if item.unit_price else None,
            "customer_name": item.customer_name,
            "customer_phone": item.customer_phone,
            "notes": item.notes,
            "status": item.status,
            "registered_by": registrar.name if registrar else "System",
            "registered_at": item.registered_at,
            "received_by": receiver.name if receiver else None,
            "received_at": item.received_at
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update temp item: {str(e)}"
        )


@router.put("/{item_id}/receive", response_model=TempItemResponse)
async def receive_temp_item(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Mark a temporary item as received (Admin, Tenant Admin, or Manager only).
    
    - **item_id**: The ID of the item to receive
    
    This approves the temporary item and marks it as processed.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        item = db.query(TempItem).filter(
            TempItem.id == item_id,
            TempItem.tenant_id == tenant_id
        ).first()
        
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Temp item with id {item_id} not found in this tenant"
            )
        
        if item.status == TempItemStatus.RECEIVED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Item already received"
            )
        
        if item.status == TempItemStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot receive a cancelled item"
            )
        
        item.status = TempItemStatus.RECEIVED
        item.received_by = current_user.id
        item.received_at = datetime.now()
        
        db.commit()
        db.refresh(item)
        
        # Log receipt
        log = SystemLog(
            tenant_id=tenant_id,
            log_type="info",
            message=f"Temp item received: {item.item_number}",
            details=f"Item: {item.item_name}, Quantity: {item.quantity}",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        registrar = db.query(User).filter(User.id == item.registered_by).first()
        receiver = db.query(User).filter(User.id == item.received_by).first()
        
        return {
            "id": item.id,
            "tenant_id": item.tenant_id,
            "item_number": item.item_number,
            "item_name": item.item_name,
            "description": item.description,
            "quantity": item.quantity,
            "unit_price": float(item.unit_price) if item.unit_price else None,
            "customer_name": item.customer_name,
            "customer_phone": item.customer_phone,
            "notes": item.notes,
            "status": item.status,
            "registered_by": registrar.name if registrar else "System",
            "registered_at": item.registered_at,
            "received_by": receiver.name if receiver else None,
            "received_at": item.received_at
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to receive temp item: {str(e)}"
        )


@router.put("/{item_id}/cancel", response_model=TempItemResponse)
async def cancel_temp_item(
    item_id: int,
    request: Request,
    cancellation_reason: Optional[str] = Query(None, description="Reason for cancellation"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Cancel a temporary item.
    
    - **item_id**: The ID of the item to cancel
    - **cancellation_reason**: Optional reason for cancellation
    
    Salesmen can cancel their own pending items.
    Admin/Manager can cancel any pending item within their tenant.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        item = db.query(TempItem).filter(
            TempItem.id == item_id,
            TempItem.tenant_id == tenant_id
        ).first()
        
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Temp item with id {item_id} not found in this tenant"
            )
        
        # Check permissions
        if current_user.role == UserRole.SALESMAN.value:
            if item.registered_by != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to cancel this item"
                )
        
        if item.status == TempItemStatus.RECEIVED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot cancel a received item"
            )
        
        if item.status == TempItemStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Item already cancelled"
            )
        
        item.status = TempItemStatus.CANCELLED
        
        if cancellation_reason:
            item.notes = f"{item.notes or ''}\nCancelled: {cancellation_reason}".strip()
        
        db.commit()
        db.refresh(item)
        
        # Log cancellation
        log = SystemLog(
            tenant_id=tenant_id,
            log_type="info",
            message=f"Temp item cancelled: {item.item_number}",
            details=f"Item: {item.item_name}, Reason: {cancellation_reason}",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        registrar = db.query(User).filter(User.id == item.registered_by).first()
        receiver = db.query(User).filter(User.id == item.received_by).first() if item.received_by else None
        
        return {
            "id": item.id,
            "tenant_id": item.tenant_id,
            "item_number": item.item_number,
            "item_name": item.item_name,
            "description": item.description,
            "quantity": item.quantity,
            "unit_price": float(item.unit_price) if item.unit_price else None,
            "customer_name": item.customer_name,
            "customer_phone": item.customer_phone,
            "notes": item.notes,
            "status": item.status,
            "registered_by": registrar.name if registrar else "System",
            "registered_at": item.registered_at,
            "received_by": receiver.name if receiver else None,
            "received_at": item.received_at
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel temp item: {str(e)}"
        )


# ==================== DELETE TEMP ITEMS ====================

@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_temp_item(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Delete a temporary item (Super Admin or Tenant Admin only).
    
    - **item_id**: The ID of the item to delete
    """
    try:
        tenant_id = get_current_tenant(request)
        
        item = db.query(TempItem).filter(
            TempItem.id == item_id,
            TempItem.tenant_id == tenant_id
        ).first()
        
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Temp item with id {item_id} not found in this tenant"
            )
        
        db.delete(item)
        db.commit()
        
        # Log deletion
        log = SystemLog(
            tenant_id=tenant_id,
            log_type="info",
            message=f"Temp item deleted: {item.item_number}",
            details=f"Item: {item.item_name}",
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
            detail=f"Failed to delete temp item: {str(e)}"
        )


# ==================== STATISTICS ====================

@router.get("/stats/summary")
async def get_temp_items_statistics(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get temporary items statistics (Admin, Tenant Admin, or Manager only).
    
    Returns counts of items by status and other metrics within the tenant.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        total_pending = db.query(TempItem).filter(
            TempItem.tenant_id == tenant_id,
            TempItem.status == TempItemStatus.PENDING
        ).count()
        
        total_received = db.query(TempItem).filter(
            TempItem.tenant_id == tenant_id,
            TempItem.status == TempItemStatus.RECEIVED
        ).count()
        
        total_cancelled = db.query(TempItem).filter(
            TempItem.tenant_id == tenant_id,
            TempItem.status == TempItemStatus.CANCELLED
        ).count()
        
        # Items registered today
        today_start = datetime.combine(date.today(), datetime.min.time())
        today_end = datetime.combine(date.today(), datetime.max.time())
        registered_today = db.query(TempItem).filter(
            TempItem.tenant_id == tenant_id,
            TempItem.registered_at.between(today_start, today_end)
        ).count()
        
        # Top registrars
        top_registrars = db.query(
            TempItem.registered_by,
            func.count(TempItem.id).label('count')
        ).filter(
            TempItem.tenant_id == tenant_id
        ).group_by(TempItem.registered_by).order_by(
            func.count(TempItem.id).desc()
        ).limit(5).all()
        
        registrars_info = []
        for reg in top_registrars:
            user = db.query(User).filter(User.id == reg.registered_by).first()
            registrars_info.append({
                "user_id": reg.registered_by,
                "user_name": user.name if user else "Unknown",
                "item_count": reg.count
            })
        
        return {
            "total_pending": total_pending,
            "total_received": total_received,
            "total_cancelled": total_cancelled,
            "total_items": total_pending + total_received + total_cancelled,
            "registered_today": registered_today,
            "top_registrars": registrars_info
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve statistics: {str(e)}"
        )


# ==================== HELPER FUNCTIONS ====================

def get_pending_count_by_user(db: Session, tenant_id: int, user_id: int) -> int:
    """Get count of pending items for a specific user within a tenant"""
    return db.query(TempItem).filter(
        TempItem.tenant_id == tenant_id,
        TempItem.registered_by == user_id,
        TempItem.status == TempItemStatus.PENDING
    ).count()


def get_temp_items_by_customer(db: Session, tenant_id: int, customer_phone: str) -> List[TempItem]:
    """Get all temp items for a specific customer within a tenant"""
    return db.query(TempItem).filter(
        TempItem.tenant_id == tenant_id,
        TempItem.customer_phone == customer_phone
    ).order_by(TempItem.registered_at.desc()).all()