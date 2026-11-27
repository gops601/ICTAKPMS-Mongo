# Deployment Guide

The application runs as two Docker containers, started with one `docker compose` command, and stores its data in
MongoDB Atlas. MongoDB is not dockerised.

| Service    | Image / build          | Role                                                              |
|------------|------------------------|-------------------------------------------------------------------|
| `backend`  | `./backend`            | Flask API served by gunicorn on port 5000 (volume `uploads_data`) |
| `frontend` | `./frontend`           | React app built into nginx; proxies `/api/` to the backend        |

Only the `frontend` container is meant to be reachable from outside. It listens on `APP_PORT` (default `8080`).

---

## 1. Server requirements

- A Linux server (Ubuntu 22.04+ recommended), 2 vCPU / 4 GB RAM / 20 GB disk or more
- Docker Engine 24+ and the Docker Compose plugin (`docker compose version`)
- A domain name pointing at the server (needed for HTTPS)
- Ports 80 and 443 open in the firewall
- A MongoDB Atlas cluster (use a dedicated tier, in the region closest to the server, for production), a database user
  with `readWrite` on the app's database, and the server's IP under *Network Access*

Install Docker (Ubuntu):

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER        # log out and back in afterwards
docker compose version
```

---

## 2. Get the code

```bash
git clone https://github.com/gops601/ICTAKPMS_Flask.git
cd ICTAKPMS_Flask
git checkout main                    # use the branch that holds the MongoDB version until it is merged
```

---

## 3. Configure the environment

```bash
cp .env.example .env
nano .env
```

Replace **every** placeholder. Never commit `.env`.

| Variable | What to set |
|----------|-------------|
| `MONGODB_URI` | The Atlas connection string (`mongodb+srv://...`), with the password percent-encoded |
| `MONGODB_DB` | Database name, e.g. `plms` |
| `SECRET_KEY`, `JWT_SECRET_KEY` | Random 48+ character values (see below). **Must be changed**, the built-in defaults are insecure |
| `JWT_ACCESS_MINUTES` / `JWT_REFRESH_DAYS` | Session lengths, e.g. `30` / `7` |
| `APP_PORT` | Host port for the web app. Use `8080` behind a reverse proxy |
| `CORS_ORIGINS` | The public URL of the app, e.g. `https://pms.example.com` |
| `FRONTEND_URL` | Same public URL. Used in password-reset links |
| `MAX_UPLOAD_MB` | Upload limit (default 50). nginx allows up to 60 MB |
| `LOGIN_RATE_LIMIT` | e.g. `10 per minute` |
| `LOG_LEVEL` | `INFO` |
| `ADMIN_NAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` | The first administrator, created on first start only if both email and password are set |

Generate secrets:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Run it once per value (`SECRET_KEY`, `JWT_SECRET_KEY`).

Moving an existing MySQL installation? Follow *Moving data from the old MySQL release* in `README.md` before step 5.

---

## 4. Production hardening of `docker-compose.yml`

If you will put an HTTPS reverse proxy on the same server (step 6), bind the web app to localhost only.
In the `frontend` service change:

```yaml
    ports:
      - "127.0.0.1:${APP_PORT:-8080}:80"
```

---

## 5. Build and start

```bash
docker compose up -d --build
docker compose ps                  # both services should be "Up"
docker compose logs -f backend     # watch the start-up
```

On every start the backend entrypoint automatically:

1. creates missing collections and indexes (`flask init-db`)
2. seeds reference data: roles, organisational units and their batch fields, domains (`flask seed`, safe to repeat)
3. creates the first administrator if `ADMIN_EMAIL` and `ADMIN_PASSWORD` are set (`flask create-admin`)
4. repairs domain data (`flask sync-data`)
5. starts gunicorn (3 workers)

Open `http://<server-ip>:8080` (or your domain) and sign in with the admin account from `.env`.

> After the first login, change the administrator password. You can then remove `ADMIN_PASSWORD` from
> `.env`. It is not needed again.

Quick checks:

```bash
curl -I http://localhost:8080/                       # 200, the web app
curl -s http://localhost:8080/api/docs | head        # API documentation page
```

---

## 6. HTTPS with a reverse proxy (recommended)

