# app/seeders/user_seeder.py
from sqlalchemy.orm import Session
from app.models import User, Branch
from app.services import AuthService
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Default credentials
DEFAULT_ADMIN_EMAIL = "admin@example.com"
DEFAULT_ADMIN_PASSWORD = "admin123"
DEFAULT_ADMIN_NAME = "System Administrator"

DEFAULT_MANAGER_EMAIL = "manager@example.com"
DEFAULT_MANAGER_PASSWORD = "manager123"
DEFAULT_MANAGER_NAME = "Branch Manager"

DEFAULT_SALESMAN_EMAIL = "sales@example.com"
DEFAULT_SALESMAN_PASSWORD = "sales123"
DEFAULT_SALESMAN_NAME = "Sales Representative"


def seed_users(db: Session):
    """Seed users into the database"""
    
    # Check if users already exist
    existing_users = db.query(User).count()
    if existing_users > 0:
        logger.info(f"Users already exist ({existing_users} users). Skipping seeding.")
        return
    
    logger.info("=" * 60)
    logger.info("Starting user seeding...")
    logger.info("=" * 60)
    
    # Get branches
    branches = db.query(Branch).all()
    logger.info(f"Found {len(branches)} branch(es) in database")
    
    users = []
    created_roles = []
    
    # ==================== CREATE ADMIN USER ====================
    try:
        admin_password_hash = AuthService.get_password_hash(DEFAULT_ADMIN_PASSWORD)
        
        admin_user = User(
            name=DEFAULT_ADMIN_NAME,
            email=DEFAULT_ADMIN_EMAIL,
            password_hash=admin_password_hash,
            role="admin",
            branch_id=None,
            active=True
        )
        users.append(admin_user)
        created_roles.append("admin")
        logger.info(f"✅ Created admin user: {DEFAULT_ADMIN_EMAIL}")
        
    except Exception as e:
        logger.error(f"❌ Failed to create admin user: {e}")
        return
    
    # ==================== CREATE MANAGER USER ====================
    try:
        manager_password_hash = AuthService.get_password_hash(DEFAULT_MANAGER_PASSWORD)
        
        # Assign manager to first branch if available
        manager_branch_id = branches[0].id if branches else None
        
        manager_user = User(
            name=DEFAULT_MANAGER_NAME,
            email=DEFAULT_MANAGER_EMAIL,
            password_hash=manager_password_hash,
            role="manager",
            branch_id=manager_branch_id,
            active=True
        )
        users.append(manager_user)
        created_roles.append("manager")
        
        if manager_branch_id:
            branch_name = next((b.name for b in branches if b.id == manager_branch_id), "Unknown")
            logger.info(f"✅ Created manager user: {DEFAULT_MANAGER_EMAIL} (Branch: {branch_name})")
        else:
            logger.info(f"✅ Created manager user: {DEFAULT_MANAGER_EMAIL} (No branch assigned)")
        
    except Exception as e:
        logger.error(f"❌ Failed to create manager user: {e}")
    
    # ==================== CREATE SALESMAN USER ====================
    try:
        salesman_password_hash = AuthService.get_password_hash(DEFAULT_SALESMAN_PASSWORD)
        
        # Assign salesman to first branch if available
        salesman_branch_id = branches[0].id if branches else None
        
        salesman_user = User(
            name=DEFAULT_SALESMAN_NAME,
            email=DEFAULT_SALESMAN_EMAIL,
            password_hash=salesman_password_hash,
            role="salesman",
            branch_id=salesman_branch_id,
            active=True
        )
        users.append(salesman_user)
        created_roles.append("salesman")
        
        if salesman_branch_id:
            branch_name = next((b.name for b in branches if b.id == salesman_branch_id), "Unknown")
            logger.info(f"✅ Created salesman user: {DEFAULT_SALESMAN_EMAIL} (Branch: {branch_name})")
        else:
            logger.info(f"✅ Created salesman user: {DEFAULT_SALESMAN_EMAIL} (No branch assigned)")
        
    except Exception as e:
        logger.error(f"❌ Failed to create salesman user: {e}")
    
    # ==================== CREATE ADDITIONAL SALESMAN FOR SECOND BRANCH ====================
    if len(branches) >= 2:
        try:
            second_salesman_password_hash = AuthService.get_password_hash("sales456")
            
            second_salesman = User(
                name="Second Sales Representative",
                email="sales2@example.com",
                password_hash=second_salesman_password_hash,
                role="salesman",
                branch_id=branches[1].id,
                active=True
            )
            users.append(second_salesman)
            created_roles.append("salesman")
            logger.info(f"✅ Created second salesman user: sales2@example.com (Branch: {branches[1].name})")
            
        except Exception as e:
            logger.error(f"❌ Failed to create second salesman user: {e}")
    
    # ==================== CREATE READONLY USER (Optional) ====================
    try:
        readonly_password_hash = AuthService.get_password_hash("readonly123")
        
        readonly_user = User(
            name="Readonly User",
            email="readonly@example.com",
            password_hash=readonly_password_hash,
            role="salesman",  # Using salesman role with limited permissions
            branch_id=branches[0].id if branches else None,
            active=True
        )
        users.append(readonly_user)
        created_roles.append("readonly")
        logger.info(f"✅ Created readonly user: readonly@example.com")
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to create readonly user: {e}")
    
    # ==================== COMMIT ALL USERS ====================
    try:
        db.add_all(users)
        db.commit()
        
        logger.info("=" * 60)
        logger.info(f"✅ Successfully created {len(users)} user(s)")
        logger.info("=" * 60)
        logger.info("")
        logger.info("📋 LOGIN CREDENTIALS:")
        logger.info("-" * 40)
        
        for user in users:
            if user.role == 'admin':
                logger.info(f"👑 ADMIN:")
                logger.info(f"   Email: {user.email}")
                logger.info(f"   Password: {DEFAULT_ADMIN_PASSWORD}")
                logger.info(f"   Role: {user.role}")
                logger.info(f"   Branch: All branches")
                logger.info("")
            elif user.role == 'manager':
                branch_name = next((b.name for b in branches if b.id == user.branch_id), "None")
                logger.info(f"📊 MANAGER:")
                logger.info(f"   Email: {user.email}")
                logger.info(f"   Password: {DEFAULT_MANAGER_PASSWORD}")
                logger.info(f"   Role: {user.role}")
                logger.info(f"   Branch: {branch_name}")
                logger.info("")
            elif user.role == 'salesman':
                branch_name = next((b.name for b in branches if b.id == user.branch_id), "None")
                logger.info(f"🛒 SALESMAN:")
                logger.info(f"   Email: {user.email}")
                if user.email == DEFAULT_SALESMAN_EMAIL:
                    logger.info(f"   Password: {DEFAULT_SALESMAN_PASSWORD}")
                elif user.email == "sales2@example.com":
                    logger.info(f"   Password: sales456")
                elif user.email == "readonly@example.com":
                    logger.info(f"   Password: readonly123")
                logger.info(f"   Role: {user.role}")
                logger.info(f"   Branch: {branch_name}")
                logger.info("")
        
        logger.info("=" * 60)
        logger.info("💡 TIPS:")
        logger.info("-" * 40)
        logger.info("• Admin can access all features and branches")
        logger.info("• Manager can manage their assigned branch")
        logger.info("• Salesman can create sales and view their branch only")
        logger.info("• Change passwords after first login for security")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"❌ Failed to commit users: {e}")
        db.rollback()
        raise


