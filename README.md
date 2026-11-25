# ICTAK

ICTAK is a web application for managing student projects from problem-statement allocation through SRS approval,
Scrum tracking, weekly reviews and final evaluation, across multiple OUs, domains and batches.

**Stack:** Flask · PyMongo · JWT · MongoDB (Atlas) · React (Vite) · Tailwind · Docker Compose.

See [ARCHITECTURE.md](ARCHITECTURE.md) for design decisions and [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md)
for progress. Current status: **Phase 8 (Dashboards & reports) complete.**

## What runs where

| Part | Runs in |
|---|---|
| Frontend (React, served by nginx) | Docker |
| Backend (Flask, gunicorn) | Docker |
| MongoDB | **MongoDB Atlas** (hosted). It is not dockerised |
| Uploaded files | Docker volume `uploads_data` |

## Quick start

1. **Atlas:** create a cluster and a database user, and add this machine's IP under *Network Access*. Copy the
   connection string from *Connect → Drivers*.
2. **Configure:** create `.env` in the project folder (skip if you already have one).

   ```bash
   cp .env.example .env          # PowerShell: Copy-Item .env.example .env
   ```

   Set `MONGODB_URI` (percent-encode special characters in the password), `MONGODB_DB`, `SECRET_KEY`,
   `JWT_SECRET_KEY` and `ADMIN_*`.
3. **Start** (Docker Desktop must be running):

   ```bash
   docker compose up -d --build
   docker compose ps                     # backend and frontend should be "Up"
   docker compose logs -f backend        # ready at "Listening at: http://0.0.0.0:5000"
   ```

4. Open http://localhost:8080 (or the `APP_PORT` in `.env`). API docs (Swagger UI): `/api/docs` on the same address.

Upgrading an installation that ran the MySQL release? First read *Moving data from the old MySQL release* below, and
add `--remove-orphans` to the first `up` so the old database container is removed (its data volume is kept).

| To do this | Command |
|---|---|
| Start | `docker compose up -d` |
| Stop | `docker compose down` |
| Apply code changes | `docker compose up -d --build` |
| Recent errors | `docker compose logs backend --tail 50` |

Never add `-v` to `docker compose down`: it deletes the uploaded files.

On start the backend container automatically runs `flask init-db` (collections and indexes), `flask seed` (roles, the four OUs with
their batch fields, the six domains) and, when `ADMIN_EMAIL` and `ADMIN_PASSWORD` are set, `flask create-admin`.
All of these are idempotent. Data lives in Atlas; uploads persist across restarts.

Manual equivalents:

```bash
docker compose exec backend flask init-db
docker compose exec backend flask create-admin --email you@example.com --password 'S3cure-pass1'
```

## Local development (without Docker)

The backend reads `.env` from the project folder. To keep development work apart from real data, set another database
name for the terminal (it takes precedence over `.env`).

```powershell
# terminal 1 - backend, http://localhost:5000
cd backend
python -m venv .venv
.venv\Scripts\activate                       # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
$env:MONGODB_DB = "plms_dev"                  # Linux/macOS: export MONGODB_DB=plms_dev
$env:FLASK_APP = "run.py"                     # Linux/macOS: export FLASK_APP=run.py
flask init-db
flask create-admin                            # uses ADMIN_* from .env; also seeds roles, OUs and domains
python run.py

# terminal 2 - frontend, http://localhost:5173 (proxies /api to :5000)
cd frontend
npm install
npm run dev
```

Uploaded files are stored in `backend/uploads` here, not in the Docker volume. To see files uploaded through Docker,
copy them while its containers exist: `docker compose cp backend:/app/uploads/. backend/uploads`.

## Tests

**Every backend test deletes all data in its test database.** Never point the tests at a database you care about.

