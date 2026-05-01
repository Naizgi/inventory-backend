from enum import Enum
from pydantic import BaseModel, Field, EmailStr, ConfigDict, field_validator
from datetime import datetime, date
from typing import Optional, List, Any
from decimal import Decimal

# ==================== ENUMS ====================
class BusinessType(str, Enum):
    SHOP = "shop"
    PHARMACY = "pharmacy"
    MINI_MARKET = "mini_market"
    SUPERMARKET = "supermarket"

class TenantStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIAL = "trial"
    EXPIRED = "expired"
    PENDING_PAYMENT = "pending_payment"

class UserRole(str, Enum):
    SUPER_ADMIN = "super_admin"
    TENANT_ADMIN = "tenant_admin"
    MANAGER = "manager"
    SALESMAN = "salesman"

class PurchaseStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    PARTIALLY_RECEIVED = "partially_received"

class LoanStatus(str, Enum):
    ACTIVE = "active"
    PARTIALLY_PAID = "partially_paid"
    SETTLED = "settled"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"

class LoanPaymentMethod(str, Enum):
    CASH = "cash"
    TICKET = "ticket"
    COUPON = "coupon"
    MIXED = "mixed"

class TempItemStatus(str, Enum):
    PENDING = "pending"
    RECEIVED = "received"
    CANCELLED = "cancelled"

class MovementType(str, Enum):
    SALE = "sale"
    PURCHASE = "purchase"
    ADJUSTMENT = "adjustment"
    RETURN = "return"
    TRANSFER = "transfer"

class ReturnStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"

# ==================== SUBSCRIPTION ENUMS (NEW) ====================
class PaymentStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"

class PaymentMethod(str, Enum):
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    CREDIT_CARD = "credit_card"
    MOBILE_MONEY = "mobile_money"
    OTHER = "other"

class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    PENDING_PAYMENT = "pending_payment"

class SubscriptionPlanType(str, Enum):
    BASIC = "basic"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"

class SubscriptionDuration(str, Enum):
    THREE_MONTHS = "3"
    SIX_MONTHS = "6"
    TWELVE_MONTHS = "12"


# ==================== SUBSCRIPTION PLAN SCHEMAS (NEW) ====================
class SubscriptionPlanBase(BaseModel):
    plan_code: str = Field(..., min_length=1, max_length=50)
    plan_name: str = Field(..., min_length=1, max_length=100)
    plan_type: SubscriptionPlanType
    duration_months: int = Field(..., ge=1, le=12)
    price: Decimal = Field(..., gt=0)
    max_users: int = Field(default=5, ge=1)
    max_branches: int = Field(default=1, ge=1)
    max_products: int = Field(default=1000, ge=1)
    max_storage_mb: int = Field(default=500, ge=100)
    
    # Feature flags
    has_loans: bool = False
    has_batch_tracking: bool = False
    has_pharmacy_features: bool = False
    has_advanced_reports: bool = False
    has_api_access: bool = False
    has_custom_branding: bool = False
    has_multi_branch: bool = False
    has_priority_support: bool = False
    
    # Offers
    discount_percentage: Decimal = Field(default=0, ge=0, le=100)
    is_popular: bool = False
    active: bool = True


class SubscriptionPlanCreate(SubscriptionPlanBase):
    pass


class SubscriptionPlanUpdate(BaseModel):
    plan_name: Optional[str] = Field(None, min_length=1, max_length=100)
    price: Optional[Decimal] = Field(None, gt=0)
    max_users: Optional[int] = Field(None, ge=1)
    max_branches: Optional[int] = Field(None, ge=1)
    max_products: Optional[int] = Field(None, ge=1)
    max_storage_mb: Optional[int] = Field(None, ge=100)
    has_loans: Optional[bool] = None
    has_batch_tracking: Optional[bool] = None
    has_pharmacy_features: Optional[bool] = None
    has_advanced_reports: Optional[bool] = None
    has_api_access: Optional[bool] = None
    has_custom_branding: Optional[bool] = None
    has_multi_branch: Optional[bool] = None
    has_priority_support: Optional[bool] = None
    discount_percentage: Optional[Decimal] = Field(None, ge=0, le=100)
    is_popular: Optional[bool] = None
    active: Optional[bool] = None


