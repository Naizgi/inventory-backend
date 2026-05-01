# app/utils/subscription_seed.py
from sqlalchemy.orm import Session
from app.models import SubscriptionPlan, seed_subscription_plans as get_plans_data
import logging

logger = logging.getLogger(__name__)

def seed_subscription_plans(db: Session):
    """Seed subscription plans into the database if they don't exist"""
    
    # Check if plans already exist
    existing_count = db.query(SubscriptionPlan).count()
    
    if existing_count > 0:
        logger.info(f"Subscription plans already exist ({existing_count} plans found)")
        return
    
    # Get plans data from models.py
    plans_data = get_plans_data()
    
    logger.info("Seeding subscription plans...")
    
    for plan_data in plans_data:
        # Check if plan with this code exists
        existing = db.query(SubscriptionPlan).filter(
            SubscriptionPlan.plan_code == plan_data["plan_code"]
        ).first()
        
        if not existing:
            plan = SubscriptionPlan(**plan_data)
            db.add(plan)
            logger.info(f"Added subscription plan: {plan_data['plan_name']}")
    
    db.commit()
    logger.info("✅ Subscription plans seeded successfully")