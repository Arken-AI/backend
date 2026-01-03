"""
Redis Client for MCP Chat Backend

This module provides a Redis client for:
- Event streaming (conversation events with TTL)
- Job status tracking (background task monitoring)
- Session state management (conversation history)
- Connection pooling and lifecycle management

Architecture:
- Uses redis-py with connection pooling
- Async/await pattern for non-blocking operations
- Automatic reconnection handling
- TTL-based expiration for ephemeral data
"""

import json
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

import redis.asyncio as redis
from redis.asyncio import Redis
from redis.exceptions import RedisError, ConnectionError


logger = logging.getLogger(__name__)


class RedisClient:
    """
    Async Redis client for event streaming, job tracking, and session management.
    
    Key Responsibilities:
    1. Event Streaming: Store/retrieve conversation events with TTL
    2. Job Tracking: Monitor background task status
    3. Session State: Manage conversation history and metadata
    4. Connection Management: Handle connection lifecycle
    
    Redis Key Patterns:
    - events:session:{session_id} -> List of event dictionaries
    - job:{job_id} -> Job status dictionary
    - session:{session_id}:messages -> List of message dictionaries
    - session:{session_id}:metadata -> Session metadata dictionary
    """
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        decode_responses: bool = True,
        max_connections: int = 10,
    ):
        """
        Initialize Redis client with connection parameters.
        
        Args:
            host: Redis host address
            port: Redis port number
            db: Redis database number
            password: Redis password (if authentication enabled)
            decode_responses: Auto-decode byte responses to strings
            max_connections: Maximum connections in the pool
        """
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.decode_responses = decode_responses
        self.max_connections = max_connections
        
        self._client: Optional[Redis] = None
        self._pool: Optional[redis.ConnectionPool] = None
        
        # Default TTL values (in seconds)
        self.EVENT_TTL = 3600  # 1 hour for events
        self.JOB_TTL = 86400  # 24 hours for job status
        self.SESSION_TTL = 7200  # 2 hours for session data
        
        logger.info(
            f"RedisClient initialized (host={host}, port={port}, db={db})"
        )
    
    async def connect(self) -> None:
        """
        Establish connection to Redis with connection pool.
        
        Creates connection pool and Redis client instance.
        Verifies connection with PING command.
        
        Raises:
            ConnectionError: If unable to connect to Redis
        """
        try:
            # Create connection pool
            self._pool = redis.ConnectionPool(
                host=self.host,
                port=self.port,
                db=self.db,
                password=self.password,
                decode_responses=self.decode_responses,
                max_connections=self.max_connections,
            )
            
            # Create Redis client from pool
            self._client = redis.Redis(connection_pool=self._pool)
            
            # Verify connection
            await self._client.ping()
            
            logger.info("Successfully connected to Redis")
            
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise ConnectionError(f"Redis connection failed: {e}")
    
    async def disconnect(self) -> None:
        """
        Close Redis connection and cleanup connection pool.
        
        Gracefully closes all connections in the pool.
        Safe to call multiple times.
        """
        try:
            if self._client:
                await self._client.close()
                self._client = None
            
            if self._pool:
                await self._pool.disconnect()
                self._pool = None
            
            logger.info("Disconnected from Redis")
            
        except Exception as e:
            logger.error(f"Error during Redis disconnect: {e}")
    
    async def is_connected(self) -> bool:
        """
        Check if Redis connection is active.
        
        Returns:
            True if connected and responsive, False otherwise
        """
        if not self._client:
            return False
        
        try:
            await self._client.ping()
            return True
        except Exception:
            return False
    
    # -------------------------------------------------------------------------
    # Event Streaming Methods
    # -------------------------------------------------------------------------
    
    async def add_event(
        self,
        session_id: str,
        event_type: str,
        data: Dict[str, Any],
        ttl: Optional[int] = None,
    ) -> None:
        """
        Add event to session's event stream.
        
        Events are stored as JSON in a Redis list with automatic expiration.
        Each event includes timestamp and monotonic ordering.
        
        Args:
            session_id: Unique session identifier
            event_type: Type of event (e.g., "message", "tool_call", "error")
            data: Event payload (must be JSON-serializable)
            ttl: Time-to-live in seconds (defaults to EVENT_TTL)
        
        Example:
            await redis_client.add_event(
                session_id="sess_123",
                event_type="message",
                data={"role": "user", "content": "Hello"}
            )
        """
        if not self._client:
            raise RuntimeError("Redis client not connected")
        
        key = f"events:session:{session_id}"
        ttl = ttl or self.EVENT_TTL
        
        # Create event structure
        event = {
            "type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "data": data,
        }
        
        try:
            # Add event to list
            await self._client.rpush(key, json.dumps(event))
            
            # Set expiration on first event
            await self._client.expire(key, ttl)
            
            logger.debug(f"Added {event_type} event to session {session_id}")
            
        except RedisError as e:
            logger.error(f"Failed to add event: {e}")
            raise
    
    async def get_events(
        self,
        session_id: str,
        start: int = 0,
        end: int = -1,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve events from session's event stream.
        
        Args:
            session_id: Unique session identifier
            start: Start index (0-based, inclusive)
            end: End index (-1 for all remaining events)
        
        Returns:
            List of event dictionaries ordered chronologically
        
        Example:
            events = await redis_client.get_events("sess_123")
            # [{"type": "message", "timestamp": "...", "data": {...}}, ...]
        """
        if not self._client:
            raise RuntimeError("Redis client not connected")
        
        key = f"events:session:{session_id}"
        
        try:
            # Get events from list
            raw_events = await self._client.lrange(key, start, end)
            
            # Parse JSON events
            events = [json.loads(event) for event in raw_events]
            
            logger.debug(f"Retrieved {len(events)} events from session {session_id}")
            
            return events
            
        except RedisError as e:
            logger.error(f"Failed to get events: {e}")
            raise
    
    # -------------------------------------------------------------------------
    # Job Status Tracking Methods
    # -------------------------------------------------------------------------
    
    async def set_job_status(
        self,
        job_id: str,
        status: str,
        metadata: Optional[Dict[str, Any]] = None,
        ttl: Optional[int] = None,
    ) -> None:
        """
        Update job status with optional metadata.
        
        Job status is stored as JSON hash with automatic expiration.
        Typical statuses: "queued", "running", "completed", "failed"
        
        Args:
            job_id: Unique job identifier
            status: Current job status
            metadata: Additional job information (progress, errors, etc.)
            ttl: Time-to-live in seconds (defaults to JOB_TTL)
        
        Example:
            await redis_client.set_job_status(
                job_id="job_456",
                status="running",
                metadata={"progress": 0.5, "step": "validation"}
            )
        """
        if not self._client:
            raise RuntimeError("Redis client not connected")
        
        key = f"job:{job_id}"
        ttl = ttl or self.JOB_TTL
        
        # Create job status structure
        job_data = {
            "status": status,
            "updated_at": datetime.utcnow().isoformat(),
            "metadata": metadata or {},
        }
        
        try:
            # Store job status as JSON
            await self._client.set(key, json.dumps(job_data), ex=ttl)
            
            logger.debug(f"Set job {job_id} status to {status}")
            
        except RedisError as e:
            logger.error(f"Failed to set job status: {e}")
            raise
    
    async def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve current job status and metadata.
        
        Args:
            job_id: Unique job identifier
        
        Returns:
            Job status dictionary or None if not found
            
        Example:
            status = await redis_client.get_job_status("job_456")
            # {"status": "running", "updated_at": "...", "metadata": {...}}
        """
        if not self._client:
            raise RuntimeError("Redis client not connected")
        
        key = f"job:{job_id}"
        
        try:
            # Retrieve job status
            raw_data = await self._client.get(key)
            
            if raw_data is None:
                logger.debug(f"Job {job_id} not found")
                return None
            
            # Parse JSON
            job_data = json.loads(raw_data)
            
            logger.debug(f"Retrieved status for job {job_id}: {job_data['status']}")
            
            return job_data
            
        except RedisError as e:
            logger.error(f"Failed to get job status: {e}")
            raise
    
    # -------------------------------------------------------------------------
    # Session State Management Methods
    # -------------------------------------------------------------------------
    
    async def add_session_message(
        self,
        session_id: str,
        message: Dict[str, Any],
        ttl: Optional[int] = None,
    ) -> None:
        """
        Add message to session's conversation history.
        
        Messages are stored in Redis list for ordered retrieval.
        Used for maintaining conversation context.
        
        Args:
            session_id: Unique session identifier
            message: Message dictionary (role, content, etc.)
            ttl: Time-to-live in seconds (defaults to SESSION_TTL)
        
        Example:
            await redis_client.add_session_message(
                session_id="sess_123",
                message={"role": "user", "content": "What is the sugar yield?"}
            )
        """
        if not self._client:
            raise RuntimeError("Redis client not connected")
        
        key = f"session:{session_id}:messages"
        ttl = ttl or self.SESSION_TTL
        
        try:
            # Add message to list
            await self._client.rpush(key, json.dumps(message))
            
            # Update expiration
            await self._client.expire(key, ttl)
            
            logger.debug(f"Added message to session {session_id}")
            
        except RedisError as e:
            logger.error(f"Failed to add session message: {e}")
            raise
    
    async def get_session_messages(
        self,
        session_id: str,
        start: int = 0,
        end: int = -1,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve conversation history for session.
        
        Args:
            session_id: Unique session identifier
            start: Start index (0-based, inclusive)
            end: End index (-1 for all messages)
        
        Returns:
            List of message dictionaries in chronological order
        
        Example:
            messages = await redis_client.get_session_messages("sess_123")
            # [{"role": "user", "content": "..."}, {"role": "assistant", ...}]
        """
        if not self._client:
            raise RuntimeError("Redis client not connected")
        
        key = f"session:{session_id}:messages"
        
        try:
            # Get messages from list
            raw_messages = await self._client.lrange(key, start, end)
            
            # Parse JSON messages
            messages = [json.loads(msg) for msg in raw_messages]
            
            logger.debug(f"Retrieved {len(messages)} messages from session {session_id}")
            
            return messages
            
        except RedisError as e:
            logger.error(f"Failed to get session messages: {e}")
            raise
    
    async def set_session_metadata(
        self,
        session_id: str,
        metadata: Dict[str, Any],
        ttl: Optional[int] = None,
    ) -> None:
        """
        Store session metadata (user info, preferences, state).
        
        Args:
            session_id: Unique session identifier
            metadata: Metadata dictionary
            ttl: Time-to-live in seconds (defaults to SESSION_TTL)
        
        Example:
            await redis_client.set_session_metadata(
                session_id="sess_123",
                metadata={"user_id": "user_789", "theme": "dark"}
            )
        """
        if not self._client:
            raise RuntimeError("Redis client not connected")
        
        key = f"session:{session_id}:metadata"
        ttl = ttl or self.SESSION_TTL
        
        try:
            # Store metadata as JSON
            await self._client.set(key, json.dumps(metadata), ex=ttl)
            
            logger.debug(f"Set metadata for session {session_id}")
            
        except RedisError as e:
            logger.error(f"Failed to set session metadata: {e}")
            raise
    
    async def get_session_metadata(
        self,
        session_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve session metadata.
        
        Args:
            session_id: Unique session identifier
        
        Returns:
            Metadata dictionary or None if not found
        """
        if not self._client:
            raise RuntimeError("Redis client not connected")
        
        key = f"session:{session_id}:metadata"
        
        try:
            # Retrieve metadata
            raw_data = await self._client.get(key)
            
            if raw_data is None:
                logger.debug(f"Metadata for session {session_id} not found")
                return None
            
            # Parse JSON
            metadata = json.loads(raw_data)
            
            logger.debug(f"Retrieved metadata for session {session_id}")
            
            return metadata
            
        except RedisError as e:
            logger.error(f"Failed to get session metadata: {e}")
            raise
    
    async def delete_session(self, session_id: str) -> None:
        """
        Delete all data associated with a session.
        
        Removes:
        - Event stream
        - Message history
        - Session metadata
        
        Args:
            session_id: Unique session identifier
        """
        if not self._client:
            raise RuntimeError("Redis client not connected")
        
        keys = [
            f"events:session:{session_id}",
            f"session:{session_id}:messages",
            f"session:{session_id}:metadata",
        ]
        
        try:
            # Delete all session keys
            deleted = await self._client.delete(*keys)
            
            logger.info(f"Deleted session {session_id} ({deleted} keys removed)")
            
        except RedisError as e:
            logger.error(f"Failed to delete session: {e}")
            raise
