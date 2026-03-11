"""
Test Configuration Management

Tests for the centralized configuration system.
"""

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set required environment variables for testing
os.environ.setdefault("MONGODB_URL", "mongodb://localhost:27017/test_db")
os.environ.setdefault("MCP_SERVER_COMMAND", "/usr/bin/python")
os.environ.setdefault("MCP_SERVER_ARGS", "/path/to/server.py")
os.environ.setdefault("MCP_SERVER_ENV_MONGODB_URI", "mongodb://localhost:27017/test_db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")

import pytest
from app.config import settings, get_settings


def test_settings_loaded():
    """Test that settings are loaded successfully."""
    assert settings is not None
    assert isinstance(settings.environment, str)
    assert isinstance(settings.api_port, int)


def test_environment_helpers():
    """Test environment helper properties."""
    # In development mode
    assert settings.is_development == True
    assert settings.is_production == False


def test_llm_configuration():
    """Test LLM provider configuration."""
    # Both providers should be configured
    assert settings.has_claude == True
    assert settings.has_gemini == True
    
    # Should return gemini as active provider (default)
    assert settings.get_llm_provider() == "gemini"
    
    # Models should be configured
    assert settings.llm_model_claude == "claude-sonnet-4-6"
    assert settings.llm_model_gemini == "gemini-2.0-flash"


def test_google_api_key_cleaning():
    """Test that Google API key is cleaned (removes leading =)."""
    # Skip if using test key
    if settings.google_api_key == "test-key":
        return
    
    # API key should not start with =
    assert not settings.google_api_key.startswith("=")
    # Should start with expected prefix
    assert settings.google_api_key.startswith("AIzaSy")


def test_redis_url():
    """Test Redis URL construction."""
    redis_url = settings.redis_url
    assert redis_url.startswith("redis://")
    assert f"{settings.redis_host}:{settings.redis_port}" in redis_url


def test_mongodb_configuration():
    """Test MongoDB configuration."""
    assert settings.mongodb_db_name == "arken_process_db"
    assert "mongodb://" in settings.mongodb_url.lower()


def test_cors_origins():
    """Test CORS origins configuration."""
    assert isinstance(settings.cors_origins, str)
    assert "localhost" in settings.cors_origins


def test_llm_parameters():
    """Test LLM parameter constraints."""
    # Temperature should be in valid range
    assert 0.0 <= settings.llm_temperature <= 2.0
    
    # Max tokens should be positive
    assert settings.llm_max_tokens > 0
    
    # Timeout should be positive
    assert settings.llm_timeout > 0


def test_get_settings_dependency():
    """Test FastAPI dependency function."""
    dep_settings = get_settings()
    assert dep_settings is settings
    assert dep_settings.environment == settings.environment


def test_worker_configuration():
    """Test RQ worker configuration."""
    assert settings.rq_queue_name == "default"
    assert settings.rq_worker_count >= 1
    assert settings.rq_job_timeout > 0


def test_api_configuration():
    """Test API server configuration."""
    assert settings.api_host in ("0.0.0.0", "localhost", "127.0.0.1")
    assert 1000 <= settings.api_port <= 65535
    assert isinstance(settings.api_debug, bool)


if __name__ == "__main__":
    # Run with: python -m pytest test_config.py -v
    print("✅ All configuration tests defined")
    print(f"Run with: python -m pytest test_config.py -v")