class SubscriptionPlanResponse(SubscriptionPlanBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)


class SubscriptionPlanListResponse(BaseModel):
    plans: List[SubscriptionPlanResponse]
    total_count: int


# ==================== TENANT SUBSCRIPTION SCHEMAS (NEW) ====================
class TenantSubscriptionBase(BaseModel):
    plan_id: int
    auto_renew: bool = False


class TenantSubscriptionCreate(TenantSubscriptionBase):
    payment_method: PaymentMethod
    tenant_id: int
    
    @field_validator('plan_id')
    @classmethod
    def validate_plan_id(cls, v):
        if v <= 0:
            raise ValueError('Plan ID must be positive')
        return v


class TenantSubscriptionUpdate(BaseModel):
    status: Optional[SubscriptionStatus] = None
    auto_renew: Optional[bool] = None
    cancelled_at: Optional[datetime] = None
    notes: Optional[str] = None


class TenantSubscriptionResponse(TenantSubscriptionBase):
    id: int
    tenant_id: int
    start_date: datetime
    end_date: datetime
    status: SubscriptionStatus
    is_current: bool
    amount_paid: Decimal
    payment_status: PaymentStatus
    payment_id: Optional[int] = None
    created_at: datetime
    activated_at: Optional[datetime] = None
    expired_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    grace_period_end: Optional[datetime] = None
    notes: Optional[str] = None
    
    # Nested objects
    plan: Optional[SubscriptionPlanResponse] = None
    tenant_name: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


class SubscriptionCheckResponse(BaseModel):
    is_valid: bool
    status: str
    plan_type: Optional[str] = None
    expires_in_days: Optional[int] = None
    features: Optional[dict] = None
    message: str


# ==================== PAYMENT SCHEMAS (NEW) ====================
class PaymentBase(BaseModel):
    payment_method: PaymentMethod
    payment_type: str = Field(default="subscription")
    
    # Bank transfer details
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    account_holder: Optional[str] = None
    
    # Mobile money details
    phone_number: Optional[str] = None
    provider: Optional[str] = None
    
    # Reference
    transaction_reference: Optional[str] = None
    notes: Optional[str] = None


class PaymentCreate(PaymentBase):
    amount: Decimal = Field(..., gt=0)
    plan_id: Optional[int] = None
    subscription_id: Optional[int] = None


class PaymentUpdate(BaseModel):
    payment_status: Optional[PaymentStatus] = None
    verified_by: Optional[int] = None
    rejection_reason: Optional[str] = None
    receipt_url: Optional[str] = None


class PaymentVerificationRequest(BaseModel):
    payment_id: int
    verified: bool
    rejection_reason: Optional[str] = None
    
    @field_validator('rejection_reason')
    @classmethod
    def validate_rejection_reason(cls, v, info):
        if 'verified' in info.data and not info.data['verified'] and not v:
            raise ValueError('Rejection reason is required when payment is rejected')
        return v


class PaymentResponse(PaymentBase):
    id: int
    tenant_id: int
    payment_number: str
    amount: Decimal
    payment_status: PaymentStatus
    payment_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    receipt_url: Optional[str] = None
    verified_by: Optional[int] = None
    verified_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    
    # Invoice details
    invoice_number: Optional[str] = None
    invoice_url: Optional[str] = None
    
    # Metadata
    notes: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    # Nested
    tenant_name: Optional[str] = None
    verifier_name: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


class PaymentListResponse(BaseModel):
    payments: List[PaymentResponse]
    total_count: int
    total_amount: Decimal
    pending_count: int
    pending_amount: Decimal


# ==================== INVOICE SCHEMAS (NEW) ====================
class InvoiceItemBase(BaseModel):
    description: str = Field(..., min_length=1, max_length=255)
    quantity: int = Field(default=1, ge=1)
    unit_price: Decimal = Field(..., gt=0)


class InvoiceItemCreate(InvoiceItemBase):
    pass


class InvoiceItemResponse(InvoiceItemBase):
    id: int
    invoice_id: int
    total_price: Decimal
    
    model_config = ConfigDict(from_attributes=True)


