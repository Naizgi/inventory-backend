from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from typing import List, Optional
from datetime import datetime, date, timedelta
from decimal import Decimal
from app.database import get_db
from app.services import BatchService, StockService, ProductService
from app.schemas import BatchCreate, BatchUpdate, Batch
from app.utils.auth import get_current_user, require_role, get_current_tenant, verify_branch_access
from app.models import User, Batch, Product, StockMovement, MovementType, Branch, UserRole

router = APIRouter(prefix="/batches", tags=["Batches"])


@router.post("/", response_model=Batch, status_code=status.HTTP_201_CREATED)
async def create_batch(
    batch_data: BatchCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Create a new batch for a product (for pharmacy/food items with expiry).
    
    - **product_id**: ID of the product
    - **branch_id**: ID of the branch
    - **batch_number**: Unique batch number
    - **supplier_batch**: Supplier's batch number (optional)
    - **manufacturing_date**: Date of manufacture (optional)
    - **expiry_date**: Expiration date (optional)
    - **quantity**: Initial quantity
    - **unit_cost**: Cost per unit
    
    Only admin, tenant admin, and manager can create batches.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check if product exists in this tenant
        product = ProductService.get_product(db, batch_data.product_id, tenant_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with id {batch_data.product_id} not found in this tenant"
            )
        
        # Check if branch exists in this tenant
        branch = db.query(Branch).filter(
            Branch.id == batch_data.branch_id,
            Branch.tenant_id == tenant_id
        ).first()
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Branch with id {batch_data.branch_id} not found in this tenant"
            )
        
        # Check if batch number already exists for this branch
        existing_batch = db.query(Batch).filter(
            Batch.tenant_id == tenant_id,
            Batch.batch_number == batch_data.batch_number,
            Batch.branch_id == batch_data.branch_id
        ).first()
        
        if existing_batch:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Batch number '{batch_data.batch_number}' already exists for this branch"
            )
        
        # Validate expiry date
        if batch_data.expiry_date and batch_data.expiry_date <= datetime.now():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Expiry date must be in the future"
            )
        
        # Create batch
        batch = BatchService.create_batch(db, batch_data, tenant_id)
        
        return batch
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create batch: {str(e)}"
        )


@router.get("/", response_model=List[Batch])
async def get_batches(
    request: Request,
    product_id: Optional[int] = Query(None, description="Filter by product ID"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    include_expired: bool = Query(False, description="Include expired batches"),
    status: Optional[str] = Query(None, description="Filter by status (active, expired, low)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all batches with optional filters.
    
    - **product_id**: Filter by product
    - **branch_id**: Filter by branch
    - **include_expired**: Include expired batches
    - **status**: Filter by status (active, expired, low)
    
    Returns a list of batches within the current tenant.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(Batch).filter(Batch.tenant_id == tenant_id)
        
        if product_id:
            # Verify product exists in tenant
            product = ProductService.get_product(db, product_id, tenant_id)
            if not product:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Product with id {product_id} not found in this tenant"
                )
            query = query.filter(Batch.product_id == product_id)
        
        if branch_id:
            # Verify branch exists in tenant
            branch = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.tenant_id == tenant_id
            ).first()
            if not branch:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Branch with id {branch_id} not found in this tenant"
                )
            query = query.filter(Batch.branch_id == branch_id)
        
        if not include_expired:
            query = query.filter(
                or_(Batch.expiry_date.is_(None), Batch.expiry_date > datetime.now())
            )
        
        if status:
            now = datetime.now()
            if status == "active":
                query = query.filter(
                    Batch.remaining_quantity > 0,
                    or_(Batch.expiry_date.is_(None), Batch.expiry_date > now)
                )
            elif status == "expired":
                query = query.filter(
                    Batch.expiry_date <= now,
                    Batch.expiry_date.isnot(None)
                )
            elif status == "low":
                query = query.filter(
                    Batch.remaining_quantity > 0,
                    Batch.remaining_quantity <= 10
                )
        
        batches = query.order_by(Batch.expiry_date.asc()).all()
        
        # Add product names
        for batch in batches:
            product = ProductService.get_product(db, batch.product_id, tenant_id)
            if product:
                batch.product_name = product.name
        
        return batches
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve batches: {str(e)}"
        )


