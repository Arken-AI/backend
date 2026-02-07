# Backend (Chat API) — AWS Production Installation

> **Assumes**: EC2 instance running (t3.medium+), Python 3.10+ installed, SSH access ready.  
> **Assumes**: MongoDB + Redis already running (via `docker/docker-compose.yml`).  
> **Assumes**: Both MCP servers already running:
>   - MCP Process Server on port 8080
>   - MCP Calculation Engine Server on port 8082  
> **Port**: **8001** — the only service exposed to the internet (via reverse proxy)

---

## 1. Clone & Setup

```bash
cd ~/workspace
git clone https://github.com/Arken-AI/backend.git
cd backend

python3 -m venv venv
source venv/bin/activate
pip install -e .
```

---

## 2. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```bash
# MongoDB — same credentials as docker/.env (@ must be URL-encoded as %40)
MONGODB_URL=mongodb://arken_app:<URL_ENCODED_PASSWORD>@localhost:27017/arken_process_db?authSource=admin
MONGODB_DB_NAME=arken_process_db

# Redis — password from docker/.env
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=<YOUR_REDIS_PASSWORD>
REDIS_EVENT_TTL=3600

# LLM Provider
DEFAULT_LLM_PROVIDER=claude
ANTHROPIC_API_KEY=<YOUR_ANTHROPIC_API_KEY>
GOOGLE_API_KEY=<YOUR_GOOGLE_API_KEY>  # Optional fallback

# MCP Servers (SSE transport — both on localhost)
MCP_SERVER_URL=http://localhost:8080/sse
MCP_PROCESS_SERVER_ENABLED=true
MCP_CALC_ENGINE_SERVER_URL=http://localhost:8082/sse
MCP_CALC_ENGINE_ENABLED=true

# API — port 8001, allow your domain
CORS_ORIGINS=https://yourdomain.com,http://localhost:5173
API_HOST=0.0.0.0
API_PORT=8001
API_DEBUG=false
FRONTEND_URL=https://yourdomain.com

# Reports
REPORT_STORAGE_PATH=storage/reports
REPORT_MAX_STREAMS_PER_TABLE=10
REPORT_LLM_MODEL=claude-sonnet-4-20250514
REPORT_LLM_MAX_TOKENS=1500
REPORT_PDF_PAGE_SIZE=letter

# Application
LOG_LEVEL=INFO
ENVIRONMENT=production
```

Create the reports directory:

```bash
mkdir -p storage/reports
```

---

## 3. Quick Test

```bash
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

In another terminal:

```bash
curl http://localhost:8001/
# Should return: {"service": "MCP Chat Backend", ...}

curl http://localhost:8001/api/health
# Should return health status with MongoDB, Redis, MCP connections
```

Stop with `Ctrl+C` after verifying.

---

## 4. systemd Service (Production)

```bash
sudo tee /etc/systemd/system/arken-backend.service << 'EOF'
[Unit]
Description=ARKEN Chat Backend (FastAPI on port 8001)
After=network.target docker.service arken-mcp-process-server.service arken-mcp-calc-engine.service
Wants=docker.service
Requires=arken-mcp-process-server.service arken-mcp-calc-engine.service

[Service]
Type=exec
User=ec2-user
WorkingDirectory=/home/ec2-user/workspace/backend
Environment=PATH=/home/ec2-user/workspace/backend/venv/bin:/usr/bin:/bin
EnvironmentFile=/home/ec2-user/workspace/backend/.env
ExecStart=/home/ec2-user/workspace/backend/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 2
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable arken-backend
sudo systemctl start arken-backend
```

> **Note**: Using `--workers 2` for t3.medium (2 vCPU). The backend handles SSE streaming
> which is long-lived; keep worker count low to avoid memory pressure.

---

## 5. Nginx Reverse Proxy (HTTPS + SSE)

The backend is the **only** service that needs external access. Use Nginx to handle HTTPS and proxy to port 8001.

```bash
sudo amazon-linux-extras install nginx1 -y  # or: sudo yum install nginx
sudo systemctl enable nginx
```

Create the config:

```bash
sudo tee /etc/nginx/conf.d/arken.conf << 'EOF'
server {
    listen 80;
    server_name yourdomain.com;

    # Redirect HTTP to HTTPS (after SSL is set up)
    # return 301 https://$host$request_uri;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;

        # Required for SSE streaming
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding off;

        # Standard proxy headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Long timeout for SSE connections
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
EOF

sudo nginx -t && sudo systemctl restart nginx
```

For **HTTPS with Let's Encrypt**:

```bash
sudo yum install certbot python3-certbot-nginx -y
sudo certbot --nginx -d yourdomain.com
```

---

## 6. EC2 Security Group

Open **only** these ports in your EC2 security group:

| Port | Protocol | Source | Purpose |
|---|---|---|---|
| 22 | TCP | Your IP | SSH |
| 80 | TCP | 0.0.0.0/0 | HTTP → HTTPS redirect |
| 443 | TCP | 0.0.0.0/0 | HTTPS (Nginx → Backend) |

**Do NOT expose** ports 8000, 8001, 8080, 8082, 27017, 6379 — all internal.

---

## 7. Verify

```bash
# Service status
sudo systemctl status arken-backend

# Follow logs
sudo journalctl -u arken-backend -f

# Test from outside
curl https://yourdomain.com/
curl https://yourdomain.com/api/health
```

---

## 8. Day-to-Day Commands

```bash
sudo systemctl status arken-backend     # Status
sudo journalctl -u arken-backend -f     # Follow logs
sudo systemctl restart arken-backend    # Restart
sudo systemctl stop arken-backend       # Stop
```

---

## 9. Update Code

```bash
cd ~/workspace/backend
git pull
source venv/bin/activate
pip install -e .
sudo systemctl restart arken-backend
```

---

## 10. Full Startup Order

```
1. Docker (MongoDB + Redis)         → docker.service
2. Calculation Engine (port 8000)    → arken-calc-engine.service
3. MCP Process Server (port 8080)    → arken-mcp-process-server.service
4. MCP Calc Engine (port 8082)       → arken-mcp-calc-engine.service
5. Backend (port 8001)               → arken-backend.service  ← this service
6. Nginx (port 80/443)               → nginx.service
```

The systemd `Requires=` ensures MCP servers are running before backend starts.

---

## 11. Production Checklist

- [ ] `ENVIRONMENT=production` in `.env`
- [ ] `API_DEBUG=false` in `.env`
- [ ] `CORS_ORIGINS` set to your actual domain (not `*`)
- [ ] `FRONTEND_URL` set to your actual domain
- [ ] `ANTHROPIC_API_KEY` is valid and has sufficient credits
- [ ] `REPORT_STORAGE_PATH` directory exists and is writable
- [ ] Nginx configured with HTTPS (Let's Encrypt)
- [ ] EC2 security group only exposes ports 22, 80, 443
- [ ] `.env` is in `.gitignore` (never committed)
