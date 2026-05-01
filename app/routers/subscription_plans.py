from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models import SubscriptionPlan
from app.schemas import (
    SubscriptionPlanCreate,
    SubscriptionPlanUpdate,
    SubscriptionPlanResponse,
    SubscriptionPlanListResponse
)
from app.utils.auth import require_super_admin

router = APIRouter(prefix="/api/subscription-plans", tags=["Subscription Plans"])

@router.get("/", response_model=SubscriptionPlanListResponse)
async def get_plans(
    active_only: bool = Query(True),
    db: Session = Depends(get_db)
):
    """Get all available subscription plans (public endpoint)"""
    query = db.query(SubscriptionPlan)
    
    if active_only:
        query = query.filter(SubscriptionPlan.active == True)
    
    plans = query.order_by(SubscriptionPlan.price).all()
    
    return SubscriptionPlanListResponse(
        plans=[SubscriptionPlanResponse.model_validate(p) for p in plans],
        total_count=len(plans)
    )

@router.get("/{plan_id}", response_model=SubscriptionPlanResponse)
async def get_plan(
    plan_id: int,
    db: Session = Depends(get_db)
):
    """Get specific plan details"""
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return SubscriptionPlanResponse.model_validate(plan)

@router.post("/", response_model=SubscriptionPlanResponse)
async def create_plan(
    plan: SubscriptionPlanCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_super_admin)
):
    """Create new subscription plan (Super Admin only)"""
    # Check for duplicate plan code
    existing = db.query(SubscriptionPlan).filter(
        SubscriptionPlan.plan_code == plan.plan_code
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Plan code already exists")
    
    db_plan = SubscriptionPlan(**plan.model_dump())
    db.add(db_plan)
    db.commit()
    db.refresh(db_plan)
    return SubscriptionPlanResponse.model_validate(db_plan)

@router.put("/{plan_id}", response_model=SubscriptionPlanResponse)
async def update_plan(
    plan_id: int,
    plan_update: SubscriptionPlanUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_super_admin)
):
    """Update subscription plan (Super Admin only)"""
    db_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
    if not db_plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    
    update_data = plan_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_plan, field, value)
    
    db.commit()
    db.refresh(db_plan)
    return SubscriptionPlanResponse.model_validate(db_plan)

@router.delete("/{plan_id}")
async def deactivate_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_super_admin)
):
    """Deactivate subscription plan (Super Admin only)"""
    db_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
    if not db_plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    
    db_plan.active = False
    db.commit()
    return {"message": "Plan deactivated successfully"}