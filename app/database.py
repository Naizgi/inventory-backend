# app/database.py
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from app.config import settings
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== DATABASE URL CONFIGURATION ====================

# Get database URL from settings
DATABASE_URL = settings.DATABASE_URL

# Mask password in logs
masked_url = DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'sqlite'
logger.info(f"🔗 Database URL: {masked_url}")

# Configure engine with retry logic for MySQL
def get_engine():
    """Create engine with retry logic for MySQL connections"""
    max_retries = 5
    retry_delay = 3
    
    for attempt in range(max_retries):
        try:
            engine = create_engine(
                DATABASE_URL,
                echo=settings.DB_ECHO,
                pool_size=settings.DB_POOL_SIZE,
                max_overflow=settings.DB_MAX_OVERFLOW,
                pool_timeout=settings.DB_POOL_TIMEOUT,
                pool_recycle=settings.DB_POOL_RECYCLE,
                pool_pre_ping=settings.DB_POOL_PRE_PING,
                # MySQL-specific settings
                connect_args={
                    "charset": "utf8mb4",
                    "use_unicode": True,
                } if "mysql" in DATABASE_URL else {}
            )
            
            # Test connection
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            
            logger.info("✅ Database connection established")
            return engine
            
        except Exception as e:
            logger.warning(f"⚠️ Database connection attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                logger.error("❌ Failed to connect to database after all retries")
                raise

# Create engine
engine = get_engine()

# Session factories
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


class TenantSession(Session):
    """
    Custom session class that automatically adds tenant_id filtering.
    Ensures data isolation between tenants.
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tenant_id = None
        self._skip_tenant_filter = False
    
    @property
    def tenant_id(self):
        return self._tenant_id
    
    @tenant_id.setter
    def tenant_id(self, value):
        self._tenant_id = value
    
    @property
    def skip_tenant_filter(self):
        return self._skip_tenant_filter
    
    @skip_tenant_filter.setter
    def skip_tenant_filter(self, value):
        self._skip_tenant_filter = value
    
    def query(self, *entities, **kwargs):
        """Override query to automatically add tenant_id filter"""
        query = super().query(*entities, **kwargs)
        
        # Skip tenant filter for:
        # 1. Tenant model itself
        # 2. When explicitly disabled
        # 3. For super admin queries
        if self._skip_tenant_filter:
            return query
        
        if self._tenant_id:
            for entity in entities:
                model = None
                if hasattr(entity, 'entity'):
                    model = entity.entity
                elif hasattr(entity, '__tablename__'):
                    model = entity
                elif hasattr(entity, 'class_'):
                    model = entity.class_
                
                # Apply filter if model has tenant_id and it's not the Tenant model
                if model and hasattr(model, 'tenant_id') and model.__name__ != 'Tenant':
                    query = query.filter(model.tenant_id == self._tenant_id)
                    break
        
        return query


def create_tenant_session(tenant_id: int = None, skip_filter: bool = False):
    """Create a tenant-aware database session"""
    session = TenantSession(bind=engine)
    session.tenant_id = tenant_id
    session.skip_tenant_filter = skip_filter
    return session


# Tenant-aware session factory
TenantSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=TenantSession
)


# ==================== DEPENDENCY FUNCTIONS ====================

def get_db():
    """Standard database dependency (no tenant filtering)"""
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        yield db
    except Exception as e:
        logger.error(f"Database error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def get_tenant_db(request=None):
    """
    Tenant-aware database dependency.
    Automatically filters queries by tenant_id based on request context.
    """
    db = TenantSessionLocal()
    tenant_id = None
    skip_filter = False
    
    if request:
        tenant_id = getattr(request.state, 'tenant_id', None)
        
        # Skip filtering for admin routes
        path = request.url.path if hasattr(request, 'url') else ""
        if path.startswith("/tenants") or path.startswith("/admin") or path == "/health":
            skip_filter = True
    
    db.tenant_id = tenant_id
    db.skip_tenant_filter = skip_filter
    
    try:
        db.execute(text("SELECT 1"))
        yield db
    except Exception as e:
        logger.error(f"Database error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def get_super_admin_db():
    """
    Database dependency for super admin operations (no tenant filtering).
    """
    db = TenantSessionLocal()
    db.skip_tenant_filter = True
    db.tenant_id = None
    
    try:
        db.execute(text("SELECT 1"))
        yield db
    except Exception as e:
        logger.error(f"Database error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def check_db_health():
    """Check if database is accessible"""
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        return True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False


def get_tenant_connection(tenant_id: int):
    """Get a database connection for a specific tenant."""
    db = TenantSessionLocal()
    db.tenant_id = tenant_id
    db.skip_tenant_filter = False
    return db


# ==================== EVENT LISTENERS ====================

@event.listens_for(engine, "connect")
def receive_connect(dbapi_connection, connection_record):
    """Called when a new database connection is created"""
    logger.debug("New database connection established")


@event.listens_for(engine, "checkout")
def receive_checkout(dbapi_connection, connection_record, connection_proxy):
    """Called when a connection is checked out from the pool"""
    logger.debug("Database connection checked out")


@event.listens_for(engine, "close")
def receive_close(dbapi_connection, connection_record):
    """Called when a connection is closed"""
    logger.debug("Database connection closed")


# ==================== HELPER FUNCTIONS ====================

def get_tenant_from_subdomain(subdomain: str):
    """Helper to get tenant ID from subdomain."""
    from app.models import Tenant, TenantStatus
    
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(
            Tenant.subdomain == subdomain,
            Tenant.status == TenantStatus.ACTIVE.value
        ).first()
        return tenant.id if tenant else None
    finally:
        db.close()


def validate_tenant_exists(tenant_id: int) -> bool:
    """Check if a tenant exists and is active"""
    from app.models import Tenant, TenantStatus
    
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(
            Tenant.id == tenant_id,
            Tenant.status == TenantStatus.ACTIVE.value
        ).first()
        return tenant is not None
    finally:
        db.close()


def get_tenant_count() -> int:
    """Get total number of active tenants"""
    from app.models import Tenant, TenantStatus
    
    db = SessionLocal()
    try:
        return db.query(Tenant).filter(Tenant.status == TenantStatus.ACTIVE.value).count()
    finally:
        db.close()


def execute_tenant_query(tenant_id: int, query_func):
    """Execute a query within a tenant's context."""
    db = get_tenant_connection(tenant_id)
    try:
        return query_func(db)
    finally:
        db.close()


def execute_for_all_tenants(query_func):
    """Execute a query for all active tenants."""
    from app.models import Tenant, TenantStatus
    
    results = []
    db = SessionLocal()
    try:
        tenants = db.query(Tenant).filter(Tenant.status == TenantStatus.ACTIVE.value).all()
        
        for tenant in tenants:
            tenant_db = get_tenant_connection(tenant.id)
            try:
                result = query_func(tenant_db, tenant.id)
                results.append(result)
            finally:
                tenant_db.close()
    finally:
        db.close()
    
    return results


# ==================== TRANSACTION HELPERS ====================

class TenantTransaction:
    """
    Context manager for tenant-aware transactions.
    
    Usage:
        with TenantTransaction(tenant_id) as db:
            db.add(something)
    """
    
    def __init__(self, tenant_id: int = None, skip_filter: bool = False):
        self.tenant_id = tenant_id
        self.skip_filter = skip_filter
        self.db = None
    
    def __enter__(self):
        self.db = create_tenant_session(self.tenant_id, self.skip_filter)
        return self.db
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.db.rollback()
        else:
            self.db.commit()
        self.db.close()
        return False


class MultiTenantTransaction:
    """
    Context manager for multi-tenant transactions.
    """
    
    def __init__(self):
        self.transactions = []
    
    def __enter__(self):
        from app.models import Tenant, TenantStatus
        
        db = SessionLocal()
        try:
            tenants = db.query(Tenant).filter(Tenant.status == TenantStatus.ACTIVE.value).all()
            
            for tenant in tenants:
                tenant_db = create_tenant_session(tenant.id, False)
                self.transactions.append((tenant.id, tenant_db))
        finally:
            db.close()
        
        return self.transactions
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        for tenant_id, tenant_db in self.transactions:
            if exc_type is not None:
                tenant_db.rollback()
            else:
                tenant_db.commit()
            tenant_db.close()
        return False


# ==================== DATABASE INITIALIZATION ====================

def init_db():
    """Initialize database - create all tables and seed data"""
    try:
        # Import all models to ensure they're registered
        from app.models import (
            Tenant, User, Branch, Category, Unit, Product,
            Stock, Batch, Sale, SaleItem, SaleReturn, SaleReturnItem,
            PurchaseOrder, PurchaseOrderItem, Purchase, PurchaseItem,
            Loan, LoanItem, LoanPayment, LoanSummary,
            StockMovement, Alert, TempItem,
            SystemSetting, BackupRecord, SystemLog,
            SubscriptionPlan, TenantSubscription, Payment, Invoice, InvoiceItem
        )
        
        # Create all tables
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Database tables created/verified")
        
        # Seed subscription plans
        db = SessionLocal()
        try:
            from app.utils.subscription_seed import seed_subscription_plans
            seed_subscription_plans(db)
            logger.info("✅ Subscription plans seeded")
            
            # Create default super admin if not exists
            from app.services import AuthService
            
            admin = db.query(User).filter(
                User.email == "admin@example.com",
                User.role == "super_admin"
            ).first()
            
            if not admin:
                admin = User(
                    name="Super Admin",
                    email="admin@example.com",
                    password_hash=AuthService.get_password_hash("admin123"),
                    role="super_admin",
                    active=True
                )
                db.add(admin)
                db.commit()
                logger.info("✅ Default super admin created (admin@example.com / admin123)")
            else:
                logger.info("✅ Super admin already exists")
            
        except Exception as e:
            logger.error(f"❌ Error during seeding: {e}")
        finally:
            db.close()
        
        logger.info("✅ Database initialization complete")
        return True
        
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        return False


# ==================== AUTO-INITIALIZE ON MODULE LOAD ====================

# Uncomment one of these lines to auto-initialize:

# Option 1: Initialize immediately when module loads
# init_db()

# Option 2: Initialize on first use (lazy initialization)
_initialized = False

def ensure_db_initialized():
    """Ensure database is initialized (lazy initialization)"""
    global _initialized
    if not _initialized:
        init_db()
        _initialized = True