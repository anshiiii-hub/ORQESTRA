# ORQESTRA AI deployment architecture

```mermaid
flowchart TB
  Browser[Browser]
  Caddy[Caddy reverse proxy\nHTTPS and security headers]
  Web[Static React application\nNginx]
  API[FastAPI API]
  Worker[Polling workflow worker]
  DB[(PostgreSQL 16\nworkflow and artifact state)]
  Redis[(Redis 7\nprovisioned; not in execution path)]
  Secret[Mounted database password secret]
  Backup[Encrypted pg_dump archive]

  Browser -->|TLS 443| Caddy
  Caddy -->|/api, health| API
  Caddy -->|/| Web
  API --> DB
  Worker --> DB
  API -. optional future queue .-> Redis
  Worker -. optional future queue .-> Redis
  Secret -. mounted at runtime .-> DB
  Secret -. mounted at runtime .-> API
  Secret -. mounted at runtime .-> Worker
  DB -->|operator backup drill| Backup
```

The production Compose overlay publishes only Caddy on ports 80 and 443. The frontend and API are internal Compose services; PostgreSQL and Redis have no host port mapping. Caddy terminates TLS and forwards `/api/*` plus the health routes to FastAPI. The single VPS model assumes host firewall rules restrict SSH to operator addresses and permit inbound web traffic only on 80/443.

The backend stores workflows, events, approvals, documents, research provenance, and generated artifacts in PostgreSQL. Redis is provisioned for a future queue/cache use but is not part of execution. A separate polling worker claims ready tasks from PostgreSQL and publishes results while it owns a live lease.

The optional code sandbox must use a separate, dedicated Docker Engine over TLS. The production profile leaves it disabled. Do not mount the production host Docker socket into the API.
