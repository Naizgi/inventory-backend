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
    TempItem, BusinessType, MovementType, ReturnStatus, TenantStatus, UserRole,TenantSubscription, SubscriptionPlan, Payment
)
from app.schemas import (
    UserCreate, SaleCreate, PurchaseCreate, StockCreate,
    CategoryCreate, UnitCreate, BatchCreate, SaleReturnCreate,
    PurchaseOrderCreate, LoanCreate, TempItemCreate, TenantCreate,SubscriptionStatus, PaymentStatus
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


# ==================== TENANT SERVICE (NEW) ====================
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
            subscription_plan=tenant_data.subscription_plan,
            subscription_start=datetime.now(),
            subscription_end=tenant_data.subscription_end,
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
            "subscription_plan": tenant.subscription_plan,
            "subscription_end": tenant.subscription_end,
            "status": tenant.status
        }


# ==================== AUTH SERVICE (UPDATED) ====================
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


# ==================== BRANCH SERVICE (UPDATED) ====================
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


# ==================== CATEGORY SERVICE (UPDATED) ====================
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


# ==================== UNIT SERVICE (UPDATED) ====================
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


# ==================== PRODUCT SERVICE (UPDATED) ====================
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


# ==================== BATCH SERVICE (UPDATED) ====================
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


# ==================== STOCK SERVICE (UPDATED) ====================
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


# ==================== SALE SERVICE (UPDATED) ====================
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


# ==================== SALE RETURN SERVICE (UPDATED) ====================
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
    
    @staticmethod
    def approve_return(db: Session, return_id: int, approver_id: int, tenant_id: int) -> Optional[SaleReturn]:
        return_item = db.query(SaleReturn).filter(
            SaleReturn.id == return_id,
            SaleReturn.tenant_id == tenant_id
        ).first()
        if not return_item:
            return None
        
        return_item.status = ReturnStatus.APPROVED.value
        return_item.approved_by = approver_id
        return_item.approved_at = datetime.now()
        
        db.commit()
        db.refresh(return_item)
        return return_item


# ==================== PURCHASE ORDER SERVICE (UPDATED) ====================
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
            status=PurchaseStatus.PENDING.value,
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
    
    @staticmethod
    def receive_purchase_order(db: Session, po_id: int, receive_data, user_id: int, tenant_id: int) -> PurchaseOrder:
        po = db.query(PurchaseOrder).filter(
            PurchaseOrder.id == po_id,
            PurchaseOrder.tenant_id == tenant_id
        ).first()
        if not po:
            raise ValueError("Purchase order not found")
        
        for receive_item in receive_data.items:
            po_item = db.query(PurchaseOrderItem).filter(
                PurchaseOrderItem.purchase_order_id == po_id,
                PurchaseOrderItem.product_id == receive_item.product_id
            ).first()
            
            if not po_item:
                continue
            
            po_item.quantity_received = receive_item.quantity_received
            po_item.received_at = datetime.now()
            
            # Create batch if needed
            product = db.query(Product).filter(
                Product.id == receive_item.product_id,
                Product.tenant_id == tenant_id
            ).first()
            if product and product.track_batch and receive_item.batch_number:
                batch = Batch(
                    tenant_id=tenant_id,
                    product_id=receive_item.product_id,
                    branch_id=po.branch_id,
                    batch_number=receive_item.batch_number,
                    expiry_date=receive_item.expiry_date,
                    quantity=receive_item.quantity_received,
                    remaining_quantity=receive_item.quantity_received,
                    unit_cost=po_item.unit_cost
                )
                db.add(batch)
                db.flush()
                batch_id = batch.id
            else:
                batch_id = None
            
            # Update stock
            StockService.add_stock(
                db, po.branch_id, receive_item.product_id,
                receive_item.quantity_received, user_id, tenant_id,
                f"Purchase Order #{po.order_number}",
                batch_id=batch_id
            )
        
        po.actual_delivery_date = receive_data.actual_delivery_date
        po.status = PurchaseStatus.COMPLETED.value
        
        db.commit()
        db.refresh(po)
        return po


