from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import List, Optional
from datetime import datetime
from decimal import Decimal

from app.database import get_db
from app.models import User, Stock, Product, Branch, StockMovement, Batch, UserRole
from app.schemas import StockResponse, StockMovementResponse, StockCreate, StockUpdate, BatchCreate
from app.services import StockService, ProductService, BatchService
from app.utils.auth import get_current_user, require_role, verify_branch_access, get_user_branch_id, get_current_tenant

router = APIRouter(prefix="/stock", tags=["Stock"])


# ==================== STOCK RETRIEVAL ====================

@router.get("/branch/{branch_id}", response_model=List[StockResponse])
async def get_branch_stock(
    branch_id: int,
    request: Request,
    low_stock: bool = Query(False, description="Show only low stock items"),
    category_id: Optional[int] = Query(None, description="Filter by category"),
    search: Optional[str] = Query(None, description="Search by product name or SKU"),
    include_batches: bool = Query(False, description="Include batch information"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get stock for a specific branch.
    
    - **branch_id**: Branch ID
    - **low_stock**: Filter to show only items below reorder level
    - **category_id**: Filter by product category
    - **search**: Search by product name or SKU
    - **include_batches**: Include batch details for batch-tracked products
    
    Admin can view any branch, others can only view their assigned branch.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check branch exists in this tenant
        branch = db.query(Branch).filter(
            Branch.id == branch_id,
            Branch.tenant_id == tenant_id
        ).first()
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Branch with id {branch_id} not found in this tenant"
            )
        
        # Permission check
        if not verify_branch_access(current_user, branch_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this branch"
            )
        
        # Build query with tenant filtering
        query = db.query(Stock).filter(
            Stock.branch_id == branch_id,
            Stock.product.has(Product.tenant_id == tenant_id)
        )
        
        # Join with product for filtering
        if category_id or search:
            query = query.join(Product)
            
            if category_id:
                query = query.filter(Product.category_id == category_id)
            
            if search:
                search_term = f"%{search}%"
                query = query.filter(
                    (Product.name.ilike(search_term)) | (Product.sku.ilike(search_term))
                )
        
        stocks = query.all()
        
        result = []
        for stock in stocks:
            product = stock.product
            if not product:
                continue
            
            # Determine status
            if stock.quantity <= 0:
                status_val = "out_of_stock"
            elif stock.quantity <= stock.reorder_level:
                status_val = "low"
            else:
                status_val = "normal"
            
            # Apply low stock filter
            if low_stock and status_val != "low":
                continue
            
            stock_response = {
                "product_id": product.id,
                "product_name": product.name,
                "product_sku": product.sku,
                "quantity": float(stock.quantity),
                "reorder_level": float(stock.reorder_level),
                "status": status_val
            }
            
            # Include batch information if requested
            if include_batches and product.track_batch:
                batches = db.query(Batch).filter(
                    Batch.tenant_id == tenant_id,
                    Batch.product_id == product.id,
                    Batch.branch_id == branch_id,
                    Batch.remaining_quantity > 0
                ).order_by(Batch.expiry_date.asc()).all()
                
                stock_response["batches"] = [
                    {
                        "id": b.id,
                        "batch_number": b.batch_number,
                        "quantity": float(b.remaining_quantity),
                        "expiry_date": b.expiry_date.isoformat() if b.expiry_date else None,
                        "unit_cost": float(b.unit_cost)
                    }
                    for b in batches
                ]
            
            result.append(stock_response)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve branch stock: {str(e)}"
        )


