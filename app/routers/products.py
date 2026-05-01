from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func
from typing import List, Optional
from decimal import Decimal
from app.database import get_db
from app.services import ProductService, CategoryService, UnitService
from app.schemas import Product, ProductCreate, ProductUpdate
from app.utils.auth import get_current_user, require_role, get_current_active_user, get_current_tenant, verify_branch_access
from app.models import User, UserRole, Category, Unit, Stock, Product as ProductModel

router = APIRouter(prefix="/products", tags=["Products"])


# ==================== READ OPERATIONS (Any authenticated user) ====================

@router.get("/", response_model=List[Product])
async def get_products(
    request: Request,
    active: Optional[bool] = Query(True, description="Filter by active status"),
    category_id: Optional[int] = Query(None, description="Filter by category ID"),
    unit_id: Optional[int] = Query(None, description="Filter by unit ID"),
    branch_id: Optional[int] = Query(None, description="Get stock quantity for this branch"),
    search: Optional[str] = Query(None, description="Search by name, SKU, or barcode"),
    low_stock_only: bool = Query(False, description="Show only products with low stock"),
    has_expiry: Optional[bool] = Query(None, description="Filter products with expiry tracking"),
    track_batch: Optional[bool] = Query(None, description="Filter products with batch tracking"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of records"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get all products with optional filters - Any authenticated user.
    
    - **active**: Filter by active status
    - **category_id**: Filter by category
    - **unit_id**: Filter by unit
    - **branch_id**: Get stock quantity for specific branch
    - **search**: Search by name, SKU, or barcode
    - **low_stock_only**: Show only products below reorder level
    - **has_expiry**: Filter products with expiry dates
    - **track_batch**: Filter products with batch tracking
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(ProductModel).filter(ProductModel.tenant_id == tenant_id)
        
        # Apply filters
        if active is not None:
            query = query.filter(ProductModel.active == active)
        
        if category_id:
            # Check if category exists in this tenant
            category = CategoryService.get_category(db, category_id, tenant_id)
            if not category:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Category with id {category_id} not found in this tenant"
                )
            query = query.filter(ProductModel.category_id == category_id)
        
        if unit_id:
            unit = UnitService.get_unit(db, unit_id, tenant_id)
            if not unit:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Unit with id {unit_id} not found in this tenant"
                )
            query = query.filter(ProductModel.unit_id == unit_id)
        
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    ProductModel.name.ilike(search_term),
                    ProductModel.sku.ilike(search_term),
                    ProductModel.barcode.ilike(search_term)
                )
            )
        
        if has_expiry is not None:
            query = query.filter(ProductModel.has_expiry == has_expiry)
        
        if track_batch is not None:
            query = query.filter(ProductModel.track_batch == track_batch)
        
        products = query.order_by(ProductModel.name).offset(skip).limit(limit).all()
        
        # Add stock information if branch_id provided
        if branch_id:
            # Check branch access
            if not verify_branch_access(current_user, branch_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have access to this branch"
                )
            
            for product in products:
                stock = db.query(Stock).filter(
                    Stock.branch_id == branch_id,
                    Stock.product_id == product.id
                ).first()
                
                if stock:
                    product.stock_quantity = stock.quantity
                    product.reorder_level = stock.reorder_level
                else:
                    product.stock_quantity = Decimal(0)
                    product.reorder_level = Decimal(0)
        
        # Filter low stock if requested (requires branch_id)
        if low_stock_only and branch_id:
            products = [
                p for p in products 
                if p.stock_quantity <= p.reorder_level and p.stock_quantity > 0
            ]
        elif low_stock_only and not branch_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="branch_id required for low_stock_only filter"
            )
        
        return products
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve products: {str(e)}"
        )