@router.get("/expiring-soon", response_model=List[Batch])
async def get_expiring_soon_batches(
    request: Request,
    days: int = Query(30, description="Number of days to check for expiry", ge=1, le=365),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get batches that are expiring within the specified number of days.
    
    - **days**: Number of days to look ahead (default: 30)
    - **branch_id**: Optional branch filter
    
    Returns batches expiring soon.
    """
    try:
        tenant_id = get_current_tenant(request)
        now = datetime.now()
        expiry_threshold = now + timedelta(days=days)
        
        query = db.query(Batch).filter(
            Batch.tenant_id == tenant_id,
            Batch.expiry_date.isnot(None),
            Batch.expiry_date <= expiry_threshold,
            Batch.expiry_date > now,
            Batch.remaining_quantity > 0
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
            query = query.filter(Batch.branch_id == branch_id)
        
        batches = query.order_by(Batch.expiry_date.asc()).all()
        
        # Add product names and days until expiry
        for batch in batches:
            product = ProductService.get_product(db, batch.product_id, tenant_id)
            if product:
                batch.product_name = product.name
            batch.days_until_expiry = (batch.expiry_date - now).days
        
        return batches
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve expiring batches: {str(e)}"
        )


@router.get("/expired", response_model=List[Batch])
async def get_expired_batches(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get all expired batches.
    
    - **branch_id**: Optional branch filter
    
    Returns list of expired batches.
    Only admin, tenant admin, and manager can view expired batches.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(Batch).filter(
            Batch.tenant_id == tenant_id,
            Batch.expiry_date.isnot(None),
            Batch.expiry_date <= datetime.now()
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
            query = query.filter(Batch.branch_id == branch_id)
        
        batches = query.order_by(Batch.expiry_date.desc()).all()
        
        # Add product names
        for batch in batches:
            product = ProductService.get_product(db, batch.product_id, tenant_id)
            if product:
                batch.product_name = product.name
        
        return batches
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve expired batches: {str(e)}"
        )


@router.get("/{batch_id}", response_model=Batch)
async def get_batch(
    batch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific batch by ID.
    
    - **batch_id**: The ID of the batch to retrieve
    
    Returns the batch details.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        batch = BatchService.get_batch(db, batch_id, tenant_id)
        if not batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Batch with id {batch_id} not found in this tenant"
            )
        
        # Add product name
        product = ProductService.get_product(db, batch.product_id, tenant_id)
        if product:
            batch.product_name = product.name
        
        return batch
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve batch: {str(e)}"
        )


@router.get("/by-number/{batch_number}", response_model=Batch)
async def get_batch_by_number(
    batch_number: str,
    request: Request,
    branch_id: Optional[int] = Query(None, description="Branch ID (required if batch number not unique)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a batch by its batch number.
    
    - **batch_number**: The batch number
    - **branch_id**: Optional branch ID (required if same batch number exists in multiple branches)
    
    Returns the batch details.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(Batch).filter(
            Batch.tenant_id == tenant_id,
            Batch.batch_number == batch_number
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
            query = query.filter(Batch.branch_id == branch_id)
        
        batch = query.first()
        
        if not batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Batch with number '{batch_number}' not found in this tenant"
            )
        
        # Add product name
        product = ProductService.get_product(db, batch.product_id, tenant_id)
        if product:
            batch.product_name = product.name
        
        return batch
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve batch: {str(e)}"
        )


@router.put("/{batch_id}", response_model=Batch)
async def update_batch(
    batch_id: int,
    batch_data: BatchUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Update a batch.
    
    - **batch_id**: The ID of the batch to update
    - **quantity**: Update quantity (optional)
    - **remaining_quantity**: Update remaining quantity (optional)
    - **expiry_date**: Update expiry date (optional)
    
    Only admin, tenant admin, and manager can update batches.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        batch = BatchService.get_batch(db, batch_id, tenant_id)
        if not batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Batch with id {batch_id} not found in this tenant"
            )
        
        # Update fields
        if batch_data.quantity is not None:
            # If quantity changes, adjust remaining_quantity proportionally
            old_quantity = batch.quantity
            old_remaining = batch.remaining_quantity
            if old_quantity > 0:
                ratio = old_remaining / old_quantity
                batch.remaining_quantity = batch_data.quantity * ratio
            batch.quantity = batch_data.quantity
        
        if batch_data.remaining_quantity is not None:
            batch.remaining_quantity = batch_data.remaining_quantity
        
        if batch_data.expiry_date is not None:
            if batch_data.expiry_date <= datetime.now():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Expiry date must be in the future"
                )
            batch.expiry_date = batch_data.expiry_date
        
        db.commit()
        db.refresh(batch)
        
        # Add product name
        product = ProductService.get_product(db, batch.product_id, tenant_id)
        if product:
            batch.product_name = product.name
        
        return batch
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update batch: {str(e)}"
        )


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_batch(
    batch_id: int,
    request: Request,
    force: bool = Query(False, description="Force delete even if batch has stock movements"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Delete a batch.
    
    - **batch_id**: The ID of the batch to delete
    - **force**: If True, delete batch even if it has associated stock movements
    
    Only super admin and tenant admin can delete batches.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        batch = BatchService.get_batch(db, batch_id, tenant_id)
        if not batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Batch with id {batch_id} not found in this tenant"
            )
        
        # Check if batch has stock movements
        movements_count = db.query(StockMovement).filter(
            StockMovement.batch_id == batch_id,
            StockMovement.branch.has(Branch.tenant_id == tenant_id)
        ).count()
        
        if movements_count > 0 and not force:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Batch has {movements_count} stock movement(s). Use force=True to delete anyway."
            )
        
        # Update stock to remove this batch's quantity
        if not force and batch.remaining_quantity > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Batch has {batch.remaining_quantity} remaining quantity. Please adjust stock first or use force=True."
            )
        
        db.delete(batch)
        db.commit()
        
        return None
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete batch: {str(e)}"
        )


@router.post("/{batch_id}/adjust", response_model=Batch)
async def adjust_batch_quantity(
    batch_id: int,
    request: Request,
    adjustment: Decimal = Query(..., description="Adjustment amount (positive to add, negative to remove)"),
    reason: str = Query(..., description="Reason for adjustment"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value, UserRole.SALESMAN.value]))
):
    """
    Adjust batch quantity (add or remove).
    
    - **batch_id**: The batch ID
    - **adjustment**: Amount to adjust (positive = add, negative = remove)
    - **reason**: Reason for adjustment
    
    Returns the updated batch.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        batch = BatchService.get_batch(db, batch_id, tenant_id)
        if not batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Batch with id {batch_id} not found in this tenant"
            )
        
        new_quantity = batch.remaining_quantity + adjustment
        
        if new_quantity < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot remove more than available. Available: {batch.remaining_quantity}"
            )
        
        batch.remaining_quantity = new_quantity
        
        # Record stock movement
        movement = StockMovement(
            branch_id=batch.branch_id,
            product_id=batch.product_id,
            user_id=current_user.id,
            batch_id=batch_id,
            change_qty=adjustment,
            movement_type=MovementType.ADJUSTMENT.value,
            notes=f"Batch adjustment: {reason}"
        )
        db.add(movement)
        
        # Update main stock
        stock = StockService.get_stock(db, batch.branch_id, batch.product_id, tenant_id)
        if stock:
            stock.quantity += adjustment
            db.add(stock)
        
        db.commit()
        db.refresh(batch)
        
        # Add product name
        product = ProductService.get_product(db, batch.product_id, tenant_id)
        if product:
            batch.product_name = product.name
        
        return batch
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to adjust batch: {str(e)}"
        )