class InvoiceBase(BaseModel):
    due_date: datetime
    discount_amount: Decimal = Field(default=0, ge=0)
    tax_amount: Decimal = Field(default=0, ge=0)
    description: Optional[str] = None


class InvoiceCreate(InvoiceBase):
    tenant_id: int
    items: List[InvoiceItemCreate] = Field(..., min_length=1)


class InvoiceResponse(InvoiceBase):
    id: int
    payment_id: Optional[int] = None
    tenant_id: int
    invoice_number: str
    invoice_date: datetime
    subtotal: Decimal
    total_amount: Decimal
    status: str  # pending, paid, overdue, cancelled
    invoice_file_url: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    # Nested
    items: List[InvoiceItemResponse] = []
    tenant_name: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


# ==================== TENANT SCHEMAS (UPDATED) ====================
class TenantBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    subdomain: Optional[str] = Field(None, max_length=100, pattern="^[a-zA-Z0-9-]+$")
    business_type: BusinessType = BusinessType.SHOP
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = None
    logo_url: Optional[str] = Field(None, max_length=500)

class TenantCreate(TenantBase):
    admin_email: Optional[EmailStr] = None
    admin_name: Optional[str] = None
    admin_password: Optional[str] = Field(None, min_length=6)
    trial_days: int = Field(default=14, ge=0, le=90)

class TenantUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    subdomain: Optional[str] = Field(None, max_length=100)
    business_type: Optional[BusinessType] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = None
    logo_url: Optional[str] = None
    status: Optional[TenantStatus] = None

class TenantResponse(TenantBase):
    id: int
    status: TenantStatus
    trial_start: Optional[datetime] = None
    trial_end: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    # Subscription info
    current_subscription: Optional[TenantSubscriptionResponse] = None
    has_valid_subscription: bool = False
    days_until_expiry: Optional[int] = None
    
    model_config = ConfigDict(from_attributes=True)

class TenantListResponse(BaseModel):
    tenants: List[TenantResponse]
    total_count: int


# ==================== BRANCH SCHEMAS (UPDATED) ====================
class BranchBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    business_type: BusinessType = BusinessType.SHOP
    address: Optional[str] = None
    phone: Optional[str] = Field(None, max_length=50)
    is_head_office: bool = False

class BranchCreate(BranchBase):
    pass

class BranchUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    business_type: Optional[BusinessType] = None
    address: Optional[str] = None
    phone: Optional[str] = Field(None, max_length=50)
    is_head_office: Optional[bool] = None

class Branch(BranchBase):
    id: int
    tenant_id: int
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ==================== CATEGORY SCHEMAS (UPDATED) ====================
class CategoryBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    parent_id: Optional[int] = None

class CategoryCreate(CategoryBase):
    pass

class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    parent_id: Optional[int] = None

class Category(CategoryBase):
    id: int
    tenant_id: int
    created_at: datetime
    subcategories: List['Category'] = []
    
    model_config = ConfigDict(from_attributes=True)


# ==================== UNIT SCHEMAS (UPDATED) ====================
class UnitBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    symbol: str = Field(..., min_length=1, max_length=10)

class UnitCreate(UnitBase):
    pass

class UnitUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    symbol: Optional[str] = Field(None, min_length=1, max_length=10)

class Unit(UnitBase):
    id: int
    tenant_id: int
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ==================== PRODUCT SCHEMAS (UPDATED) ====================
class ProductBase(BaseModel):
    sku: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    category_id: Optional[int] = None
    unit_id: Optional[int] = None
    barcode: Optional[str] = Field(None, max_length=100)
    price: Decimal = Field(..., gt=0)
    cost: Decimal = Field(..., gt=0)
    has_expiry: bool = False
    track_batch: bool = False
    requires_prescription: bool = False
    color: Optional[str] = Field(None, max_length=50)
    size: Optional[str] = Field(None, max_length=50)
    pages: Optional[int] = Field(None, ge=0)
    active: bool = True

class ProductCreate(ProductBase):
    pass