@router.get("/{product_id}", response_model=Product)
async def get_product(
    product_id: int,
    request: Request,
    branch_id: Optional[int] = Query(None, description="Get stock quantity for this branch"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get product details by ID - Any authenticated user.
    
    - **product_id**: The ID of the product to retrieve
    - **branch_id**: Optional branch ID to get stock quantity
    """
    try:
        tenant_id = get_current_tenant(request)
        
        product = ProductService.get_product(db, product_id, tenant_id)
        
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with id {product_id} not found in this tenant"
            )
        
        # Add stock information if branch_id provided
        if branch_id:
            if not verify_branch_access(current_user, branch_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have access to this branch"
                )
            
            stock = db.query(Stock).filter(
                Stock.branch_id == branch_id,
                Stock.product_id == product.id
            ).first()
            
            if stock:
                product.stock_quantity = stock.quantity
                product.reorder_level = stock.reorder_level
            else:
                product.stock_quantity = Decimal(0)
                product.reorder_level = Decimal(0)
        
        return product
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve product: {str(e)}"
        )


@router.get("/by-sku/{sku}", response_model=Product)
async def get_product_by_sku(
    sku: str,
    request: Request,
    branch_id: Optional[int] = Query(None, description="Get stock quantity for this branch"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get product by SKU - Any authenticated user.
    
    - **sku**: The SKU of the product to retrieve
    - **branch_id**: Optional branch ID to get stock quantity
    """
    try:
        tenant_id = get_current_tenant(request)
        
        product = db.query(ProductModel).filter(
            ProductModel.tenant_id == tenant_id,
            ProductModel.sku == sku
        ).first()
        
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with SKU '{sku}' not found in this tenant"
            )
        
        # Add stock information if branch_id provided
        if branch_id:
            if not verify_branch_access(current_user, branch_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have access to this branch"
                )
            
            stock = db.query(Stock).filter(
                Stock.branch_id == branch_id,
                Stock.product_id == product.id
            ).first()
            
            if stock:
                product.stock_quantity = stock.quantity
                product.reorder_level = stock.reorder_level
        
        return product
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve product: {str(e)}"
        )


