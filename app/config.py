from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from typing import List, Optional
import os
import secrets

class Settings(BaseSettings):
    # ==================== DATABASE CONFIGURATION ====================
    DB_HOST: str = Field(default="mysql.railway.internal", description="Database host")
    DB_PORT: int = Field(default=3306, description="Database port")
    DB_USER: str = Field(default="root", description="Database username")
    DB_PASSWORD: str = Field(default="dHylhDZNHcepytUEqMoDGXjntUVgmgSn", description="Database password")
    DB_NAME: str = Field(default="railway", description="Database name")
    DB_DRIVER: str = Field(default="mysql+pymysql", description="Database driver")
    
    # Connection Pool Settings
    DB_POOL_SIZE: int = Field(default=10, ge=1, le=50, description="Connection pool size")
    DB_MAX_OVERFLOW: int = Field(default=20, ge=0, description="Max overflow connections")
    DB_POOL_TIMEOUT: int = Field(default=30, ge=1, description="Connection timeout in seconds")
    DB_POOL_RECYCLE: int = Field(default=3600, ge=60, description="Connection recycle time in seconds")
    DB_POOL_PRE_PING: bool = Field(default=True, description="Verify connections before using")
    DB_ECHO: bool = Field(default=False, description="Log SQL statements")
    
    @property
    def DATABASE_URL(self) -> str:
        """Construct database URL dynamically"""
        return f"{self.DB_DRIVER}://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"
    
    @property
    def ASYNC_DATABASE_URL(self) -> str:
        """Construct async database URL (for potential async operations)"""
        async_driver = self.DB_DRIVER.replace("pymysql", "aiomysql")
        return f"{async_driver}://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"
    
    # ==================== TENANT CONFIGURATION ====================
    ENABLE_MULTI_TENANT: bool = Field(default=True, description="Enable multi-tenant mode")
    TENANT_MODE: str = Field(default="subdomain", description="Tenant identification mode: subdomain, header, or path")
    DEFAULT_TENANT_ID: Optional[int] = Field(default=None, description="Default tenant ID for single-tenant mode")
    TENANT_HEADER_NAME: str = Field(default="X-Tenant-ID", description="Header name for tenant identification")
    ALLOWED_TENANT_MODES: List[str] = Field(default=["subdomain", "header", "path"])
    
    # ==================== SECURITY CONFIGURATION ====================
    SECRET_KEY: str = Field(default=secrets.token_urlsafe(32), description="JWT secret key")
    ALGORITHM: str = Field(default="HS256", description="JWT algorithm")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, ge=1, description="Access token expiration in minutes")
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, ge=1, description="Refresh token expiration in days")
    
    # Password policy
    PASSWORD_MIN_LENGTH: int = Field(default=6, ge=4, description="Minimum password length")
    PASSWORD_REQUIRE_UPPER: bool = Field(default=False, description="Require uppercase letters")
    PASSWORD_REQUIRE_LOWER: bool = Field(default=False, description="Require lowercase letters")
    PASSWORD_REQUIRE_DIGITS: bool = Field(default=False, description="Require digits")
    PASSWORD_REQUIRE_SPECIAL: bool = Field(default=False, description="Require special characters")
    
    # ==================== SUBSCRIPTION CONFIGURATION (NEW) ====================
    TRIAL_DAYS: int = Field(default=14, ge=1, le=90, description="Default trial period in days")
    GRACE_PERIOD_DAYS: int = Field(default=7, ge=1, le=30, description="Grace period after subscription expiry")
    SUBSCRIPTION_CURRENCY: str = Field(default="USD", description="Currency for subscription pricing")
    AUTO_ACTIVATE_TRIAL: bool = Field(default=True, description="Auto-activate trial for new tenants")
    ALLOW_TRIAL_EXTENSION: bool = Field(default=True, description="Allow super admins to extend trials")
    MAX_TRIAL_EXTENSION_DAYS: int = Field(default=90, ge=1, le=365, description="Maximum total trial extension days")
    
    # Payment Settings
    PAYMENT_METHODS: List[str] = Field(
        default=["cash", "bank_transfer", "credit_card", "mobile_money"],
        description="Available payment methods"
    )
    PAYMENT_VERIFICATION_REQUIRED: bool = Field(
        default=True,
        description="Require manual payment verification for subscriptions"
    )
    AUTO_ACTIVATE_ON_PAYMENT: bool = Field(
        default=True,
        description="Auto-activate subscription when payment is verified"
    )
    
    # Subscription Features
    DEFAULT_MAX_USERS_BASIC: int = Field(default=3, ge=1, description="Default max users for basic plan")
    DEFAULT_MAX_USERS_PRO: int = Field(default=10, ge=1, description="Default max users for professional plan")
    DEFAULT_MAX_USERS_ENTERPRISE: int = Field(default=50, ge=1, description="Default max users for enterprise plan")
    
    # ==================== CORS CONFIGURATION ====================
    CORS_ORIGINS: List[str] = Field(
        default=[
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:8080",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:3000",
        ],
        description="Allowed CORS origins"
    )
    CORS_ALLOW_CREDENTIALS: bool = Field(default=True, description="Allow credentials in CORS")
    CORS_ALLOW_METHODS: List[str] = Field(default=["*"], description="Allowed HTTP methods")
    CORS_ALLOW_HEADERS: List[str] = Field(default=["*"], description="Allowed headers")
    
    # ==================== APPLICATION CONFIGURATION ====================
    APP_NAME: str = Field(default="Multi-Tenant Inventory Management System", description="Application name")
    APP_VERSION: str = Field(default="4.0.0", description="Application version")
    DEBUG: bool = Field(default=False, description="Debug mode")
    ENVIRONMENT: str = Field(default="production", description="Environment: development, staging, production")
    API_PREFIX: str = Field(default="/api", description="API route prefix")
    
    # ==================== FRONTEND CONFIGURATION ====================
    FRONTEND_URL: str = Field(default="http://localhost:5173", description="Frontend application URL")
    FRONTEND_RESET_PASSWORD_URL: str = Field(default="/reset-password", description="Password reset path")
    FRONTEND_VERIFY_EMAIL_URL: str = Field(default="/verify-email", description="Email verification path")
    
    # ==================== EMAIL CONFIGURATION (Optional) ====================
    SMTP_HOST: Optional[str] = Field(default=None, description="SMTP server host")
    SMTP_PORT: int = Field(default=587, description="SMTP server port")
    SMTP_USER: Optional[str] = Field(default=None, description="SMTP username")
    SMTP_PASSWORD: Optional[str] = Field(default=None, description="SMTP password")
    SMTP_FROM_EMAIL: Optional[str] = Field(default=None, description="From email address")
    SMTP_FROM_NAME: str = Field(default="Inventory System", description="From name")
    EMAIL_ENABLED: bool = Field(default=False, description="Enable email notifications")
    
    # Subscription email notifications
    SUBSCRIPTION_EXPIRY_REMINDER_DAYS: List[int] = Field(
        default=[7, 3, 1],
        description="Days before expiry to send reminder emails"
    )
    TRIAL_EXPIRY_REMINDER_DAYS: List[int] = Field(
        default=[7, 3, 1],
        description="Days before trial expiry to send reminder emails"
    )
    
    # ==================== REDIS CONFIGURATION (Optional) ====================
    REDIS_HOST: Optional[str] = Field(default=None, description="Redis host")
    REDIS_PORT: int = Field(default=6379, description="Redis port")
    REDIS_DB: int = Field(default=0, description="Redis database number")
    REDIS_PASSWORD: Optional[str] = Field(default=None, description="Redis password")
    CACHE_ENABLED: bool = Field(default=False, description="Enable Redis caching")
    
    # ==================== RATE LIMITING ====================
    RATE_LIMIT_ENABLED: bool = Field(default=False, description="Enable rate limiting")
    RATE_LIMIT_REQUESTS: int = Field(default=100, description="Number of requests per window")
    RATE_LIMIT_WINDOW: int = Field(default=60, description="Rate limit window in seconds")
    
    # ==================== FILE UPLOAD CONFIGURATION ====================
    MAX_UPLOAD_SIZE_MB: int = Field(default=10, ge=1, le=100, description="Maximum upload size in MB")
    ALLOWED_EXTENSIONS: List[str] = Field(default=[".jpg", ".jpeg", ".png", ".pdf", ".csv", ".xlsx"], description="Allowed file extensions")
    UPLOAD_PATH: str = Field(default="uploads", description="Upload directory path")
    
    # ==================== BACKUP CONFIGURATION ====================
    BACKUP_ENABLED: bool = Field(default=True, description="Enable automated backups")
    BACKUP_PATH: str = Field(default="backups", description="Backup directory path")
    BACKUP_RETENTION_DAYS: int = Field(default=30, ge=1, le=365, description="Backup retention in days")
    BACKUP_SCHEDULE: str = Field(default="0 2 * * *", description="Backup cron schedule")
    
    # ==================== LOGGING CONFIGURATION ====================
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")
    LOG_FILE: str = Field(default="logs/app.log", description="Log file path")
    LOG_MAX_SIZE_MB: int = Field(default=10, ge=1, description="Maximum log file size in MB")
    LOG_BACKUP_COUNT: int = Field(default=5, ge=1, description="Number of backup log files")
    
    # ==================== SUBSCRIPTION SCHEDULER CONFIGURATION (NEW) ====================
    SUBSCRIPTION_CHECK_INTERVAL_HOURS: int = Field(
        default=24,
        ge=1,
        le=168,
        description="Interval in hours for subscription status checks"
    )
    AUTO_SUSPEND_EXPIRED: bool = Field(
        default=True,
        description="Auto-suspend tenants with expired subscriptions"
    )
    SUBSCRIPTION_REMINDER_ENABLED: bool = Field(
        default=True,
        description="Enable subscription expiry reminders"
    )
    
    # ==================== VALIDATORS ====================
    @field_validator("TENANT_MODE")
    def validate_tenant_mode(cls, v):
        """Validate tenant mode is allowed"""
        allowed_modes = ["subdomain", "header", "path"]
        if v not in allowed_modes:
            raise ValueError(f"TENANT_MODE must be one of: {allowed_modes}")
        return v
    
    @field_validator("ENVIRONMENT")
    def validate_environment(cls, v):
        """Validate environment is valid"""
        allowed = ["development", "staging", "production"]
        if v not in allowed:
            raise ValueError(f"ENVIRONMENT must be one of: {allowed}")
        return v
    
    @field_validator("LOG_LEVEL")
    def validate_log_level(cls, v):
        """Validate log level is valid"""
        allowed = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of: {allowed}")
        return v.upper()
    
    @field_validator("SUBSCRIPTION_CURRENCY")
    def validate_currency(cls, v):
        """Validate currency code"""
        allowed_currencies = ["USD", "EUR", "GBP", "ETB", "KES", "NGN", "ZAR"]
        if v.upper() not in allowed_currencies:
            raise ValueError(f"SUBSCRIPTION_CURRENCY must be one of: {allowed_currencies}")
        return v.upper()
    
    @field_validator("TRIAL_DAYS")
    def validate_trial_days(cls, v):
        """Validate trial days"""
        if v < 1:
            raise ValueError("TRIAL_DAYS must be at least 1")
        if v > 90:
            raise ValueError("TRIAL_DAYS cannot exceed 90")
        return v
    
    # ==================== PROPERTIES ====================
    @property
    def is_development(self) -> bool:
        """Check if running in development mode"""
        return self.ENVIRONMENT == "development"
    
    @property
    def is_production(self) -> bool:
        """Check if running in production mode"""
        return self.ENVIRONMENT == "production"
    
    @property
    def is_staging(self) -> bool:
        """Check if running in staging mode"""
        return self.ENVIRONMENT == "staging"
    
    @property
    def is_multi_tenant(self) -> bool:
        """Check if multi-tenant mode is enabled"""
        return self.ENABLE_MULTI_TENANT
    
    @property
    def database_url_without_db(self) -> str:
        """Get database URL without database name (for creating databases)"""
        return f"{self.DB_DRIVER}://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}"
    
    @property
    def redis_url(self) -> Optional[str]:
        """Get Redis URL if Redis is configured"""
        if self.REDIS_HOST:
            auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
            return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return None
    
    @property
    def payment_methods_list(self) -> List[str]:
        """Get list of available payment methods"""
        return self.PAYMENT_METHODS
    
    @property
    def trial_enabled(self) -> bool:
        """Check if trial period is enabled"""
        return self.TRIAL_DAYS > 0
    
    @property
    def grace_period_enabled(self) -> bool:
        """Check if grace period is enabled"""
        return self.GRACE_PERIOD_DAYS > 0
    
    # ==================== CONFIGURATION DICT ====================
    def dict(self):
        """Return configuration as dictionary (excluding sensitive values)"""
        return {
            "app": {
                "name": self.APP_NAME,
                "version": self.APP_VERSION,
                "environment": self.ENVIRONMENT,
                "debug": self.DEBUG,
                "api_prefix": self.API_PREFIX,
            },
            "database": {
                "host": self.DB_HOST,
                "port": self.DB_PORT,
                "name": self.DB_NAME,
                "pool_size": self.DB_POOL_SIZE,
            },
            "tenant": {
                "enabled": self.ENABLE_MULTI_TENANT,
                "mode": self.TENANT_MODE,
            },
            "security": {
                "algorithm": self.ALGORITHM,
                "token_expiry_minutes": self.ACCESS_TOKEN_EXPIRE_MINUTES,
            },
            "subscription": {
                "trial_days": self.TRIAL_DAYS,
                "grace_period_days": self.GRACE_PERIOD_DAYS,
                "currency": self.SUBSCRIPTION_CURRENCY,
                "auto_activate_trial": self.AUTO_ACTIVATE_TRIAL,
                "payment_verification_required": self.PAYMENT_VERIFICATION_REQUIRED,
            },
            "cors": {
                "origins": self.CORS_ORIGINS,
            },
            "features": {
                "email": self.EMAIL_ENABLED,
                "cache": self.CACHE_ENABLED,
                "rate_limiting": self.RATE_LIMIT_ENABLED,
                "backup": self.BACKUP_ENABLED,
            },
        }
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"  # Ignore extra environment variables