def seed_single_user(db: Session, name: str, email: str, password: str, role: str, branch_id: int = None):
    """
    Seed a single user into the database.
    
    Args:
        db: Database session
        name: User's full name
        email: User's email address
        password: Plain text password (will be hashed)
        role: User role (admin, manager, salesman)
        branch_id: Optional branch ID for non-admin users
    """
    try:
        # Check if user already exists
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            logger.warning(f"User {email} already exists. Skipping.")
            return None
        
        password_hash = AuthService.get_password_hash(password)
        
        user = User(
            name=name,
            email=email,
            password_hash=password_hash,
            role=role,
            branch_id=branch_id,
            active=True
        )
        
        db.add(user)
        db.commit()
        db.refresh(user)
        
        logger.info(f"✅ Created user: {email} (Role: {role})")
        return user
        
    except Exception as e:
        logger.error(f"❌ Failed to create user {email}: {e}")
        db.rollback()
        return None


def seed_multiple_users(db: Session, users_data: list):
    """
    Seed multiple users from a list of user data.
    
    Args:
        db: Database session
        users_data: List of dicts with keys: name, email, password, role, branch_id
    """
    created_count = 0
    failed_count = 0
    
    for user_data in users_data:
        try:
            # Check if user already exists
            existing = db.query(User).filter(User.email == user_data['email']).first()
            if existing:
                logger.warning(f"User {user_data['email']} already exists. Skipping.")
                continue
            
            password_hash = AuthService.get_password_hash(user_data['password'])
            
            user = User(
                name=user_data['name'],
                email=user_data['email'],
                password_hash=password_hash,
                role=user_data['role'],
                branch_id=user_data.get('branch_id'),
                active=user_data.get('active', True)
            )
            
            db.add(user)
            created_count += 1
            logger.info(f"✅ Created user: {user_data['email']}")
            
        except Exception as e:
            failed_count += 1
            logger.error(f"❌ Failed to create user {user_data.get('email', 'unknown')}: {e}")
    
    try:
        db.commit()
        logger.info(f"📊 Summary: {created_count} created, {failed_count} failed")
        
    except Exception as e:
        logger.error(f"❌ Failed to commit users: {e}")
        db.rollback()
        raise