@router.get("/by-barcode/{barcode}", response_model=Product)
async def get_product_by_barcode(
    barcode: str,
    request: Request,
    branch_id: Optional[int] = Query(None, description="Get stock quantity for this branch"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get product by barcode - Any authenticated user.
    
    - **barcode**: The barcode of the product to retrieve
    - **branch_id**: Optional branch ID to get stock quantity
    """
    try:
        tenant_id = get_current_tenant(request)
        
        product = ProductService.get_product_by_barcode(db, barcode, tenant_id)
        
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with barcode '{barcode}' not found in this tenant"
            )
        
        # Add stock information if branch_id provided
        if branch_id:
            if not verify_branch_access(current_user, branch_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have access to this branch"
                )
            
            stock = db.query(Stock).filter(
                Stock.branch_id == branch_id,
                Stock.product_id == product.id
            ).first()
            
            if stock:
                product.stock_quantity = stock.quantity
                product.reorder_level = stock.reorder_level
        
        return product
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve product: {str(e)}"
        )


# ==================== WRITE OPERATIONS ====================

@router.post("/", response_model=Product, status_code=status.HTTP_201_CREATED)
async def create_product(
    product: ProductCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Create a new product (Super Admin, Tenant Admin, or Manager only).
    
    - **sku**: Unique SKU (required)
    - **name**: Product name (required)
    - **category_id**: Category ID (optional)
    - **unit_id**: Unit ID (optional)
    - **barcode**: Product barcode (optional, unique)
    - **price**: Selling price (required)
    - **cost**: Cost price (required)
    - **has_expiry**: Whether product has expiry date
    - **track_batch**: Whether to track batches
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Validate category if provided
        if product.category_id:
            category = CategoryService.get_category(db, product.category_id, tenant_id)
            if not category:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Category with id {product.category_id} not found in this tenant"
                )
        
        # Validate unit if provided
        if product.unit_id:
            unit = UnitService.get_unit(db, product.unit_id, tenant_id)
            if not unit:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Unit with id {product.unit_id} not found in this tenant"
                )
        
        return ProductService.create_product(db, product, tenant_id)
        
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
            detail=f"Failed to create product: {str(e)}"
        )


@router.put("/{product_id}", response_model=Product)
async def update_product(
    product_id: int,
    product: ProductUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Update product (Super Admin, Tenant Admin, or Manager only).
    
    - **product_id**: The ID of the product to update
    - Any field can be updated
    """
    try:
        tenant_id = get_current_tenant(request)
        
        existing = ProductService.get_product(db, product_id, tenant_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with id {product_id} not found in this tenant"
            )
        
        # Validate category if provided
        if product.category_id:
            category = CategoryService.get_category(db, product.category_id, tenant_id)
            if not category:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Category with id {product.category_id} not found in this tenant"
                )
        
        # Validate unit if provided
        if product.unit_id:
            unit = UnitService.get_unit(db, product.unit_id, tenant_id)
            if not unit:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Unit with id {product.unit_id} not found in this tenant"
                )
        
        updated_product = ProductService.update_product(db, product_id, tenant_id, product)
        
        return updated_product
        
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
            detail=f"Failed to update product: {str(e)}"
        )


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: int,
    request: Request,
    force: bool = Query(False, description="Force delete even if product has stock or sales"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Delete product (Super Admin or Tenant Admin only).
    
    - **product_id**: The ID of the product to delete
    - **force**: If True, delete even if product has associated data
    
    Cannot delete product with existing stock, sales, or loans unless force=True.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        product = ProductService.get_product(db, product_id, tenant_id)
        
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with id {product_id} not found in this tenant"
            )
        
        # Check for associated data within tenant
        from app.models import Stock, SaleItem, LoanItem, PurchaseOrderItem, Batch
        
        stock_count = db.query(Stock).filter(
            Stock.product_id == product_id,
            Stock.branch.has(Branch.tenant_id == tenant_id)
        ).count()
        
        sale_items_count = db.query(SaleItem).filter(
            SaleItem.product_id == product_id,
            SaleItem.sale.has(Sale.tenant_id == tenant_id)
        ).count()
        
        loan_items_count = db.query(LoanItem).filter(
            LoanItem.product_id == product_id,
            LoanItem.loan.has(Loan.tenant_id == tenant_id)
        ).count()
        
        po_items_count = db.query(PurchaseOrderItem).filter(
            PurchaseOrderItem.product_id == product_id,
            PurchaseOrderItem.purchase_order.has(PurchaseOrder.tenant_id == tenant_id)
        ).count()
        
        batches_count = db.query(Batch).filter(
            Batch.product_id == product_id,
            Batch.tenant_id == tenant_id
        ).count()
        
        has_associated_data = stock_count > 0 or sale_items_count > 0 or loan_items_count > 0 or po_items_count > 0 or batches_count > 0
        
        if has_associated_data and not force:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete product with associated data. Stock: {stock_count}, Sales: {sale_items_count}, Loans: {loan_items_count}. Use force=True to delete anyway."
            )
        
        if force and has_associated_data:
            # Delete associated batches
            if batches_count > 0:
                db.query(Batch).filter(Batch.product_id == product_id, Batch.tenant_id == tenant_id).delete()
            
            # Delete associated stock
            if stock_count > 0:
                db.query(Stock).filter(Stock.product_id == product_id).delete()
            
            # Delete associated sale items (will cascade)
            if sale_items_count > 0:
                db.query(SaleItem).filter(SaleItem.product_id == product_id).delete()
            
            # Delete associated loan items
            if loan_items_count > 0:
                db.query(LoanItem).filter(LoanItem.product_id == product_id).delete()
        
        success = ProductService.delete_product(db, product_id, tenant_id)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete product"
            )
        
        return None
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete product: {str(e)}"
        )


# ==================== BULK OPERATIONS ====================

@router.post("/bulk", response_model=dict, status_code=status.HTTP_201_CREATED)
async def bulk_create_products(
    products_data: List[ProductCreate],
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Bulk create multiple products (Super Admin or Tenant Admin only).
    
    - **products_data**: List of product creation data
    
    Creates multiple products in a single request.
    """
    try:
        tenant_id = get_current_tenant(request)
        created_products = []
        errors = []
        
        for product_data in products_data:
            try:
                # Validate category if provided
                if product_data.category_id:
                    category = CategoryService.get_category(db, product_data.category_id, tenant_id)
                    if not category:
                        errors.append(f"Category {product_data.category_id} not found for product {product_data.sku}")
                        continue
                
                product = ProductService.create_product(db, product_data, tenant_id)
                created_products.append(product)
                
            except ValueError as e:
                errors.append(f"Failed to create {product_data.sku}: {str(e)}")
            except Exception as e:
                errors.append(f"Error creating {product_data.sku}: {str(e)}")
        
        db.commit()
        
        return {
            "created": created_products,
            "created_count": len(created_products),
            "errors": errors
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to bulk create products: {str(e)}"
        )


@router.post("/{product_id}/toggle-active", response_model=Product)
async def toggle_product_active(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Toggle product active status (Super Admin, Tenant Admin, or Manager only).
    
    - **product_id**: The ID of the product to toggle
    """
    try:
        tenant_id = get_current_tenant(request)
        
        product = ProductService.get_product(db, product_id, tenant_id)
        
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with id {product_id} not found in this tenant"
            )
        
        product.active = not product.active
        db.commit()
        db.refresh(product)
        
        return product
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to toggle product status: {str(e)}"
        )


