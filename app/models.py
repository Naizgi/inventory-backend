import enum
from sqlalchemy import Column, Integer, String, Text, Float, Boolean, DateTime, ForeignKey, DECIMAL, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base

# ==================== ENUMS ====================
class BusinessType(str, enum.Enum):
    SHOP = "shop"
    PHARMACY = "pharmacy"
    MINI_MARKET = "mini_market"
    SUPERMARKET = "supermarket"

class TenantStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIAL = "trial"
    EXPIRED = "expired"
    PENDING_PAYMENT = "pending_payment"

class UserRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    TENANT_ADMIN = "tenant_admin"
    MANAGER = "manager"
    SALESMAN = "salesman"

class PurchaseStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    PARTIALLY_RECEIVED = "partially_received"

class LoanStatus(str, enum.Enum):
    ACTIVE = "active"
    PARTIALLY_PAID = "partially_paid"
    SETTLED = "settled"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"

class LoanPaymentMethod(str, enum.Enum):
    CASH = "cash"
    TICKET = "ticket"
    COUPON = "coupon"
    MIXED = "mixed"

class TempItemStatus(str, enum.Enum):
    PENDING = "pending"
    RECEIVED = "received"
    CANCELLED = "cancelled"

class MovementType(str, enum.Enum):
    SALE = "sale"
    PURCHASE = "purchase"
    ADJUSTMENT = "adjustment"
    RETURN = "return"
    TRANSFER = "transfer"

class ReturnStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"

class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"

class PaymentMethod(str, enum.Enum):
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    CREDIT_CARD = "credit_card"
    MOBILE_MONEY = "mobile_money"
    OTHER = "other"

class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    PENDING_PAYMENT = "pending_payment"

# ==================== SUBSCRIPTION PLAN MODEL ====================
class SubscriptionPlan(Base):
    """Predefined subscription plans configuration"""
    __tablename__ = "subscription_plans"
    
    id = Column(Integer, primary_key=True, index=True)
    plan_code = Column(String(50), unique=True, nullable=False, index=True)
    plan_name = Column(String(100), nullable=False)
    plan_type = Column(String(50), nullable=False)  # basic, professional, enterprise
    duration_months = Column(Integer, nullable=False)  # 3, 6, 12
    price = Column(DECIMAL(12, 2), nullable=False)
    max_users = Column(Integer, default=5)
    max_branches = Column(Integer, default=1)
    max_products = Column(Integer, default=1000)
    max_storage_mb = Column(Integer, default=500)
    
    # Feature flags
    has_loans = Column(Boolean, default=False)
    has_batch_tracking = Column(Boolean, default=False)
    has_pharmacy_features = Column(Boolean, default=False)
    has_advanced_reports = Column(Boolean, default=False)
    has_api_access = Column(Boolean, default=False)
    has_custom_branding = Column(Boolean, default=False)
    has_multi_branch = Column(Boolean, default=False)
    has_priority_support = Column(Boolean, default=False)
    
    # Offers
    discount_percentage = Column(DECIMAL(5, 2), default=0)
    is_popular = Column(Boolean, default=False)
    
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    subscriptions = relationship("TenantSubscription", back_populates="plan")