Run nginx or Caddy on the host in front of the `frontend` container (which listens on `127.0.0.1:8080`).

### Option A: Caddy (automatic certificates)

```bash
sudo apt install -y caddy
```

`/etc/caddy/Caddyfile`:

```
pms.example.com {
    request_body {
        max_size 60MB
    }
    reverse_proxy 127.0.0.1:8080
}
```

```bash
sudo systemctl reload caddy
```

### Option B: nginx + Let's Encrypt

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

`/etc/nginx/sites-available/pms`:

```nginx
server {
    server_name pms.example.com;
    client_max_body_size 60m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/pms /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d pms.example.com
```

Then make sure `.env` has `CORS_ORIGINS=https://pms.example.com` and `FRONTEND_URL=https://pms.example.com`,
and restart the backend: `docker compose up -d backend`.

---

## 7. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Docker publishes ports itself and can bypass `ufw`. That is why step 4 binds the app to `127.0.0.1`.

---

## 8. Updating to a new version

```bash
cd ICTAKPMS_Flask
git pull origin main
sh deploy.sh                         # zero-downtime update: builds, sets up the database, swaps the backend one container at a time
docker compose ps
docker compose logs --tail 50 backend
```

Take a backup first (step 9). Data in Atlas and the uploads volume are kept.

Rollback: `git checkout <previous-commit>` then `docker compose up -d --build`. If the new version changed stored
data, also restore the backup taken before the update.

---

## 9. Backups

Two things hold data: the **database** and the **uploads volume** (SRS files, datasets, problem statement
documents). Back up both.

