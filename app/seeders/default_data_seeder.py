# app/seeders/default_data_seeder.py
from sqlalchemy.orm import Session
from app.models import Unit, Category, SystemSetting
from app.services import UnitService, CategoryService
from app.schemas import UnitCreate, CategoryCreate

def seed_default_units(db: Session):
    """Seed default units of measurement"""
    existing_units = UnitService.get_units(db)
    if existing_units:
        return
    
    default_units = [
        {"name": "Piece", "symbol": "pcs"},
        {"name": "Kilogram", "symbol": "kg"},
        {"name": "Gram", "symbol": "g"},
        {"name": "Liter", "symbol": "L"},
        {"name": "Milliliter", "symbol": "mL"},
        {"name": "Meter", "symbol": "m"},
        {"name": "Box", "symbol": "box"},
        {"name": "Pack", "symbol": "pack"},
    ]
    
    for unit_data in default_units:
        unit = UnitCreate(**unit_data)
        UnitService.create_unit(db, unit)

def seed_default_categories(db: Session):
    """Seed default categories"""
    existing_categories = CategoryService.get_categories(db)
    if existing_categories:
        return
    
    default_categories = [
        {"name": "Electronics", "description": "Electronic devices and accessories"},
        {"name": "Clothing", "description": "Apparel and fashion items"},
        {"name": "Food", "description": "Food and beverages"},
        {"name": "Beverages", "description": "Drinks and refreshments", "parent_name": "Food"},
        {"name": "Snacks", "description": "Snack items", "parent_name": "Food"},
    ]
    
    category_map = {}
    for cat_data in default_categories:
        parent_name = cat_data.pop("parent_name", None)
        category = CategoryCreate(**cat_data)
        
        if parent_name and parent_name in category_map:
            category.parent_id = category_map[parent_name].id
        
        created = CategoryService.create_category(db, category)
        category_map[cat_data["name"]] = created

def seed_default_data(db: Session):
    """Seed all default data"""
    seed_default_units(db)
    seed_default_categories(db)

# Update your existing user_seeder.py to use the new User model