The tests need a MongoDB replica set (for transactions). By default they use `localhost:27027`, so install
[MongoDB Community Server](https://www.mongodb.com/try/download/community) and `mongosh`, then run a throwaway
instance:

```powershell
# terminal 1 - leave running
mkdir C:\mongo-test
mongod --replSet rs0 --port 27027 --dbpath C:\mongo-test

# terminal 2 - the first time only
mongosh --port 27027 --eval "rs.initiate()"

cd backend
python -m pytest -q
cd ..\frontend
npm test
```

An Atlas database can be used instead, with `TEST_MONGODB_URI` set to the connection string and `TEST_MONGODB_DB` to a
name used for nothing else (default `plms_test`). Expect it to be very much slower: every database call crosses the
network.

## Data model

Each model in `backend/app/models` is one collection with integer `_id`s; links between documents are ids, as the
former tables were. `backend/app/odm` provides the models, queries and unit of work on top of PyMongo:

- Queries address **one collection**. To filter by a related document, pass a sub-query to `in_()`:
  `db.select(Project).where(Project.batch_id.in_(db.select(Batch.id).where(Batch.domain_id == 3)))`.
- `db.session.prefetch(objects, "batch.ou")` loads relationships in bulk (avoids one query per object).
- Writes run in a MongoDB transaction until `db.session.commit()`; `with_for_update()` write-locks documents, and a
  request that collides with another one is replayed.
- Foreign-key rules (`ondelete`) are applied by the application; unique keys are MongoDB indexes. Text comparison
  and sorting are case-insensitive.
- MongoDB aborts a transaction after 60 seconds. Bulk operations must add objects and let one flush insert them,
  rather than query and flush row by row.
- A new field needs no migration (missing fields read as `None`). New collections and indexes are created by
  `flask init-db`.

## Moving data from the old MySQL release

Needed once, on a machine that has the old Docker volume `mysql_data`. The MySQL schema must be that of the last MySQL
release, and `.env` needs `MONGODB_URI` as well as the old `MYSQL_*` values. MySQL runs only for the duration of the
copy ([docker-compose.migrate.yml](docker-compose.migrate.yml)).

```bash
docker compose stop backend frontend
docker compose -f docker-compose.yml -f docker-compose.migrate.yml run --rm --build migrate
docker compose -f docker-compose.yml -f docker-compose.migrate.yml stop db
docker compose up -d --build --remove-orphans
```

It copies every table with the same ids, checks the counts and only reads MySQL. It refuses to write into a database
that already has data.

To copy again, **replacing everything in `MONGODB_DB`** (including anything entered in the new version), use this in
place of the second command:

```bash
docker compose -f docker-compose.yml -f docker-compose.migrate.yml run --rm --build --entrypoint sh migrate -c "pip install --quiet --user -r requirements-migrate.txt && python scripts/migrate_mysql_to_mongo.py --replace"
```

Once you no longer need the old data, the `MYSQL_*` lines in `.env` and `docker-compose.migrate.yml` can be removed.

## Security notes

- Secrets only via environment (`.env` is git-ignored). Never put them in frontend code.
- Roles are re-read from the database on every request; tokens are not trusted for authorisation.
- Data scope (domain / batch / project) is enforced in `backend/app/permissions/scope.py`.
- Login and password-reset endpoints are rate-limited, and accounts lock after repeated failures.
- In production, restrict Atlas *Network Access* to the server's IP and terminate TLS in front of nginx.
- Password-reset links are logged to the backend log until an SMTP mailer is configured.

## Layout

```
backend/   Flask app (app/models, odm, routes, services, schemas, permissions, utils), scripts, tests
frontend/  React + Vite + Tailwind SPA (served by nginx in Docker)
uploads/   Placeholder for file storage (Docker uses the volume uploads_data)
```

## Bulk import workflow (admin)

1. Admin → *Batches → Bulk upload* (or *Students → Bulk upload*), pick the OU, download its template.
2. Fill the template (columns follow the OU's configured fields) and upload `.csv`/`.xlsx` (max 5 MB, 5000 rows).
3. Review the preview and row-wise errors; download the error report if needed.
4. *Confirm import* (blocked while errors exist) or explicitly *Import valid rows only*.
5. For students, download the one-time credentials CSV from the summary screen.

## Problem statements & files

Domain leads (own domains) and admins manage problem statements under *Problem Statements*. Create a statement,
then attach datasets (CSV/XLSX/JSON/ZIP… ≤ 50 MB) and reference documents (PDF/Office/images ≤ 25 MB). Bulk
upload takes an Excel/CSV template; large data files are attached afterwards, not embedded in cells.
Files are stored under `UPLOAD_FOLDER` (Docker volume `uploads_data`) with random names; downloads always go
through authorised API endpoints. To move to S3-compatible storage, implement `StorageBackend` in
`backend/app/services/storage.py` and set `STORAGE_BACKEND`.

## Allocation workflow

1. Admin assigns a batch to a domain (its Domain Lead is notified).
2. Domain Lead → *My Batches → batch → Mentors*: assign mentors (reassign/remove any time).
3. *Allocate projects*: pick a problem statement + students (individual projects or one team). Each project gets a
   Scrum board, four weekly review slots and a 4-week schedule; students and mentors are notified.
4. Student starts SRS preparation from *My Project*. Later stages (SRS approval, implementation, final evaluation,
   completion) are enforced by `backend/app/services/lifecycle.py`; admins can reopen a project with a reason.

## SRS workflow

Student: *My Project → Start SRS preparation → SRS Submission* (PDF/DOCX) → mentor reviews in *SRS Review*
(start review / approve / request changes / reject, comments required for the last two) → on approval *implementation*
unlocks and the student can add the GitHub repository. Each resubmission is a new version; the full decision history is kept.

## Scrum

Each project has a board (Backlog → To Do → In Progress → In Review → Completed) that unlocks after SRS approval.
Students create, assign, prioritise, date and move tasks (drag & drop or the per-card selector) and comment; mentors view
and comment (and move tasks only if the Domain Lead enables it on the project); leads and admins monitor via
*Scrum Monitoring*. Every change is kept in the task's history and the project's activity timeline.

## Evaluations

Mentors define criteria per weekly review (or apply their own templates, *Evaluation Templates → Add sample templates* offers the
brief's examples), score each criterion, add feedback and submit; totals and percentages are calculated automatically and the
student sees the evaluation only once submitted. Corrections after submission need *Reopen* with a reason (audited, previous
version kept). After all four reviews the mentor moves the project to *Final Evaluation*, signs it off, then completes it.

Run the full 20-step lifecycle against a running stack: see `backend/tests/lifecycle_flow.py` (`run(call, admin_email, admin_password, tag)`).

## Dashboards & reports

Each role lands on a live dashboard (admin: institution-wide; domain lead: own domains; mentor: queues and students needing attention;
student: own project). Charts always have a *View as table* option. *Reports* (admin, domain lead) and *Project Reports* (mentor) offer OU,
domain, batch, student, mentor, weekly-evaluation and project-completion reports with filters and CSV/Excel export; data is limited to what
the signed-in role may see.

## Batch filter

Admins, Domain Leads and mentors can narrow dashboards and the project, SRS, Scrum and evaluation lists to a single batch (the
selector only offers batches the signed-in user may access). Reports have their own batch filter.

## Sharing student logins

*Bulk upload* (and *Add student*) end with **Download student details with passwords** (CSV: name, login email, temporary password,
registration number, phone, batch, login URL). Passwords are only shown once, so later use **Students → Download credentials**: pick a
batch and new temporary passwords are generated for students who have not logged in yet (students who already set their own password are
never changed). Everyone must change the password at first login.

## Look & feel, artwork

The interface uses an indigo / teal scheme (tokens in `frontend/tailwind.config.js` and `frontend/src/index.css`). All artwork is built-in SVG
(`frontend/src/components/illustrations.jsx`: logo, sign-in illustration, empty states, dashboard banner), so there are no external image
requests. To show your own photography or AI-generated artwork on the sign-in page, put the file in `frontend/public/images/` and set
`VITE_HERO_IMAGE=/images/your-file.jpg` before building the frontend.

## Scoring rules

* **Only mentors give scores** (the project's assigned mentor). Admins and Domain Leads can view, never score.
* A mentor adds a **remark for every student** while scoring (each student sees only their own remark) as well as overall feedback.
* A **Domain Lead can revert a submitted score to the mentor** with a reason (*Revert to mentor*): the mentor is notified, sees the reason,
  updates the score and resubmits. The previous version stays in the evaluation history.
* **Batch detailed report** (admin and Domain Lead): *Reports → Batch detailed report*, or *Detailed report* on a batch. One Excel workbook with
  Summary, Projects, Students, Weekly scores (with remarks) and Criteria scores sheets (or any sheet as CSV).

## Review dates

A mentor sets a review's date freely the first time. Once it is saved the date is **locked**; to move it the mentor uses **Request date change**
(new date + reason). The project's Domain Lead approves or rejects it under **Date Approvals** (a reason is needed to reject); only then does the
date change, and the mentor and student are notified. Mentors can withdraw a pending request; admins can decide as a fallback.
