# app/middleware/tenant.py
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import Tenant
import re

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
                finally:
                    db.close()
        
        # Method 3: Use default tenant for super admin routes
        if not tenant_id:
            # Check if this is a super admin route
            path = request.url.path
            if path.startswith("/admin") or path.startswith("/tenants"):
                # Skip tenant requirement for super admin routes
                request.state.tenant_id = None
                return await call_next(request)
        
        # Store tenant_id in request state
        request.state.tenant_id = tenant_id
        
        # For tenant-specific routes, ensure tenant_id exists
        if not tenant_id and not path.startswith(("/admin", "/tenants", "/docs", "/openapi.json")):
            raise HTTPException(
                status_code=400,
                detail="Tenant identification required. Provide X-Tenant-ID header or use tenant subdomain."
            )
        
        response = await call_next(request)
        return response