class ProductUpdate(BaseModel):
    sku: Optional[str] = Field(None, min_length=1, max_length=100)
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    category_id: Optional[int] = None
    unit_id: Optional[int] = None
    barcode: Optional[str] = Field(None, max_length=100)
    price: Optional[Decimal] = Field(None, gt=0)
    cost: Optional[Decimal] = Field(None, gt=0)
    has_expiry: Optional[bool] = None
    track_batch: Optional[bool] = None
    requires_prescription: Optional[bool] = None
    color: Optional[str] = Field(None, max_length=50)
    size: Optional[str] = Field(None, max_length=50)
    pages: Optional[int] = Field(None, ge=0)
    active: Optional[bool] = None

class Product(ProductBase):
    id: int
    tenant_id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    category: Optional[Category] = None
    unit: Optional[Unit] = None
    stock_quantity: Decimal = 0
    reorder_level: Decimal = 0
    
    model_config = ConfigDict(from_attributes=True)


# ==================== BATCH SCHEMAS (UPDATED) ====================
class BatchBase(BaseModel):
    product_id: int
    branch_id: int
    batch_number: str = Field(..., min_length=1, max_length=100)
    supplier_batch: Optional[str] = None
    manufacturing_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    quantity: Decimal = Field(..., gt=0)
    unit_cost: Decimal = Field(..., gt=0)

class BatchCreate(BatchBase):
    pass

class BatchUpdate(BaseModel):
    quantity: Optional[Decimal] = Field(None, ge=0)
    remaining_quantity: Optional[Decimal] = Field(None, ge=0)
    expiry_date: Optional[datetime] = None

class Batch(BatchBase):
    id: int
    tenant_id: int
    remaining_quantity: Decimal
    received_date: datetime
    created_at: datetime
    product_name: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


# ==================== USER SCHEMAS (UPDATED) ====================
class UserBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    role: str = Field(..., pattern="^(super_admin|tenant_admin|manager|salesman)$")
    branch_id: Optional[int] = None
    active: bool = True

class UserCreate(UserBase):
    password: str = Field(..., min_length=6)

class UserUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    email: Optional[EmailStr] = None
    role: Optional[str] = Field(None, pattern="^(super_admin|tenant_admin|manager|salesman)$")
    branch_id: Optional[int] = None
    active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=6)

class User(UserBase):
    id: int
    tenant_id: Optional[int] = None
    last_login: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    branch_name: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)

class UserResponse(User):
    pass


# ==================== STOCK SCHEMAS ====================
class StockBase(BaseModel):
    branch_id: int
    product_id: int
    quantity: Decimal = Field(0, ge=0)
    reorder_level: Decimal = Field(0, ge=0)

class StockCreate(StockBase):
    pass

class StockUpdate(BaseModel):
    quantity: Optional[Decimal] = Field(None, ge=0)
    reorder_level: Optional[Decimal] = Field(None, ge=0)

class Stock(StockBase):
    id: int
    updated_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)

class StockResponse(BaseModel):
    product_id: int
    product_name: str
    product_sku: str
    quantity: Decimal
    reorder_level: Decimal
    status: str  # "normal", "low", "out_of_stock"
    batches: Optional[List[Batch]] = None


# ==================== SALE SCHEMAS (UPDATED) ====================
class SaleItemCreate(BaseModel):
    product_id: int
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., gt=0)
    batch_id: Optional[int] = None

class SaleItem(BaseModel):
    id: int
    sale_id: int
    product_id: int
    batch_id: Optional[int]
    quantity: Decimal
    unit_price: Decimal
    total: Decimal
    cost: Decimal
    product_name: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)

class SaleCreate(BaseModel):
    branch_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    discount_amount: Decimal = Field(default=0, ge=0)
    tax_amount: Decimal = Field(default=0, ge=0)
    payment_method: Optional[str] = None
    items: List[SaleItemCreate] = Field(..., min_length=1)

class Sale(BaseModel):
    id: int
    tenant_id: int
    branch_id: int
    user_id: int
    customer_name: Optional[str]
    customer_phone: Optional[str]
    total_amount: Decimal
    total_cost: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    payment_method: Optional[str]
    created_at: datetime
    items: List[SaleItem] = []
    user_name: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


# ==================== SALE RETURN SCHEMAS (UPDATED) ====================
class SaleReturnItemCreate(BaseModel):
    sale_item_id: int
    quantity: Decimal = Field(..., gt=0)
    reason: Optional[str] = None

