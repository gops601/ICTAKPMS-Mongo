#!/bin/sh
set -e

prepare() {
  echo "Creating MongoDB collections and indexes..."
  flask init-db

  echo "Seeding reference data..."
  flask seed

  if [ -n "$ADMIN_EMAIL" ] && [ -n "$ADMIN_PASSWORD" ]; then
    flask create-admin
  fi

  echo "Syncing domain data..."
  flask sync-data || echo "sync-data reported a problem (continuing)"
}

# `entrypoint.sh prepare` runs the one-off database set-up and exits (the compose `prepare` service, run before a
# rolling update). Otherwise it is part of start-up unless RUN_PREPARE=0, which lets a new backend become healthy in seconds.
if [ "$1" = "prepare" ]; then
  prepare
  exit 0
fi
if [ "${RUN_PREPARE:-1}" != "0" ]; then
  prepare
fi

exec gunicorn --bind 0.0.0.0:5000 --workers 3 --timeout 120 --access-logfile - run:app