# ==================== TENANT MODEL ====================
class Tenant(Base):
    """Multi-tenant core model - each tenant is a separate business"""
    __tablename__ = "tenants"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    subdomain = Column(String(100), unique=True, nullable=True)
    business_type = Column(String(50), default=BusinessType.SHOP.value)
    status = Column(String(50), default=TenantStatus.TRIAL.value)
    
    # Contact Information
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    address = Column(Text, nullable=True)
    logo_url = Column(String(500), nullable=True)
    
    # Configuration
    settings = Column(Text, nullable=True)  # JSON string for tenant-specific settings
    
    # Trial period tracking
    trial_start = Column(DateTime, nullable=True)
    trial_end = Column(DateTime, nullable=True)
    
    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_by = Column(Integer, nullable=True)  # Super admin ID
    
    # Relationships
    subscriptions = relationship("TenantSubscription", back_populates="tenant", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="tenant", cascade="all, delete-orphan")
    branches = relationship("Branch", back_populates="tenant", cascade="all, delete-orphan")
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    products = relationship("Product", back_populates="tenant", cascade="all, delete-orphan")
    categories = relationship("Category", back_populates="tenant", cascade="all, delete-orphan")
    units = relationship("Unit", back_populates="tenant", cascade="all, delete-orphan")
    sales = relationship("Sale", back_populates="tenant", cascade="all, delete-orphan")
    purchases = relationship("Purchase", back_populates="tenant", cascade="all, delete-orphan")
    purchase_orders = relationship("PurchaseOrder", back_populates="tenant", cascade="all, delete-orphan")
    loans = relationship("Loan", back_populates="tenant", cascade="all, delete-orphan")
    alerts = relationship("Alert", back_populates="tenant", cascade="all, delete-orphan")
    system_logs = relationship("SystemLog", back_populates="tenant", cascade="all, delete-orphan")
    batches = relationship("Batch", back_populates="tenant", cascade="all, delete-orphan")
    sale_returns = relationship("SaleReturn", back_populates="tenant", cascade="all, delete-orphan")
    temp_items = relationship("TempItem", back_populates="tenant", cascade="all, delete-orphan")


# ==================== TENANT SUBSCRIPTION MODEL ====================
class TenantSubscription(Base):
    """Tracks active and historical subscriptions for each tenant"""
    __tablename__ = "tenant_subscriptions"
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id"), nullable=False)
    
    # Subscription period
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    
    # Status tracking
    status = Column(String(50), default=SubscriptionStatus.PENDING_PAYMENT.value)
    auto_renew = Column(Boolean, default=False)
    is_current = Column(Boolean, default=False)
    
    # Payment tracking
    amount_paid = Column(DECIMAL(12, 2), nullable=False)
    payment_status = Column(String(50), default=PaymentStatus.PENDING.value)
    payment_id = Column(Integer, ForeignKey("payments.id"), nullable=True)
    
    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    activated_at = Column(DateTime(timezone=True), nullable=True)
    expired_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    grace_period_end = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="subscriptions")
    plan = relationship("SubscriptionPlan", back_populates="subscriptions")
    payment = relationship("Payment", foreign_keys=[payment_id], back_populates="subscription_payments")


# ==================== PAYMENT MODEL ====================
class Payment(Base):
    """Tracks all payments made by tenants"""
    __tablename__ = "payments"
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    
    payment_number = Column(String(50), unique=True, nullable=False, index=True)
    amount = Column(DECIMAL(12, 2), nullable=False)
    payment_method = Column(String(50), nullable=False)
    payment_status = Column(String(50), default=PaymentStatus.PENDING.value)
    payment_type = Column(String(50), default="subscription")  # subscription, renewal, upgrade
    
    # Payment details
    transaction_reference = Column(String(255), nullable=True, unique=True)
    payment_date = Column(DateTime(timezone=True), nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=True)
    
    # For bank transfers
    bank_name = Column(String(100), nullable=True)
    account_number = Column(String(50), nullable=True)
    account_holder = Column(String(255), nullable=True)
    
    # For mobile money
    phone_number = Column(String(50), nullable=True)
    provider = Column(String(50), nullable=True)  # M-Pesa, Airtel Money, etc.
    
    # Receipt/Proof
    receipt_url = Column(String(500), nullable=True)
    verified_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    
    # Invoice details
    invoice_number = Column(String(50), nullable=True, unique=True)
    invoice_url = Column(String(500), nullable=True)
    
    # Metadata
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    tenant = relationship("Tenant", back_populates="payments")
    verifier = relationship("User", foreign_keys=[verified_by])
    subscription_payments = relationship("TenantSubscription", back_populates="payment")
    invoices = relationship("Invoice", back_populates="payment", cascade="all, delete-orphan")


