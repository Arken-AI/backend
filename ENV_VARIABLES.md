# Environment Variables Documentation

This document explains all environment variables used in the MCP Chat Backend.

## MongoDB Configuration

### `MONGODB_URL`
- **Description**: Connection string for MongoDB database
- **Default**: `mongodb://your_username:your_password@localhost:27017/arken_process_db?authSource=admin`
- **Example**: `mongodb://arken_app:arken_app_password@localhost:27017/arken_process_db?authSource=admin`
- **Required**: Yes
- **Note**: Uses existing `arken_process_db` database with authentication. Replace `your_username` and `your_password` with actual credentials.

### `MONGODB_DB_NAME`
- **Description**: Name of the MongoDB database to use
- **Default**: `arken_process_db`
- **Required**: Yes
- **Note**: This is the existing database created by `mongo-init.js`

---

## Redis Configuration

### `REDIS_HOST`
- **Description**: Hostname or IP address of Redis server
- **Default**: `localhost`
- **Production**: Use actual Redis host
- **Required**: Yes

### `REDIS_PORT`
- **Description**: Port number for Redis connection
- **Default**: `6379`
- **Required**: Yes

### `REDIS_DB`
- **Description**: Redis logical database number (0-15)
- **Default**: `0`
- **Required**: No

### `REDIS_PASSWORD`
- **Description**: Password for Redis authentication
- **Default**: Empty (no auth for local development)
- **Production**: Set a strong password
- **Required**: No (for local), Yes (for production)

### `REDIS_EVENT_TTL`
- **Description**: Time-to-live for SSE event streams in seconds
- **Default**: `3600` (1 hour)
- **Purpose**: Automatically clean up old event streams to prevent memory bloat
- **Required**: Yes

---

## Anthropic API Configuration

### `ANTHROPIC_API_KEY`
- **Description**: API key for Anthropic Claude LLM
- **Where to get**: https://console.anthropic.com/
- **Format**: `sk-ant-...`
- **Required**: Yes
- **⚠️ Security**: Never commit this to git!

---

## MCP Server Configuration

### `MCP_SERVER_COMMAND`
- **Description**: Command to launch MCP server
- **Default**: `python`
- **Required**: Yes

### `MCP_SERVER_ARGS`
- **Description**: Arguments for MCP server command (comma-separated)
- **Default**: `-m,mcp_process_server.server`
- **Format**: Comma-separated list (no spaces)
- **Required**: Yes

### `MCP_SERVER_CWD`
- **Description**: Working directory for MCP server process
- **Default**: `/Users/akashnikam/arken/calculation_engine`
- **Important**: Set to absolute path of your project root
- **Required**: Yes

---

## MCP Calculation Engine Server Configuration

These settings configure the second MCP server that handles dynamic flowsheet simulations.

### `MCP_CALC_ENGINE_ENABLED`
- **Description**: Enable or disable the calculation engine MCP server
- **Default**: `true`
- **Options**: `true`, `false`
- **Required**: No
- **Note**: Set to `false` to run with only the process server

### `MCP_CALC_ENGINE_COMMAND`
- **Description**: Command to launch the calc engine MCP server
- **Default**: `python`
- **Required**: Yes (if enabled)

### `MCP_CALC_ENGINE_ARGS`
- **Description**: Arguments for the calc engine server command
- **Default**: `server.py`
- **Required**: Yes (if enabled)

### `MCP_CALC_ENGINE_CWD`
- **Description**: Working directory for the calc engine MCP server
- **Example**: `/path/to/mcp_calculation_engine_server`
- **Required**: Yes (if enabled)
- **Important**: Set to absolute path where the server code is located

### `MCP_CALC_ENGINE_ENV_CALC_ENGINE_URL`
- **Description**: URL of the calculation engine API
- **Default**: `http://localhost:8000`
- **Required**: No (uses default if not set)
- **Note**: Usually the same as the process server's calculation engine

### `MCP_CALC_ENGINE_ENV_MONGODB_URI`
- **Description**: MongoDB URI for the calc engine server
- **Default**: Uses `MONGODB_URL` if not set
- **Required**: No
- **Note**: Both servers typically use the same database

---

## API Configuration

### `CORS_ORIGINS`
- **Description**: Allowed origins for CORS (comma-separated)
- **Default**: `http://localhost:5173,http://localhost:3000`
- **Purpose**: Allow frontend apps to call backend API
- **Production**: Set to actual frontend domain(s)
- **Required**: Yes

### `API_HOST`
- **Description**: Host to bind the API server
- **Default**: `0.0.0.0` (all interfaces)
- **Required**: Yes