@router.get("/product/{product_id}/available", response_model=List[Batch])
async def get_available_batches_for_product(
    product_id: int,
    request: Request,
    branch_id: int = Query(..., description="Branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all available (non-expired, positive quantity) batches for a product.
    
    - **product_id**: The product ID
    - **branch_id**: The branch ID
    
    Returns available batches sorted by expiry date (earliest first).
    """
    try:
        tenant_id = get_current_tenant(request)
        
        product = ProductService.get_product(db, product_id, tenant_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with id {product_id} not found in this tenant"
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
        
        batches = db.query(Batch).filter(
            Batch.tenant_id == tenant_id,
            Batch.product_id == product_id,
            Batch.branch_id == branch_id,
            Batch.remaining_quantity > 0,
            or_(Batch.expiry_date.is_(None), Batch.expiry_date > datetime.now())
        ).order_by(Batch.expiry_date.asc()).all()
        
        return batches
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve available batches: {str(e)}"
        )


@router.get("/summary/low-stock", response_model=List[dict])
async def get_low_stock_batches(
    request: Request,
    threshold: int = Query(10, description="Threshold for low stock", ge=1),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get batches with low remaining quantity.
    
    - **threshold**: Quantity threshold (default: 10)
    - **branch_id**: Optional branch filter
    
    Returns batches with quantity <= threshold.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(Batch).filter(
            Batch.tenant_id == tenant_id,
            Batch.remaining_quantity <= threshold,
            Batch.remaining_quantity > 0,
            or_(Batch.expiry_date.is_(None), Batch.expiry_date > datetime.now())
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
            query = query.filter(Batch.branch_id == branch_id)
        
        batches = query.order_by(Batch.remaining_quantity.asc()).all()
        
        result = []
        for batch in batches:
            product = ProductService.get_product(db, batch.product_id, tenant_id)
            result.append({
                "id": batch.id,
                "batch_number": batch.batch_number,
                "product_id": batch.product_id,
                "product_name": product.name if product else "Unknown",
                "remaining_quantity": float(batch.remaining_quantity),
                "expiry_date": batch.expiry_date,
                "branch_id": batch.branch_id
            })
        
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve low stock batches: {str(e)}"
        )


@router.get("/summary/by-product", response_model=List[dict])
async def get_batch_summary_by_product(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get batch summary grouped by product.
    
    - **branch_id**: Optional branch filter
    
    Returns summary of batches per product.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(Batch).filter(Batch.tenant_id == tenant_id)
        
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
            query = query.filter(Batch.branch_id == branch_id)
        
        batches = query.filter(Batch.remaining_quantity > 0).all()
        
        summary = {}
        for batch in batches:
            if batch.product_id not in summary:
                product = ProductService.get_product(db, batch.product_id, tenant_id)
                summary[batch.product_id] = {
                    "product_id": batch.product_id,
                    "product_name": product.name if product else "Unknown",
                    "total_quantity": 0,
                    "batch_count": 0,
                    "batches": []
                }
            
            summary[batch.product_id]["total_quantity"] += float(batch.remaining_quantity)
            summary[batch.product_id]["batch_count"] += 1
            summary[batch.product_id]["batches"].append({
                "batch_id": batch.id,
                "batch_number": batch.batch_number,
                "quantity": float(batch.remaining_quantity),
                "expiry_date": batch.expiry_date,
                "unit_cost": float(batch.unit_cost)
            })
        
        return list(summary.values())
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve batch summary: {str(e)}"
        )


# ==================== HELPER FUNCTIONS ====================

def get_batch_by_number_and_branch(db: Session, tenant_id: int, batch_number: str, branch_id: int) -> Optional[Batch]:
    """
    Get a batch by its number and branch ID within a tenant.
    """
    return db.query(Batch).filter(
        Batch.tenant_id == tenant_id,
        Batch.batch_number == batch_number,
        Batch.branch_id == branch_id
    ).first()


def get_expired_batches_count(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> int:
    """
    Get count of expired batches within a tenant.
    """
    query = db.query(Batch).filter(
        Batch.tenant_id == tenant_id,
        Batch.expiry_date.isnot(None),
        Batch.expiry_date <= datetime.now()
    )
    if branch_id:
        query = query.filter(Batch.branch_id == branch_id)
    return query.count()


def get_expiring_soon_count(db: Session, tenant_id: int, days: int = 30, branch_id: Optional[int] = None) -> int:
    """
    Get count of batches expiring soon within a tenant.
    """
    now = datetime.now()
    threshold = now + timedelta(days=days)
    
    query = db.query(Batch).filter(
        Batch.tenant_id == tenant_id,
        Batch.expiry_date.isnot(None),
        Batch.expiry_date <= threshold,
        Batch.expiry_date > now,
        Batch.remaining_quantity > 0
    )
    if branch_id:
        query = query.filter(Batch.branch_id == branch_id)
    return query.count()