from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.services import CategoryService
from app.schemas import CategoryCreate, CategoryUpdate, Category
from app.utils.auth import get_current_user, require_role, get_current_tenant
from app.models import User, UserRole, Product

router = APIRouter(prefix="/categories", tags=["Categories"])


@router.post("/", response_model=Category, status_code=status.HTTP_201_CREATED)
async def create_category(
    category_data: CategoryCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Create a new category.
    
    - **name**: Category name (required)
    - **description**: Optional description
    - **parent_id**: Optional parent category ID for subcategories
    
    Only admin, tenant admin, and manager can create categories.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check if parent category exists if parent_id is provided
        if category_data.parent_id:
            parent_category = CategoryService.get_category(db, category_data.parent_id, tenant_id)
            if not parent_category:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Parent category with id {category_data.parent_id} not found in this tenant"
                )
        
        category = CategoryService.create_category(db, category_data, tenant_id)
        return category
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create category: {str(e)}"
        )


@router.get("/", response_model=List[Category])
async def get_categories(
    request: Request,
    parent_id: Optional[int] = Query(None, description="Filter by parent category ID"),
    include_subcategories: bool = Query(True, description="Include subcategories in response"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all categories.
    
    - **parent_id**: Filter categories by parent (null for root categories)
    - **include_subcategories**: If true, includes subcategories in the response
    
    Returns a list of categories within the current tenant.
    """
    try:
        tenant_id = get_current_tenant(request)
        categories = CategoryService.get_categories(db, tenant_id, parent_id)
        
        if include_subcategories and parent_id is None:
            # Build hierarchical structure
            category_dict = {cat.id: cat for cat in categories}
            root_categories = []
            
            for cat in categories:
                if cat.parent_id:
                    parent = category_dict.get(cat.parent_id)
                    if parent:
                        if not hasattr(parent, 'subcategories'):
                            parent.subcategories = []
                        parent.subcategories.append(cat)
                else:
                    root_categories.append(cat)
            
            return root_categories
        
        return categories
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve categories: {str(e)}"
        )


@router.get("/tree", response_model=List[Category])
async def get_category_tree(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get full category tree (hierarchical structure).
    
    Returns categories organized in a tree structure with parent-child relationships.
    """
    try:
        tenant_id = get_current_tenant(request)
        all_categories = CategoryService.get_categories(db, tenant_id, parent_id=None)
        
        # Build tree structure
        category_map = {cat.id: cat for cat in all_categories}
        root_categories = []
        
        for cat in all_categories:
            if cat.parent_id and cat.parent_id in category_map:
                parent = category_map[cat.parent_id]
                if not hasattr(parent, 'subcategories'):
                    parent.subcategories = []
                parent.subcategories.append(cat)
            else:
                root_categories.append(cat)
        
        return root_categories
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve category tree: {str(e)}"
        )


@router.get("/{category_id}", response_model=Category)
async def get_category(
    category_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific category by ID.
    
    - **category_id**: The ID of the category to retrieve
    
    Returns the category details.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        category = CategoryService.get_category(db, category_id, tenant_id)
        if not category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category with id {category_id} not found in this tenant"
            )
        
        # Load subcategories
        subcategories = CategoryService.get_categories(db, tenant_id, parent_id=category_id)
        category.subcategories = subcategories
        
        return category
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve category: {str(e)}"
        )


