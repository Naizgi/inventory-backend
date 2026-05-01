# app/seeders/run_seeders.py
#!/usr/bin/env python
"""Run all seeders to populate initial data"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.database import SessionLocal

from app.seeders.user_seeder import seed_users
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_seeders():
    """Run all seeders in the correct order"""
    db = SessionLocal()
    try:
        logger.info("=" * 60)
        logger.info("Starting database seeding...")
        logger.info("=" * 60)
        
        # Run seeders in order (branches first, then products, then users)
       
       
        
        logger.info("\n3. Seeding users...")
        seed_users(db)
        
        logger.info("\n" + "=" * 60)
        logger.info("Database seeding completed successfully!")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Error during seeding: {e}")
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    run_seeders()