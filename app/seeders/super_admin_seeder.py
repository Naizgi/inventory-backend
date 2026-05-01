# app/seeders/super_admin_seeder.py
from sqlalchemy.orm import Session
from app.models import User
from app.services import AuthService
import logging

logger = logging.getLogger(__name__)

SUPER_ADMIN_EMAIL = "superadmin@system.com"
SUPER_ADMIN_PASSWORD = "SuperAdmin123!"
SUPER_ADMIN_NAME = "System Super Administrator"


def seed_super_admin(db: Session):
    """Seed the super admin user"""
    
    existing = db.query(User).filter(User.email == SUPER_ADMIN_EMAIL).first()
    if existing:
        logger.info(f"Super admin already exists: {SUPER_ADMIN_EMAIL}")
        return
    
    password_hash = AuthService.get_password_hash(SUPER_ADMIN_PASSWORD)
    
    super_admin = User(
        name=SUPER_ADMIN_NAME,
        email=SUPER_ADMIN_EMAIL,
        password_hash=password_hash,
        role="super_admin",
        tenant_id=None,  # Super admin has no tenant
        active=True
    )
    
    db.add(super_admin)
    db.commit()
    
    logger.info("=" * 60)
    logger.info("✅ SUPER ADMIN CREATED")
    logger.info("=" * 60)
    logger.info(f"Email: {SUPER_ADMIN_EMAIL}")
    logger.info(f"Password: {SUPER_ADMIN_PASSWORD}")
    logger.info("=" * 60)


def seed_sample_tenant(db: Session):
    """Seed a sample tenant for testing"""
    from app.models import Tenant
    
    existing = db.query(Tenant).filter(Tenant.name == "Demo Pharmacy").first()
    if existing:
        logger.info("Sample tenant already exists")
        return
    
    tenant = Tenant(
        name="Demo Pharmacy",
        subdomain="demo",
        business_type="pharmacy",
        email="admin@demopharmacy.com",
        phone="+251911111111",
        address="Addis Ababa, Ethiopia",
        subscription_plan="professional",
        status="active"
    )
    
    db.add(tenant)
    db.commit()
    
    logger.info(f"✅ Sample tenant created: {tenant.name} (ID: {tenant.id})")
    
    # Create tenant admin
    admin_password = "Admin123!"
    admin_user = User(
        tenant_id=tenant.id,
        name="Demo Pharmacy Admin",
        email="admin@demopharmacy.com",
        password_hash=AuthService.get_password_hash(admin_password),
        role="tenant_admin",
        active=True
    )
    
    db.add(admin_user)
    db.commit()
    
    logger.info(f"✅ Tenant admin created: admin@demopharmacy.com / {admin_password}")