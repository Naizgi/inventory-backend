from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta
from app.database import get_db
from app.services import AlertService
from app.schemas import AlertResponse, AlertCreate
from app.utils.auth import get_current_user, require_role, verify_branch_access, get_current_tenant
from app.models import User, Alert, Branch, Product, Stock, Batch, UserRole

router = APIRouter(prefix="/alerts", tags=["Alerts"])


# ==================== ALERT RETRIEVAL ====================

@router.get("/", response_model=List[AlertResponse])
async def get_alerts(
    request: Request,
    resolved: bool = Query(False, description="Show resolved or unresolved alerts"),
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    alert_type: Optional[str] = Query(None, description="Filter by alert type (low_stock, out_of_stock, expiry)"),
    limit: int = Query(100, ge=1, le=500, description="Maximum records"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get alerts with optional filters.
    
    - **resolved**: Show resolved (true) or unresolved (false) alerts
    - **branch_id**: Filter by branch (admin only)
    - **alert_type**: Filter by alert type
    - **limit**: Maximum number of alerts to return
    
    Admin can view all branches, others can only view their assigned branch.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Apply branch filtering based on role
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
            if branch_id:
                # Verify branch belongs to tenant
                branch = db.query(Branch).filter(
                    Branch.id == branch_id,
                    Branch.tenant_id == tenant_id
                ).first()
                if not branch:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Branch with id {branch_id} not found in this tenant"
                    )
                if not verify_branch_access(current_user, branch_id):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Not authorized to view alerts for this branch"
                    )
        else:
            # Non-admin users can only see their branch
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            
            if branch_id and branch_id != current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to view alerts for other branches"
                )
            
            branch_id = current_user.branch_id
        
        alerts = AlertService.get_alerts(db, tenant_id, resolved, branch_id)
        
        # Apply alert type filter
        if alert_type:
            alerts = [a for a in alerts if a.get("alert_type") == alert_type]
        
        return alerts[:limit]
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve alerts: {str(e)}"
        )