# Create singleton instance
settings = Settings()


# ==================== ENVIRONMENT-SPECIFIC HELPERS ====================

def get_database_url_for_tenant(tenant_id: int) -> str:
    """
    Get database URL for a specific tenant (for separate database per tenant).
    This is an advanced multi-tenant pattern where each tenant has their own database.
    """
    db_name = f"{settings.DB_NAME}_tenant_{tenant_id}"
    return f"{settings.DB_DRIVER}://{settings.DB_USER}:{settings.DB_PASSWORD}@{settings.DB_HOST}:{settings.DB_PORT}/{db_name}?charset=utf8mb4"


def is_tenant_database_isolated() -> bool:
    """
    Check if using separate databases per tenant.
    Default is shared database with tenant_id column.
    """
    return os.getenv("TENANT_DATABASE_ISOLATION", "false").lower() == "true"


# ==================== SUBSCRIPTION HELPERS (NEW) ====================

def get_trial_days() -> int:
    """Get configured trial days"""
    return settings.TRIAL_DAYS


def get_grace_period_days() -> int:
    """Get configured grace period days"""
    return settings.GRACE_PERIOD_DAYS


def is_payment_verification_required() -> bool:
    """Check if payment verification is required"""
    return settings.PAYMENT_VERIFICATION_REQUIRED


