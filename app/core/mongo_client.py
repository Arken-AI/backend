"""
MongoDB Client for MCP Chat Backend

This module provides MongoDB client for persistent storage of:
- Conversation history (permanent chat records)
- User-to-run mapping (link users to their simulations)
- User preferences (settings, defaults)

Architecture:
- Uses Motor async driver for non-blocking MongoDB operations
- Stores conversations with user_id and linked run_ids
- Enables analytics, search, and GDPR compliance
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import PyMongoError, DuplicateKeyError


logger = logging.getLogger(__name__)


class MongoClient:
    """
    Async MongoDB client for chat backend persistence.
    
    Key Responsibilities:
    1. Conversation Storage: Permanent chat history with user mapping
    2. Run Tracking: Link users to their MCP simulation runs
    3. User Preferences: Store user settings and defaults
    4. Analytics Support: Enable queries for insights
    
    Collections:
    - conversations: User chat sessions with linked run_ids
    - user_preferences: User-specific settings
    """
    
    def __init__(
        self,
        connection_url: str,
        database_name: str = "arken_process_db",
        max_pool_size: int = 10,
        min_pool_size: int = 1,
    ):
        """
        Initialize MongoDB client with connection parameters.
        
        Args:
            connection_url: MongoDB connection string (mongodb://...)
            database_name: Database name for chat backend
            max_pool_size: Maximum connections in pool
            min_pool_size: Minimum connections in pool
        """
        self.connection_url = connection_url
        self.database_name = database_name
        self.max_pool_size = max_pool_size
        self.min_pool_size = min_pool_size
        
        self._client: Optional[AsyncIOMotorClient] = None
        self._db: Optional[AsyncIOMotorDatabase] = None
        
        logger.info(
            f"MongoClient initialized (database={database_name})"
        )
    
    async def connect(self) -> None:
        """
        Establish connection to MongoDB and create indexes.
        
        Creates:
        - Motor async client with connection pool
        - Database reference
        - Required indexes for performance
        
        Raises:
            PyMongoError: If unable to connect to MongoDB
        """
        try:
            # Create Motor client with connection pool
            self._client = AsyncIOMotorClient(
                self.connection_url,
                maxPoolSize=self.max_pool_size,
                minPoolSize=self.min_pool_size,
            )
            
            # Get database reference
            self._db = self._client[self.database_name]
            
            # Verify connection with ping
            await self._client.admin.command('ping')
            
            # Create indexes for performance
            await self._create_indexes()
            
            logger.info("Successfully connected to MongoDB")
            
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise PyMongoError(f"MongoDB connection failed: {e}")
    
    async def disconnect(self) -> None:
        """
        Close MongoDB connection and cleanup resources.
        
        Gracefully closes all connections in the pool.
        Safe to call multiple times.
        """
        try:
            if self._client:
                self._client.close()
                self._client = None
                self._db = None
            
            logger.info("Disconnected from MongoDB")
            
        except Exception as e:
            logger.error(f"Error during MongoDB disconnect: {e}")
    
    @property
    def db(self) -> Optional[AsyncIOMotorDatabase]:
        """
        Get the underlying AsyncIOMotorDatabase instance.
        
        This property provides direct access to the database for services
        that need to perform custom queries (e.g., ReportGeneratorService).
        
        Returns:
            AsyncIOMotorDatabase instance, or None if not connected
        """
        return self._db
    
    async def is_connected(self) -> bool:
        """
        Check if MongoDB connection is active.
        
        Returns:
            True if connected and responsive, False otherwise
        """
        if self._client is None or self._db is None:
            return False
        
        try:
            await self._client.admin.command('ping')
            return True
        except Exception:
            return False
    
    async def _create_indexes(self) -> None:
        """
        Create database indexes for performance.
        
        Indexes:
        - conversations.session_id (unique, sparse - allows multiple null values)
        - conversations.user_id
        - conversations.created_at
        - conversations.linked_runs
        - user_preferences.user_id (unique)
        """
        try:
            # Conversations collection indexes
            # Use sparse=True so multiple documents can have session_id=null
            await self._db.conversations.create_index("session_id", unique=True, sparse=True)
            await self._db.conversations.create_index("user_id")
            await self._db.conversations.create_index("created_at")
            await self._db.conversations.create_index("linked_runs")
            
            # User preferences collection indexes
            await self._db.user_preferences.create_index("user_id", unique=True)
            
            # Visitors collection indexes
            await self._db.visitors.create_index("username", unique=True)
            
            logger.info("MongoDB indexes created successfully")
            
        except Exception as e:
            # Index creation may fail due to auth, but connection still works
            logger.warning(f"Index creation skipped (auth may be required): {e}")
    
    # -------------------------------------------------------------------------
    # Conversation Storage Methods
    # -------------------------------------------------------------------------
    
    async def save_conversation(
        self,
        session_id: str,
        user_id: str,
        messages: List[Dict[str, Any]],
        linked_runs: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Save or update conversation in MongoDB.
        
        Uses upsert pattern:
        - If session_id exists → update messages, linked_runs, updated_at
        - If new → create new document with created_at
        
        Args:
            session_id: Unique session identifier
            user_id: User who owns this conversation
            messages: Full conversation history
            linked_runs: List of MCP run_ids from this conversation
            metadata: Additional context (source, ip, etc.)
        
        Returns:
            conversation_id (MongoDB ObjectId as string)
        
        Example:
            await mongo_client.save_conversation(
                session_id="sess_123",
                user_id="user_789",
                messages=[{"role": "user", "content": "Hello"}],
                linked_runs=["run_abc"]
            )
        """
        if self._db is None:
            raise RuntimeError("MongoDB client not connected")
        
        now = datetime.now(timezone.utc)
        
        # Prepare document
        document = {
            "session_id": session_id,
            "user_id": user_id,
            "messages": messages,
            "linked_runs": linked_runs or [],
            "metadata": metadata or {},
            "updated_at": now,
        }
        
        try:
            # Upsert: update if exists, insert if new
            result = await self._db.conversations.update_one(
                {"session_id": session_id},
                {
                    "$set": document,
                    "$setOnInsert": {"created_at": now}
                },
                upsert=True
            )
            
            # Get the document ID
            if result.upserted_id:
                conv_id = str(result.upserted_id)
                logger.info(f"Created new conversation: {session_id}")
            else:
                # Find existing document to get its ID
                doc = await self._db.conversations.find_one({"session_id": session_id})
                conv_id = str(doc["_id"]) if doc else None
                logger.info(f"Updated conversation: {session_id}")
            
            return conv_id
            
        except PyMongoError as e:
            logger.error(f"Failed to save conversation: {e}")
            raise
    
    async def get_conversation(
        self,
        session_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve conversation by session_id.
        
        Args:
            session_id: Unique session identifier
        
        Returns:
            Full conversation document or None if not found
        
        Example:
            conv = await mongo_client.get_conversation("sess_123")
            # {"session_id": "sess_123", "user_id": "user_789", "messages": [...]}
        """
        if self._db is None:
            raise RuntimeError("MongoDB client not connected")
        
        try:
            conversation = await self._db.conversations.find_one(
                {"session_id": session_id}
            )
            
            if conversation:
                # Convert ObjectId to string
                conversation["_id"] = str(conversation["_id"])
                logger.debug(f"Retrieved conversation: {session_id}")
            else:
                logger.debug(f"Conversation not found: {session_id}")
            
            return conversation
            
        except PyMongoError as e:
            logger.error(f"Failed to get conversation: {e}")
            raise
    
    async def list_conversations(
        self,
        user_id: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
        sort_by: str = "created_at",
        sort_order: int = -1,
    ) -> List[Dict[str, Any]]:
        """
        List conversations with pagination and filtering.
        
        Args:
            user_id: Filter by user (None = all users)
            limit: Max results per page
            skip: Number of results to skip (for pagination)
            sort_by: Field to sort by
            sort_order: 1 = ascending, -1 = descending
        
        Returns:
            List of conversation documents
        
        Example:
            # Get user's 10 most recent conversations
            conversations = await mongo_client.list_conversations(
                user_id="user_789",
                limit=10
            )
        """
        if self._db is None:
            raise RuntimeError("MongoDB client not connected")
        
        try:
            # Build query filter
            query = {}
            if user_id:
                query["user_id"] = user_id
            
            # Execute query with pagination
            cursor = self._db.conversations.find(query)
            cursor = cursor.sort(sort_by, sort_order).skip(skip).limit(limit)
            
            # Convert cursor to list
            conversations = []
            async for doc in cursor:
                doc["_id"] = str(doc["_id"])
                conversations.append(doc)
            
            logger.debug(
                f"Listed {len(conversations)} conversations (user_id={user_id})"
            )
            
            return conversations
            
        except PyMongoError as e:
            logger.error(f"Failed to list conversations: {e}")
            raise
    
    async def add_run_to_conversation(
        self,
        session_id: str,
        run_id: str
    ) -> bool:
        """
        Add MCP run_id to conversation's linked_runs array.
        
        Uses $addToSet to avoid duplicates.
        
        Args:
            session_id: Unique session identifier
            run_id: MCP run identifier to link
        
        Returns:
            True if successful, False if conversation not found
        
        Example:
            # After MCP tool returns run_id
            await mongo_client.add_run_to_conversation("sess_123", "run_abc")
        """
        if self._db is None:
            raise RuntimeError("MongoDB client not connected")
        
        try:
            result = await self._db.conversations.update_one(
                {"session_id": session_id},
                {
                    "$addToSet": {"linked_runs": run_id},
                    "$set": {"updated_at": datetime.now(timezone.utc)}
                }
            )
            
            if result.matched_count > 0:
                logger.debug(f"Added run {run_id} to conversation {session_id}")
                return True
            else:
                logger.warning(f"Conversation not found: {session_id}")
                return False
            
        except PyMongoError as e:
            logger.error(f"Failed to add run to conversation: {e}")
            raise
    
    async def delete_conversation(
        self,
        session_id: str
    ) -> bool:
        """
        Delete conversation by session_id.
        
        Used for:
        - User requests to delete chat history
        - GDPR compliance (right to be forgotten)
        
        Args:
            session_id: Unique session identifier
        
        Returns:
            True if deleted, False if not found
        """
        if self._db is None:
            raise RuntimeError("MongoDB client not connected")
        
        try:
            result = await self._db.conversations.delete_one(
                {"session_id": session_id}
            )
            
            if result.deleted_count > 0:
                logger.info(f"Deleted conversation: {session_id}")
                return True
            else:
                logger.warning(f"Conversation not found: {session_id}")
                return False
            
        except PyMongoError as e:
            logger.error(f"Failed to delete conversation: {e}")
            raise
    
    # -------------------------------------------------------------------------
    # Visitor Tracking Methods
    # -------------------------------------------------------------------------
    
    async def save_visitor(
        self,
        username: str,
        display_name: str,
    ) -> Dict[str, Any]:
        """
        Save or update a visitor record in MongoDB.
        
        Uses upsert pattern:
        - If username exists → update last_login_time, increment login_count
        - If new → create record with first_login_time, last_login_time, login_count=1
        
        Args:
            username: Lowercase username (unique key)
            display_name: Original case username as entered by user
        
        Returns:
            Visitor document dict
        
        Example:
            visitor = await mongo_client.save_visitor("johndoe", "JohnDoe")
        """
        if self._db is None:
            raise RuntimeError("MongoDB client not connected")
        
        now = datetime.now(timezone.utc)
        
        try:
            result = await self._db.visitors.find_one_and_update(
                {"username": username},
                {
                    "$set": {
                        "display_name": display_name,
                        "last_login_time": now,
                    },
                    "$setOnInsert": {
                        "username": username,
                        "first_login_time": now,
                    },
                    "$inc": {
                        "login_count": 1,
                    },
                },
                upsert=True,
                return_document=True,  # Return updated document
            )
            
            if result:
                result["_id"] = str(result["_id"])
                logger.info(f"Visitor record upserted: {username}")
            
            return result
            
        except PyMongoError as e:
            logger.error(f"Failed to save visitor: {e}")
            raise
    
    async def get_visitor(
        self,
        username: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve visitor record by lowercase username.
        
        Args:
            username: Lowercase username to look up
        
        Returns:
            Visitor document or None if not found
        
        Example:
            visitor = await mongo_client.get_visitor("johndoe")
        """
        if self._db is None:
            raise RuntimeError("MongoDB client not connected")
        
        try:
            doc = await self._db.visitors.find_one({"username": username})
            
            if doc:
                doc["_id"] = str(doc["_id"])
                logger.debug(f"Retrieved visitor: {username}")
            else:
                logger.debug(f"Visitor not found: {username}")
            
            return doc
            
        except PyMongoError as e:
            logger.error(f"Failed to get visitor: {e}")
            raise
    
    # -------------------------------------------------------------------------
    # User Preferences Methods
    # -------------------------------------------------------------------------
    
    async def save_user_preferences(
        self,
        user_id: str,
        preferences: Dict[str, Any]
    ) -> None:
        """
        Save or update user preferences.
        
        Uses upsert pattern.
        
        Args:
            user_id: Unique user identifier
            preferences: User settings dictionary
        
        Example:
            await mongo_client.save_user_preferences(
                user_id="user_789",
                preferences={
                    "units": "metric",
                    "default_industry": "sugar",
                    "theme": "dark"
                }
            )
        """
        if self._db is None:
            raise RuntimeError("MongoDB client not connected")
        
        try:
            await self._db.user_preferences.update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "user_id": user_id,
                        "preferences": preferences,
                        "updated_at": datetime.now(timezone.utc)
                    },
                    "$setOnInsert": {
                        "created_at": datetime.now(timezone.utc)
                    }
                },
                upsert=True
            )
            
            logger.debug(f"Saved preferences for user: {user_id}")
            
        except PyMongoError as e:
            logger.error(f"Failed to save user preferences: {e}")
            raise
    
    async def get_user_preferences(
        self,
        user_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve user preferences.
        
        Args:
            user_id: Unique user identifier
        
        Returns:
            Preferences dictionary or None if not found
        
        Example:
            prefs = await mongo_client.get_user_preferences("user_789")
            # {"units": "metric", "default_industry": "sugar"}
        """
        if self._db is None:
            raise RuntimeError("MongoDB client not connected")
        
        try:
            doc = await self._db.user_preferences.find_one(
                {"user_id": user_id}
            )
            
            if doc:
                logger.debug(f"Retrieved preferences for user: {user_id}")
                return doc.get("preferences")
            else:
                logger.debug(f"No preferences found for user: {user_id}")
                return None
            
        except PyMongoError as e:
            logger.error(f"Failed to get user preferences: {e}")
            raise