@router.get("/unresolved", response_model=List[AlertResponse])
async def get_unresolved_alerts(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    alert_type: Optional[str] = Query(None, description="Filter by alert type"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all unresolved alerts.
    
    Returns all alerts that haven't been resolved yet.
    """
    return await get_alerts(request, False, branch_id, alert_type, 100, db, current_user)


@router.get("/resolved", response_model=List[AlertResponse])
async def get_resolved_alerts(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    alert_type: Optional[str] = Query(None, description="Filter by alert type"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get resolved alerts (Admin, Tenant Admin, or Manager only).
    
    Returns alerts that have been resolved.
    """
    return await get_alerts(request, True, branch_id, alert_type, limit, db, current_user)


@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(
    alert_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific alert by ID.
    
    - **alert_id**: The ID of the alert to retrieve
    """
    try:
        tenant_id = get_current_tenant(request)
        
        alert = db.query(Alert).filter(
            Alert.id == alert_id,
            Alert.tenant_id == tenant_id
        ).first()
        
        if not alert:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Alert with id {alert_id} not found in this tenant"
            )
        
        # Check permissions
        if not verify_branch_access(current_user, alert.branch_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this alert"
            )
        
        # Get alert with product and branch names
        product = db.query(Product).filter(
            Product.id == alert.product_id,
            Product.tenant_id == tenant_id
        ).first()
        branch = db.query(Branch).filter(
            Branch.id == alert.branch_id,
            Branch.tenant_id == tenant_id
        ).first()
        resolver = db.query(User).filter(User.id == alert.resolved_by).first() if alert.resolved_by else None
        
        return {
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
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve alert: {str(e)}"
        )


# ==================== ALERT MANAGEMENT ====================

@router.post("/{alert_id}/resolve")
async def resolve_alert(
    alert_id: int,
    request: Request,
    resolution_notes: Optional[str] = Query(None, description="Notes about resolution"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Resolve an alert.
    
    - **alert_id**: The ID of the alert to resolve
    - **resolution_notes**: Optional notes about how the alert was resolved
    
    Users can only resolve alerts for their assigned branch.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # First get the alert to check permissions
        alert = db.query(Alert).filter(
            Alert.id == alert_id,
            Alert.tenant_id == tenant_id
        ).first()
        
        if not alert:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Alert with id {alert_id} not found in this tenant"
            )
        
        # Check permissions
        if not verify_branch_access(current_user, alert.branch_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to resolve alerts for this branch"
            )
        
        if alert.resolved:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Alert already resolved"
            )
        
        # Resolve the alert
        resolved_alert = AlertService.resolve_alert(db, alert_id, current_user.id, tenant_id)
        
        # Add resolution notes if provided
        if resolution_notes:
            # You could store resolution notes in a separate table or add to alert
            pass
        
        return {
            "message": "Alert resolved successfully",
            "alert_id": alert_id,
            "resolved_by": current_user.name,
            "resolved_at": datetime.now().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resolve alert: {str(e)}"
        )


@router.post("/bulk-resolve")
async def bulk_resolve_alerts(
    request: Request,
    alert_ids: List[int] = Query(..., description="List of alert IDs to resolve"),
    resolution_notes: Optional[str] = Query(None, description="Resolution notes"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Bulk resolve multiple alerts.
    
    - **alert_ids**: List of alert IDs to resolve
    - **resolution_notes**: Optional notes about resolution
    
    Users can only resolve alerts for their assigned branch.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        resolved_count = 0
        failed_ids = []
        
        for alert_id in alert_ids:
            try:
                alert = db.query(Alert).filter(
                    Alert.id == alert_id,
                    Alert.tenant_id == tenant_id
                ).first()
                
                if not alert:
                    failed_ids.append({"id": alert_id, "reason": "Not found"})
                    continue
                
                # Check permissions
                if not verify_branch_access(current_user, alert.branch_id):
                    failed_ids.append({"id": alert_id, "reason": "Not authorized"})
                    continue
                
                if alert.resolved:
                    failed_ids.append({"id": alert_id, "reason": "Already resolved"})
                    continue
                
                AlertService.resolve_alert(db, alert_id, current_user.id, tenant_id)
                resolved_count += 1
                
            except Exception as e:
                failed_ids.append({"id": alert_id, "reason": str(e)})
        
        db.commit()
        
        return {
            "message": f"Resolved {resolved_count} out of {len(alert_ids)} alerts",
            "resolved_count": resolved_count,
            "failed_count": len(failed_ids),
            "failed_ids": failed_ids
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to bulk resolve alerts: {str(e)}"
        )


# ==================== ALERT GENERATION ====================

@router.post("/check-low-stock")
async def check_low_stock_manual(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Branch ID to check (admin only)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Manually trigger low stock check and create alerts.
    
    - **branch_id**: Optional branch ID (admin only, checks all branches if not specified)
    
    Scans stock levels and creates alerts for low stock and out of stock items.
    Also auto-resolves alerts for items that are no longer low stock.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        alerts_created = 0
        alerts_resolved = 0
        
        # Admin can check all branches or specific branch
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
            if branch_id:
                # Verify branch belongs to tenant
                branch = db.query(Branch).filter(
                    Branch.id == branch_id,
                    Branch.tenant_id == tenant_id
                ).first()
                if not branch:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Branch with id {branch_id} not found in this tenant"
                    )
                
                # Check only specific branch
                if not verify_branch_access(current_user, branch_id):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Not authorized to check this branch"
                    )
                
                # Check low stock for specific branch
                stocks = db.query(Stock).join(Product).filter(
                    Product.tenant_id == tenant_id,
                    Stock.branch_id == branch_id
                ).all()
                
                for stock in stocks:
                    alert_created = AlertService.check_and_create_alert(db, branch_id, stock.product_id, tenant_id)
                    if alert_created:
                        alerts_created += 1
                
                # Auto-resolve alerts for this branch
                alerts_resolved = AlertService.auto_resolve_alerts_for_branch(db, branch_id, tenant_id)
            else:
                # Check all branches in tenant
                alerts_created = AlertService.check_low_stock_and_create_alerts(db, tenant_id)
                alerts_resolved = AlertService.auto_resolve_alerts(db, tenant_id)
        else:
            # Non-admin can only check their own branch
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            
            if branch_id and branch_id != current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to check other branches"
                )
            
            # Check low stock for user's branch
            stocks = db.query(Stock).join(Product).filter(
                Product.tenant_id == tenant_id,
                Stock.branch_id == current_user.branch_id
            ).all()
            
            for stock in stocks:
                alert_created = AlertService.check_and_create_alert(db, current_user.branch_id, stock.product_id, tenant_id)
                if alert_created:
                    alerts_created += 1
            
            # Auto-resolve alerts for this branch
            alerts_resolved = AlertService.auto_resolve_alerts_for_branch(db, current_user.branch_id, tenant_id)
        
        db.commit()
        
        return {
            "message": "Low stock check completed",
            "alerts_created": alerts_created,
            "alerts_resolved": alerts_resolved,
            "checked_by": current_user.name,
            "timestamp": datetime.now().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check low stock: {str(e)}"
        )


@router.post("/check-expiry")
async def check_expiry_alerts(
    request: Request,
    days_threshold: int = Query(30, description="Days before expiry to create alert", ge=1, le=365),
    branch_id: Optional[int] = Query(None, description="Branch ID to check (admin only)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Check for expiring products and create alerts (Admin, Tenant Admin, or Manager only).
    
    - **days_threshold**: Number of days before expiry to create alert
    - **branch_id**: Optional branch filter
    
    Creates alerts for products that are expiring soon.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        now = datetime.now()
        expiry_threshold = now + timedelta(days=days_threshold)
        
        query = db.query(Batch).filter(
            Batch.tenant_id == tenant_id,
            Batch.expiry_date.isnot(None),
            Batch.expiry_date <= expiry_threshold,
            Batch.expiry_date > now,
            Batch.remaining_quantity > 0
        )
        
        # Apply branch filter
        if branch_id:
            branch = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.tenant_id == tenant_id
            ).first()
            if not branch:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Branch with id {branch_id} not found in this tenant"
                )
            if not verify_branch_access(current_user, branch_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to check this branch"
                )
            query = query.filter(Batch.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            query = query.filter(Batch.branch_id == current_user.branch_id)
        
        batches = query.all()
        
        alerts_created = 0
        for batch in batches:
            # Check if alert already exists
            existing = db.query(Alert).filter(
                Alert.tenant_id == tenant_id,
                Alert.branch_id == batch.branch_id,
                Alert.product_id == batch.product_id,
                Alert.alert_type == "expiry",
                Alert.resolved == False
            ).first()
            
            if not existing:
                product = batch.product
                days_until_expiry = (batch.expiry_date - now).days
                
                alert = Alert(
                    tenant_id=tenant_id,
                    branch_id=batch.branch_id,
                    product_id=batch.product_id,
                    alert_type="expiry",
                    message=f"Product '{product.name}' (Batch: {batch.batch_number}) expires in {days_until_expiry} days on {batch.expiry_date.strftime('%Y-%m-%d')}",
                    resolved=False
                )
                db.add(alert)
                alerts_created += 1
        
        db.commit()
        
        return {
            "message": f"Expiry check completed",
            "alerts_created": alerts_created,
            "expiring_batches_found": len(batches),
            "days_threshold": days_threshold,
            "checked_by": current_user.name
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check expiry alerts: {str(e)}"
        )


# ==================== ALERT SUMMARY ====================

@router.get("/summary/low-stock")
async def get_low_stock_summary(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get summary of all low stock items.
    
    Returns detailed information about products that are below reorder level.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Apply branch filtering
        if current_user.role == UserRole.SUPER_ADMIN.value or current_user.role == UserRole.TENANT_ADMIN.value:
            if branch_id:
                branch = db.query(Branch).filter(
                    Branch.id == branch_id,
                    Branch.tenant_id == tenant_id
                ).first()
                if not branch:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Branch with id {branch_id} not found in this tenant"
                    )
                if not verify_branch_access(current_user, branch_id):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Not authorized to view this branch"
                    )
        else:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User not assigned to a branch"
                )
            
            if branch_id and branch_id != current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to view other branches"
                )
            
            branch_id = current_user.branch_id
        
        summary = AlertService.get_low_stock_summary(db, tenant_id, branch_id)
        
        return {
            "total_low_stock_items": summary["total_low_stock_items"],
            "items": summary["items"],
            "generated_at": datetime.now().isoformat(),
            "branch_filter": branch_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get low stock summary: {str(e)}"
        )


@router.get("/summary/by-type")
async def get_alerts_by_type(
    request: Request,
    branch_id: Optional[int] = Query(None, description="Filter by branch ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get alert counts grouped by type (Admin, Tenant Admin, or Manager only).
    
    Returns statistics about different types of alerts.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(Alert).filter(
            Alert.tenant_id == tenant_id,
            Alert.resolved == False
        )
        
        # Apply branch filter
        if branch_id:
            branch = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.tenant_id == tenant_id
            ).first()
            if not branch:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Branch with id {branch_id} not found in this tenant"
                )
            if not verify_branch_access(current_user, branch_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to view this branch"
                )
            query = query.filter(Alert.branch_id == branch_id)
        elif current_user.role != UserRole.SUPER_ADMIN.value and current_user.branch_id:
            query = query.filter(Alert.branch_id == current_user.branch_id)
        
        # Count by type
        low_stock_count = query.filter(Alert.alert_type == "low_stock").count()
        out_of_stock_count = query.filter(Alert.alert_type == "out_of_stock").count()
        expiry_count = query.filter(Alert.alert_type == "expiry").count()
        
        return {
            "low_stock": low_stock_count,
            "out_of_stock": out_of_stock_count,
            "expiry": expiry_count,
            "total": low_stock_count + out_of_stock_count + expiry_count,
            "generated_at": datetime.now().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get alerts by type: {str(e)}"
        )


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert(
    alert_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Delete an alert (Super Admin or Tenant Admin only).
    
    - **alert_id**: The ID of the alert to delete
    
    Permanently removes the alert from the system.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        alert = db.query(Alert).filter(
            Alert.id == alert_id,
            Alert.tenant_id == tenant_id
        ).first()
        
        if not alert:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Alert with id {alert_id} not found in this tenant"
            )
        
        db.delete(alert)
        db.commit()
        
        return None
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete alert: {str(e)}"
        )


@router.delete("/branch/{branch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_alerts_for_branch(
    branch_id: int,
    request: Request,
    resolved_only: bool = Query(True, description="Delete only resolved alerts"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Delete all alerts for a branch (Super Admin or Tenant Admin only).
    
    - **branch_id**: Branch ID
    - **resolved_only**: If True, only delete resolved alerts
    """
    try:
        tenant_id = get_current_tenant(request)
        
        # Verify branch belongs to tenant
        branch = db.query(Branch).filter(
            Branch.id == branch_id,
            Branch.tenant_id == tenant_id
        ).first()
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Branch with id {branch_id} not found in this tenant"
            )
        
        query = db.query(Alert).filter(
            Alert.tenant_id == tenant_id,
            Alert.branch_id == branch_id
        )
        
        if resolved_only:
            query = query.filter(Alert.resolved == True)
        
        deleted_count = query.delete()
        db.commit()
        
        return {
            "message": f"Deleted {deleted_count} alerts for branch {branch.name}",
            "deleted_count": deleted_count,
            "branch_id": branch_id,
            "branch_name": branch.name
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete alerts: {str(e)}"
        )


# ==================== HELPER FUNCTIONS ====================

def get_unresolved_alert_count(db: Session, tenant_id: int, branch_id: Optional[int] = None) -> int:
    """Get count of unresolved alerts for a branch within a tenant"""
    query = db.query(Alert).filter(
        Alert.tenant_id == tenant_id,
        Alert.resolved == False
    )
    if branch_id:
        query = query.filter(Alert.branch_id == branch_id)
    return query.count()


def get_alerts_by_product(db: Session, tenant_id: int, product_id: int, resolved: bool = False) -> List[Alert]:
    """Get all alerts for a specific product within a tenant"""
    return db.query(Alert).filter(
        Alert.tenant_id == tenant_id,
        Alert.product_id == product_id,
        Alert.resolved == resolved
    ).all()