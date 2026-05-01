from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.services import UnitService
from app.schemas import UnitCreate, UnitUpdate, Unit
from app.utils.auth import get_current_user, require_role, get_current_tenant
from app.models import User, UserRole, Product

router = APIRouter(prefix="/units", tags=["Units"])


@router.post("/", response_model=Unit, status_code=status.HTTP_201_CREATED)
async def create_unit(
    unit_data: UnitCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Create a new unit of measurement.
    
    - **name**: Unit name (e.g., "Kilogram", "Piece", "Liter")
    - **symbol**: Unit symbol (e.g., "kg", "pcs", "L")
    
    Only super admin, tenant admin, and manager can create units.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check if unit with same name already exists in this tenant
        existing_units = UnitService.get_units(db, tenant_id)
        for unit in existing_units:
            if unit.name.lower() == unit_data.name.lower():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unit with name '{unit_data.name}' already exists in this tenant"
                )
            if unit.symbol.lower() == unit_data.symbol.lower():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unit with symbol '{unit_data.symbol}' already exists in this tenant"
                )
        
        unit = UnitService.create_unit(db, unit_data, tenant_id)
        return unit
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create unit: {str(e)}"
        )


@router.get("/", response_model=List[Unit])
async def get_units(
    request: Request,
    search: Optional[str] = Query(None, description="Search by name or symbol"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all units of measurement.
    
    - **search**: Optional search term to filter units by name or symbol
    
    Returns a list of all units within the current tenant.
    """
    try:
        tenant_id = get_current_tenant(request)
        units = UnitService.get_units(db, tenant_id)
        
        # Apply search filter if provided
        if search:
            search_lower = search.lower()
            units = [
                unit for unit in units 
                if search_lower in unit.name.lower() or search_lower in unit.symbol.lower()
            ]
        
        return units
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve units: {str(e)}"
        )


@router.get("/{unit_id}", response_model=Unit)
async def get_unit(
    unit_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific unit by ID.
    
    - **unit_id**: The ID of the unit to retrieve
    
    Returns the unit details.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        unit = UnitService.get_unit(db, unit_id, tenant_id)
        if not unit:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unit with id {unit_id} not found in this tenant"
            )
        return unit
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve unit: {str(e)}"
        )


@router.put("/{unit_id}", response_model=Unit)
async def update_unit(
    unit_id: int,
    unit_data: UnitUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Update a unit.
    
    - **unit_id**: The ID of the unit to update
    - **name**: Updated unit name (optional)
    - **symbol**: Updated unit symbol (optional)
    
    Only super admin, tenant admin, and manager can update units.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check if unit exists
        existing_unit = UnitService.get_unit(db, unit_id, tenant_id)
        if not existing_unit:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unit with id {unit_id} not found in this tenant"
            )
        
        # Check for name conflicts (excluding current unit)
        if unit_data.name:
            all_units = UnitService.get_units(db, tenant_id)
            for unit in all_units:
                if unit.id != unit_id and unit.name.lower() == unit_data.name.lower():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Unit with name '{unit_data.name}' already exists in this tenant"
                    )
        
        # Check for symbol conflicts (excluding current unit)
        if unit_data.symbol:
            all_units = UnitService.get_units(db, tenant_id)
            for unit in all_units:
                if unit.id != unit_id and unit.symbol.lower() == unit_data.symbol.lower():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Unit with symbol '{unit_data.symbol}' already exists in this tenant"
                    )
        
        unit = UnitService.update_unit(db, unit_id, tenant_id, unit_data)
        return unit
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update unit: {str(e)}"
        )


@router.delete("/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_unit(
    unit_id: int,
    request: Request,
    force: bool = Query(False, description="Force delete even if unit is used by products"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Delete a unit.
    
    - **unit_id**: The ID of the unit to delete
    - **force**: If True, reassign products to NULL before deletion
    
    Only super admin and tenant admin can delete units.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check if unit exists
        unit = UnitService.get_unit(db, unit_id, tenant_id)
        if not unit:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unit with id {unit_id} not found in this tenant"
            )
        
        # Check if unit is used by any products in this tenant
        products_count = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.unit_id == unit_id
        ).count()
        
        if products_count > 0 and not force:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unit is used by {products_count} product(s) in this tenant. Use force=True to reassign products to NULL and delete."
            )
        
        # If force is True, reassign products to NULL
        if force and products_count > 0:
            db.query(Product).filter(
                Product.tenant_id == tenant_id,
                Product.unit_id == unit_id
            ).update({Product.unit_id: None})
        
        # Delete the unit
        db.delete(unit)
        db.commit()
        
        return None
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete unit: {str(e)}"
        )


@router.post("/bulk", response_model=List[Unit], status_code=status.HTTP_201_CREATED)
async def bulk_create_units(
    units_data: List[UnitCreate],
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Create multiple units in bulk.
    
    - **units_data**: List of unit data objects
    
    Only super admin and tenant admin can bulk create units.
    """
    try:
        tenant_id = get_current_tenant(request)
        created_units = []
        existing_units = UnitService.get_units(db, tenant_id)
        existing_names = {u.name.lower() for u in existing_units}
        existing_symbols = {u.symbol.lower() for u in existing_units}
        
        for unit_data in units_data:
            # Check for duplicates
            if unit_data.name.lower() in existing_names:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unit with name '{unit_data.name}' already exists in this tenant"
                )
            if unit_data.symbol.lower() in existing_symbols:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unit with symbol '{unit_data.symbol}' already exists in this tenant"
                )
            
            unit = UnitService.create_unit(db, unit_data, tenant_id)
            created_units.append(unit)
            existing_names.add(unit_data.name.lower())
            existing_symbols.add(unit_data.symbol.lower())
        
        return created_units
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to bulk create units: {str(e)}"
        )