# ==================== INVOICE MODEL ====================
class Invoice(Base):
    """Generated invoices for payments"""
    __tablename__ = "invoices"
    
    id = Column(Integer, primary_key=True, index=True)
    payment_id = Column(Integer, ForeignKey("payments.id", ondelete="CASCADE"), nullable=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    
    invoice_number = Column(String(50), unique=True, nullable=False, index=True)
    invoice_date = Column(DateTime(timezone=True), server_default=func.now())
    due_date = Column(DateTime(timezone=True), nullable=False)
    
    subtotal = Column(DECIMAL(12, 2), nullable=False)
    tax_amount = Column(DECIMAL(12, 2), default=0)
    discount_amount = Column(DECIMAL(12, 2), default=0)
    total_amount = Column(DECIMAL(12, 2), nullable=False)
    
    status = Column(String(50), default="pending")  # pending, paid, overdue, cancelled
    description = Column(Text, nullable=True)
    
    # PDF/Print details
    invoice_file_url = Column(String(500), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    payment = relationship("Payment", back_populates="invoices")
    tenant = relationship("Tenant")
    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")


class InvoiceItem(Base):
    """Line items for invoices"""
    __tablename__ = "invoice_items"
    
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    
    description = Column(String(255), nullable=False)
    quantity = Column(Integer, default=1)
    unit_price = Column(DECIMAL(12, 2), nullable=False)
    total_price = Column(DECIMAL(12, 2), nullable=False)
    
    # Relationships
    invoice = relationship("Invoice", back_populates="items")


# ==================== BRANCH MODEL ====================
class Branch(Base):
    __tablename__ = "branches"
    __table_args__ = (
        UniqueConstraint('tenant_id', 'name', name='unique_tenant_branch_name'),
    )
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    business_type = Column(String(50), default=BusinessType.SHOP.value)
    address = Column(Text)
    phone = Column(String(50))
    is_head_office = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    tenant = relationship("Tenant", back_populates="branches")
    users = relationship("User", back_populates="branch", cascade="all, delete-orphan")
    stock = relationship("Stock", back_populates="branch", cascade="all, delete-orphan")
    sales = relationship("Sale", back_populates="branch", cascade="all, delete-orphan")
    purchases = relationship("Purchase", back_populates="branch", cascade="all, delete-orphan")
    purchase_orders = relationship("PurchaseOrder", back_populates="branch", cascade="all, delete-orphan")
    stock_movements = relationship("StockMovement", back_populates="branch")
    alerts = relationship("Alert", back_populates="branch")
    loans = relationship("Loan", back_populates="branch", cascade="all, delete-orphan")
    batches = relationship("Batch", back_populates="branch", cascade="all, delete-orphan")
    sale_returns = relationship("SaleReturn", back_populates="branch")


# ==================== CATEGORY MODEL ====================
class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint('tenant_id', 'name', name='unique_tenant_category_name'),
    )
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    parent_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    tenant = relationship("Tenant", back_populates="categories")
    parent = relationship("Category", remote_side=[id], backref="subcategories")
    products = relationship("Product", back_populates="category")


# ==================== UNIT MODEL ====================
class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (
        UniqueConstraint('tenant_id', 'name', name='unique_tenant_unit_name'),
        UniqueConstraint('tenant_id', 'symbol', name='unique_tenant_unit_symbol'),
    )
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(50), nullable=False)
    symbol = Column(String(10), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    tenant = relationship("Tenant", back_populates="units")
    products = relationship("Product", back_populates="unit")


# ==================== PRODUCT MODEL ====================
class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint('tenant_id', 'sku', name='unique_tenant_product_sku'),
        UniqueConstraint('tenant_id', 'barcode', name='unique_tenant_product_barcode'),
    )
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    sku = Column(String(100), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=True)
    barcode = Column(String(100), nullable=True)
    
    price = Column(DECIMAL(12, 2), nullable=False)
    cost = Column(DECIMAL(12, 2), nullable=False)
    
    has_expiry = Column(Boolean, default=False)
    track_batch = Column(Boolean, default=False)
    requires_prescription = Column(Boolean, default=False)
    
    color = Column(String(50), nullable=True)
    size = Column(String(50), nullable=True)
    pages = Column(Integer, nullable=True)
    
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    tenant = relationship("Tenant", back_populates="products")
    category = relationship("Category", back_populates="products")
    unit = relationship("Unit", back_populates="products")
    stock = relationship("Stock", back_populates="product", cascade="all, delete-orphan")
    sale_items = relationship("SaleItem", back_populates="product")
    purchase_items = relationship("PurchaseItem", back_populates="product")
    purchase_order_items = relationship("PurchaseOrderItem", back_populates="product")
    stock_movements = relationship("StockMovement", back_populates="product")
    alerts = relationship("Alert", back_populates="product")
    loan_items = relationship("LoanItem", back_populates="product")
    batches = relationship("Batch", back_populates="product", cascade="all, delete-orphan")