@router.put("/{category_id}", response_model=Category)
async def update_category(
    category_id: int,
    category_data: CategoryUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Update a category.
    
    - **category_id**: The ID of the category to update
    - **name**: Updated category name (optional)
    - **description**: Updated description (optional)
    - **parent_id**: Updated parent category ID (optional)
    
    Only admin, tenant admin, and manager can update categories.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check if category exists in this tenant
        category = CategoryService.get_category(db, category_id, tenant_id)
        if not category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category with id {category_id} not found in this tenant"
            )
        
        # Check if trying to set parent to itself
        if category_data.parent_id == category_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A category cannot be its own parent"
            )
        
        # Check if parent category exists
        if category_data.parent_id:
            parent_category = CategoryService.get_category(db, category_data.parent_id, tenant_id)
            if not parent_category:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Parent category with id {category_data.parent_id} not found in this tenant"
                )
        
        updated_category = CategoryService.update_category(db, category_id, tenant_id, category_data)
        
        return updated_category
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update category: {str(e)}"
        )


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: int,
    request: Request,
    force: bool = Query(False, description="Force delete even if category has products or subcategories"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Delete a category.
    
    - **category_id**: The ID of the category to delete
    - **force**: If True, deletes category even if it has products or subcategories
    
    Only super admin and tenant admin can delete categories.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check if category exists
        category = CategoryService.get_category(db, category_id, tenant_id)
        if not category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category with id {category_id} not found in this tenant"
            )
        
        # Check for subcategories
        subcategories = CategoryService.get_categories(db, tenant_id, parent_id=category_id)
        if subcategories and not force:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Category has {len(subcategories)} subcategories. Use force=True to delete anyway."
            )
        
        # Check for products in this category
        products_count = db.query(Product).filter(
            Product.category_id == category_id,
            Product.tenant_id == tenant_id
        ).count()
        
        if products_count > 0 and not force:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Category has {products_count} products. Use force=True to delete anyway."
            )
        
        # If force is True, reassign products to None or handle accordingly
        if force and products_count > 0:
            db.query(Product).filter(
                Product.category_id == category_id,
                Product.tenant_id == tenant_id
            ).update({Product.category_id: None})
        
        # Delete category
        success = CategoryService.delete_category(db, category_id, tenant_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete category"
            )
        
        return None
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete category: {str(e)}"
        )


@router.get("/{category_id}/products", response_model=List)
async def get_category_products(
    category_id: int,
    request: Request,
    include_subcategories: bool = Query(True, description="Include products from subcategories"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all products in a category.
    
    - **category_id**: The category ID
    - **include_subcategories**: If True, includes products from all subcategories
    
    Returns a list of products in the category.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check if category exists
        category = CategoryService.get_category(db, category_id, tenant_id)
        if not category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category with id {category_id} not found in this tenant"
            )
        
        # Get category IDs to include
        category_ids = [category_id]
        
        if include_subcategories:
            # Recursively get all subcategory IDs
            def get_subcategory_ids(parent_id):
                subcats = CategoryService.get_categories(db, tenant_id, parent_id=parent_id)
                for subcat in subcats:
                    category_ids.append(subcat.id)
                    get_subcategory_ids(subcat.id)
            
            get_subcategory_ids(category_id)
        
        # Get products
        products = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.category_id.in_(category_ids),
            Product.active == True
        ).all()
        
        return products
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve category products: {str(e)}"
        )


@router.post("/bulk", response_model=List[Category], status_code=status.HTTP_201_CREATED)
async def bulk_create_categories(
    categories_data: List[CategoryCreate],
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Create multiple categories in bulk.
    
    - **categories_data**: List of category data objects
    
    Only super admin and tenant admin can bulk create categories.
    """
    try:
        tenant_id = get_current_tenant(request)
        created_categories = []
        
        for category_data in categories_data:
            # Check if parent exists
            if category_data.parent_id:
                parent = CategoryService.get_category(db, category_data.parent_id, tenant_id)
                if not parent:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Parent category {category_data.parent_id} not found for category {category_data.name}"
                    )
            
            category = CategoryService.create_category(db, category_data, tenant_id)
            created_categories.append(category)
        
        return created_categories
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to bulk create categories: {str(e)}"
        )


@router.get("/search/", response_model=List[Category])
async def search_categories(
    request: Request,
    q: str = Query(..., min_length=1, description="Search query"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Search categories by name.
    
    - **q**: Search query string
    
    Returns categories matching the search query within the current tenant.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        categories = db.query(Category).filter(
            Category.tenant_id == tenant_id,
            Category.name.ilike(f"%{q}%")
        ).all()
        
        return categories
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to search categories: {str(e)}"
        )


# ==================== HELPER FUNCTIONS ====================

def get_category_hierarchy(db: Session, tenant_id: int, category_id: int) -> List[Category]:
    """
    Get the full hierarchy path for a category (parent chain).
    """
    hierarchy = []
    current = CategoryService.get_category(db, category_id, tenant_id)
    
    while current:
        hierarchy.insert(0, current)
        if current.parent_id:
            current = CategoryService.get_category(db, current.parent_id, tenant_id)
        else:
            break
    
    return hierarchy


def get_all_descendant_categories(db: Session, tenant_id: int, category_id: int) -> List[int]:
    """
    Get all descendant category IDs for a given category within a tenant.
    """
    descendant_ids = []
    subcategories = CategoryService.get_categories(db, tenant_id, parent_id=category_id)
    
    for subcat in subcategories:
        descendant_ids.append(subcat.id)
        descendant_ids.extend(get_all_descendant_categories(db, tenant_id, subcat.id))
    
    return descendant_ids