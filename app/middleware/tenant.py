# app/middleware/tenant.py
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import Tenant
import re
import logging

logger = logging.getLogger(__name__)

class TenantMiddleware(BaseHTTPMiddleware):
    """
    Multi-tenant middleware that extracts tenant from subdomain or header.
    
    Priority:
    1. X-Tenant-ID header (for API keys)
    2. Subdomain (tenant.yourdomain.com)
    3. Default tenant (if configured)
    """
    
    async def dispatch(self, request: Request, call_next):
        tenant_id = None
        path = request.url.path
        
        # Skip tenant extraction for public/static endpoints
        skip_paths = [
            "/docs", "/openapi.json", "/redoc", "/health", 
            "/", "/favicon.ico", "/api/health", "/api/plans/public"
        ]
        
        # Check if this is a public endpoint
        if any(path.startswith(skip_path) for skip_path in skip_paths):
            request.state.tenant_id = None
            return await call_next(request)
        
        try:
            # Method 1: Check for X-Tenant-ID header
            tenant_header = request.headers.get("X-Tenant-ID")
            if tenant_header:
                tenant_id = tenant_header
            
            # Method 2: Extract from subdomain
            if not tenant_id:
                host = request.headers.get("host", "")
                # Extract subdomain (e.g., "abc" from "abc.yourdomain.com")
                subdomain_match = re.match(r"^([a-zA-Z0-9-]+)\.", host)
                if subdomain_match:
                    subdomain = subdomain_match.group(1)
                    db = SessionLocal()
                    try:
                        tenant = db.query(Tenant).filter(
                            Tenant.subdomain == subdomain,
                            Tenant.status == "active"
                        ).first()
                        if tenant:
                            tenant_id = str(tenant.id)
                    except Exception as e:
                        logger.error(f"Error looking up tenant by subdomain: {e}")
                    finally:
                        db.close()
            
            # Method 3: Skip tenant requirement for super admin routes
            if not tenant_id:
                # Check if this is a super admin route
                if path.startswith("/api/tenants") or path.startswith("/api/super-admin") or "/admin" in path:
                    request.state.tenant_id = None
                    return await call_next(request)
            
            # Store tenant_id in request state
            request.state.tenant_id = int(tenant_id) if tenant_id and tenant_id.isdigit() else None
            
            # For tenant-specific routes, ensure tenant_id exists
            if not tenant_id and not any(path.startswith(skip_path) for skip_path in skip_paths):
                logger.warning(f"No tenant ID for path: {path}")
                # Don't raise exception - just continue without tenant context
                # Let the database layer handle the filtering
                request.state.tenant_id = None
                return await call_next(request)
            
        except Exception as e:
            logger.error(f"Tenant middleware error: {e}")
            request.state.tenant_id = None
        
        response = await call_next(request)
        return response