### `API_PORT`
- **Description**: Port number for API server
- **Default**: `8000`
- **Required**: Yes

### `API_DEBUG`
- **Description**: Enable debug mode (detailed error messages)
- **Default**: `true`
- **Production**: Set to `false`
- **Required**: No

---

## Worker Configuration (RQ)

### `RQ_QUEUE_NAME`
- **Description**: Name of the Redis Queue for background jobs
- **Default**: `default`
- **Required**: Yes

### `RQ_WORKER_COUNT`
- **Description**: Number of RQ worker processes to run
- **Default**: `1`
- **Production**: Set based on server capacity (2-4 recommended)
- **Required**: Yes

### `RQ_JOB_TIMEOUT`
- **Description**: Maximum execution time for a job (seconds)
- **Default**: `300` (5 minutes)
- **Purpose**: Prevent runaway jobs
- **Required**: Yes

---

## Report Generation Settings

These settings configure the detailed PDF report generation feature with AI-powered narratives.

### `REPORT_STORAGE_PATH`
- **Description**: Directory path for storing generated PDF reports
- **Default**: `storage/reports`
- **Example**: `/var/data/reports` (production)
- **Required**: No (uses default)
- **Note**: Directory will be created automatically if it doesn't exist

### `REPORT_MAX_STREAMS_PER_TABLE`
- **Description**: Maximum number of streams to display per table before splitting
- **Default**: `10`
- **Range**: `5-20` recommended
- **Required**: No
- **Purpose**: Prevents tables from becoming too wide for PDF pages

### `REPORT_LLM_MODEL`
- **Description**: Anthropic model to use for generating report narratives
- **Default**: `claude-sonnet-4-20250514`
- **Options**: Any valid Anthropic model (e.g., `claude-3-opus-20240229`)
- **Required**: No (uses default)
- **Note**: Requires `ANTHROPIC_API_KEY` to be set for AI features

### `REPORT_LLM_MAX_TOKENS`
- **Description**: Maximum tokens for AI-generated narrative sections
- **Default**: `1500`
- **Range**: `500-4000` recommended
- **Required**: No
- **Purpose**: Controls length of executive summaries and observations

### `REPORT_PDF_PAGE_SIZE`
- **Description**: PDF page size for generated reports
- **Default**: `letter`
- **Options**: `letter` (8.5" x 11"), `A4` (210mm x 297mm)
- **Required**: No

---

## Application Settings

### `LOG_LEVEL`
- **Description**: Logging level for the application
- **Options**: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
- **Default**: `INFO`
- **Development**: Use `DEBUG` for detailed logs
- **Production**: Use `INFO` or `WARNING`
- **Required**: Yes

### `ENVIRONMENT`
- **Description**: Current environment name
- **Options**: `development`, `staging`, `production`
- **Default**: `development`
- **Required**: Yes

---

## Security Notes

### Never commit to Git:
- `.env` file (add to `.gitignore`)
- `ANTHROPIC_API_KEY`
- `REDIS_PASSWORD` (in production)
- Any production credentials

### Safe to commit:
- `.env.example` (template with placeholder values)
- This documentation file

---

## Quick Setup

1. Copy `.env.example` to `.env`:
   ```bash
   cp backend/.env.example backend/.env
   ```

2. Edit `backend/.env` and update:
   - `ANTHROPIC_API_KEY` - Get from https://console.anthropic.com/
   - `MCP_SERVER_CWD` - Set to your project's absolute path
   - Other values as needed

3. Verify configuration:
   ```bash
   # Test MongoDB connection
   mongosh --eval "db.adminCommand('ping')"
   
   # Test Redis connection
   redis-cli ping
   ```

---

## Troubleshooting

### Can't connect to MongoDB
- Check `MONGODB_URL` is correct
- Verify MongoDB container is running: `docker ps`
- Test connection: `mongosh <MONGODB_URL>`

### Can't connect to Redis
- Check `REDIS_HOST` and `REDIS_PORT` are correct
- Verify Redis container is running: `docker ps`
- Test connection: `redis-cli -h <REDIS_HOST> -p <REDIS_PORT> ping`

### Invalid Anthropic API Key
- Verify key format starts with `sk-ant-`
- Check key is active at https://console.anthropic.com/
- Ensure no extra spaces in `.env` file

### MCP Server won't start
- Verify `MCP_SERVER_CWD` points to correct directory
- Check MCP server is installed: `python -m mcp_process_server.server --help`
- Ensure all MCP dependencies are installed