# ==================== LOAN SERVICE (UPDATED) ====================
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
            payment_method=payment_data.payment_method.value if hasattr(payment_data.payment_method, 'value') else payment_data.payment_method,
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
    
    @staticmethod
    def get_overdue_loans(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> List[Loan]:
        query = db.query(Loan).filter(
            Loan.tenant_id == tenant_id,
            Loan.due_date < datetime.now(),
            Loan.status.in_([LoanStatus.ACTIVE.value, LoanStatus.PARTIALLY_PAID.value])
        )
        if branch_id:
            query = query.filter(Loan.branch_id == branch_id)
        return query.all()


# ==================== ALERT SERVICE (UPDATED) ====================
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
    def get_alerts(db: Session, tenant_id: int, resolved: bool = False, branch_id: Optional[int] = None) -> List:
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
                "branch_id": alert.branch_id,
                "branch_name": branch.name if branch else "Unknown Branch",
                "product_id": alert.product_id,
                "product_name": product.name if product else "Unknown Product",
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


# ==================== REPORT SERVICE (UPDATED) ====================
class ReportService:
    @staticmethod
    def generate_sales_report(db: Session, tenant_id: int, report_type: str, branch_id: Optional[int] = None) -> Dict:
        now = datetime.now()
        if report_type == "weekly":
            start_date = now - timedelta(days=7)
        elif report_type == "monthly":
            start_date = now - timedelta(days=30)
        else:
            raise ValueError("Report type must be 'weekly' or 'monthly'")
        
        query = db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.created_at >= start_date
        )
        if branch_id:
            query = query.filter(Sale.branch_id == branch_id)
        
        sales = query.all()
        total_revenue = sum(float(sale.total_amount) for sale in sales)
        total_profit = sum(float(sale.total_amount - sale.total_cost) for sale in sales)
        
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
        slow_movers = sorted(product_sales.items(), key=lambda x: x[1]["quantity"])[:10]
        
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
            ],
            "slow_moving_products": [
                {"product_id": pid, "product_name": data["product_name"], "product_sku": data["product_sku"], 
                 "quantity_sold": float(data["quantity"]), "revenue": float(data["revenue"])}
                for pid, data in slow_movers
            ]
        }
    
    @staticmethod
    def generate_loan_report(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> Dict:
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


# ==================== SETTINGS SERVICE (UPDATED) ====================
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
    def get_category_settings(db: Session, category: str, tenant_id: Optional[int] = None) -> Dict[str, Any]:
        query = db.query(SystemSetting).filter(SystemSetting.category == category)
        if tenant_id:
            query = query.filter(SystemSetting.tenant_id == tenant_id)
        else:
            query = query.filter(SystemSetting.tenant_id.is_(None))
        
        settings_list = query.all()
        return {s.key: SettingsService._get_value(s) for s in settings_list}
    
    @staticmethod
    def set_setting(db: Session, category: str, key: str, value: Any, 
                    user_id: int = None, tenant_id: Optional[int] = None) -> Any:
        query = db.query(SystemSetting).filter(
            SystemSetting.category == category,
            SystemSetting.key == key
        )
        if tenant_id:
            query = query.filter(SystemSetting.tenant_id == tenant_id)
        else:
            query = query.filter(SystemSetting.tenant_id.is_(None))
        
        setting = query.first()
        
        old_value = SettingsService._get_value(setting) if setting else None
        
        if setting:
            setting.value = SettingsService._set_value(value)
        else:
            setting = SystemSetting(
                tenant_id=tenant_id,
                category=category,
                key=key,
                value=SettingsService._set_value(value)
            )
            db.add(setting)
        
        db.commit()
        db.refresh(setting)
        
        if user_id:
            log = SystemLog(
                tenant_id=tenant_id,
                log_type="settings",
                message=f"Setting changed: {category}.{key}",
                details=f"Old: {old_value}, New: {value}",
                user_id=user_id
            )
            db.add(log)
            db.commit()
        
        return SettingsService._get_value(setting)
    
    @staticmethod
    def set_multiple_settings(db: Session, category: str, settings_dict: Dict[str, Any], 
                              user_id: int = None, tenant_id: Optional[int] = None):
        for key, value in settings_dict.items():
            SettingsService.set_setting(db, category, key, value, user_id, tenant_id)
    
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
    
    @staticmethod
    def initialize_default_settings(db: Session, tenant_id: Optional[int] = None):
        defaults = {
            "general": {
                "system_name": "Inventory System",
                "timezone": "Africa/Addis_Ababa",
                "date_format": "YYYY-MM-DD",
                "currency": "ETB",
                "language": "en",
                "items_per_page": 20,
                "business_type": "shop"
            },
            "notification": {
                "low_stock_email": True,
                "daily_report_email": True,
                "sms_alerts": False,
                "loan_overdue_alerts": True,
                "email_recipients": ["admin@example.com"],
                "sms_recipients": []
            },
            "backup": {
                "auto_backup": True,
                "frequency": "daily",
                "backup_time": "23:00",
                "location": "local",
                "retention_days": 30
            }
        }
        
        for category, category_settings in defaults.items():
            for key, value in category_settings.items():
                existing = db.query(SystemSetting).filter(
                    SystemSetting.category == category,
                    SystemSetting.key == key
                )
                if tenant_id:
                    existing = existing.filter(SystemSetting.tenant_id == tenant_id)
                else:
                    existing = existing.filter(SystemSetting.tenant_id.is_(None))
                
                if not existing.first():
                    db.add(SystemSetting(
                        tenant_id=tenant_id,
                        category=category,
                        key=key,
                        value=SettingsService._set_value(value)
                    ))
        db.commit()
    
    @staticmethod
    def get_system_info(db: Session, tenant_id: Optional[int] = None) -> Dict:
        user_query = db.query(User)
        product_query = db.query(Product)
        branch_query = db.query(Branch)
        
        if tenant_id:
            user_query = user_query.filter(User.tenant_id == tenant_id)
            product_query = product_query.filter(Product.tenant_id == tenant_id)
            branch_query = branch_query.filter(Branch.tenant_id == tenant_id)
        
        total_users = user_query.count()
        total_products = product_query.count()
        total_branches = branch_query.count()
        
        last_week = datetime.now() - timedelta(days=7)
        sale_query = db.query(Sale).filter(Sale.created_at >= last_week)
        if tenant_id:
            sale_query = sale_query.filter(Sale.tenant_id == tenant_id)
        recent_sales = sale_query.count()
        
        last_backup = db.query(BackupRecord).order_by(BackupRecord.created_at.desc()).first()
        
        loan_query = db.query(Loan).filter(Loan.status.in_(['active', 'partially_paid']))
        if tenant_id:
            loan_query = loan_query.filter(Loan.tenant_id == tenant_id)
        active_loans = loan_query.count()
        
        cache_size = SettingsService.get_setting(db, "system", "cache_size", tenant_id) or 24.5
        
        return {
            "version": "4.0.0",
            "build_date": "2025-04-10",
            "database": "PostgreSQL/SQLite",
            "server_status": "online",
            "total_users": total_users,
            "total_products": total_products,
            "total_branches": total_branches,
            "recent_sales": recent_sales,
            "uptime_days": 45,
            "active_loans": active_loans,
            "last_backup": last_backup.created_at.isoformat() if last_backup else None,
            "cache_size_mb": float(cache_size),
            "last_cache_clear": SettingsService.get_setting(db, "system", "last_cache_clear", tenant_id)
        }
    
    @staticmethod
    def clear_cache() -> Dict:
        return {"cleared": True, "size_freed_mb": 24.5}
    
    @staticmethod
    def create_backup(db: Session, user_id: int = None, tenant_id: Optional[int] = None) -> Dict[str, Any]:
        try:
            backup_dir = "backups"
            os.makedirs(backup_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            tenant_suffix = f"_tenant_{tenant_id}" if tenant_id else ""
            backup_filename = f"backup_{timestamp}{tenant_suffix}.sql"
            backup_path = os.path.join(backup_dir, backup_filename)
            with open(backup_path, 'w') as f:
                f.write(f"-- Backup created at {datetime.now()}\n")
                f.write(f"-- Tenant ID: {tenant_id if tenant_id else 'System'}\n")
                f.write("-- Database backup content\n")
            file_size = os.path.getsize(backup_path) / (1024 * 1024)
            backup = BackupRecord(
                tenant_id=tenant_id,
                name=backup_filename,
                file_path=backup_path,
                size_mb=file_size,
                created_by=user_id
            )
            db.add(backup)
            db.commit()
            if user_id:
                log = SystemLog(
                    tenant_id=tenant_id,
                    log_type="backup",
                    message=f"Backup created: {backup_filename}",
                    details=f"Size: {file_size:.2f} MB",
                    user_id=user_id
                )
                db.add(log)
                db.commit()
            return {"id": backup.id, "name": backup.name, "size_mb": file_size, "created_at": backup.created_at.isoformat()}
        except Exception as e:
            raise Exception(f"Failed to create backup: {str(e)}")
    
    @staticmethod
    def get_backups(db: Session, limit: int = 10, tenant_id: Optional[int] = None) -> List[Dict]:
        query = db.query(BackupRecord)
        if tenant_id:
            query = query.filter(BackupRecord.tenant_id == tenant_id)
        backups = query.order_by(BackupRecord.created_at.desc()).limit(limit).all()
        return [{"id": b.id, "name": b.name, "size_mb": float(b.size_mb), "created_at": b.created_at.isoformat(), "created_by": b.creator.name if b.creator else "System"} for b in backups]
    
    @staticmethod
    def delete_backup(db: Session, backup_id: int, user_id: int = None, tenant_id: Optional[int] = None) -> bool:
        query = db.query(BackupRecord).filter(BackupRecord.id == backup_id)
        if tenant_id:
            query = query.filter(BackupRecord.tenant_id == tenant_id)
        backup = query.first()
        if backup:
            if os.path.exists(backup.file_path):
                os.remove(backup.file_path)
            db.delete(backup)
            db.commit()
            if user_id:
                log = SystemLog(
                    tenant_id=tenant_id,
                    log_type="backup",
                    message=f"Backup deleted: {backup.name}",
                    user_id=user_id
                )
                db.add(log)
                db.commit()
            return True
        return False
    
    @staticmethod
    def export_all_data(db: Session, tenant_id: int) -> Dict:
        products = db.query(Product).filter(Product.tenant_id == tenant_id).all()
        branches = db.query(Branch).filter(Branch.tenant_id == tenant_id).all()
        users = db.query(User).filter(User.tenant_id == tenant_id).all()
        categories = db.query(Category).filter(Category.tenant_id == tenant_id).all()
        units = db.query(Unit).filter(Unit.tenant_id == tenant_id).all()
        
        return {
            "export_date": datetime.now().isoformat(),
            "export_version": "4.0.0",
            "tenant_id": tenant_id,
            "categories": [{"id": c.id, "name": c.name, "parent_id": c.parent_id} for c in categories],
            "units": [{"id": u.id, "name": u.name, "symbol": u.symbol} for u in units],
            "products": [{"id": p.id, "sku": p.sku, "name": p.name, "description": p.description, 
                         "price": float(p.price), "cost": float(p.cost), "active": p.active,
                         "category_id": p.category_id, "unit_id": p.unit_id, "barcode": p.barcode,
                         "has_expiry": p.has_expiry, "track_batch": p.track_batch} for p in products],
            "branches": [{"id": b.id, "name": b.name, "business_type": b.business_type, 
                         "address": b.address, "phone": b.phone} for b in branches],
            "users": [{"id": u.id, "name": u.name, "email": u.email, "role": u.role, 
                      "branch_id": u.branch_id, "active": u.active} for u in users]
        }
    
    @staticmethod
    def reset_system_data(db: Session, tenant_id: int, user_id: int = None) -> Dict:
        try:
            # Delete in correct order (child tables first) for specific tenant
            db.query(LoanPayment).filter(LoanPayment.loan.has(Loan.tenant_id == tenant_id)).delete(synchronize_session=False)
            db.query(LoanItem).filter(LoanItem.loan.has(Loan.tenant_id == tenant_id)).delete(synchronize_session=False)
            db.query(Loan).filter(Loan.tenant_id == tenant_id).delete()
            db.query(SaleReturnItem).filter(SaleReturnItem.return_parent.has(SaleReturn.tenant_id == tenant_id)).delete(synchronize_session=False)
            db.query(SaleReturn).filter(SaleReturn.tenant_id == tenant_id).delete()
            db.query(SaleItem).filter(SaleItem.sale.has(Sale.tenant_id == tenant_id)).delete(synchronize_session=False)
            db.query(Sale).filter(Sale.tenant_id == tenant_id).delete()
            db.query(PurchaseOrderItem).filter(PurchaseOrderItem.purchase_order.has(PurchaseOrder.tenant_id == tenant_id)).delete(synchronize_session=False)
            db.query(PurchaseOrder).filter(PurchaseOrder.tenant_id == tenant_id).delete()
            db.query(PurchaseItem).filter(PurchaseItem.purchase.has(Purchase.tenant_id == tenant_id)).delete(synchronize_session=False)
            db.query(Purchase).filter(Purchase.tenant_id == tenant_id).delete()
            db.query(StockMovement).filter(StockMovement.branch.has(Branch.tenant_id == tenant_id)).delete(synchronize_session=False)
            db.query(Batch).filter(Batch.tenant_id == tenant_id).delete()
            db.query(Stock).filter(Stock.branch.has(Branch.tenant_id == tenant_id)).delete(synchronize_session=False)
            db.query(Alert).filter(Alert.tenant_id == tenant_id).delete()
            db.query(TempItem).filter(TempItem.tenant_id == tenant_id).delete()
            db.commit()
            
            if user_id:
                log = SystemLog(
                    tenant_id=tenant_id,
                    log_type="warning",
                    message="System data reset",
                    details="All transactional data has been cleared",
                    user_id=user_id
                )
                db.add(log)
                db.commit()
            return {"message": "System data reset successfully"}
        except Exception as e:
            db.rollback()
            raise Exception(f"Failed to reset data: {str(e)}")
        
        
        
        
        
        # ==================== SUBSCRIPTION SERVICE (NEW) ====================
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
    def get_current_subscription(db: Session, tenant_id: int) -> Optional[TenantSubscription]:
        """Get current subscription (active or pending)"""
        return db.query(TenantSubscription).filter(
            TenantSubscription.tenant_id == tenant_id,
            TenantSubscription.is_current == True
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
        
        # Grace period check
        latest_sub = db.query(TenantSubscription).filter(
            TenantSubscription.tenant_id == tenant_id
        ).order_by(TenantSubscription.end_date.desc()).first()
        
        if latest_sub:
            grace_end = latest_sub.end_date + timedelta(days=settings.GRACE_PERIOD_DAYS)
            if datetime.now() < grace_end:
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
                "trial_end": tenant.trial_end.isoformat() if tenant.trial_end else None,
                "trial_start": tenant.trial_start.isoformat() if tenant.trial_start else None,
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
                "start_date": active_sub.start_date.isoformat() if active_sub.start_date else None,
                "end_date": active_sub.end_date.isoformat() if active_sub.end_date else None,
                "plan_name": plan.plan_name if plan else "Unknown",
                "plan_type": plan.plan_type if plan else None,
                "auto_renew": active_sub.auto_renew,
                "message": f"Active subscription: {days_left} days remaining"
            }
        
        # Pending payment
        pending_sub = db.query(TenantSubscription).filter(
            TenantSubscription.tenant_id == tenant_id,
            TenantSubscription.payment_status == PaymentStatus.PENDING.value
        ).first()
        
        if pending_sub:
            return {
                "status": "pending_payment",
                "is_valid": False,
                "message": "Payment pending. Please complete payment to activate."
            }
        
        # Expired
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
                "max_storage_mb": plan.max_storage_mb,
                "features": {
                    "loans": plan.has_loans,
                    "batch_tracking": plan.has_batch_tracking,
                    "pharmacy_features": plan.has_pharmacy_features,
                    "advanced_reports": plan.has_advanced_reports,
                    "api_access": plan.has_api_access,
                    "custom_branding": plan.has_custom_branding,
                    "multi_branch": plan.has_multi_branch,
                    "priority_support": plan.has_priority_support,
                },
                "discount_percentage": float(plan.discount_percentage),
                "is_popular": plan.is_popular,
                "active": plan.active
            }
            for plan in plans
        ]
    
    @staticmethod
    def create_subscription(
        db: Session,
        tenant_id: int,
        plan_id: int,
        payment_method: str,
        auto_renew: bool = False
    ) -> TenantSubscription:
        """Create a new subscription for a tenant"""
        
        # Validate tenant
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise ValueError("Tenant not found")
        
        # Validate plan
        plan = db.query(SubscriptionPlan).filter(
            SubscriptionPlan.id == plan_id,
            SubscriptionPlan.active == True
        ).first()
        if not plan:
            raise ValueError("Plan not found or inactive")
        
        # Check for existing active subscription
        existing_active = SubscriptionService.get_active_subscription(db, tenant_id)
        if existing_active:
            raise ValueError("Tenant already has an active subscription")
        
        # Check for pending subscription
        pending_sub = db.query(TenantSubscription).filter(
            TenantSubscription.tenant_id == tenant_id,
            TenantSubscription.payment_status == PaymentStatus.PENDING.value
        ).first()
        if pending_sub:
            raise ValueError("Tenant already has a pending subscription. Complete payment or cancel it first.")
        
        # Create payment record
        payment_number = f"PAY-{datetime.now().strftime('%Y%m%d%H%M%S')}-{tenant_id}"
        payment = Payment(
            tenant_id=tenant_id,
            payment_number=payment_number,
            amount=plan.price,
            payment_method=payment_method,
            payment_status=PaymentStatus.PENDING.value,
            payment_type="subscription",
            notes=f"Subscription to {plan.plan_name}",
            created_at=datetime.now()
        )
        db.add(payment)
        db.flush()
        
        # Calculate subscription dates
        start_date = datetime.now()
        end_date = start_date + timedelta(days=plan.duration_months * 30)
        
        # Create subscription
        subscription = TenantSubscription(
            tenant_id=tenant_id,
            plan_id=plan.id,
            start_date=start_date,
            end_date=end_date,
            status=SubscriptionStatus.PENDING_PAYMENT.value,
            auto_renew=auto_renew,
            amount_paid=plan.price,
            payment_status=PaymentStatus.PENDING.value,
            payment_id=payment.id,
            created_at=datetime.now()
        )
        db.add(subscription)
        
        # Update tenant status
        tenant.status = TenantStatus.PENDING_PAYMENT.value
        tenant.updated_at = datetime.now()
        
        db.flush()
        return subscription
    
    @staticmethod
    def verify_payment(
        db: Session,
        payment_id: int,
        verified: bool,
        verifier_id: int,
        rejection_reason: Optional[str] = None
    ) -> Dict:
        """Verify or reject a payment"""
        
        payment = db.query(Payment).filter(Payment.id == payment_id).first()
        if not payment:
            raise ValueError("Payment not found")
        
        if payment.payment_status != PaymentStatus.PENDING.value:
            raise ValueError(f"Payment is already {payment.payment_status}")
        
        if verified:
            # Approve payment
            payment.payment_status = PaymentStatus.COMPLETED.value
            payment.verified_by = verifier_id
            payment.verified_at = datetime.now()
            payment.payment_date = datetime.now()
            
            # Activate subscription
            subscription = db.query(TenantSubscription).filter(
                TenantSubscription.payment_id == payment.id
            ).first()
            
            if subscription:
                subscription.payment_status = PaymentStatus.COMPLETED.value
                subscription.status = SubscriptionStatus.ACTIVE.value
                subscription.is_current = True
                subscription.activated_at = datetime.now()
                
                # Deactivate other subscriptions for this tenant
                db.query(TenantSubscription).filter(
                    TenantSubscription.tenant_id == subscription.tenant_id,
                    TenantSubscription.id != subscription.id,
                    TenantSubscription.status == SubscriptionStatus.ACTIVE.value
                ).update({
                    "status": SubscriptionStatus.EXPIRED.value,
                    "is_current": False
                })
                
                # Activate tenant
                tenant = db.query(Tenant).filter(Tenant.id == subscription.tenant_id).first()
                if tenant:
                    tenant.status = TenantStatus.ACTIVE.value
                    tenant.updated_at = datetime.now()
            
            return {"status": "approved", "message": "Payment verified and subscription activated"}
        else:
            # Reject payment
            payment.payment_status = PaymentStatus.FAILED.value
            payment.rejection_reason = rejection_reason
            payment.verified_by = verifier_id
            payment.verified_at = datetime.now()
            
            # Cancel subscription
            subscription = db.query(TenantSubscription).filter(
                TenantSubscription.payment_id == payment.id
            ).first()
            if subscription:
                subscription.status = SubscriptionStatus.CANCELLED.value
                subscription.payment_status = PaymentStatus.FAILED.value
            
            return {"status": "rejected", "message": f"Payment rejected: {rejection_reason}"}
    
    @staticmethod
    def cancel_subscription(db: Session, tenant_id: int) -> bool:
        """Cancel current subscription"""
        
        active_sub = db.query(TenantSubscription).filter(
            TenantSubscription.tenant_id == tenant_id,
            TenantSubscription.status.in_([
                SubscriptionStatus.ACTIVE.value,
                SubscriptionStatus.PENDING_PAYMENT.value
            ])
        ).first()
        
        if not active_sub:
            raise ValueError("No active or pending subscription found")
        
        active_sub.status = SubscriptionStatus.CANCELLED.value
        active_sub.cancelled_at = datetime.now()
        active_sub.is_current = False
        
        # Update tenant status if no other subscription
        other_active = db.query(TenantSubscription).filter(
            TenantSubscription.tenant_id == tenant_id,
            TenantSubscription.id != active_sub.id,
            TenantSubscription.status == SubscriptionStatus.ACTIVE.value
        ).first()
        
        if not other_active:
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if tenant:
                tenant.status = TenantStatus.EXPIRED.value
                tenant.updated_at = datetime.now()
        
        return True
    
    @staticmethod
    def get_payment_history(db: Session, tenant_id: int) -> List[Dict]:
        """Get payment history for a tenant"""
        
        payments = db.query(Payment).filter(
            Payment.tenant_id == tenant_id
        ).order_by(Payment.created_at.desc()).all()
        
        return [
            {
                "id": p.id,
                "payment_number": p.payment_number,
                "amount": float(p.amount),
                "payment_method": p.payment_method,
                "payment_status": p.payment_status,
                "payment_type": p.payment_type,
                "transaction_reference": p.transaction_reference,
                "payment_date": p.payment_date.isoformat() if p.payment_date else None,
                "receipt_url": p.receipt_url,
                "notes": p.notes,
                "created_at": p.created_at.isoformat() if p.created_at else None
            }
            for p in payments
        ]
    
    @staticmethod
    def get_subscription_features(db: Session, tenant_id: int) -> Dict:
        """Get features available based on subscription"""
        
        # Default features (no subscription needed)
        features = {
            "max_users": 1,
            "max_branches": 1,
            "max_products": 100,
            "has_loans": False,
            "has_batch_tracking": False,
            "has_pharmacy_features": False,
            "has_advanced_reports": False,
            "has_api_access": False,
            "has_custom_branding": False,
            "has_multi_branch": False,
            "has_priority_support": False,
        }
        
        # Check trial
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant and tenant.status == TenantStatus.TRIAL.value:
            # Trial gets all features
            return {
                "max_users": 10,
                "max_branches": 3,
                "max_products": 5000,
                "has_loans": True,
                "has_batch_tracking": True,
                "has_pharmacy_features": True,
                "has_advanced_reports": True,
                "has_api_access": True,
                "has_custom_branding": False,
                "has_multi_branch": True,
                "has_priority_support": False,
            }
        
        # Check active subscription
        active_sub = SubscriptionService.get_active_subscription(db, tenant_id)
        if active_sub and active_sub.plan:
            plan = active_sub.plan
            features = {
                "max_users": plan.max_users,
                "max_branches": plan.max_branches,
                "max_products": plan.max_products,
                "has_loans": plan.has_loans,
                "has_batch_tracking": plan.has_batch_tracking,
                "has_pharmacy_features": plan.has_pharmacy_features,
                "has_advanced_reports": plan.has_advanced_reports,
                "has_api_access": plan.has_api_access,
                "has_custom_branding": plan.has_custom_branding,
                "has_multi_branch": plan.has_multi_branch,
                "has_priority_support": plan.has_priority_support,
            }
        
        return features