from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from app.config import settings
from app.models import (
    Tenant, User, Branch, Product, Stock, Sale, SaleItem, 
    Purchase, PurchaseItem, StockMovement, Alert,
    SystemSetting, BackupRecord, SystemLog, Loan,
    Category, Unit, Batch, SaleReturn, SaleReturnItem,
    PurchaseOrder, PurchaseOrderItem, LoanPayment, LoanSummary,
    TempItem, BusinessType, MovementType, ReturnStatus, TenantStatus, UserRole,
    TenantSubscription, SubscriptionPlan, Payment, LoanStatus, SubscriptionStatus, PaymentStatus
)
from app.schemas import (
    UserCreate, SaleCreate, PurchaseCreate, StockCreate,
    CategoryCreate, UnitCreate, BatchCreate, SaleReturnCreate,
    PurchaseOrderCreate, LoanCreate, TempItemCreate, TenantCreate, SubscriptionStatus, PaymentStatus
)
import json
import os
import bcrypt
from decimal import Decimal

# Password context for hashing - with fallback handling
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)


# ==================== TENANT SERVICE ====================
class TenantService:
    @staticmethod
    def create_tenant(db: Session, tenant_data: TenantCreate, created_by: int) -> Tenant:
        """Create a new tenant"""
        # Check if tenant name exists
        existing = db.query(Tenant).filter(Tenant.name == tenant_data.name).first()
        if existing:
            raise ValueError(f"Tenant with name '{tenant_data.name}' already exists")
        
        # Check subdomain uniqueness
        if tenant_data.subdomain:
            existing = db.query(Tenant).filter(Tenant.subdomain == tenant_data.subdomain).first()
            if existing:
                raise ValueError(f"Subdomain '{tenant_data.subdomain}' already taken")
        
        tenant = Tenant(
            name=tenant_data.name,
            subdomain=tenant_data.subdomain,
            business_type=tenant_data.business_type,
            email=tenant_data.email,
            phone=tenant_data.phone,
            address=tenant_data.address,
            created_by=created_by,
            status=TenantStatus.ACTIVE.value
        )
        
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        
        return tenant
    
    @staticmethod
    def get_tenant(db: Session, tenant_id: int) -> Optional[Tenant]:
        return db.query(Tenant).filter(Tenant.id == tenant_id).first()
    
    @staticmethod
    def get_tenant_by_subdomain(db: Session, subdomain: str) -> Optional[Tenant]:
        return db.query(Tenant).filter(
            Tenant.subdomain == subdomain,
            Tenant.status == TenantStatus.ACTIVE.value
        ).first()
    
    @staticmethod
    def get_tenants(db: Session, status: Optional[TenantStatus] = None) -> List[Tenant]:
        query = db.query(Tenant)
        if status:
            query = query.filter(Tenant.status == status)
        return query.order_by(Tenant.created_at.desc()).all()
    
    @staticmethod
    def update_tenant(db: Session, tenant_id: int, tenant_data) -> Optional[Tenant]:
        tenant = TenantService.get_tenant(db, tenant_id)
        if not tenant:
            return None
        
        update_data = tenant_data.dict(exclude_unset=True)
        
        # Check subdomain uniqueness if changing
        if "subdomain" in update_data and update_data["subdomain"] != tenant.subdomain:
            existing = db.query(Tenant).filter(
                Tenant.subdomain == update_data["subdomain"],
                Tenant.id != tenant_id
            ).first()
            if existing:
                raise ValueError(f"Subdomain '{update_data['subdomain']}' already taken")
        
        for key, value in update_data.items():
            setattr(tenant, key, value)
        
        tenant.updated_at = datetime.now()
        db.commit()
        db.refresh(tenant)
        return tenant
    
    @staticmethod
    def delete_tenant(db: Session, tenant_id: int, force: bool = False) -> bool:
        tenant = TenantService.get_tenant(db, tenant_id)
        if not tenant:
            return False
        
        if not force:
            # Check if tenant has data
            user_count = db.query(User).filter(User.tenant_id == tenant_id).count()
            if user_count > 0:
                raise ValueError(f"Tenant has {user_count} users. Use force=True to delete anyway.")
        
        db.delete(tenant)
        db.commit()
        return True
    
    @staticmethod
    def activate_tenant(db: Session, tenant_id: int) -> Optional[Tenant]:
        tenant = TenantService.get_tenant(db, tenant_id)
        if not tenant:
            return None
        
        tenant.status = TenantStatus.ACTIVE.value
        tenant.updated_at = datetime.now()
        db.commit()
        db.refresh(tenant)
        return tenant
    
    @staticmethod
    def suspend_tenant(db: Session, tenant_id: int) -> Optional[Tenant]:
        tenant = TenantService.get_tenant(db, tenant_id)
        if not tenant:
            return None
        
        tenant.status = TenantStatus.SUSPENDED.value
        tenant.updated_at = datetime.now()
        db.commit()
        db.refresh(tenant)
        return tenant
    
    @staticmethod
    def get_tenant_stats(db: Session, tenant_id: int) -> Dict:
        tenant = TenantService.get_tenant(db, tenant_id)
        if not tenant:
            return {}
        
        user_count = db.query(User).filter(User.tenant_id == tenant_id).count()
        branch_count = db.query(Branch).filter(Branch.tenant_id == tenant_id).count()
        product_count = db.query(Product).filter(Product.tenant_id == tenant_id).count()
        sale_count = db.query(Sale).filter(Sale.tenant_id == tenant_id).count()
        loan_count = db.query(Loan).filter(Loan.tenant_id == tenant_id).count()
        
        return {
            "tenant_id": tenant.id,
            "tenant_name": tenant.name,
            "user_count": user_count,
            "branch_count": branch_count,
            "product_count": product_count,
            "sale_count": sale_count,
            "loan_count": loan_count,
            "status": tenant.status
        }