def get_subscription_currency() -> str:
    """Get subscription currency"""
    return settings.SUBSCRIPTION_CURRENCY


# ==================== CONFIGURATION VALIDATION ====================

def validate_configuration():
    """Validate critical configuration settings"""
    errors = []
    
    # Check required settings for production
    if settings.is_production:
        if settings.SECRET_KEY == "your-secret-key-change-this":
            errors.append("SECRET_KEY must be changed from default in production")
        
        if settings.DEBUG:
            errors.append("DEBUG must be False in production")
        
        if settings.DB_HOST == "localhost":
            errors.append("DB_HOST should not be localhost in production")
        
        # Subscription checks for production
        if settings.PAYMENT_VERIFICATION_REQUIRED == False:
            errors.append("PAYMENT_VERIFICATION_REQUIRED should be True in production")
    
    # Check database connection parameters
    if settings.DB_POOL_SIZE < 1:
        errors.append("DB_POOL_SIZE must be at least 1")
    
    if settings.DB_POOL_TIMEOUT < 1:
        errors.append("DB_POOL_TIMEOUT must be at least 1")
    
    # Check tenant configuration
    if settings.ENABLE_MULTI_TENANT and settings.TENANT_MODE not in settings.ALLOWED_TENANT_MODES:
        errors.append(f"Invalid TENANT_MODE: {settings.TENANT_MODE}")
    
    # Check subscription configuration
    if settings.TRIAL_DAYS < 1:
        errors.append("TRIAL_DAYS must be at least 1")
    
    if settings.GRACE_PERIOD_DAYS < 1:
        errors.append("GRACE_PERIOD_DAYS must be at least 1")
    
    if errors:
        for error in errors:
            print(f"⚠️ Configuration Warning: {error}")
    
    return len(errors) == 0


# Run validation on import
if __name__ != "__main__":
    validate_configuration()