class SaleReturnCreate(BaseModel):
    sale_id: int
    items: List[SaleReturnItemCreate]
    reason: Optional[str] = None
    notes: Optional[str] = None

class SaleReturnItemResponse(BaseModel):
    id: int
    sale_item_id: int
    product_id: int
    product_name: Optional[str]
    quantity: Decimal
    refund_amount: Decimal
    reason: Optional[str]
    
    model_config = ConfigDict(from_attributes=True)

class SaleReturnResponse(BaseModel):
    id: int
    tenant_id: int
    return_number: str
    sale_id: int
    branch_id: int
    user_id: int
    total_return_amount: Decimal
    reason: Optional[str]
    status: ReturnStatus
    notes: Optional[str]
    created_at: datetime
    items: List[SaleReturnItemResponse]
    user_name: Optional[str]
    approver_name: Optional[str]
    
    model_config = ConfigDict(from_attributes=True)


# ==================== PURCHASE ORDER SCHEMAS (UPDATED) ====================
class PurchaseOrderItemBase(BaseModel):
    product_id: int
    quantity_ordered: Decimal = Field(gt=0)
    unit_cost: Decimal = Field(gt=0)
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None
    manufacturing_date: Optional[date] = None
    notes: Optional[str] = None

class PurchaseOrderItemCreate(PurchaseOrderItemBase):
    pass

class PurchaseOrderItemResponse(PurchaseOrderItemBase):
    id: int
    quantity_received: Decimal
    total_cost: Decimal
    received_at: Optional[datetime] = None
    product_name: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)

class PurchaseOrderBase(BaseModel):
    supplier: str = Field(..., min_length=1, max_length=200)
    expected_delivery_date: Optional[date] = None
    tax_amount: Decimal = Field(default=0, ge=0)
    shipping_cost: Decimal = Field(default=0, ge=0)
    discount_amount: Decimal = Field(default=0, ge=0)
    notes: Optional[str] = None

class PurchaseOrderCreate(PurchaseOrderBase):
    items: List[PurchaseOrderItemCreate]

class PurchaseOrderUpdate(BaseModel):
    status: Optional[PurchaseStatus] = None
    actual_delivery_date: Optional[date] = None
    notes: Optional[str] = None

class PurchaseOrderResponse(PurchaseOrderBase):
    id: int
    tenant_id: int
    order_number: str
    branch_id: int
    order_date: datetime
    actual_delivery_date: Optional[datetime] = None
    status: PurchaseStatus
    subtotal: Decimal
    total_amount: Decimal
    items: List[PurchaseOrderItemResponse]
    created_by: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)

class ReceivePurchaseItem(BaseModel):
    product_id: int
    quantity_received: Decimal = Field(gt=0)
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None

class ReceivePurchaseOrder(BaseModel):
    items: List[ReceivePurchaseItem]
    actual_delivery_date: date


# ==================== LEGACY PURCHASE SCHEMAS (UPDATED) ====================
class PurchaseItemCreate(BaseModel):
    product_id: int
    quantity: Decimal = Field(..., gt=0)
    unit_cost: Decimal = Field(..., gt=0)

class PurchaseItem(BaseModel):
    id: int
    purchase_id: int
    product_id: int
    quantity: Decimal
    unit_cost: Decimal
    
    model_config = ConfigDict(from_attributes=True)

class PurchaseCreate(BaseModel):
    branch_id: int
    supplier_name: Optional[str] = None
    items: List[PurchaseItemCreate] = Field(..., min_length=1)

class Purchase(BaseModel):
    id: int
    tenant_id: int
    branch_id: int
    supplier_name: Optional[str]
    total_amount: Decimal
    created_at: datetime
    items: List[PurchaseItem] = []
    
    model_config = ConfigDict(from_attributes=True)


# ==================== LOAN SCHEMAS (UPDATED) ====================
class LoanItemBase(BaseModel):
    product_id: int
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(gt=0)

class LoanItemCreate(LoanItemBase):
    pass

class LoanItemResponse(LoanItemBase):
    id: int
    line_total: Decimal
    product_name: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)

