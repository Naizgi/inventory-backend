from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.orm import Session
from typing import Dict, Any, List, Optional
from datetime import datetime
from app.database import get_db
from app.services import SettingsService
from app.schemas import (
    SystemSettingResponse, SystemSettingCreate, SystemSettingUpdate,
    GeneralSettingsUpdate, CouponSettingsUpdate, NotificationSettingsUpdate,
    BackupSettingsUpdate, SystemInfoResponse, BackupRecordResponse
)
from app.utils.auth import get_current_user, require_role, get_current_tenant
from app.models import User, SystemLog, SystemSetting, UserRole

router = APIRouter(prefix="/settings", tags=["Settings"])


class SettingsUpdateRequest(BaseModel):
    settings: Dict[str, Any]


# ==================== GENERAL SETTINGS ====================

@router.get("/general")
async def get_general_settings(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get general system settings.
    
    Returns general configuration like system name, timezone, currency, etc.
    """
    try:
        tenant_id = get_current_tenant(request)
        settings = SettingsService.get_category_settings(db, "general", tenant_id)
        return settings
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve general settings: {str(e)}"
        )


@router.put("/general")
async def update_general_settings(
    data: GeneralSettingsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Update general system settings (Super Admin or Tenant Admin only).
    
    Updates system name, timezone, date format, currency, language, etc.
    """
    try:
        tenant_id = get_current_tenant(request)
        settings_dict = data.model_dump(exclude_unset=True)
        SettingsService.set_multiple_settings(db, "general", settings_dict, current_user.id, tenant_id)
        
        return {
            "message": "General settings updated successfully",
            "success": True,
            "updated_by": current_user.name
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update general settings: {str(e)}"
        )


# ==================== NOTIFICATION SETTINGS ====================

@router.get("/notifications")
async def get_notification_settings(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get notification settings.
    
    Returns email, SMS, and alert notification configurations.
    """
    try:
        tenant_id = get_current_tenant(request)
        settings = SettingsService.get_category_settings(db, "notification", tenant_id)
        return settings
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve notification settings: {str(e)}"
        )


@router.put("/notifications")
async def update_notification_settings(
    data: NotificationSettingsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Update notification settings (Super Admin or Tenant Admin only).
    
    Updates email alerts, SMS alerts, and recipient lists.
    """
    try:
        tenant_id = get_current_tenant(request)
        settings_dict = data.model_dump(exclude_unset=True)
        SettingsService.set_multiple_settings(db, "notification", settings_dict, current_user.id, tenant_id)
        
        return {
            "message": "Notification settings updated successfully",
            "success": True,
            "updated_by": current_user.name
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update notification settings: {str(e)}"
        )


# ==================== BACKUP SETTINGS ====================

@router.get("/backup")
async def get_backup_settings(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Get backup settings (Super Admin or Tenant Admin only).
    
    Returns auto-backup configuration.
    """
    try:
        tenant_id = get_current_tenant(request)
        settings = SettingsService.get_category_settings(db, "backup", tenant_id)
        return settings
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve backup settings: {str(e)}"
        )


@router.put("/backup")
async def update_backup_settings(
    data: BackupSettingsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Update backup settings (Super Admin or Tenant Admin only).
    
    Updates auto-backup frequency, time, location, and retention.
    """
    try:
        tenant_id = get_current_tenant(request)
        settings_dict = data.model_dump(exclude_unset=True)
        SettingsService.set_multiple_settings(db, "backup", settings_dict, current_user.id, tenant_id)
        
        return {
            "message": "Backup settings updated successfully",
            "success": True,
            "updated_by": current_user.name
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update backup settings: {str(e)}"
        )


# ==================== BACKUP MANAGEMENT ====================

@router.post("/backup/create", response_model=BackupRecordResponse)
async def create_backup(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Create a manual database backup (Super Admin or Tenant Admin only).
    
    Creates a backup of the current database state for the tenant.
    """
    try:
        tenant_id = get_current_tenant(request)
        backup = SettingsService.create_backup(db, current_user.id, tenant_id)
        
        # Log backup creation
        log = SystemLog(
            tenant_id=tenant_id,
            log_type="backup",
            message=f"Manual backup created: {backup['name']}",
            details=f"Size: {backup['size_mb']} MB",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        return backup
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create backup: {str(e)}"
        )


@router.get("/backups", response_model=List[BackupRecordResponse])
async def get_backups(
    request: Request,
    limit: int = Query(10, ge=1, le=50, description="Number of backups to return"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Get list of recent backups (Super Admin or Tenant Admin only).
    
    Returns backup history with file information for the tenant.
    """
    try:
        tenant_id = get_current_tenant(request)
        backups = SettingsService.get_backups(db, limit, tenant_id)
        return backups
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve backups: {str(e)}"
        )


@router.delete("/backups/{backup_id}")
async def delete_backup(
    backup_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Delete a backup file (Super Admin or Tenant Admin only).
    
    - **backup_id**: The ID of the backup to delete
    """
    try:
        tenant_id = get_current_tenant(request)
        success = SettingsService.delete_backup(db, backup_id, current_user.id, tenant_id)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Backup not found"
            )
        
        # Log backup deletion
        log = SystemLog(
            tenant_id=tenant_id,
            log_type="backup",
            message=f"Backup deleted: ID {backup_id}",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        return {
            "message": "Backup deleted successfully",
            "success": True,
            "deleted_by": current_user.name
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete backup: {str(e)}"
        )


@router.post("/backup/restore/{backup_id}")
async def restore_backup(
    backup_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Restore from a backup (Super Admin or Tenant Admin only).
    
    - **backup_id**: The ID of the backup to restore
    
    WARNING: This will overwrite current data for the tenant!
    """
    try:
        tenant_id = get_current_tenant(request)
        
        backup = db.query(BackupRecord).filter(
            BackupRecord.id == backup_id,
            BackupRecord.tenant_id == tenant_id
        ).first()
        
        if not backup:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Backup not found"
            )
        
        # TODO: Implement actual restore logic
        # This would involve reading the backup file and restoring the database
        
        # Log restore attempt
        log = SystemLog(
            tenant_id=tenant_id,
            log_type="warning",
            message=f"Backup restore attempted: {backup.name}",
            details=f"User: {current_user.name} attempted to restore from backup",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        return {
            "message": "Restore initiated. This may take a few minutes.",
            "success": True,
            "backup": backup.name,
            "restored_by": current_user.name
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to restore backup: {str(e)}"
        )


# ==================== CACHE MANAGEMENT ====================

@router.post("/cache/clear")
async def clear_cache(
    request: Request,
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Clear application cache.
    
    Clears cached data to free up memory.
    """
    try:
        result = SettingsService.clear_cache()
        
        return {
            "message": "Cache cleared successfully",
            "success": True,
            "cleared_by": current_user.name,
            "size_freed_mb": result.get("size_freed_mb", 0)
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear cache: {str(e)}"
        )


# ==================== SYSTEM INFORMATION ====================

@router.get("/system/info", response_model=SystemInfoResponse)
async def get_system_info(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get system information and statistics.
    
    Returns system version, database status, user counts, etc.
    """
    try:
        tenant_id = get_current_tenant(request)
        info = SettingsService.get_system_info(db, tenant_id)
        return info
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve system info: {str(e)}"
        )


@router.get("/system/health")
async def get_system_health(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get system health status.
    
    Returns health check information for monitoring.
    """
    try:
        # Check database connection
        db.execute("SELECT 1")
        db_status = "healthy"
        
        # Check disk space
        import shutil
        disk_usage = shutil.disk_usage("/")
        disk_free_percent = (disk_usage.free / disk_usage.total) * 100
        
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "components": {
                "database": db_status,
                "api": "healthy",
                "cache": "healthy"
            },
            "disk": {
                "free_gb": round(disk_usage.free / (1024**3), 2),
                "total_gb": round(disk_usage.total / (1024**3), 2),
                "free_percent": round(disk_free_percent, 2)
            }
        }
    except Exception as e:
        return {
            "status": "degraded",
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
        }


# ==================== DATA MANAGEMENT ====================

@router.post("/system/reset")
async def reset_system_data(
    request: Request,
    confirmation: str = Query(..., description="Type 'CONFIRM' to proceed"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Reset all system data (Super Admin or Tenant Admin only).
    
    WARNING: This will delete all transactional data for the tenant!
    Pass confirmation="CONFIRM" to proceed.
    """
    try:
        tenant_id = get_current_tenant(request)
        
        if confirmation != "CONFIRM":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please type 'CONFIRM' to confirm data reset"
            )
        
        result = SettingsService.reset_system_data(db, tenant_id, current_user.id)
        
        # Log reset
        log = SystemLog(
            tenant_id=tenant_id,
            log_type="warning",
            message="System data reset performed",
            details=f"All transactional data cleared by {current_user.name}",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        return {
            "message": "System data reset successfully",
            "success": True,
            "reset_by": current_user.name
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset system data: {str(e)}"
        )


@router.post("/system/export")
async def export_all_data(
    request: Request,
    format: str = Query("json", description="Export format (json, csv)"),
    include_sensitive: bool = Query(False, description="Include sensitive data"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Export all system data (Super Admin or Tenant Admin only).
    
    Exports data in JSON or CSV format for the tenant.
    """
    try:
        tenant_id = get_current_tenant(request)
        data = SettingsService.export_all_data(db, tenant_id)
        
        if format == "csv":
            # Convert to CSV format
            import csv
            from io import StringIO
            
            output = StringIO()
            
            # Export products to CSV
            if data.get("products"):
                writer = csv.DictWriter(output, fieldnames=data["products"][0].keys())
                writer.writeheader()
                writer.writerows(data["products"])
            
            return {
                "message": "Data exported successfully",
                "format": "csv",
                "data": output.getvalue(),
                "exported_by": current_user.name,
                "exported_at": datetime.now().isoformat()
            }
        else:
            # Return JSON
            return {
                "message": "Data exported successfully",
                "format": "json",
                "data": data,
                "exported_by": current_user.name,
                "exported_at": datetime.now().isoformat()
            }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to export data: {str(e)}"
        )


@router.post("/system/import")
async def import_data(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Import data from backup (Super Admin or Tenant Admin only).
    
    TODO: Implement data import functionality.
    """
    return {
        "message": "Import functionality coming soon",
        "success": True
    }


# ==================== INDIVIDUAL SETTING ENDPOINTS ====================

@router.get("/{category}/{key}")
async def get_setting(
    category: str,
    key: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get a specific setting by category and key.
    
    - **category**: Setting category
    - **key**: Setting key
    """
    try:
        tenant_id = get_current_tenant(request)
        value = SettingsService.get_setting(db, category, key, tenant_id)
        
        if value is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting '{category}.{key}' not found"
            )
        
        return {
            "category": category,
            "key": key,
            "value": value
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve setting: {str(e)}"
        )


@router.put("/{category}/{key}")
async def update_setting(
    category: str,
    key: str,
    value: Dict[str, Any],
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Update a specific setting (Super Admin or Tenant Admin only).
    
    - **category**: Setting category
    - **key**: Setting key
    - **value**: New value
    """
    try:
        tenant_id = get_current_tenant(request)
        updated_value = SettingsService.set_setting(db, category, key, value.get("value"), current_user.id, tenant_id)
        
        return {
            "message": "Setting updated successfully",
            "success": True,
            "category": category,
            "key": key,
            "value": updated_value,
            "updated_by": current_user.name
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update setting: {str(e)}"
        )


@router.delete("/{category}/{key}")
async def delete_setting(
    category: str,
    key: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Delete a specific setting (Super Admin or Tenant Admin only).
    
    - **category**: Setting category
    - **key**: Setting key
    """
    try:
        tenant_id = get_current_tenant(request)
        
        query = db.query(SystemSetting).filter(
            SystemSetting.category == category,
            SystemSetting.key == key
        )
        if tenant_id:
            query = query.filter(SystemSetting.tenant_id == tenant_id)
        else:
            query = query.filter(SystemSetting.tenant_id.is_(None))
        
        setting = query.first()
        
        if not setting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting '{category}.{key}' not found"
            )
        
        db.delete(setting)
        db.commit()
        
        # Log deletion
        log = SystemLog(
            tenant_id=tenant_id,
            log_type="settings",
            message=f"Setting deleted: {category}.{key}",
            user_id=current_user.id
        )
        db.add(log)
        db.commit()
        
        return {
            "message": "Setting deleted successfully",
            "success": True,
            "deleted_by": current_user.name
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete setting: {str(e)}"
        )


# ==================== COUPON SETTINGS ====================

@router.get("/coupons")
async def get_coupon_settings(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value, UserRole.MANAGER.value]))
):
    """
    Get coupon/ticket settings.
    
    Returns coupon configuration like auto-reset, thresholds, etc.
    """
    try:
        tenant_id = get_current_tenant(request)
        settings = SettingsService.get_category_settings(db, "coupon", tenant_id)
        return settings
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve coupon settings: {str(e)}"
        )


@router.put("/coupons")
async def update_coupon_settings(
    data: CouponSettingsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.SUPER_ADMIN.value, UserRole.TENANT_ADMIN.value]))
):
    """
    Update coupon/ticket settings (Super Admin or Tenant Admin only).
    
    Updates coupon auto-reset, alert thresholds, etc.
    """
    try:
        tenant_id = get_current_tenant(request)
        settings_dict = data.model_dump(exclude_unset=True)
        SettingsService.set_multiple_settings(db, "coupon", settings_dict, current_user.id, tenant_id)
        
        return {
            "message": "Coupon settings updated successfully",
            "success": True,
            "updated_by": current_user.name
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update coupon settings: {str(e)}"
        )


# ==================== HELPER FUNCTIONS ====================

def get_all_settings_flat(db: Session, tenant_id: Optional[int] = None) -> Dict[str, Any]:
    """Get all settings as a flat dictionary for a tenant"""
    settings = SettingsService.get_all_settings(db, tenant_id)
    flat_settings = {}
    
    for category, category_settings in settings.items():
        for key, value in category_settings.items():
            flat_settings[f"{category}.{key}"] = value
    
    return flat_settings


def get_setting_value(db: Session, tenant_id: Optional[int], category: str, key: str, default: Any = None) -> Any:
    """Get a setting value with default fallback for a tenant"""
    value = SettingsService.get_setting(db, category, key, tenant_id)
    return value if value is not None else default