"""
Authentication API Endpoints

REST API endpoints for user authentication:
- POST /login - Authenticate user with shared password and track visitor
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import settings
from app.core.mongo_client import MongoClient
from app.dependencies import get_mongo_client
from app.models.visitor import LoginRequest, LoginResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="User Login",
    description="Authenticate user with a shared password and track visitor in MongoDB.",
)
async def login(
    request: LoginRequest,
    mongo_client: MongoClient = Depends(get_mongo_client),
) -> LoginResponse:
    """
    Authenticate user and record visitor in MongoDB.
    
    Flow:
    1. Validate username is not empty (handled by model validator)
    2. Compare password against shared password from config
    3. If invalid → return 401
    4. If valid → upsert visitor record in MongoDB
    5. Return success with display name
    
    Args:
        request: LoginRequest with username and password
        mongo_client: MongoDB client dependency
    
    Returns:
        LoginResponse with success status and username
    """
    # Validate password against shared password
    if request.password != settings.app_shared_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    
    # Prepare username variants
    display_name = request.username  # Original case (already trimmed by validator)
    username_lower = display_name.lower()  # Lowercase for storage/matching
    
    try:
        # Upsert visitor record in MongoDB
        visitor = await mongo_client.save_visitor(
            username=username_lower,
            display_name=display_name,
        )
        
        logger.info(f"User logged in: {username_lower} (display: {display_name})")
        
        return LoginResponse(
            success=True,
            username=display_name,
            message="Login successful",
        )
        
    except Exception as e:
        logger.error(f"Login failed for user {username_lower}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to connect. Please try again later.",
        )