Database: turn on **Cloud Backup** for the cluster in Atlas (dedicated tiers). On tiers without it, install the
[MongoDB Database Tools](https://www.mongodb.com/try/download/database-tools) on the server and dump the database.
Run these in the project folder:

```bash
mkdir -p ~/backups
MONGODB_URI=$(grep '^MONGODB_URI=' .env | cut -d= -f2-)
MONGODB_DB=$(grep '^MONGODB_DB=' .env | cut -d= -f2-)
mongodump --uri="$MONGODB_URI" --db="${MONGODB_DB:-plms}" --gzip --archive="$HOME/backups/plms-$(date +%F).archive.gz"
```

Uploaded files:

```bash
docker run --rm -v projectmanagementapp_uploads_data:/data -v ~/backups:/backup alpine \
  tar czf /backup/uploads-$(date +%F).tar.gz -C /data .
```

> The volume name is prefixed with the project folder name. Check it with `docker volume ls`; replace
> `projectmanagementapp_uploads_data` with the exact name shown.

To run it daily, put those commands in a script and schedule it with cron (`crontab -e`).

Copy backups off the server (object storage or another machine) and test a restore periodically.

### Restore

`--drop` replaces the collections in the database with those from the archive.

```bash
MONGODB_URI=$(grep '^MONGODB_URI=' .env | cut -d= -f2-)
mongorestore --uri="$MONGODB_URI" --drop --gzip --archive="$HOME/backups/plms-YYYY-MM-DD.archive.gz"

docker run --rm -v projectmanagementapp_uploads_data:/data -v ~/backups:/backup alpine \
  sh -c 'cd /data && tar xzf /backup/uploads-YYYY-MM-DD.tar.gz'
```

---

## 10. Everyday operations

| Task | Command |
|------|---------|
| Status | `docker compose ps` |
| Live logs | `docker compose logs -f backend` / `frontend` |
| Restart one service | `docker compose restart backend` |
| Stop everything (data kept) | `docker compose down` |
| Start again | `docker compose up -d` |
| Re-run seed / repair manually | `docker compose exec backend flask seed` / `flask sync-data` |
| Create another administrator | `docker compose exec backend flask create-admin` (uses `ADMIN_*` from `.env`) |
| Run the backend tests | On a development machine, see `README.md` (never against production data) |

Containers have `restart: unless-stopped`, so they come back after a server reboot.

> **Never** run `docker compose down -v`. The `-v` flag deletes the uploads volume.

---

## 11. Going live checklist

- [ ] `.env` filled with strong, unique secrets; `.env` is not committed to git
- [ ] Atlas *Network Access* allows only the server (not `0.0.0.0/0`)
- [ ] HTTPS enabled; `CORS_ORIGINS` and `FRONTEND_URL` use the `https://` domain
- [ ] First administrator signed in and the password changed
- [ ] Reference data checked: OUs, domains and subdomains (Admin → Domains), domain leads assigned
- [ ] Backup job scheduled and a restore tested
- [ ] `docker compose ps` shows all services up after a server reboot

---

## 12. Troubleshooting

| Symptom | What to check |
|---------|---------------|
| Backend keeps restarting | `docker compose logs backend`. Usually a wrong `MONGODB_URI` or the server IP missing from Atlas *Network Access* |
| `502 Bad Gateway` on `/api` | Backend still starting or crashed; check backend logs |
| Login works but pages show CORS errors | `CORS_ORIGINS` must exactly match the browser URL (scheme + host, no trailing slash); restart the backend |
| Reset-password links point to the wrong address | Set `FRONTEND_URL` and restart the backend |
| Upload fails with "413" | Raise `MAX_UPLOAD_MB` (backend), `client_max_body_size` in `frontend/nginx.conf` and in your reverse proxy, then rebuild |
| "Too many requests" on login | `LOGIN_RATE_LIMIT` reached; wait or raise it. The limiter is per worker (in-memory) |
| Database password changed | Update `MONGODB_URI` in `.env`, then `docker compose up -d backend` |
| Large bulk upload fails after about a minute | MongoDB cancels a transaction after 60 seconds. Split the file (batch imports are the slowest) and keep the server in the cluster's region |
| Pages are slow | Check the distance between the server and the Atlas region; every database call is a network round trip |
| Disk filling up | `docker system df`; prune old images with `docker image prune -f` |

Backend API documentation is served at `/api/docs`. Consider restricting it at the reverse proxy if you do
not want it public.

---

## 13. CI/CD with Jenkins

The [Jenkinsfile](Jenkinsfile) runs the backend and frontend tests and builds both images on every branch. On
`main`, it then waits for approval and updates the server over SSH.

### Jenkins

1. Use a Linux agent with Docker, git and ssh. The `jenkins` user must be able to run Docker
   (`sudo usermod -aG docker jenkins`, then restart Jenkins). Membership of that group is equivalent to root access.
2. Install the Pipeline, Git, JUnit and Credentials Binding plugins.
3. Create a **Multibranch Pipeline** job pointing at the Git repository (add repository credentials if it is private).
   Add a webhook or periodic scan so that pushes start builds.

### Deployment

Deployment is skipped until `DEPLOY_HOST` is set.

1. Prepare the server with sections 1 to 6. It must be able to `git fetch` the repository without a password prompt,
   and the SSH user must be able to run Docker.
2. In Jenkins, add a credential of type **SSH Username with private key** with the ID `pms-deploy-ssh`.
3. Add the server's host key to `~/.ssh/known_hosts` of the `jenkins` user, after checking its fingerprint
   (`ssh <user>@<server>` once as that user).
4. Set the environment variables (*Manage Jenkins → System → Global properties*, or folder properties):
   - `DEPLOY_HOST`: the server's host name or IP
   - `DEPLOY_DIR`: the project folder on the server, absolute or relative to the SSH user's home
     (default `ICTAKPMS-Mongo`)

Keep the same project folder on an existing server: the uploads volume name is derived from it.

The server is left on the deployed commit (detached HEAD), so use Jenkins rather than `git pull` for later updates.
To update by hand, run `git checkout main` first.

The pipeline does not take a backup. Check that the backups in section 9 are running before you approve a deployment.

Rollback: on the server, `git checkout <previous-commit>` (printed in the deployment log), then
`docker compose up -d --build`.

### Updating without downtime

`sh deploy.sh` (after `git pull`) builds the new images while the old ones keep serving, runs the database set-up once
(the `prepare` service), starts new backend containers beside the old ones, waits until they are healthy, then removes the old
ones. nginx follows through Docker DNS. If the new backend does not become healthy, the old one stays running and the
script stops. The frontend swap takes about a second. `BACKEND_REPLICAS` (default 2) sets the number of backend copies.
A plain `docker compose up -d --build` still works but restarts the backend in one step, so use `deploy.sh`.
