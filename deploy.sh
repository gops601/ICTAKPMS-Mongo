#!/bin/sh
# Update the running app without taking it down.
#   1. build the new images (the old containers keep serving meanwhile)
#   2. run the one-off database set-up (indexes, seed, domain sync)
#   3. start the same number of NEW backend containers next to the old ones, wait until they are healthy,
#      then remove the old ones (nginx follows via Docker DNS, so requests never hit a stopped container)
#   4. refresh the frontend (a static site: the swap takes about a second)
# Usage: sh deploy.sh        (run from the project folder, after `git pull`)
set -e

N=${BACKEND_REPLICAS:-2}
echo "==> Building images"
docker compose build

echo "==> Database set-up"
docker compose run --rm --no-deps prepare

OLD=$(docker compose ps -q backend || true)
if [ -z "$OLD" ]; then
  echo "==> First start"
  docker compose up -d
  exit 0
fi

echo "==> Starting $N new backend container(s) next to the old ones"
# --no-recreate keeps the old containers as they are and adds new ones built from the new image
docker compose up -d --no-deps --no-recreate --scale backend=$((N + $(echo "$OLD" | wc -w))) backend
NEW=$(docker compose ps -q backend | grep -v -F "$OLD" || true)

echo "==> Waiting for the new backend to be healthy"
for i in $(seq 1 60); do
  bad=0
  for c in $NEW; do
    [ "$(docker inspect -f '{{.State.Health.Status}}' "$c")" = "healthy" ] || bad=1
  done
  [ "$bad" = 0 ] && break
  sleep 3
done
if [ "$bad" != 0 ]; then
  echo "New backend did not become healthy; keeping the old one running. Logs:"
  for c in $NEW; do docker logs --tail 40 "$c"; done
  for c in $NEW; do docker rm -f "$c" >/dev/null; done
  exit 1
fi

echo "==> Retiring the old backend container(s)"
for c in $OLD; do docker stop "$c" >/dev/null; docker rm "$c" >/dev/null; done
docker compose up -d --no-deps --no-recreate --scale backend=$N backend

echo "==> Frontend"
docker compose up -d --no-deps frontend
docker compose ps