class LoanBase(BaseModel):
    customer_name: str = Field(min_length=2, max_length=255)
    customer_phone: Optional[str] = None
    customer_email: Optional[EmailStr] = None
    due_date: date
    interest_rate: Decimal = Field(default=0, ge=0, le=100)
    notes: Optional[str] = None

class LoanCreate(LoanBase):
    items: List[LoanItemCreate]

class LoanUpdate(BaseModel):
    due_date: Optional[date] = None
    interest_rate: Optional[Decimal] = Field(None, ge=0, le=100)
    status: Optional[LoanStatus] = None
    notes: Optional[str] = None

class LoanPaymentBase(BaseModel):
    amount: Decimal = Field(gt=0)
    payment_method: LoanPaymentMethod
    reference_number: Optional[str] = None
    notes: Optional[str] = None

class LoanPaymentCreate(LoanPaymentBase):
    sale_id: Optional[int] = None

class LoanPaymentResponse(LoanPaymentBase):
    id: int
    payment_number: str
    payment_date: datetime
    recorded_by: str
    sale_id: Optional[int] = None
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class LoanResponse(LoanBase):
    id: int
    tenant_id: int
    loan_number: str
    branch_id: int
    loan_date: datetime
    total_amount: Decimal
    paid_amount: Decimal
    remaining_amount: Decimal
    interest_amount: Decimal
    status: LoanStatus
    items: List[LoanItemResponse]
    payments: List[LoanPaymentResponse] = []
    created_by: str
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)

class LoanSettleRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    payment_method: LoanPaymentMethod
    reference_number: Optional[str] = None
    notes: Optional[str] = None

class LoanSummaryResponse(BaseModel):
    summary_date: date
    tenant_id: int
    branch_id: int
    total_loans_issued: int
    total_loan_amount: Decimal
    total_repayments: Decimal
    total_outstanding: Decimal
    active_loans_count: int
    overdue_loans_count: int
    
    model_config = ConfigDict(from_attributes=True)

class LoanReport(BaseModel):
    date_range: dict
    total_loans: int
    total_loan_value: Decimal
    total_repayments: Decimal
    total_outstanding: Decimal
    average_loan_size: Decimal
    repayment_rate: float
    loans_by_status: dict
    daily_breakdown: List[dict]


# ==================== STOCK MOVEMENT SCHEMAS ====================
class StockMovementBase(BaseModel):
    product_id: int
    branch_id: int
    change_qty: Decimal
    movement_type: MovementType
    reference_id: Optional[int] = None
    batch_id: Optional[int] = None
    notes: Optional[str] = None

class StockMovementCreate(StockMovementBase):
    pass

class StockMovementResponse(StockMovementBase):
    id: int
    user_id: int
    user_name: Optional[str]
    product_name: Optional[str]
    batch_number: Optional[str]
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ==================== ALERT SCHEMAS (UPDATED) ====================
class AlertBase(BaseModel):
    branch_id: int
    product_id: int
    message: str

class AlertCreate(AlertBase):
    pass

class Alert(AlertBase):
    id: int
    tenant_id: int
    created_at: datetime
    resolved: bool
    resolved_at: Optional[datetime]
    
    model_config = ConfigDict(from_attributes=True)

class AlertResponse(BaseModel):
    id: int
    tenant_id: int
    branch_id: int
    branch_name: Optional[str]
    product_id: int
    product_name: Optional[str]
    alert_type: str
    message: str
    created_at: datetime
    resolved: bool
    resolved_at: Optional[datetime]
    resolved_by: Optional[str]
    
    model_config = ConfigDict(from_attributes=True)


# ==================== TEMP ITEM SCHEMAS (UPDATED) ====================
class TempItemBase(BaseModel):
    item_name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    quantity: int = Field(default=1, ge=1)
    unit_price: Optional[Decimal] = Field(None, gt=0)
    customer_name: Optional[str] = Field(None, max_length=255)
    customer_phone: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = None

class TempItemCreate(TempItemBase):
    pass

class TempItemUpdate(BaseModel):
    status: Optional[TempItemStatus] = None
    notes: Optional[str] = None

class TempItemResponse(TempItemBase):
    id: int
    tenant_id: int
    item_number: str
    status: TempItemStatus
    registered_by: str
    registered_at: datetime
    received_by: Optional[str] = None
    received_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)