# ==================== STATISTICS ENDPOINTS ====================

@router.get("/stats/summary")
async def get_product_statistics(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Branch ID for stock statistics"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get product statistics.
    
    - **branch_id**: Optional branch for branch-specific statistics
    
    Returns counts of products by various categories.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        total_products = db.query(ProductModel).filter(ProductModel.tenant_id == tenant_id).count()
        active_products = db.query(ProductModel).filter(
            ProductModel.tenant_id == tenant_id,
            ProductModel.active == True
        ).count()
        inactive_products = total_products - active_products
        
        # Products with expiry tracking
        expiry_products = db.query(ProductModel).filter(
            ProductModel.tenant_id == tenant_id,
            ProductModel.has_expiry == True
        ).count()
        
        # Products with batch tracking
        batch_products = db.query(ProductModel).filter(
            ProductModel.tenant_id == tenant_id,
            ProductModel.track_batch == True
        ).count()
        
        # Products by category
        products_by_category = db.query(
            Category.name,
            func.count(ProductModel.id).label('count')
        ).outerjoin(
            ProductModel, Category.id == ProductModel.category_id
        ).filter(
            Category.tenant_id == tenant_id
        ).group_by(Category.id).all()
        
        # Stock statistics if branch_id provided
        stock_stats = {}
        if branch_id:
            if not verify_branch_access(current_user, branch_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have access to this branch"
                )
            
            total_stock_value = db.query(func.sum(Stock.quantity * ProductModel.cost)).join(
                ProductModel, Stock.product_id == ProductModel.id
            ).filter(
                ProductModel.tenant_id == tenant_id,
                Stock.branch_id == branch_id
            ).scalar() or Decimal(0)
            
            low_stock_count = db.query(Stock).join(ProductModel).filter(
                ProductModel.tenant_id == tenant_id,
                Stock.branch_id == branch_id,
                Stock.quantity <= Stock.reorder_level,
                Stock.quantity > 0
            ).count()
            
            out_of_stock_count = db.query(Stock).join(ProductModel).filter(
                ProductModel.tenant_id == tenant_id,
                Stock.branch_id == branch_id,
                Stock.quantity == 0
            ).count()
            
            stock_stats = {
                "total_stock_value": float(total_stock_value),
                "low_stock_count": low_stock_count,
                "out_of_stock_count": out_of_stock_count
            }
        
        return {
            "total_products": total_products,
            "active_products": active_products,
            "inactive_products": inactive_products,
            "expiry_tracking_products": expiry_products,
            "batch_tracking_products": batch_products,
            "products_by_category": [
                {"category": cat_name or "Uncategorized", "count": count}
                for cat_name, count in products_by_category
            ],
            **stock_stats
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve product statistics: {str(e)}"
        )


# ==================== HELPER FUNCTIONS ====================

def search_products_by_name(db: Session, tenant_id: int, name: str, limit: int = 10) -> List[ProductModel]:
    """Search products by name (partial match) within a tenant"""
    return db.query(ProductModel).filter(
        ProductModel.tenant_id == tenant_id,
        ProductModel.name.ilike(f"%{name}%"),
        ProductModel.active == True
    ).limit(limit).all()


def get_products_by_category(db: Session, tenant_id: int, category_id: int) -> List[ProductModel]:
    """Get all products in a category (including subcategories) within a tenant"""
    # Get all subcategory IDs
    from app.routers.categories import get_all_descendant_categories
    category_ids = [category_id] + get_all_descendant_categories(db, tenant_id, category_id)
    
    return db.query(ProductModel).filter(
        ProductModel.tenant_id == tenant_id,
        ProductModel.category_id.in_(category_ids),
        ProductModel.active == True
    ).all()