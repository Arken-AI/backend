"""
Visitor & Authentication Models

Pydantic models for login functionality and visitor tracking.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    """Request model for user login"""
    
    username: str = Field(
        ...,
        description="Username for login (any text, case-insensitive)"
    )
    password: str = Field(
        ...,
        description="Shared password for authentication"
    )
    
    @field_validator("username")
    @classmethod
    def username_not_empty(cls, v: str) -> str:
        """Ensure username is not empty or whitespace-only."""
        if not v or not v.strip():
            raise ValueError("Username cannot be empty")
        return v.strip()


class LoginResponse(BaseModel):
    """Response model for login attempts"""
    
    success: bool = Field(
        description="Whether the login was successful"
    )
    username: str = Field(
        default="",
        description="Display name (original case) on success"
    )
    message: Optional[str] = Field(
        default=None,
        description="Optional message (e.g., error description)"
    )


class VisitorRecord(BaseModel):
    """Database model representing a visitor record in MongoDB"""
    
    username: str = Field(
        description="Lowercase username (used as unique key)"
    )
    display_name: str = Field(
        description="Original case username as entered by user"
    )
    first_login_time: datetime = Field(
        description="Timestamp of the user's first login"
    )
    last_login_time: datetime = Field(
        description="Timestamp of the user's most recent login"
    )
    login_count: int = Field(
        default=1,
        description="Total number of logins by this user"
    )