# ==================== USER MODEL ====================
class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint('tenant_id', 'email', name='unique_tenant_user_email'),
    )
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)  # Null for super admins
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False)  # super_admin, tenant_admin, manager, salesman
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True)
    active = Column(Boolean, default=True)
    last_login = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    tenant = relationship("Tenant", back_populates="users")
    branch = relationship("Branch", back_populates="users")
    sales = relationship("Sale", back_populates="user")
    stock_movements = relationship("StockMovement", back_populates="user")
    purchase_orders = relationship("PurchaseOrder", foreign_keys="PurchaseOrder.created_by", back_populates="creator")
    loans_created = relationship("Loan", foreign_keys="Loan.created_by", back_populates="creator")
    loans_approved = relationship("Loan", foreign_keys="Loan.approved_by", back_populates="approver")
    loan_payments = relationship("LoanPayment", back_populates="recorder")
    sale_returns = relationship("SaleReturn", foreign_keys="SaleReturn.user_id", back_populates="user")
    temp_items_registered = relationship("TempItem", foreign_keys="TempItem.registered_by", back_populates="registrar")
    temp_items_received = relationship("TempItem", foreign_keys="TempItem.received_by", back_populates="receiver")
    backup_records = relationship("BackupRecord", foreign_keys="BackupRecord.created_by", back_populates="creator")
    system_logs = relationship("SystemLog", foreign_keys="SystemLog.user_id", back_populates="user")