@router.get("/my", response_model=List[StockResponse])
async def get_my_branch_stock(
    request: Request,
    low_stock: bool = Query(False, description="Show only low stock items"),
    category_id: Optional[int] = Query(None, description="Filter by category"),
    search: Optional[str] = Query(None, description="Search by product name or SKU"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get stock for the current user's branch.
    
    Returns stock information for the branch the user is assigned to.
    """
    try:
        if not current_user.branch_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User not assigned to a branch"
            )
        
        return await get_branch_stock(
            current_user.branch_id, request, low_stock, category_id, search, False, db, current_user
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve branch stock: {str(e)}"
        )


@router.get("/product/{product_id}", response_model=List[StockResponse])
async def get_product_stock_across_branches(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get stock for a specific product across all branches (Admin/Manager only).
    
    - **product_id**: The product ID
    
    Returns stock information for the product in all branches.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        product = ProductService.get_product(db, product_id, tenant_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with id {product_id} not found in this tenant"
            )
        
        stocks = db.query(Stock).filter(
            Stock.product_id == product_id,
            Stock.branch.has(Branch.tenant_id == tenant_id)
        ).all()
        
        result = []
        for stock in stocks:
            branch = db.query(Branch).filter(Branch.id == stock.branch_id).first()
            
            if stock.quantity <= 0:
                status_val = "out_of_stock"
            elif stock.quantity <= stock.reorder_level:
                status_val = "low"
            else:
                status_val = "normal"
            
            result.append({
                "product_id": product.id,
                "product_name": product.name,
                "product_sku": product.sku,
                "branch_id": stock.branch_id,
                "branch_name": branch.name if branch else "Unknown",
                "quantity": float(stock.quantity),
                "reorder_level": float(stock.reorder_level),
                "status": status_val
            })
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve product stock: {str(e)}"
        )


# ==================== STOCK MOVEMENTS ====================

@router.get("/movements", response_model=List[StockMovementResponse])
async def get_stock_movements(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch"),
    product_id: Optional[int] = Query(None, description="Filter by product"),
    movement_type: Optional[str] = Query(None, description="Filter by movement type"),
    start_date: Optional[datetime] = Query(None, description="Start date"),
    end_date: Optional[datetime] = Query(None, description="End date"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get stock movement history (Admin/Manager only).
    
    Returns a log of all stock changes with optional filters.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(StockMovement).join(
            Product, StockMovement.product_id == Product.id
        ).filter(Product.tenant_id == tenant_id)
        
        # Apply branch filter
        if branch_id:
            if not verify_branch_access(current_user, branch_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to view this branch"
                )
            query = query.filter(StockMovement.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            query = query.filter(StockMovement.branch_id == current_user.branch_id)
        
        if product_id:
            query = query.filter(StockMovement.product_id == product_id)
        
        if movement_type:
            query = query.filter(StockMovement.movement_type == movement_type)
        
        if start_date:
            query = query.filter(StockMovement.created_at >= start_date)
        
        if end_date:
            query = query.filter(StockMovement.created_at <= end_date)
        
        movements = query.order_by(StockMovement.created_at.desc()).limit(limit).all()
        
        result = []
        for movement in movements:
            product = ProductService.get_product(db, movement.product_id, tenant_id)
            user = db.query(User).filter(User.id == movement.user_id).first()
            batch = None
            if movement.batch_id:
                batch = BatchService.get_batch(db, movement.batch_id, tenant_id)
            
            result.append({
                "id": movement.id,
                "product_id": movement.product_id,
                "product_name": product.name if product else "Unknown",
                "branch_id": movement.branch_id,
                "change_qty": float(movement.change_qty),
                "movement_type": movement.movement_type,
                "reference_id": movement.reference_id,
                "batch_id": movement.batch_id,
                "batch_number": batch.batch_number if batch else None,
                "notes": movement.notes,
                "user_id": movement.user_id,
                "user_name": user.name if user else "Unknown",
                "created_at": movement.created_at
            })
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve stock movements: {str(e)}"
        )


# ==================== STOCK OPERATIONS ====================

@router.post("/add/{branch_id}/{product_id}")
async def add_stock(
    branch_id: int,
    product_id: int,
    request: Request,
    quantity: float = Query(..., gt=0, description="Quantity to add"),
    batch_number: Optional[str] = Query(None, description="Batch number (for tracked products)"),
    expiry_date: Optional[datetime] = Query(None, description="Expiry date (for tracked products)"),
    unit_cost: Optional[float] = Query(None, description="Unit cost (for new batch)"),
    notes: Optional[str] = Query(None, description="Optional notes"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Add stock to a branch.
    
    - **branch_id**: Branch ID
    - **product_id**: Product ID
    - **quantity**: Quantity to add (positive number)
    - **batch_number**: Batch number for batch-tracked products
    - **expiry_date**: Expiry date for products with expiry tracking
    - **unit_cost**: Unit cost for new batch
    - **notes**: Optional notes
    
    Admin can add to any branch, others can only add to their assigned branch.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check branch exists in this tenant
        branch = db.query(Branch).filter(
            Branch.id == branch_id,
            Branch.tenant_id == tenant_id
        ).first()
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Branch with id {branch_id} not found in this tenant"
            )
        
        # Check product exists in this tenant
        product = ProductService.get_product(db, product_id, tenant_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with id {product_id} not found in this tenant"
            )
        
        # Permission check
        if not verify_branch_access(current_user, branch_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to add stock to this branch"
            )
        
        quantity_decimal = Decimal(str(quantity))
        
        # Handle batch tracking
        batch_id = None
        if product.track_batch:
            if not batch_number:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Batch number required for batch-tracked product"
                )
            
            if product.has_expiry and not expiry_date:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Expiry date required for product with expiry tracking"
                )
            
            if not unit_cost:
                unit_cost = float(product.cost)
            
            # Create batch
            batch_data = BatchCreate(
                product_id=product_id,
                branch_id=branch_id,
                batch_number=batch_number,
                expiry_date=expiry_date,
                quantity=quantity_decimal,
                unit_cost=Decimal(str(unit_cost))
            )
            batch = BatchService.create_batch(db, batch_data, tenant_id)
            batch_id = batch.id
        
        # Update stock
        stock = StockService.add_stock(
            db, branch_id, product_id, quantity_decimal,
            current_user.id, tenant_id, notes or f"Stock added by {current_user.name}",
            batch_id=batch_id
        )
        
        return {
            "success": True,
            "message": f"Added {quantity} units of {product.name}",
            "product_id": product_id,
            "product_name": product.name,
            "branch_id": branch_id,
            "branch_name": branch.name,
            "new_quantity": float(stock.quantity),
            "added_by": current_user.name,
            "batch_id": batch_id,
            "batch_number": batch_number
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add stock: {str(e)}"
        )


@router.put("/adjust/{branch_id}/{product_id}")
async def adjust_stock(
    branch_id: int,
    product_id: int,
    request: Request,
    new_quantity: float = Query(..., ge=0, description="New quantity"),
    reason: Optional[str] = Query(None, description="Reason for adjustment"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Adjust stock to a specific quantity.
    
    - **branch_id**: Branch ID
    - **product_id**: Product ID
    - **new_quantity**: Target quantity (must be >= 0)
    - **reason**: Reason for adjustment
    
    Admin can adjust any branch, others can only adjust their assigned branch.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check branch exists in this tenant
        branch = db.query(Branch).filter(
            Branch.id == branch_id,
            Branch.tenant_id == tenant_id
        ).first()
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Branch with id {branch_id} not found in this tenant"
            )
        
        # Check product exists in this tenant
        product = ProductService.get_product(db, product_id, tenant_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with id {product_id} not found in this tenant"
            )
        
        # Permission check
        if not verify_branch_access(current_user, branch_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to adjust stock for this branch"
            )
        
        # Get stock record
        stock = db.query(Stock).filter(
            Stock.branch_id == branch_id,
            Stock.product_id == product_id
        ).first()
        
        if not stock:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Stock record not found"
            )
        
        old_quantity = stock.quantity
        quantity_change = Decimal(str(new_quantity)) - stock.quantity
        
        # Update stock
        stock.quantity = Decimal(str(new_quantity))
        
        # Record stock movement
        stock_movement = StockMovement(
            branch_id=branch_id,
            product_id=product_id,
            user_id=current_user.id,
            change_qty=quantity_change,
            movement_type="adjustment",
            notes=reason or f"Stock adjusted by {current_user.name}"
        )
        db.add(stock_movement)
        
        db.commit()
        db.refresh(stock)
        
        return {
            "success": True,
            "message": f"Adjusted {product.name} stock to {new_quantity} units",
            "product_id": product_id,
            "product_name": product.name,
            "branch_id": branch_id,
            "branch_name": branch.name,
            "old_quantity": float(old_quantity),
            "new_quantity": float(stock.quantity),
            "change": float(quantity_change),
            "adjusted_by": current_user.name
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to adjust stock: {str(e)}"
        )


@router.post("/transfer")
async def transfer_stock(
    request: Request,
    from_branch_id: int = Query(..., description="Source branch ID"),
    to_branch_id: int = Query(..., description="Destination branch ID"),
    product_id: int = Query(..., description="Product ID"),
    quantity: float = Query(..., gt=0, description="Quantity to transfer"),
    notes: Optional[str] = Query(None, description="Transfer notes"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Transfer stock between branches (Admin/Manager only).
    
    - **from_branch_id**: Source branch
    - **to_branch_id**: Destination branch
    - **product_id**: Product to transfer
    - **quantity**: Quantity to transfer
    - **notes**: Optional transfer notes
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check branches exist in this tenant
        from_branch = db.query(Branch).filter(
            Branch.id == from_branch_id,
            Branch.tenant_id == tenant_id
        ).first()
        if not from_branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Source branch {from_branch_id} not found in this tenant"
            )
        
        to_branch = db.query(Branch).filter(
            Branch.id == to_branch_id,
            Branch.tenant_id == tenant_id
        ).first()
        if not to_branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Destination branch {to_branch_id} not found in this tenant"
            )
        
        # Check product exists in this tenant
        product = ProductService.get_product(db, product_id, tenant_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with id {product_id} not found in this tenant"
            )
        
        quantity_decimal = Decimal(str(quantity))
        
        # Check sufficient stock in source branch
        from_stock = StockService.get_stock(db, from_branch_id, product_id, tenant_id)
        if not from_stock or from_stock.quantity < quantity_decimal:
            available = float(from_stock.quantity) if from_stock else 0
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient stock in {from_branch.name}. Available: {available}, Requested: {quantity}"
            )
        
        # Perform transfer
        result = StockService.transfer_stock(
            db, from_branch_id, to_branch_id, product_id,
            quantity_decimal, current_user.id, tenant_id
        )
        
        return {
            "success": True,
            "message": f"Transferred {quantity} units of {product.name} from {from_branch.name} to {to_branch.name}",
            "product_id": product_id,
            "product_name": product.name,
            "from_branch": from_branch.name,
            "to_branch": to_branch.name,
            "quantity": quantity,
            "from_branch_new_quantity": float(result["from_branch"].quantity),
            "to_branch_new_quantity": float(result["to_branch"].quantity),
            "transferred_by": current_user.name
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to transfer stock: {str(e)}"
        )


@router.post("/initialize/{branch_id}")
async def initialize_branch_stock(
    branch_id: int,
    request: Request,
    reorder_level: float = Query(10, ge=0, description="Default reorder level"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Initialize stock records for all active products in a branch.
    
    - **branch_id**: Branch ID
    - **reorder_level**: Default reorder level for new stock records
    
    Creates stock records for products that don't have them yet.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check branch exists in this tenant
        branch = db.query(Branch).filter(
            Branch.id == branch_id,
            Branch.tenant_id == tenant_id
        ).first()
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Branch with id {branch_id} not found in this tenant"
            )
        
        # Permission check
        if not verify_branch_access(current_user, branch_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to initialize stock for this branch"
            )
        
        # Get all active products in this tenant
        products = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.active == True
        ).all()
        
        created_count = 0
        skipped_count = 0
        
        for product in products:
            existing = db.query(Stock).filter(
                Stock.branch_id == branch_id,
                Stock.product_id == product.id
            ).first()
            
            if not existing:
                stock = Stock(
                    branch_id=branch_id,
                    product_id=product.id,
                    quantity=Decimal(0),
                    reorder_level=Decimal(str(reorder_level))
                )
                db.add(stock)
                created_count += 1
            else:
                skipped_count += 1
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Initialized stock for {created_count} products in branch {branch.name}",
            "branch_id": branch_id,
            "branch_name": branch.name,
            "products_initialized": created_count,
            "products_already_existing": skipped_count,
            "initialized_by": current_user.name
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialize branch stock: {str(e)}"
        )


@router.put("/reorder-level/{branch_id}/{product_id}")
async def update_reorder_level(
    branch_id: int,
    product_id: int,
    request: Request,
    reorder_level: float = Query(..., ge=0, description="New reorder level"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Update reorder level for a product in a branch (Admin/Manager only).
    
    - **branch_id**: Branch ID
    - **product_id**: Product ID
    - **reorder_level**: New reorder level
    """
    try:
        tenant_id = get_current_tenant(request)
        
        stock = StockService.update_reorder_level(
            db, branch_id, product_id, Decimal(str(reorder_level)), tenant_id
        )
        
        product = ProductService.get_product(db, product_id, tenant_id)
        
        return {
            "success": True,
            "message": f"Updated reorder level for {product.name} to {reorder_level}",
            "product_id": product_id,
            "product_name": product.name,
            "branch_id": branch_id,
            "reorder_level": reorder_level
        }
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update reorder level: {str(e)}"
        )


# ==================== STOCK SUMMARY ====================

@router.get("/summary/low-stock")
async def get_low_stock_summary(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get low stock summary across branches (Admin/Manager only).
    
    Returns all products that are below their reorder level.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(Stock).join(Product).filter(
            Product.tenant_id == tenant_id,
            Stock.quantity <= Stock.reorder_level,
            Stock.quantity > 0
        )
        
        if branch_id:
            if not verify_branch_access(current_user, branch_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to view this branch"
                )
            query = query.filter(Stock.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            query = query.filter(Stock.branch_id == current_user.branch_id)
        
        stocks = query.all()
        
        result = []
        for stock in stocks:
            product = stock.product
            branch = db.query(Branch).filter(Branch.id == stock.branch_id).first()
            
            result.append({
                "product_id": product.id,
                "product_name": product.name,
                "product_sku": product.sku,
                "branch_id": stock.branch_id,
                "branch_name": branch.name if branch else "Unknown",
                "current_quantity": float(stock.quantity),
                "reorder_level": float(stock.reorder_level),
                "shortage": float(stock.reorder_level - stock.quantity)
            })
        
        return {
            "total_low_stock_items": len(result),
            "items": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve low stock summary: {str(e)}"
        )


@router.get("/summary/out-of-stock")
async def get_out_of_stock_summary(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get out of stock summary across branches (Admin/Manager only).
    
    Returns all products that are out of stock (quantity = 0).
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(Stock).join(Product).filter(
            Product.tenant_id == tenant_id,
            Stock.quantity == 0
        )
        
        if branch_id:
            if not verify_branch_access(current_user, branch_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to view this branch"
                )
            query = query.filter(Stock.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            query = query.filter(Stock.branch_id == current_user.branch_id)
        
        stocks = query.all()
        
        result = []
        for stock in stocks:
            product = stock.product
            branch = db.query(Branch).filter(Branch.id == stock.branch_id).first()
            
            result.append({
                "product_id": product.id,
                "product_name": product.name,
                "product_sku": product.sku,
                "branch_id": stock.branch_id,
                "branch_name": branch.name if branch else "Unknown"
            })
        
        return {
            "total_out_of_stock_items": len(result),
            "items": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve out of stock summary: {str(e)}"
        )


# ==================== HELPER FUNCTIONS ====================

def get_total_stock_value(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> Decimal:
    """Calculate total stock value for a branch within a tenant"""
    query = db.query(func.sum(Stock.quantity * Product.cost)).join(
        Product, Stock.product_id == Product.id
    ).filter(Product.tenant_id == tenant_id)
    
    if branch_id:
        query = query.filter(Stock.branch_id == branch_id)
    
    result = query.scalar()
    return result or Decimal(0)