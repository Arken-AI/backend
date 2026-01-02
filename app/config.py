"""
Application Configuration

This module will be implemented in Phase 2 to load environment variables
and provide configuration settings throughout the application.
"""

from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    
    This will be fully implemented in Phase 2.
    """
    
    # MongoDB Configuration
    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_DB_NAME: str = "arken_process_db"
    
    # Redis Configuration
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""
    REDIS_EVENT_TTL: int = 3600
    
    # Anthropic API
    ANTHROPIC_API_KEY: str = ""
    
    # MCP Server Configuration
    MCP_SERVER_COMMAND: str = "python"
    MCP_SERVER_ARGS: str = "-m,mcp_process_server.server"
    MCP_SERVER_CWD: str = ""
    
    # API Configuration
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8001
    API_DEBUG: bool = True
    
    # Worker Configuration
    RQ_QUEUE_NAME: str = "default"
    RQ_WORKER_COUNT: int = 1
    RQ_JOB_TIMEOUT: int = 300
    
    # Application Settings
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "development"
    
    class Config:
        env_file = ".env"
        case_sensitive = True


# Global settings instance
settings = Settings()