@router.get("/search/", response_model=List[Unit])
async def search_units(
    request: Request,
    q: str = Query(..., min_length=1, description="Search query"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Search units by name or symbol.
    
    - **q**: Search query string
    
    Returns units matching the search query within the current tenant.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        units = db.query(Unit).filter(
            Unit.tenant_id == tenant_id,
            (Unit.name.ilike(f"%{q}%")) | (Unit.symbol.ilike(f"%{q}%"))
        ).all()
        
        return units
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to search units: {str(e)}"
        )


@router.get("/common/", response_model=List[Unit])
async def get_common_units(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get commonly used units (predefined list).
    
    Returns a list of common measurement units within the current tenant.
    """
    common_unit_names = ["Piece", "Kilogram", "Gram", "Liter", "Milliliter", "Meter", "Centimeter", "Box", "Pack", "Dozen"]
    
    try:
        tenant_id = get_current_tenant(request)
        units = UnitService.get_units(db, tenant_id)
        common_units = [u for u in units if u.name in common_unit_names]
        
        return common_units
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve common units: {str(e)}"
        )


@router.post("/initialize-defaults", status_code=status.HTTP_200_OK)
async def initialize_default_units(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Initialize default units if none exist.
    
    Creates a standard set of measurement units.
    Only super admin and tenant admin can initialize default units.
    """
    try:
        tenant_id = get_current_tenant(request)
        existing_units = UnitService.get_units(db, tenant_id)
        
        if existing_units:
            return {
                "message": "Units already exist in this tenant",
                "existing_count": len(existing_units),
                "created_count": 0
            }
        
        default_units = [
            {"name": "Piece", "symbol": "pcs"},
            {"name": "Kilogram", "symbol": "kg"},
            {"name": "Gram", "symbol": "g"},
            {"name": "Liter", "symbol": "L"},
            {"name": "Milliliter", "symbol": "mL"},
            {"name": "Meter", "symbol": "m"},
            {"name": "Centimeter", "symbol": "cm"},
            {"name": "Box", "symbol": "box"},
            {"name": "Pack", "symbol": "pack"},
            {"name": "Dozen", "symbol": "doz"},
            {"name": "Set", "symbol": "set"},
            {"name": "Roll", "symbol": "roll"},
            {"name": "Bottle", "symbol": "btl"},
            {"name": "Can", "symbol": "can"},
            {"name": "Carton", "symbol": "ctn"},
        ]
        
        created_units = []
        for unit_data in default_units:
            unit = UnitService.create_unit(db, UnitCreate(**unit_data), tenant_id)
            created_units.append(unit)
        
        return {
            "message": "Default units initialized successfully for this tenant",
            "created_count": len(created_units),
            "units": created_units
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialize default units: {str(e)}"
        )


@router.get("/{unit_id}/products", response_model=List)
async def get_products_by_unit(
    unit_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all products that use a specific unit.
    
    - **unit_id**: The unit ID
    
    Returns a list of products using this unit within the current tenant.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Check if unit exists
        unit = UnitService.get_unit(db, unit_id, tenant_id)
        if not unit:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unit with id {unit_id} not found in this tenant"
            )
        
        products = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.unit_id == unit_id,
            Product.active == True
        ).all()
        
        return products
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve products: {str(e)}"
        )


# ==================== HELPER FUNCTIONS ====================

def get_unit_by_symbol(db: Session, tenant_id: int, symbol: str) -> Optional[Unit]:
    """
    Get a unit by its symbol within a tenant.
    """
    return db.query(Unit).filter(
        Unit.tenant_id == tenant_id,
        Unit.symbol == symbol
    ).first()


def get_unit_by_name(db: Session, tenant_id: int, name: str) -> Optional[Unit]:
    """
    Get a unit by its name within a tenant.
    """
    return db.query(Unit).filter(
        Unit.tenant_id == tenant_id,
        Unit.name == name
    ).first()


def validate_unit_exists(db: Session, tenant_id: int, unit_id: int) -> bool:
    """
    Check if a unit exists within a tenant.
    """
    return db.query(Unit).filter(
        Unit.id == unit_id,
        Unit.tenant_id == tenant_id
    ).first() is not None