# ==================== AUTH SERVICE ====================
class AuthService:
    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a plain password against a hashed password"""
        try:
            if len(plain_password) > 72:
                plain_password = plain_password[:72]
            return pwd_context.verify(plain_password, hashed_password)
        except Exception as e:
            print(f"❌ Passlib verification failed: {e}")
            try:
                return bcrypt.checkpw(
                    plain_password.encode('utf-8'),
                    hashed_password.encode('utf-8')
                )
            except Exception as be:
                print(f"❌ Bcrypt fallback also failed: {be}")
                return False
    
    @staticmethod
    def get_password_hash(password: str) -> str:
        """Hash a password using bcrypt"""
        try:
            if len(password) > 72:
                password = password[:72]
            return pwd_context.hash(password)
        except Exception as e:
            print(f"❌ Passlib hash failed: {e}")
            try:
                salt = bcrypt.gensalt()
                return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
            except Exception as be:
                print(f"❌ Bcrypt fallback also failed: {be}")
                raise
    
    @staticmethod
    def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        return encoded_jwt
    
    @staticmethod
    def authenticate_user(db: Session, email: str, password: str, tenant_id: Optional[int] = None) -> Optional[User]:
        """Authenticate user with optional tenant scope"""
        query = db.query(User).filter(User.email == email)
        
        if tenant_id:
            query = query.filter(User.tenant_id == tenant_id)
        
        user = query.first()
        
        if not user:
            return None
        
        if not AuthService.verify_password(password, user.password_hash):
            return None
        
        if not user.active:
            return None
        
        return user
    
    @staticmethod
    def authenticate_super_admin(db: Session, email: str, password: str) -> Optional[User]:
        """Authenticate super admin (no tenant restriction)"""
        user = db.query(User).filter(
            User.email == email,
            User.role == UserRole.SUPER_ADMIN.value
        ).first()
        
        if not user:
            return None
        
        if not AuthService.verify_password(password, user.password_hash):
            return None
        
        return user
    
    @staticmethod
    def get_current_user(db: Session, token: str, tenant_id: Optional[int] = None) -> Optional[User]:
        """Get current user with tenant validation"""
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            user_id = payload.get("user_id")
            token_tenant_id = payload.get("tenant_id")
            
            if user_id is None:
                return None
            
            # Validate tenant matches
            if tenant_id and token_tenant_id and token_tenant_id != tenant_id:
                return None
            
            query = db.query(User).filter(User.id == user_id)
            
            if tenant_id:
                query = query.filter(User.tenant_id == tenant_id)
            
            user = query.first()
            
            if not user or not user.active:
                return None
            
            return user
        except JWTError as e:
            print("❌ JWT decode error:", e)
            return None


# ==================== BRANCH SERVICE ====================
class BranchService:
    @staticmethod
    def create_branch(db: Session, branch_data, tenant_id: int) -> Branch:
        db_branch = Branch(tenant_id=tenant_id, **branch_data.dict())
        db.add(db_branch)
        db.commit()
        db.refresh(db_branch)
        return db_branch
    
    @staticmethod
    def get_branches(db: Session, tenant_id: int, business_type: Optional[BusinessType] = None) -> List[Branch]:
        query = db.query(Branch).filter(Branch.tenant_id == tenant_id)
        if business_type:
            query = query.filter(Branch.business_type == business_type)
        return query.all()
    
    @staticmethod
    def get_branch(db: Session, branch_id: int, tenant_id: int) -> Optional[Branch]:
        return db.query(Branch).filter(
            Branch.id == branch_id,
            Branch.tenant_id == tenant_id
        ).first()
    
    @staticmethod
    def update_branch(db: Session, branch_id: int, tenant_id: int, branch_data) -> Optional[Branch]:
        branch = BranchService.get_branch(db, branch_id, tenant_id)
        if not branch:
            return None
        for key, value in branch_data.dict(exclude_unset=True).items():
            setattr(branch, key, value)
        db.commit()
        db.refresh(branch)
        return branch


# ==================== CATEGORY SERVICE ====================
class CategoryService:
    @staticmethod
    def create_category(db: Session, category_data: CategoryCreate, tenant_id: int) -> Category:
        db_category = Category(tenant_id=tenant_id, **category_data.dict())
        db.add(db_category)
        db.commit()
        db.refresh(db_category)
        return db_category
    
    @staticmethod
    def get_categories(db: Session, tenant_id: int, parent_id: Optional[int] = None) -> List[Category]:
        query = db.query(Category).filter(Category.tenant_id == tenant_id)
        if parent_id:
            query = query.filter(Category.parent_id == parent_id)
        else:
            query = query.filter(Category.parent_id.is_(None))
        return query.all()
    
    @staticmethod
    def get_category(db: Session, category_id: int, tenant_id: int) -> Optional[Category]:
        return db.query(Category).filter(
            Category.id == category_id,
            Category.tenant_id == tenant_id
        ).first()
    
    @staticmethod
    def update_category(db: Session, category_id: int, tenant_id: int, category_data) -> Optional[Category]:
        category = CategoryService.get_category(db, category_id, tenant_id)
        if not category:
            return None
        for key, value in category_data.dict(exclude_unset=True).items():
            setattr(category, key, value)
        db.commit()
        db.refresh(category)
        return category
    
    @staticmethod
    def delete_category(db: Session, category_id: int, tenant_id: int) -> bool:
        category = CategoryService.get_category(db, category_id, tenant_id)
        if not category:
            return False
        db.delete(category)
        db.commit()
        return True


# ==================== UNIT SERVICE ====================
class UnitService:
    @staticmethod
    def create_unit(db: Session, unit_data: UnitCreate, tenant_id: int) -> Unit:
        db_unit = Unit(tenant_id=tenant_id, **unit_data.dict())
        db.add(db_unit)
        db.commit()
        db.refresh(db_unit)
        return db_unit
    
    @staticmethod
    def get_units(db: Session, tenant_id: int) -> List[Unit]:
        return db.query(Unit).filter(Unit.tenant_id == tenant_id).all()
    
    @staticmethod
    def get_unit(db: Session, unit_id: int, tenant_id: int) -> Optional[Unit]:
        return db.query(Unit).filter(
            Unit.id == unit_id,
            Unit.tenant_id == tenant_id
        ).first()
    
    @staticmethod
    def update_unit(db: Session, unit_id: int, tenant_id: int, unit_data) -> Optional[Unit]:
        unit = UnitService.get_unit(db, unit_id, tenant_id)
        if not unit:
            return None
        for key, value in unit_data.dict(exclude_unset=True).items():
            setattr(unit, key, value)
        db.commit()
        db.refresh(unit)
        return unit


# ==================== PRODUCT SERVICE ====================
class ProductService:
    @staticmethod
    def create_product(db: Session, product_data, tenant_id: int) -> Product:
        existing = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.sku == product_data.sku
        ).first()
        if existing:
            raise ValueError("SKU already exists for this tenant")
        if product_data.barcode:
            existing_barcode = db.query(Product).filter(
                Product.tenant_id == tenant_id,
                Product.barcode == product_data.barcode
            ).first()
            if existing_barcode:
                raise ValueError("Barcode already exists for this tenant")
        db_product = Product(tenant_id=tenant_id, **product_data.dict())
        db.add(db_product)
        db.commit()
        db.refresh(db_product)
        return db_product
    
    @staticmethod
    def get_products(db: Session, tenant_id: int, active: Optional[bool] = True, 
                     branch_id: Optional[int] = None, category_id: Optional[int] = None) -> List[Product]:
        query = db.query(Product).filter(Product.tenant_id == tenant_id)
        if active is not None:
            query = query.filter(Product.active == active)
        if category_id:
            query = query.filter(Product.category_id == category_id)
        products = query.all()
        if branch_id:
            for product in products:
                stock = db.query(Stock).filter(
                    Stock.branch_id == branch_id,
                    Stock.product_id == product.id
                ).first()
                product.stock_quantity = stock.quantity if stock else Decimal(0)
                product.reorder_level = stock.reorder_level if stock else Decimal(0)
        return products
    
    @staticmethod
    def get_product(db: Session, product_id: int, tenant_id: int) -> Optional[Product]:
        return db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == tenant_id
        ).first()
    
    @staticmethod
    def get_product_by_barcode(db: Session, barcode: str, tenant_id: int) -> Optional[Product]:
        return db.query(Product).filter(
            Product.barcode == barcode,
            Product.tenant_id == tenant_id
        ).first()
    
    @staticmethod
    def update_product(db: Session, product_id: int, tenant_id: int, product_data) -> Optional[Product]:
        product = ProductService.get_product(db, product_id, tenant_id)
        if not product:
            return None
        for key, value in product_data.dict(exclude_unset=True).items():
            setattr(product, key, value)
        db.commit()
        db.refresh(product)
        return product
    
    @staticmethod
    def delete_product(db: Session, product_id: int, tenant_id: int) -> bool:
        product = ProductService.get_product(db, product_id, tenant_id)
        if not product:
            return False
        db.delete(product)
        db.commit()
        return True


# ==================== BATCH SERVICE ====================
class BatchService:
    @staticmethod
    def create_batch(db: Session, batch_data: BatchCreate, tenant_id: int) -> Batch:
        db_batch = Batch(
            tenant_id=tenant_id,
            **batch_data.dict(),
            remaining_quantity=batch_data.quantity
        )
        db.add(db_batch)
        db.flush()
        
        # Update stock
        stock = StockService.get_stock(db, batch_data.branch_id, batch_data.product_id, tenant_id)
        if stock:
            stock.quantity += batch_data.quantity
        else:
            stock = Stock(
                branch_id=batch_data.branch_id,
                product_id=batch_data.product_id,
                quantity=batch_data.quantity,
                reorder_level=0
            )
            db.add(stock)
        
        db.commit()
        db.refresh(db_batch)
        return db_batch
    
    @staticmethod
    def get_batches(db: Session, tenant_id: int, product_id: Optional[int] = None, 
                    branch_id: Optional[int] = None, include_expired: bool = False) -> List[Batch]:
        query = db.query(Batch).filter(Batch.tenant_id == tenant_id)
        if product_id:
            query = query.filter(Batch.product_id == product_id)
        if branch_id:
            query = query.filter(Batch.branch_id == branch_id)
        if not include_expired:
            query = query.filter(
                or_(Batch.expiry_date.is_(None), Batch.expiry_date > datetime.now())
            )
        return query.filter(Batch.remaining_quantity > 0).all()
    
    @staticmethod
    def get_batch(db: Session, batch_id: int, tenant_id: int) -> Optional[Batch]:
        return db.query(Batch).filter(
            Batch.id == batch_id,
            Batch.tenant_id == tenant_id
        ).first()
    
    @staticmethod
    def deduct_from_batch(db: Session, batch_id: int, tenant_id: int, quantity: Decimal) -> Batch:
        batch = BatchService.get_batch(db, batch_id, tenant_id)
        if not batch or batch.remaining_quantity < quantity:
            raise ValueError("Insufficient batch quantity")
        
        batch.remaining_quantity -= quantity
        db.commit()
        db.refresh(batch)
        return batch


# ==================== STOCK SERVICE ====================
class StockService:
    @staticmethod
    def get_stock(db: Session, branch_id: int, product_id: int, tenant_id: int) -> Optional[Stock]:
        return db.query(Stock).join(Product).filter(
            Stock.branch_id == branch_id,
            Stock.product_id == product_id,
            Product.tenant_id == tenant_id
        ).first()
    
    @staticmethod
    def get_branch_stock(db: Session, tenant_id: int, branch_id: int, low_stock: bool = False) -> List[Dict]:
        query = db.query(Stock).join(Product).filter(
            Stock.branch_id == branch_id,
            Product.tenant_id == tenant_id
        )
        if low_stock:
            query = query.filter(Stock.quantity <= Stock.reorder_level)
        stocks = query.all()
        result = []
        for stock in stocks:
            product = stock.product
            batches = db.query(Batch).filter(
                Batch.product_id == stock.product_id,
                Batch.branch_id == branch_id,
                Batch.tenant_id == tenant_id,
                Batch.remaining_quantity > 0
            ).all()
            result.append({
                "product": product,
                "quantity": float(stock.quantity),
                "reorder_level": float(stock.reorder_level),
                "status": "low" if stock.quantity <= stock.reorder_level else "normal",
                "batches": batches
            })
        return result
    
    @staticmethod
    def add_stock(db: Session, branch_id: int, product_id: int, quantity: Decimal, 
                  user_id: int, tenant_id: int, notes: str = "", batch_id: Optional[int] = None) -> Stock:
        stock = StockService.get_stock(db, branch_id, product_id, tenant_id)
        if stock:
            stock.quantity += quantity
        else:
            stock = Stock(
                branch_id=branch_id,
                product_id=product_id,
                quantity=quantity,
                reorder_level=0
            )
            db.add(stock)
            db.flush()
        
        movement = StockMovement(
            branch_id=branch_id,
            product_id=product_id,
            user_id=user_id,
            change_qty=quantity,
            movement_type=MovementType.PURCHASE.value,
            batch_id=batch_id,
            notes=notes
        )
        db.add(movement)
        db.commit()
        db.refresh(stock)
        return stock
    
    @staticmethod
    def deduct_stock(db: Session, branch_id: int, product_id: int, quantity: Decimal, 
                     user_id: int, tenant_id: int, reference_id: int, notes: str = "", 
                     batch_id: Optional[int] = None) -> Stock:
        stock = StockService.get_stock(db, branch_id, product_id, tenant_id)
        if not stock or stock.quantity < quantity:
            raise ValueError("Insufficient stock")
        
        stock.quantity -= quantity
        
        movement = StockMovement(
            branch_id=branch_id,
            product_id=product_id,
            user_id=user_id,
            change_qty=-quantity,
            movement_type=MovementType.SALE.value,
            reference_id=reference_id,
            batch_id=batch_id,
            notes=notes
        )
        db.add(movement)
        
        if stock.quantity <= stock.reorder_level:
            AlertService.check_and_create_alert(db, branch_id, product_id, tenant_id)
        
        db.commit()
        db.refresh(stock)
        return stock
    
    @staticmethod
    def update_reorder_level(db: Session, branch_id: int, product_id: int, 
                             reorder_level: Decimal, tenant_id: int) -> Stock:
        stock = StockService.get_stock(db, branch_id, product_id, tenant_id)
        if not stock:
            raise ValueError("Stock not found")
        stock.reorder_level = reorder_level
        db.commit()
        db.refresh(stock)
        return stock
    
    @staticmethod
    def transfer_stock(db: Session, from_branch_id: int, to_branch_id: int, 
                       product_id: int, quantity: Decimal, user_id: int, tenant_id: int) -> Dict:
        # Deduct from source branch
        from_stock = StockService.deduct_stock(
            db, from_branch_id, product_id, quantity, 
            user_id, tenant_id, 0, f"Transfer to branch {to_branch_id}"
        )
        
        # Add to destination branch
        to_stock = StockService.add_stock(
            db, to_branch_id, product_id, quantity, 
            user_id, tenant_id, f"Transfer from branch {from_branch_id}"
        )
        
        # Record transfer movements
        movement_out = StockMovement(
            branch_id=from_branch_id,
            product_id=product_id,
            user_id=user_id,
            change_qty=-quantity,
            movement_type=MovementType.TRANSFER.value,
            notes=f"Transfer to branch {to_branch_id}"
        )
        movement_in = StockMovement(
            branch_id=to_branch_id,
            product_id=product_id,
            user_id=user_id,
            change_qty=quantity,
            movement_type=MovementType.TRANSFER.value,
            notes=f"Transfer from branch {from_branch_id}"
        )
        db.add_all([movement_out, movement_in])
        db.commit()
        
        return {"from_branch": from_stock, "to_branch": to_stock}


# ==================== SALE SERVICE ====================
class SaleService:
    @staticmethod
    def create_sale(db: Session, sale_data: SaleCreate, user_id: int, branch_id: int, tenant_id: int) -> Sale:
        # Check stock and get batches
        for item in sale_data.items:
            if item.batch_id:
                batch = db.query(Batch).filter(
                    Batch.id == item.batch_id,
                    Batch.tenant_id == tenant_id
                ).first()
                if not batch or batch.remaining_quantity < item.quantity:
                    raise ValueError(f"Insufficient stock in batch for product ID {item.product_id}")
            else:
                stock = StockService.get_stock(db, branch_id, item.product_id, tenant_id)
                if not stock or stock.quantity < item.quantity:
                    product = db.query(Product).filter(
                        Product.id == item.product_id,
                        Product.tenant_id == tenant_id
                    ).first()
                    raise ValueError(f"Insufficient stock for product: {product.name if product else item.product_id}")
        
        total_amount = Decimal(0)
        total_cost = Decimal(0)
        
        for item in sale_data.items:
            product = db.query(Product).filter(
                Product.id == item.product_id,
                Product.tenant_id == tenant_id
            ).first()
            if product:
                total_amount += item.quantity * item.unit_price
                total_cost += item.quantity * product.cost
        
        # Apply discount and tax
        total_amount = total_amount - sale_data.discount_amount + sale_data.tax_amount
        
        db_sale = Sale(
            tenant_id=tenant_id,
            branch_id=branch_id,
            user_id=user_id,
            customer_name=sale_data.customer_name,
            customer_phone=sale_data.customer_phone,
            total_amount=total_amount,
            total_cost=total_cost,
            discount_amount=sale_data.discount_amount,
            tax_amount=sale_data.tax_amount,
            payment_method=sale_data.payment_method
        )
        db.add(db_sale)
        db.flush()
        
        for item in sale_data.items:
            total = item.quantity * item.unit_price
            product = db.query(Product).filter(
                Product.id == item.product_id,
                Product.tenant_id == tenant_id
            ).first()
            
            sale_item = SaleItem(
                sale_id=db_sale.id,
                product_id=item.product_id,
                batch_id=item.batch_id,
                quantity=item.quantity,
                unit_price=item.unit_price,
                total=total,
                cost=product.cost * item.quantity if product else Decimal(0)
            )
            db.add(sale_item)
            
            # Deduct from batch if specified
            if item.batch_id:
                BatchService.deduct_from_batch(db, item.batch_id, tenant_id, item.quantity)
            
            StockService.deduct_stock(
                db, branch_id, item.product_id, item.quantity,
                user_id, tenant_id, db_sale.id, f"Sale #{db_sale.id}",
                batch_id=item.batch_id
            )
        
        db.commit()
        db.refresh(db_sale)
        return db_sale
    
    @staticmethod
    def get_sales(db: Session, tenant_id: int, branch_id: int = None, user_id: int = None,
                  start_date: datetime = None, end_date: datetime = None, 
                  limit: int = 100) -> List[Sale]:
        query = db.query(Sale).filter(Sale.tenant_id == tenant_id)
        if branch_id:
            query = query.filter(Sale.branch_id == branch_id)
        if user_id:
            query = query.filter(Sale.user_id == user_id)
        if start_date:
            query = query.filter(Sale.created_at >= start_date)
        if end_date:
            query = query.filter(Sale.created_at <= end_date)
        return query.order_by(Sale.created_at.desc()).limit(limit).all()
    
    @staticmethod
    def get_sale(db: Session, sale_id: int, tenant_id: int) -> Optional[Sale]:
        return db.query(Sale).filter(
            Sale.id == sale_id,
            Sale.tenant_id == tenant_id
        ).first()


# ==================== SALE RETURN SERVICE ====================
class SaleReturnService:
    @staticmethod
    def create_return(db: Session, return_data: SaleReturnCreate, user_id: int, branch_id: int, tenant_id: int) -> SaleReturn:
        sale = SaleService.get_sale(db, return_data.sale_id, tenant_id)
        if not sale:
            raise ValueError("Sale not found")
        
        # Generate return number
        return_number = f"RET-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        
        total_return_amount = Decimal(0)
        return_items = []
        
        for item in return_data.items:
            sale_item = db.query(SaleItem).filter(SaleItem.id == item.sale_item_id).first()
            if not sale_item or sale_item.sale_id != sale.id:
                raise ValueError(f"Invalid sale item ID {item.sale_item_id}")
            
            if item.quantity > sale_item.quantity:
                raise ValueError(f"Cannot return more than sold quantity for item {sale_item.product_id}")
            
            refund_amount = item.quantity * sale_item.unit_price
            total_return_amount += refund_amount
            
            return_items.append({
                "sale_item": sale_item,
                "quantity": item.quantity,
                "refund_amount": refund_amount,
                "reason": item.reason
            })
        
        db_return = SaleReturn(
            tenant_id=tenant_id,
            return_number=return_number,
            sale_id=sale.id,
            branch_id=branch_id,
            user_id=user_id,
            total_return_amount=total_return_amount,
            reason=return_data.reason,
            notes=return_data.notes,
            status=ReturnStatus.PENDING.value
        )
        db.add(db_return)
        db.flush()
        
        for item_data in return_items:
            return_item = SaleReturnItem(
                return_id=db_return.id,
                sale_item_id=item_data["sale_item"].id,
                product_id=item_data["sale_item"].product_id,
                batch_id=item_data["sale_item"].batch_id,
                quantity=item_data["quantity"],
                refund_amount=item_data["refund_amount"],
                reason=item_data["reason"]
            )
            db.add(return_item)
            
            # Restore stock
            StockService.add_stock(
                db, branch_id, item_data["sale_item"].product_id,
                item_data["quantity"], user_id, tenant_id, f"Return from sale #{sale.id}",
                batch_id=item_data["sale_item"].batch_id
            )
        
        db.commit()
        db.refresh(db_return)
        return db_return


# ==================== PURCHASE ORDER SERVICE ====================
class PurchaseOrderService:
    @staticmethod
    def create_purchase_order(db: Session, po_data: PurchaseOrderCreate, user_id: int, branch_id: int, tenant_id: int) -> PurchaseOrder:
        # Generate order number
        order_number = f"PO-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        
        subtotal = Decimal(0)
        for item in po_data.items:
            subtotal += item.quantity_ordered * item.unit_cost
        
        total_amount = subtotal + po_data.tax_amount + po_data.shipping_cost - po_data.discount_amount
        
        db_po = PurchaseOrder(
            tenant_id=tenant_id,
            order_number=order_number,
            branch_id=branch_id,
            supplier=po_data.supplier,
            expected_delivery_date=po_data.expected_delivery_date,
            status="pending",
            subtotal=subtotal,
            tax_amount=po_data.tax_amount,
            shipping_cost=po_data.shipping_cost,
            discount_amount=po_data.discount_amount,
            total_amount=total_amount,
            notes=po_data.notes,
            created_by=user_id
        )
        db.add(db_po)
        db.flush()
        
        for item in po_data.items:
            po_item = PurchaseOrderItem(
                purchase_order_id=db_po.id,
                product_id=item.product_id,
                quantity_ordered=item.quantity_ordered,
                unit_cost=item.unit_cost,
                total_cost=item.quantity_ordered * item.unit_cost,
                batch_number=item.batch_number,
                expiry_date=item.expiry_date,
                manufacturing_date=item.manufacturing_date,
                notes=item.notes
            )
            db.add(po_item)
        
        db.commit()
        db.refresh(db_po)
        return db_po


# ==================== LOAN SERVICE ====================
class LoanService:
    @staticmethod
    def create_loan(db: Session, loan_data: LoanCreate, user_id: int, branch_id: int, tenant_id: int) -> Loan:
        # Generate loan number
        loan_number = f"LN-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        
        total_amount = Decimal(0)
        for item in loan_data.items:
            total_amount += item.quantity * item.unit_price
        
        interest_amount = total_amount * (loan_data.interest_rate / Decimal(100))
        total_with_interest = total_amount + interest_amount
        
        db_loan = Loan(
            tenant_id=tenant_id,
            loan_number=loan_number,
            branch_id=branch_id,
            customer_name=loan_data.customer_name,
            customer_phone=loan_data.customer_phone,
            customer_email=loan_data.customer_email,
            due_date=loan_data.due_date,
            total_amount=total_with_interest,
            remaining_amount=total_with_interest,
            interest_rate=loan_data.interest_rate,
            interest_amount=interest_amount,
            notes=loan_data.notes,
            created_by=user_id,
            status=LoanStatus.ACTIVE.value
        )
        db.add(db_loan)
        db.flush()
        
        for item in loan_data.items:
            loan_item = LoanItem(
                loan_id=db_loan.id,
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price=item.unit_price,
                line_total=item.quantity * item.unit_price
            )
            db.add(loan_item)
        
        db.commit()
        db.refresh(db_loan)
        return db_loan
    
    @staticmethod
    def make_payment(db: Session, loan_id: int, payment_data, user_id: int, tenant_id: int) -> LoanPayment:
        loan = db.query(Loan).filter(
            Loan.id == loan_id,
            Loan.tenant_id == tenant_id
        ).first()
        if not loan:
            raise ValueError("Loan not found")
        
        if loan.remaining_amount < payment_data.amount:
            raise ValueError("Payment amount exceeds remaining loan balance")
        
        # Generate payment number
        payment_number = f"LNP-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        
        payment = LoanPayment(
            loan_id=loan_id,
            payment_number=payment_number,
            amount=payment_data.amount,
            payment_method=payment_data.payment_method,
            reference_number=payment_data.reference_number,
            notes=payment_data.notes,
            recorded_by=user_id,
            sale_id=payment_data.sale_id
        )
        db.add(payment)
        
        loan.paid_amount += payment_data.amount
        loan.remaining_amount -= payment_data.amount
        
        if loan.remaining_amount <= 0:
            loan.status = LoanStatus.SETTLED.value
        else:
            loan.status = LoanStatus.PARTIALLY_PAID.value
        
        db.commit()
        db.refresh(payment)
        return payment
    
    @staticmethod
    def get_loans(db: Session, tenant_id: int, branch_id: Optional[int] = None, 
                  status: Optional[LoanStatus] = None) -> List[Loan]:
        query = db.query(Loan).filter(Loan.tenant_id == tenant_id)
        if branch_id:
            query = query.filter(Loan.branch_id == branch_id)
        if status:
            query = query.filter(Loan.status == status.value)
        return query.order_by(Loan.created_at.desc()).all()


# ==================== ALERT SERVICE ====================
class AlertService:
    @staticmethod
    def create_alert(db: Session, branch_id: int, product_id: int, tenant_id: int, 
                     message: str, alert_type: str = "low_stock") -> Alert:
        alert = Alert(
            tenant_id=tenant_id,
            branch_id=branch_id,
            product_id=product_id,
            alert_type=alert_type,
            message=message,
            resolved=False
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)
        return alert
    
    @staticmethod
    def check_and_create_alert(db: Session, branch_id: int, product_id: int, tenant_id: int):
        stock = StockService.get_stock(db, branch_id, product_id, tenant_id)
        product = db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == tenant_id
        ).first()
        branch = db.query(Branch).filter(
            Branch.id == branch_id,
            Branch.tenant_id == tenant_id
        ).first()
        
        if not stock or not product or not branch:
            return
        
        current_qty = stock.quantity
        reorder_level = stock.reorder_level
        
        if current_qty <= 0:
            existing = db.query(Alert).filter(
                Alert.tenant_id == tenant_id,
                Alert.branch_id == branch_id,
                Alert.product_id == product_id,
                Alert.resolved == False,
                Alert.alert_type == "out_of_stock"
            ).first()
            if not existing:
                AlertService.create_alert(
                    db, branch_id, product_id, tenant_id,
                    f"Out of stock: {product.name} (SKU: {product.sku}) is out of stock at {branch.name}.",
                    "out_of_stock"
                )
        elif current_qty <= reorder_level:
            existing = db.query(Alert).filter(
                Alert.tenant_id == tenant_id,
                Alert.branch_id == branch_id,
                Alert.product_id == product_id,
                Alert.resolved == False,
                Alert.alert_type == "low_stock"
            ).first()
            if not existing:
                AlertService.create_alert(
                    db, branch_id, product_id, tenant_id,
                    f"Low stock alert: {product.name} (SKU: {product.sku}) has only {current_qty} units remaining at {branch.name}. Reorder level is {reorder_level}.",
                    "low_stock"
                )
    
    @staticmethod
    def resolve_alert(db: Session, alert_id: int, user_id: int, tenant_id: int):
        alert = db.query(Alert).filter(
            Alert.id == alert_id,
            Alert.tenant_id == tenant_id
        ).first()
        if not alert:
            return None
        alert.resolved = True
        alert.resolved_at = datetime.now()
        alert.resolved_by = user_id
        db.commit()
        db.refresh(alert)
        return alert
    
    
        # Add these methods to AlertService class in services.py

    @staticmethod
    def get_alerts(db: Session, tenant_id: int, resolved: bool = False, branch_id: Optional[int] = None) -> List[Dict]:
        """Get alerts with optional filters"""
        query = db.query(Alert).filter(
            Alert.tenant_id == tenant_id,
            Alert.resolved == resolved
        )
        if branch_id:
            query = query.filter(Alert.branch_id == branch_id)
        
        alerts = query.order_by(Alert.created_at.desc()).all()
        
        result = []
        for alert in alerts:
            product = db.query(Product).filter(Product.id == alert.product_id).first()
            branch = db.query(Branch).filter(Branch.id == alert.branch_id).first()
            resolver = db.query(User).filter(User.id == alert.resolved_by).first() if alert.resolved_by else None
            
            result.append({
                "id": alert.id,
                "tenant_id": alert.tenant_id,
                "branch_id": alert.branch_id,
                "branch_name": branch.name if branch else "Unknown",
                "product_id": alert.product_id,
                "product_name": product.name if product else "Unknown",
                "product_sku": product.sku if product else "N/A",
                "alert_type": alert.alert_type,
                "message": alert.message,
                "created_at": alert.created_at,
                "resolved": alert.resolved,
                "resolved_at": alert.resolved_at,
                "resolved_by": resolver.name if resolver else None
            })
        
        return result

    @staticmethod
    def auto_resolve_alerts(db: Session, tenant_id: int) -> int:
        """Auto-resolve alerts for items that are no longer low stock"""
        resolved_count = 0
        alerts = db.query(Alert).filter(
            Alert.tenant_id == tenant_id,
            Alert.resolved == False,
            Alert.alert_type.in_(["low_stock", "out_of_stock"])
        ).all()
        
        for alert in alerts:
            stock = db.query(Stock).filter(
                Stock.branch_id == alert.branch_id,
                Stock.product_id == alert.product_id
            ).first()
            
            if stock and stock.quantity > stock.reorder_level:
                alert.resolved = True
                alert.resolved_at = datetime.now()
                resolved_count += 1
        
        db.commit()
        return resolved_count

    @staticmethod
    def auto_resolve_alerts_for_branch(db: Session, branch_id: int, tenant_id: int) -> int:
        """Auto-resolve alerts for a specific branch"""
        resolved_count = 0
        alerts = db.query(Alert).filter(
            Alert.tenant_id == tenant_id,
            Alert.branch_id == branch_id,
            Alert.resolved == False,
            Alert.alert_type.in_(["low_stock", "out_of_stock"])
        ).all()
        
        for alert in alerts:
            stock = db.query(Stock).filter(
                Stock.branch_id == branch_id,
                Stock.product_id == alert.product_id
            ).first()
            
            if stock and stock.quantity > stock.reorder_level:
                alert.resolved = True
                alert.resolved_at = datetime.now()
                resolved_count += 1
        
        db.commit()
        return resolved_count

    @staticmethod
    def get_low_stock_summary(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> Dict:
        """Get low stock summary"""
        query = db.query(Stock).join(Product).filter(
            Product.tenant_id == tenant_id,
            Stock.quantity <= Stock.reorder_level,
            Stock.quantity > 0
        )
        
        if branch_id:
            query = query.filter(Stock.branch_id == branch_id)
        
        stocks = query.all()
        
        items = []
        for stock in stocks:
            product = stock.product
            branch = db.query(Branch).filter(Branch.id == stock.branch_id).first()
            items.append({
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
            "total_low_stock_items": len(items),
            "items": items
        }

    @staticmethod
    def check_low_stock_and_create_alerts(db: Session, tenant_id: int) -> int:
        """Check all branches and create low stock alerts"""
        alerts_created = 0
        branches = db.query(Branch).filter(Branch.tenant_id == tenant_id).all()
        
        for branch in branches:
            stocks = db.query(Stock).join(Product).filter(
                Product.tenant_id == tenant_id,
                Stock.branch_id == branch.id
            ).all()
            
            for stock in stocks:
                if AlertService.check_and_create_alert(db, branch.id, stock.product_id, tenant_id):
                    alerts_created += 1
        
        return alerts_created


# ==================== SETTINGS SERVICE ====================
class SettingsService:
    
    @staticmethod
    def _get_value(setting) -> Any:
        if setting and setting.value:
            try:
                return json.loads(setting.value)
            except:
                return setting.value
        return None
    
    @staticmethod
    def _set_value(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)
    
    @staticmethod
    def get_setting(db: Session, category: str, key: str, tenant_id: Optional[int] = None) -> Any:
        query = db.query(SystemSetting).filter(
            SystemSetting.category == category,
            SystemSetting.key == key
        )
        if tenant_id:
            query = query.filter(SystemSetting.tenant_id == tenant_id)
        else:
            query = query.filter(SystemSetting.tenant_id.is_(None))
        
        setting = query.first()
        return SettingsService._get_value(setting)
    
    @staticmethod
    def get_all_settings(db: Session, tenant_id: Optional[int] = None) -> Dict[str, Any]:
        query = db.query(SystemSetting)
        if tenant_id:
            query = query.filter(SystemSetting.tenant_id == tenant_id)
        else:
            query = query.filter(SystemSetting.tenant_id.is_(None))
        
        settings_list = query.all()
        result = {}
        for setting in settings_list:
            if setting.category not in result:
                result[setting.category] = {}
            result[setting.category][setting.key] = SettingsService._get_value(setting)
        return result


# ==================== SUBSCRIPTION SERVICE ====================
class SubscriptionService:
    """Service for managing subscriptions and payments"""
    
    @staticmethod
    def get_active_subscription(db: Session, tenant_id: int) -> Optional[TenantSubscription]:
        """Get current active subscription for a tenant"""
        return db.query(TenantSubscription).filter(
            TenantSubscription.tenant_id == tenant_id,
            TenantSubscription.status == SubscriptionStatus.ACTIVE.value,
            TenantSubscription.payment_status == PaymentStatus.COMPLETED.value,
            TenantSubscription.end_date > datetime.now()
        ).first()
    
    @staticmethod
    def check_subscription_valid(db: Session, tenant_id: int) -> bool:
        """Check if tenant has valid access"""
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            return False
        
        # Trial check
        if tenant.status == TenantStatus.TRIAL.value:
            if tenant.trial_end and tenant.trial_end > datetime.now():
                return True
        
        # Active subscription check
        active_sub = SubscriptionService.get_active_subscription(db, tenant_id)
        if active_sub:
            return True
        
        return False
    
    @staticmethod
    def get_subscription_status(db: Session, tenant_id: int) -> Dict:
        """Get detailed subscription status"""
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            return {"status": "not_found", "message": "Tenant not found"}
        
        # Trial status
        if tenant.status == TenantStatus.TRIAL.value:
            days_left = (tenant.trial_end - datetime.now()).days if tenant.trial_end else 0
            return {
                "status": "trial",
                "is_valid": days_left > 0,
                "days_left": max(0, days_left),
                "message": f"Trial period: {max(0, days_left)} days remaining"
            }
        
        # Active subscription
        active_sub = SubscriptionService.get_active_subscription(db, tenant_id)
        if active_sub:
            days_left = (active_sub.end_date - datetime.now()).days
            plan = active_sub.plan
            return {
                "status": "active",
                "is_valid": True,
                "days_left": days_left,
                "plan_name": plan.plan_name if plan else "Unknown",
                "message": f"Active subscription: {days_left} days remaining"
            }
        
        # No subscription
        return {
            "status": "expired",
            "is_valid": False,
            "message": "No active subscription. Please subscribe to continue."
        }
    
    @staticmethod
    def get_available_plans(db: Session) -> List[Dict]:
        """Get all active subscription plans"""
        plans = db.query(SubscriptionPlan).filter(
            SubscriptionPlan.active == True
        ).order_by(SubscriptionPlan.price).all()
        
        return [
            {
                "id": plan.id,
                "plan_code": plan.plan_code,
                "plan_name": plan.plan_name,
                "plan_type": plan.plan_type,
                "duration_months": plan.duration_months,
                "price": float(plan.price),
                "max_users": plan.max_users,
                "max_branches": plan.max_branches,
                "max_products": plan.max_products,
                "features": {
                    "loans": plan.has_loans,
                    "batch_tracking": plan.has_batch_tracking,
                    "api_access": plan.has_api_access,
                    "multi_branch": plan.has_multi_branch,
                },
                "is_popular": plan.is_popular,
            }
            for plan in plans
        ]
        
        
        
        
        
        # ==================== REPORT SERVICE ====================
class ReportService:
    """Service for generating reports"""
    
    @staticmethod
    def generate_sales_report(db: Session, tenant_id: int, report_type: str, branch_id: Optional[int] = None) -> Dict:
        """Generate sales report (weekly, monthly, yearly, custom)"""
        now = datetime.now()
        
        if report_type == "weekly":
            start_date = now - timedelta(days=7)
        elif report_type == "monthly":
            start_date = now - timedelta(days=30)
        elif report_type == "yearly":
            start_date = now - timedelta(days=365)
        else:
            start_date = now - timedelta(days=30)
        
        query = db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.created_at >= start_date
        )
        
        if branch_id:
            query = query.filter(Sale.branch_id == branch_id)
        
        sales = query.all()
        total_revenue = sum(float(sale.total_amount) for sale in sales)
        total_profit = sum(float(sale.total_amount - sale.total_cost) for sale in sales)
        
        # Product sales breakdown
        product_sales = {}
        for sale in sales:
            for item in sale.items:
                if item.product_id not in product_sales:
                    product = db.query(Product).filter(Product.id == item.product_id).first()
                    product_sales[item.product_id] = {
                        "quantity": Decimal(0),
                        "revenue": Decimal(0),
                        "product_name": product.name if product else "Unknown",
                        "product_sku": product.sku if product else "N/A"
                    }
                product_sales[item.product_id]["quantity"] += item.quantity
                product_sales[item.product_id]["revenue"] += item.total
        
        best_sellers = sorted(product_sales.items(), key=lambda x: x[1]["quantity"], reverse=True)[:10]
        
        return {
            "report_type": report_type,
            "period": {"start": start_date, "end": now},
            "summary": {
                "total_sales": len(sales),
                "total_revenue": total_revenue,
                "total_profit": total_profit,
                "average_sale_value": total_revenue / len(sales) if sales else 0
            },
            "best_selling_products": [
                {"product_id": pid, "product_name": data["product_name"], "product_sku": data["product_sku"],
                 "quantity_sold": float(data["quantity"]), "revenue": float(data["revenue"])}
                for pid, data in best_sellers
            ]
        }
    
    @staticmethod
    def generate_loan_report(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> Dict:
        """Generate loan report"""
        query = db.query(Loan).filter(Loan.tenant_id == tenant_id)
        
        if branch_id:
            query = query.filter(Loan.branch_id == branch_id)
        
        loans = query.all()
        
        total_issued = sum(float(loan.total_amount) for loan in loans)
        total_repaid = sum(float(loan.paid_amount) for loan in loans)
        total_outstanding = sum(float(loan.remaining_amount) for loan in loans)
        
        active_loans = [l for l in loans if l.status in [LoanStatus.ACTIVE.value, LoanStatus.PARTIALLY_PAID.value]]
        overdue_loans = [l for l in active_loans if l.due_date < datetime.now()]
        
        return {
            "summary": {
                "total_loans": len(loans),
                "total_issued": total_issued,
                "total_repaid": total_repaid,
                "total_outstanding": total_outstanding,
                "active_loans": len(active_loans),
                "overdue_loans": len(overdue_loans),
                "repayment_rate": (total_repaid / total_issued * 100) if total_issued > 0 else 0
            }
        }
    
    @staticmethod
    def generate_inventory_report(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> Dict:
        """Generate inventory valuation report"""
        stock_query = db.query(Stock).join(Product).filter(Product.tenant_id == tenant_id)
        
        if branch_id:
            stock_query = stock_query.filter(Stock.branch_id == branch_id)
        
        stocks = stock_query.all()
        
        total_value = Decimal(0)
        items = []
        
        for stock in stocks:
            item_value = stock.quantity * stock.product.cost
            total_value += item_value
            
            items.append({
                "product_id": stock.product_id,
                "product_name": stock.product.name,
                "product_sku": stock.product.sku,
                "quantity": float(stock.quantity),
                "unit_cost": float(stock.product.cost),
                "total_value": float(item_value),
                "status": "low" if stock.quantity <= stock.reorder_level else "normal"
            })
        
        return {
            "total_inventory_value": float(total_value),
            "total_items": len(items),
            "items": items[:100]
        }
        
        
    # ==================== EMAIL SERVICE (BRAVO) ====================
class EmailService:
    """Service for sending emails using Bravo Email Service"""
    
    # Bravo API Configuration
    BRAVO_API_URL = os.getenv("BRAVO_API_URL", "https://api.bravo.com/v1/email/send")
    BRAVO_API_KEY = os.getenv("BRAVO_API_KEY", "")
    BRAVO_FROM_EMAIL = os.getenv("BRAVO_FROM_EMAIL", "noreply@inventorysystem.com")
    BRAVO_FROM_NAME = os.getenv("BRAVO_FROM_NAME", "Inventory System")
    
    @classmethod
    def send_otp_email(cls, to_email: str, otp_code: str, purpose: str = "verification") -> bool:
        """
        Send OTP code via Bravo email service
        """
        # If no API key, log OTP for development
        if not cls.BRAVO_API_KEY:
            logger.warning("⚠️ Bravo API key not configured. OTP email not sent.")
            logger.info(f"📧 [DEV] OTP for {to_email}: {otp_code}")
            return True
        
        subject = f"Your {purpose.replace('_', ' ').title()} Code - Inventory System"
        
        # HTML email template
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Verification Code</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                    background-color: #f4f7f6;
                    margin: 0;
                    padding: 0;
                }}
                .container {{
                    max-width: 560px;
                    margin: 0 auto;
                    background: #ffffff;
                    border-radius: 16px;
                    overflow: hidden;
                    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
                }}
                .header {{
                    background: linear-gradient(135deg, #2FB8A6 0%, #6FD3C3 100%);
                    padding: 32px 24px;
                    text-align: center;
                }}
                .logo {{
                    font-size: 28px;
                    font-weight: 700;
                    color: white;
                    letter-spacing: -0.5px;
                }}
                .logo-icon {{
                    width: 48px;
                    height: 48px;
                    margin: 0 auto 12px;
                    background: rgba(255, 255, 255, 0.2);
                    border-radius: 50%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }}
                .content {{
                    padding: 32px 24px;
                }}
                .greeting {{
                    font-size: 24px;
                    font-weight: 600;
                    color: #1A2A2E;
                    margin-bottom: 12px;
                }}
                .message {{
                    color: #5A6E73;
                    line-height: 1.6;
                    margin-bottom: 28px;
                }}
                .otp-container {{
                    background: #F0FDFA;
                    border-radius: 12px;
                    padding: 24px;
                    text-align: center;
                    margin: 24px 0;
                    border: 1px solid #C4F0E8;
                }}
                .otp-code {{
                    font-size: 42px;
                    font-weight: 700;
                    color: #2FB8A6;
                    letter-spacing: 8px;
                    font-family: 'Courier New', monospace;
                }}
                .expiry {{
                    font-size: 12px;
                    color: #8FA3A8;
                    text-align: center;
                    margin-top: 8px;
                }}
                .footer {{
                    background: #F9FAFB;
                    padding: 24px;
                    text-align: center;
                    font-size: 12px;
                    color: #9CA3AF;
                    border-top: 1px solid #E5E7EB;
                }}
                .security-note {{
                    background: #FEF3C7;
                    border-left: 4px solid #F59E0B;
                    padding: 12px 16px;
                    margin: 20px 0;
                    font-size: 13px;
                    color: #92400E;
                    border-radius: 8px;
                }}
            </style>
        </head>
        <body>
            <div style="padding: 20px; background: #f4f7f6;">
                <div class="container">
                    <div class="header">
                        <div class="logo-icon">
                            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="1.5">
                                <path d="M20 7L12 3L4 7L12 11L20 7Z" stroke="white"/>
                                <path d="M4 7V17L12 21L20 17V7" stroke="white"/>
                                <path d="M12 11V21" stroke="white"/>
                            </svg>
                        </div>
                        <div class="logo">Inventory System</div>
                    </div>
                    <div class="content">
                        <div class="greeting">Verification Code</div>
                        <div class="message">
                            Hello,<br><br>
                            You requested a {purpose.replace('_', ' ')} code for your account. 
                            Please use the following 6-digit code to complete your verification.
                        </div>
                        <div class="otp-container">
                            <div class="otp-code">{otp_code}</div>
                            <div class="expiry">⏰ This code expires in 10 minutes</div>
                        </div>
                        <div class="security-note">
                            🔒 If you didn't request this code, please ignore this email. 
                            Never share this code with anyone.
                        </div>
                    </div>
                    <div class="footer">
                        <p>© 2024 Inventory System. All rights reserved.</p>
                        <p style="margin-top: 8px;">This is an automated message, please do not reply.</p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        
        text_content = f"""
        Inventory System - Verification Code
        
        Hello,
        
        You requested a {purpose.replace('_', ' ')} code for your account.
        
        Your verification code is: {otp_code}
        
        This code expires in 10 minutes.
        
        If you didn't request this, please ignore this email.
        
        ---
        Inventory System
        """
        
        # Prepare email payload for Bravo API
        payload = {
            "to": to_email,
            "from_email": cls.BRAVO_FROM_EMAIL,
            "from_name": cls.BRAVO_FROM_NAME,
            "subject": subject,
            "html_content": html_content,
            "text_content": text_content,
            "track_opens": True,
            "track_clicks": True
        }
        
        headers = {
            "Authorization": f"Bearer {cls.BRAVO_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        try:
            import httpx
            with httpx.Client(timeout=30.0) as client:
                response = client.post(cls.BRAVO_API_URL, json=payload, headers=headers)
                
                if response.status_code in [200, 201, 202]:
                    logger.info(f"✅ OTP email sent to {to_email} via Bravo")
                    return True
                else:
                    logger.error(f"❌ Bravo API error: {response.status_code} - {response.text}")
                    logger.info(f"📧 OTP for {to_email}: {otp_code}")
                    return True
                    
        except Exception as e:
            logger.error(f"❌ Failed to send email: {str(e)}")
            logger.info(f"📧 OTP for {to_email}: {otp_code}")
            return True    
        
