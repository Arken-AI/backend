"""
Application Configuration

Centralized configuration management using Pydantic Settings.
Loads from environment variables and .env file with validation.

Usage:
    from app.config import settings
    
    # Access configuration
    print(settings.mongodb_url)
    print(settings.api_port)
    
    # Settings are validated on startup
    # Missing required fields will raise an error
"""

from typing import Optional, List
from pydantic import Field, validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    
    Configuration priority:
    1. Environment variables
    2. .env file
    3. Default values (if provided)
    """
    
    # =============================================================================
    # MongoDB Configuration
    # =============================================================================
    mongodb_url: str = Field(
        ...,
        description="MongoDB connection URL with authentication"
    )
    mongodb_db_name: str = Field(
        default="arken_process_db",
        description="MongoDB database name"
    )
    
    # =============================================================================
    # Redis Configuration
    # =============================================================================
    redis_host: str = Field(
        default="localhost",
        description="Redis server hostname"
    )
    redis_port: int = Field(
        default=6379,
        description="Redis server port"
    )
    redis_db: int = Field(
        default=0,
        description="Redis database number"
    )
    redis_password: str = Field(
        default="",
        description="Redis password (empty for no auth)"
    )
    redis_event_ttl: int = Field(
        default=3600,
        description="TTL for Redis events in seconds"
    )
    redis_context_ttl: int = Field(
        default=3600,
        description="TTL for Redis context (conversation state) in seconds"
    )
    
    # =============================================================================
    # MCP Server Configuration
    # =============================================================================
    mcp_server_command: str = Field(
        ...,
        description="Path to Python interpreter for MCP server"
    )
    mcp_server_args: str = Field(
        ...,
        description="Path to MCP server.py file"
    )
    mcp_server_env_calc_engine_url: str = Field(
        default="http://localhost:8000",
        description="Calculation engine API URL"
    )
    mcp_server_env_max_stored_runs: int = Field(
        default=1000,
        description="Maximum stored runs in MCP server"
    )
    mcp_server_env_mongodb_uri: str = Field(
        ...,
        description="MongoDB URI for MCP server"
    )
    
    # =============================================================================
    # LLM Provider Configuration
    # =============================================================================
    anthropic_api_key: Optional[str] = Field(
        default=None,
        description="Anthropic (Claude) API key"
    )
    google_api_key: Optional[str] = Field(
        default=None,
        description="Google (Gemini) API key"
    )
    
    default_llm_provider: str = Field(
        default="gemini",
        description="Default LLM provider to use (claude or gemini)"
    )
    
    llm_model_claude: str = Field(
        default="claude-3-5-sonnet-20241022",
        description="Claude model to use"
    )
    llm_model_gemini: str = Field(
        default="gemini-2.0-flash",
        description="Gemini model to use"
    )
    
    llm_max_tokens: int = Field(
        default=8192,
        description="Maximum tokens for LLM responses"
    )
    llm_temperature: float = Field(
        default=1.0,
        description="LLM temperature (0.0 to 2.0)"
    )
    llm_timeout: float = Field(
        default=300.0,
        description="LLM request timeout in seconds"
    )
    
    # =============================================================================
    # API Configuration
    # =============================================================================
    api_host: str = Field(
        default="0.0.0.0",
        description="API server host"
    )
    api_port: int = Field(
        default=8001,
        description="API server port"
    )
    api_debug: bool = Field(
        default=False,
        description="Enable debug mode"
    )
    
    cors_origins: str = Field(
        default="http://localhost:5173,http://localhost:3000",
        description="Comma-separated CORS origins"
    )
    
    # =============================================================================
    # Worker Configuration (RQ)
    # =============================================================================
    rq_queue_name: str = Field(
        default="default",
        description="RQ queue name"
    )
    rq_worker_count: int = Field(
        default=1,
        description="Number of RQ workers"
    )
    rq_job_timeout: int = Field(
        default=300,
        description="RQ job timeout in seconds"
    )
    
    # =============================================================================
    # Application Settings
    # =============================================================================
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR)"
    )
    environment: str = Field(
        default="development",
        description="Environment (development, staging, production)"
    )
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )
    
    # =============================================================================
    # Validators
    # =============================================================================
    
    @validator("cors_origins", pre=True)
    def parse_cors_origins(cls, v):
        """Convert comma-separated string to string (no conversion needed)."""
        return v
    
    @validator("default_llm_provider")
    def validate_llm_provider(cls, v):
        """Ensure valid LLM provider."""
        if v.lower() not in ("claude", "gemini"):
            raise ValueError("default_llm_provider must be 'claude' or 'gemini'")
        return v.lower()
    
    @validator("log_level")
    def validate_log_level(cls, v):
        """Ensure valid log level."""
        valid_levels = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        if v.upper() not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}")
        return v.upper()
    
    @validator("llm_temperature")
    def validate_temperature(cls, v):
        """Ensure temperature is in valid range."""
        if not 0.0 <= v <= 2.0:
            raise ValueError("llm_temperature must be between 0.0 and 2.0")
        return v
    
    @validator("google_api_key", pre=True)
    def clean_google_api_key(cls, v):
        """Clean up Google API key (remove accidental leading =)."""
        if v and isinstance(v, str):
            return v.strip().lstrip('=')
        return v
    
    # =============================================================================
    # Helper Properties
    # =============================================================================
    
    @property
    def is_production(self) -> bool:
        """Check if running in production."""
        return self.environment.lower() == "production"
    
    @property
    def is_development(self) -> bool:
        """Check if running in development."""
        return self.environment.lower() == "development"
    
    @property
    def has_claude(self) -> bool:
        """Check if Claude API key is configured."""
        return self.anthropic_api_key is not None and len(self.anthropic_api_key) > 0
    
    @property
    def has_gemini(self) -> bool:
        """Check if Gemini API key is configured."""
        return self.google_api_key is not None and len(self.google_api_key) > 0
    
    @property
    def redis_url(self) -> str:
        """Construct Redis URL."""
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"
    
    def get_llm_provider(self) -> str:
        """
        Get the LLM provider to use based on availability and configuration.
        
        Returns:
            'claude' or 'gemini'
            
        Raises:
            ValueError if no LLM provider is configured
        """
        # If default provider is available, use it
        if self.default_llm_provider == "claude" and self.has_claude:
            return "claude"
        elif self.default_llm_provider == "gemini" and self.has_gemini:
            return "gemini"
        
        # Fallback: use whichever is available
        if self.has_gemini:
            return "gemini"
        elif self.has_claude:
            return "claude"
        
        raise ValueError(
            "No LLM provider configured. Set ANTHROPIC_API_KEY or GOOGLE_API_KEY"
        )


# =============================================================================
# Global Settings Instance
# =============================================================================

# Create a single settings instance that can be imported throughout the app
settings = Settings()


# =============================================================================
# FastAPI Dependency
# =============================================================================

def get_settings() -> Settings:
    """
    FastAPI dependency for getting settings.
    
    Usage in FastAPI endpoints:
        @app.get("/health")
        async def health(settings: Settings = Depends(get_settings)):
            return {"status": "ok", "environment": settings.environment}
    """
    return settings
    ENVIRONMENT: str = "development"
    
    class Config:
        env_file = ".env"
        case_sensitive = True


# Global settings instance
settings = Settings()