# ==================== SETTINGS SCHEMAS (UPDATED) ====================
class SystemSettingBase(BaseModel):
    category: str = Field(..., min_length=1, max_length=50)
    key: str = Field(..., min_length=1, max_length=100)
    value: Any
    value_type: str = Field(default="string")
    is_encrypted: bool = False
    description: Optional[str] = None

class SystemSettingCreate(SystemSettingBase):
    pass

class SystemSettingUpdate(BaseModel):
    value: Any
    value_type: Optional[str] = None
    description: Optional[str] = None

class SystemSettingResponse(SystemSettingBase):
    id: int
    tenant_id: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)


class BackupRecordBase(BaseModel):
    name: str
    file_path: str
    size_mb: float = 0

class BackupRecordResponse(BackupRecordBase):
    id: int
    tenant_id: Optional[int] = None
    created_by: Optional[str] = None
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class SystemLogBase(BaseModel):
    log_type: str
    message: str
    details: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

class SystemLogResponse(SystemLogBase):
    id: int
    tenant_id: Optional[int] = None
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# Settings update request models
class GeneralSettingsUpdate(BaseModel):
    system_name: str = Field(default="Inventory System")
    timezone: str = Field(default="Africa/Addis_Ababa")
    date_format: str = Field(default="YYYY-MM-DD")
    currency: str = Field(default="ETB")
    language: str = Field(default="en")
    business_type: Optional[BusinessType] = None

class CouponSettingsUpdate(BaseModel):
    auto_reset: bool = True
    reset_time: str = Field(default="00:00")
    low_stock_alert: bool = True
    alert_threshold: int = Field(default=20, ge=0, le=100)
    default_coupon: int = Field(default=100, ge=0)

class NotificationSettingsUpdate(BaseModel):
    low_stock_email: bool = True
    daily_report_email: bool = True
    sms_alerts: bool = False
    email_recipients: List[str] = []

class BackupSettingsUpdate(BaseModel):
    auto_backup: bool = True
    frequency: str = Field(default="daily", pattern="^(daily|weekly|monthly)$")
    backup_time: str = Field(default="23:00")
    location: str = Field(default="local", pattern="^(local|cloud)$")
    retention_days: int = Field(default=30, ge=1, le=365)


class SystemInfoResponse(BaseModel):
    version: str   
    build_date: str
    database: str
    server_status: str
    total_users: int
    total_products: int
    total_branches: int
    recent_sales: int
    uptime_days: int
    last_backup: Optional[str] = None
    cache_size_mb: float


# ==================== AUTH SCHEMAS (UPDATED) ====================
class Token(BaseModel):
    access_token: str
    token_type: str
    user: Optional[UserResponse] = None

class TokenData(BaseModel):
    email: Optional[str] = None
    role: Optional[str] = None
    user_id: Optional[int] = None
    tenant_id: Optional[int] = None
    branch_id: Optional[int] = None
    subscription_valid: Optional[bool] = None

class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=6)


class UserProfileUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(None, min_length=6)


# ==================== DATE RANGE SCHEMA ====================
class DateRange(BaseModel):
    from_date: date
    to_date: date


# ==================== TICKET SUMMARY SCHEMA ====================
class TicketSummary(BaseModel):
    total_tickets_purchased: int = 0
    total_tickets_used: int = 0
    total_tickets_remaining: int = 0
    total_revenue_from_tickets: float = 0
    total_purchased_value: float = 0
    ticket_utilization_rate: float = 0


# ==================== COMBINED SALES REPORT SCHEMA ====================
class CombinedSalesReport(BaseModel):
    date_range: DateRange
    total_sales: float
    total_cash_sales: float
    total_ticket_sales: float
    total_coupons_used: int
    total_tickets_used: int
    total_orders: int
    daily_breakdown: List[dict]
    top_coupon_items: List[dict] = []
    top_ticket_items: List[dict] = []
    ticket_summary: TicketSummary
    loan_summary: Optional[LoanReport] = None
    loan_repayments: float = 0


# ==================== UPDATE FORWARD REFERENCES ====================
Category.model_rebuild()