# ==================== STOCK MODEL ====================
class Stock(Base):
    __tablename__ = "stock"
    __table_args__ = (
        UniqueConstraint('branch_id', 'product_id', name='unique_branch_product'),
    )
    
    id = Column(Integer, primary_key=True, index=True)
    branch_id = Column(Integer, ForeignKey("branches.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    quantity = Column(DECIMAL(12, 2), default=0)
    reorder_level = Column(DECIMAL(12, 2), default=0)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    branch = relationship("Branch", back_populates="stock")
    product = relationship("Product", back_populates="stock")


# ==================== BATCH MODEL ====================
class Batch(Base):
    __tablename__ = "batches"
    __table_args__ = (
        UniqueConstraint('tenant_id', 'batch_number', 'branch_id', name='unique_tenant_batch_per_branch'),
    )
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    
    batch_number = Column(String(100), nullable=False)
    supplier_batch = Column(String(100), nullable=True)
    manufacturing_date = Column(DateTime, nullable=True)
    expiry_date = Column(DateTime, nullable=True)
    received_date = Column(DateTime, server_default=func.now())
    
    quantity = Column(DECIMAL(12, 2), nullable=False)
    remaining_quantity = Column(DECIMAL(12, 2), nullable=False)
    unit_cost = Column(DECIMAL(12, 2), nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    tenant = relationship("Tenant", back_populates="batches")
    product = relationship("Product", back_populates="batches")
    branch = relationship("Branch", back_populates="batches")
    stock_movements = relationship("StockMovement", back_populates="batch")
    sale_items = relationship("SaleItem", back_populates="batch")


# ==================== SALE MODELS ====================
class Sale(Base):
    __tablename__ = "sales"
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    customer_name = Column(String(255), nullable=True)
    customer_phone = Column(String(50), nullable=True)
    total_amount = Column(DECIMAL(12, 2), nullable=False)
    total_cost = Column(DECIMAL(12, 2), nullable=False)
    discount_amount = Column(DECIMAL(12, 2), default=0)
    tax_amount = Column(DECIMAL(12, 2), default=0)
    payment_method = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    tenant = relationship("Tenant", back_populates="sales")
    branch = relationship("Branch", back_populates="sales")
    user = relationship("User", back_populates="sales")
    items = relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan")
    loan_payments = relationship("LoanPayment", back_populates="sale")
    returns = relationship("SaleReturn", back_populates="sale", cascade="all, delete-orphan")


class SaleItem(Base):
    __tablename__ = "sale_items"
    
    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=True)
    
    quantity = Column(DECIMAL(12, 2), nullable=False)
    unit_price = Column(DECIMAL(12, 2), nullable=False)
    total = Column(DECIMAL(12, 2), nullable=False)
    cost = Column(DECIMAL(12, 2), nullable=False)
    
    # Relationships
    sale = relationship("Sale", back_populates="items")
    product = relationship("Product", back_populates="sale_items")
    batch = relationship("Batch", back_populates="sale_items")
    loan_items = relationship("LoanItem", back_populates="sale_item")


# ==================== SALE RETURN MODELS ====================
class SaleReturn(Base):
    __tablename__ = "sale_returns"
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    return_number = Column(String(50), unique=True, nullable=False, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    total_return_amount = Column(DECIMAL(12, 2), nullable=False)
    reason = Column(Text, nullable=True)
    status = Column(String(50), default=ReturnStatus.PENDING.value)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    tenant = relationship("Tenant", back_populates="sale_returns")
    sale = relationship("Sale", back_populates="returns")
    branch = relationship("Branch", back_populates="sale_returns")
    user = relationship("User", foreign_keys=[user_id], back_populates="sale_returns")
    approver = relationship("User", foreign_keys=[approved_by])
    items = relationship("SaleReturnItem", cascade="all, delete-orphan")


class SaleReturnItem(Base):
    __tablename__ = "sale_return_items"
    
    id = Column(Integer, primary_key=True, index=True)
    return_id = Column(Integer, ForeignKey("sale_returns.id", ondelete="CASCADE"), nullable=False)
    sale_item_id = Column(Integer, ForeignKey("sale_items.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=True)
    
    quantity = Column(DECIMAL(12, 2), nullable=False)
    refund_amount = Column(DECIMAL(12, 2), nullable=False)
    reason = Column(Text, nullable=True)
    
    # Relationships
    return_parent = relationship("SaleReturn", back_populates="items")
    sale_item = relationship("SaleItem")
    product = relationship("Product")
    batch = relationship("Batch")


# ==================== PURCHASE MODELS ====================
class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    order_number = Column(String(50), unique=True, nullable=False, index=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    supplier = Column(String(200), nullable=False)
    order_date = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expected_delivery_date = Column(DateTime(timezone=True), nullable=True)
    actual_delivery_date = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), default=PurchaseStatus.PENDING.value)
    subtotal = Column(DECIMAL(12, 2), default=0)
    tax_amount = Column(DECIMAL(12, 2), default=0)
    shipping_cost = Column(DECIMAL(12, 2), default=0)
    discount_amount = Column(DECIMAL(12, 2), default=0)
    total_amount = Column(DECIMAL(12, 2), default=0)
    notes = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    tenant = relationship("Tenant", back_populates="purchase_orders")
    branch = relationship("Branch", back_populates="purchase_orders")
    items = relationship("PurchaseOrderItem", back_populates="purchase_order", cascade="all, delete-orphan")
    creator = relationship("User", foreign_keys=[created_by], back_populates="purchase_orders")


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    
    id = Column(Integer, primary_key=True, index=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    
    quantity_ordered = Column(DECIMAL(12, 2), nullable=False)
    quantity_received = Column(DECIMAL(12, 2), default=0)
    unit_cost = Column(DECIMAL(12, 2), nullable=False)
    total_cost = Column(DECIMAL(12, 2), nullable=False)
    
    batch_number = Column(String(100), nullable=True)
    expiry_date = Column(DateTime, nullable=True)
    manufacturing_date = Column(DateTime, nullable=True)
    
    received_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    
    # Relationships
    purchase_order = relationship("PurchaseOrder", back_populates="items")
    product = relationship("Product", back_populates="purchase_order_items")


class Purchase(Base):
    __tablename__ = "purchases"
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    supplier_name = Column(String(255))
    total_amount = Column(DECIMAL(12, 2), nullable=False)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    tenant = relationship("Tenant", back_populates="purchases")
    branch = relationship("Branch", back_populates="purchases")
    items = relationship("PurchaseItem", back_populates="purchase", cascade="all, delete-orphan")
    purchase_order = relationship("PurchaseOrder")


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    
    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(DECIMAL(12, 2), nullable=False)
    unit_cost = Column(DECIMAL(12, 2), nullable=False)
    
    # Relationships
    purchase = relationship("Purchase", back_populates="items")
    product = relationship("Product", back_populates="purchase_items")


# ==================== LOAN SYSTEM MODELS ====================
class Loan(Base):
    __tablename__ = "loans"
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    loan_number = Column(String(50), unique=True, nullable=False, index=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    customer_name = Column(String(255), nullable=False)
    customer_phone = Column(String(50), nullable=True)
    customer_email = Column(String(255), nullable=True)
    loan_date = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    due_date = Column(DateTime(timezone=True), nullable=False)
    total_amount = Column(DECIMAL(12, 2), nullable=False)
    paid_amount = Column(DECIMAL(12, 2), default=0)
    remaining_amount = Column(DECIMAL(12, 2), nullable=False)
    interest_rate = Column(DECIMAL(5, 2), default=0)
    interest_amount = Column(DECIMAL(12, 2), default=0)
    status = Column(String(50), default=LoanStatus.ACTIVE.value)
    notes = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    tenant = relationship("Tenant", back_populates="loans")
    branch = relationship("Branch", back_populates="loans")
    items = relationship("LoanItem", back_populates="loan", cascade="all, delete-orphan")
    payments = relationship("LoanPayment", back_populates="loan", cascade="all, delete-orphan")
    creator = relationship("User", foreign_keys=[created_by], back_populates="loans_created")
    approver = relationship("User", foreign_keys=[approved_by], back_populates="loans_approved")


class LoanItem(Base):
    __tablename__ = "loan_items"
    
    id = Column(Integer, primary_key=True, index=True)
    loan_id = Column(Integer, ForeignKey("loans.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(DECIMAL(12, 2), nullable=False)
    unit_price = Column(DECIMAL(12, 2), nullable=False)
    line_total = Column(DECIMAL(12, 2), nullable=False)
    sale_item_id = Column(Integer, ForeignKey("sale_items.id"), nullable=True)
    
    # Relationships
    loan = relationship("Loan", back_populates="items")
    product = relationship("Product", back_populates="loan_items")
    sale_item = relationship("SaleItem", back_populates="loan_items")


class LoanPayment(Base):
    __tablename__ = "loan_payments"
    
    id = Column(Integer, primary_key=True, index=True)
    loan_id = Column(Integer, ForeignKey("loans.id", ondelete="CASCADE"), nullable=False)
    payment_number = Column(String(50), unique=True, nullable=False, index=True)
    payment_date = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    amount = Column(DECIMAL(12, 2), nullable=False)
    payment_method = Column(String(50), nullable=False)
    reference_number = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    recorded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    loan = relationship("Loan", back_populates="payments")
    recorder = relationship("User", back_populates="loan_payments")
    sale = relationship("Sale", back_populates="loan_payments")


class LoanSummary(Base):
    __tablename__ = "loan_summaries"
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    summary_date = Column(DateTime(timezone=True), nullable=False)
    total_loans_issued = Column(Integer, default=0)
    total_loan_amount = Column(DECIMAL(12, 2), default=0)
    total_repayments = Column(DECIMAL(12, 2), default=0)
    total_outstanding = Column(DECIMAL(12, 2), default=0)
    active_loans_count = Column(Integer, default=0)
    overdue_loans_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    tenant = relationship("Tenant")
    branch = relationship("Branch")


# ==================== STOCK MOVEMENT MODEL ====================
class StockMovement(Base):
    __tablename__ = "stock_movements"
    
    id = Column(Integer, primary_key=True, index=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=True)
    
    change_qty = Column(DECIMAL(12, 2), nullable=False)
    movement_type = Column(String(50), nullable=False)
    reference_id = Column(Integer)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    branch = relationship("Branch", back_populates="stock_movements")
    product = relationship("Product", back_populates="stock_movements")
    user = relationship("User", back_populates="stock_movements")
    batch = relationship("Batch", back_populates="stock_movements")


# ==================== ALERT MODEL ====================
class Alert(Base):
    __tablename__ = "alerts"
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    alert_type = Column(String(50), default="low_stock")
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime(timezone=True))
    resolved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="alerts")
    branch = relationship("Branch", back_populates="alerts")
    product = relationship("Product", back_populates="alerts")
    resolver = relationship("User", foreign_keys=[resolved_by])


# ==================== TEMP ITEM MODEL ====================
class TempItem(Base):
    __tablename__ = "temp_items"
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    item_number = Column(String(50), unique=True, nullable=False, index=True)
    item_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    quantity = Column(Integer, default=1)
    unit_price = Column(DECIMAL(12, 2), nullable=True)
    customer_name = Column(String(255), nullable=True)
    customer_phone = Column(String(50), nullable=True)
    status = Column(String(50), default=TempItemStatus.PENDING.value)
    registered_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    registered_at = Column(DateTime(timezone=True), server_default=func.now())
    received_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="temp_items")
    registrar = relationship("User", foreign_keys=[registered_by], back_populates="temp_items_registered")
    receiver = relationship("User", foreign_keys=[received_by], back_populates="temp_items_received")


# ==================== SETTINGS MODELS ====================
class SystemSetting(Base):
    __tablename__ = "system_settings"
    __table_args__ = (
        UniqueConstraint('tenant_id', 'category', 'key', name='unique_tenant_category_key'),
    )
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)  # Null for system-wide settings
    category = Column(String(50), nullable=False, index=True)
    key = Column(String(100), nullable=False)
    value = Column(Text, nullable=True)
    value_type = Column(String(20), default="string")
    is_encrypted = Column(Boolean, default=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    tenant = relationship("Tenant")


class BackupRecord(Base):
    __tablename__ = "backup_records"
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    size_mb = Column(DECIMAL(10, 2), default=0)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    tenant = relationship("Tenant")
    creator = relationship("User", foreign_keys=[created_by], back_populates="backup_records")


class SystemLog(Base):
    __tablename__ = "system_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    log_type = Column(String(50), nullable=False)
    message = Column(Text, nullable=False)
    details = Column(Text, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    tenant = relationship("Tenant", back_populates="system_logs")
    user = relationship("User", foreign_keys=[user_id], back_populates="system_logs")


# ==================== SEED DATA FUNCTION ====================
def seed_subscription_plans():
    """Helper function to create default subscription plans"""
    plans = [
        # Basic Plans
        {
            "plan_code": "BASIC_3M",
            "plan_name": "Basic 3 Months",
            "plan_type": "basic",
            "duration_months": 3,
            "price": 90.00,
            "max_users": 3,
            "max_branches": 1,
            "max_products": 500,
            "max_storage_mb": 500,
            "has_loans": False,
            "has_batch_tracking": False,
            "has_pharmacy_features": False,
            "has_advanced_reports": False,
            "has_api_access": False,
            "has_custom_branding": False,
            "has_multi_branch": False,
            "has_priority_support": False,
            "discount_percentage": 0,
            "is_popular": False,
        },
        {
            "plan_code": "BASIC_6M",
            "plan_name": "Basic 6 Months",
            "plan_type": "basic",
            "duration_months": 6,
            "price": 150.00,
            "max_users": 3,
            "max_branches": 1,
            "max_products": 500,
            "max_storage_mb": 500,
            "has_loans": False,
            "has_batch_tracking": False,
            "has_pharmacy_features": False,
            "has_advanced_reports": False,
            "has_api_access": False,
            "has_custom_branding": False,
            "has_multi_branch": False,
            "has_priority_support": False,
            "discount_percentage": 16.67,
            "is_popular": False,
        },
        {
            "plan_code": "BASIC_12M",
            "plan_name": "Basic 12 Months",
            "plan_type": "basic",
            "duration_months": 12,
            "price": 240.00,
            "max_users": 3,
            "max_branches": 1,
            "max_products": 500,
            "max_storage_mb": 500,
            "has_loans": False,
            "has_batch_tracking": False,
            "has_pharmacy_features": False,
            "has_advanced_reports": False,
            "has_api_access": False,
            "has_custom_branding": False,
            "has_multi_branch": False,
            "has_priority_support": False,
            "discount_percentage": 33.33,
            "is_popular": False,
        },
        # Professional Plans
        {
            "plan_code": "PRO_3M",
            "plan_name": "Professional 3 Months",
            "plan_type": "professional",
            "duration_months": 3,
            "price": 180.00,
            "max_users": 10,
            "max_branches": 3,
            "max_products": 5000,
            "max_storage_mb": 2000,
            "has_loans": True,
            "has_batch_tracking": True,
            "has_pharmacy_features": True,
            "has_advanced_reports": True,
            "has_api_access": False,
            "has_custom_branding": False,
            "has_multi_branch": True,
            "has_priority_support": False,
            "discount_percentage": 0,
            "is_popular": False,
        },
        {
            "plan_code": "PRO_6M",
            "plan_name": "Professional 6 Months",
            "plan_type": "professional",
            "duration_months": 6,
            "price": 300.00,
            "max_users": 10,
            "max_branches": 3,
            "max_products": 5000,
            "max_storage_mb": 2000,
            "has_loans": True,
            "has_batch_tracking": True,
            "has_pharmacy_features": True,
            "has_advanced_reports": True,
            "has_api_access": False,
            "has_custom_branding": False,
            "has_multi_branch": True,
            "has_priority_support": False,
            "discount_percentage": 16.67,
            "is_popular": True,
        },
        {
            "plan_code": "PRO_12M",
            "plan_name": "Professional 12 Months",
            "plan_type": "professional",
            "duration_months": 12,
            "price": 480.00,
            "max_users": 10,
            "max_branches": 3,
            "max_products": 5000,
            "max_storage_mb": 2000,
            "has_loans": True,
            "has_batch_tracking": True,
            "has_pharmacy_features": True,
            "has_advanced_reports": True,
            "has_api_access": False,
            "has_custom_branding": False,
            "has_multi_branch": True,
            "has_priority_support": False,
            "discount_percentage": 33.33,
            "is_popular": False,
        },
        # Enterprise Plans
        {
            "plan_code": "ENT_3M",
            "plan_name": "Enterprise 3 Months",
            "plan_type": "enterprise",
            "duration_months": 3,
            "price": 450.00,
            "max_users": 50,
            "max_branches": 10,
            "max_products": 50000,
            "max_storage_mb": 10000,
            "has_loans": True,
            "has_batch_tracking": True,
            "has_pharmacy_features": True,
            "has_advanced_reports": True,
            "has_api_access": True,
            "has_custom_branding": True,
            "has_multi_branch": True,
            "has_priority_support": True,
            "discount_percentage": 0,
            "is_popular": False,
        },
        {
            "plan_code": "ENT_6M",
            "plan_name": "Enterprise 6 Months",
            "plan_type": "enterprise",
            "duration_months": 6,
            "price": 750.00,
            "max_users": 50,
            "max_branches": 10,
            "max_products": 50000,
            "max_storage_mb": 10000,
            "has_loans": True,
            "has_batch_tracking": True,
            "has_pharmacy_features": True,
            "has_advanced_reports": True,
            "has_api_access": True,
            "has_custom_branding": True,
            "has_multi_branch": True,
            "has_priority_support": True,
            "discount_percentage": 16.67,
            "is_popular": False,
        },
        {
            "plan_code": "ENT_12M",
            "plan_name": "Enterprise 12 Months",
            "plan_type": "enterprise",
            "duration_months": 12,
            "price": 1200.00,
            "max_users": 50,
            "max_branches": 10,
            "max_products": 50000,
            "max_storage_mb": 10000,
            "has_loans": True,
            "has_batch_tracking": True,
            "has_pharmacy_features": True,
            "has_advanced_reports": True,
            "has_api_access": True,
            "has_custom_branding": True,
            "has_multi_branch": True,
            "has_priority_support": True,
            "discount_percentage": 33.33,
            "is_popular": False,
        },
    ]
    
    return plans