def reset_admin_password(db: Session, email: str = DEFAULT_ADMIN_EMAIL, new_password: str = None):
    """
    Reset admin password (useful if forgotten).
    
    Args:
        db: Database session
        email: Admin email address
        new_password: New password (defaults to DEFAULT_ADMIN_PASSWORD)
    """
    try:
        user = db.query(User).filter(User.email == email, User.role == "admin").first()
        
        if not user:
            logger.error(f"❌ Admin user {email} not found")
            return False
        
        password = new_password or DEFAULT_ADMIN_PASSWORD
        user.password_hash = AuthService.get_password_hash(password)
        db.commit()
        
        logger.info(f"✅ Password reset for {email}")
        logger.info(f"   New password: {password}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to reset password: {e}")
        db.rollback()
        return False


def deactivate_inactive_users(db: Session, days_inactive: int = 90):
    """
    Deactivate users who haven't logged in for specified days.
    
    Args:
        db: Database session
        days_inactive: Number of days of inactivity before deactivation
    """
    try:
        cutoff_date = datetime.now() - timedelta(days=days_inactive)
        
        # Note: This requires a last_login field in User model
        # If not available, skip or implement differently
        inactive_users = db.query(User).filter(
            User.last_login < cutoff_date,
            User.role != "admin",  # Never deactivate admin
            User.active == True
        ).all()
        
        deactivated_count = 0
        for user in inactive_users:
            user.active = False
            deactivated_count += 1
        
        db.commit()
        logger.info(f"✅ Deactivated {deactivated_count} inactive users (inactive for {days_inactive} days)")
        
    except Exception as e:
        logger.error(f"❌ Failed to deactivate users: {e}")
        db.rollback()


# ==================== MAIN FUNCTION FOR DIRECT EXECUTION ====================

if __name__ == "__main__":
    # This allows running the seeder directly
    from app.database import SessionLocal
    
    db = SessionLocal()
    try:
        seed_users(db)
    finally:
        db.close()