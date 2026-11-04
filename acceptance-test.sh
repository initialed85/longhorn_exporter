#!/usr/bin/env bash
# Acceptance test: does the built image actually stop exporting series for things that no
# longer exist, in a real container, against a mock Longhorn API?
#
# This is the heavier half of the two-half check. `test_exporter.py` is the fast half and
# runs in CI; this one proves the container itself -- entrypoint, env, the real HTTP scrape
# path -- and it is differential by construction: it runs a PINNED pre-fix registry digest
# alongside the local build and requires the old one to leak. If the pre-fix image ever
# passes, the test is no longer testing anything and says so.
#
# The digest is pinned, not `:latest`. `:latest` is the thing that regressed; using it as
# the "known bad" control would make this test flip its own expectation the moment a good
# build lands, and a test that changes what it asserts depending on when you run it is not
# a test. Pin the bad one forever, rebuild the good one from source.
#
# Run this locally before pushing. It cannot run as written in the Kubernetes backend:
# `docker run -p` publishes the port on the DAEMON host (docker-beefcake-ci-1), and the CI
# step then curls 127.0.0.1 on itself, so it would time out and look like an exporter bug.
# The CI gate is test_exporter.py, which needs no docker at all.
set -uo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE=initialed85/longhorn-exporter
OLD=$IMAGE@sha256:f53801735e6b86241fc0398bd127b54db8e92a66f49f1728ebf22546c183407a
TMP=$(mktemp -d)
trap 'docker rm -f lx-old lx-new >/dev/null 2>&1; rm -rf "$TMP"' EXIT

# Mock Longhorn API, served inside the same container as the exporter so the exporter's
# localhost is the mock's localhost and no host port mapping is involved in that hop.
cat >"$TMP/mock.py" <<'PY'
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

STATE = "/state.json"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        with open(STATE) as fh:
            state = json.load(fh)
        body = json.dumps({"data": state.get(self.path.rsplit("/", 1)[-1], [])}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


HTTPServer(("0.0.0.0", 8099), Handler).serve_forever()
PY

volume() { # name state robustness
  printf '{"name":"%s","state":"%s","robustness":"%s","accessMode":"rwo","image":"img","size":"1073741824","numberOfReplicas":2,"unhealthyReplicaCount":0,"conditions":{"Scheduled":{"status":"True","type":"Scheduled"}},"purgeStatus":[],"backupStatus":[],"restore":[],"expand":{}}' "$1" "$2" "$3"
}
printf '{"volumes":[%s,%s],"nodes":[],"backupvolumes":[],"backuptargets":[],"systembackups":[],"orphans":[]}' \
  "$(volume vol-a attached healthy)" "$(volume vol-b attached healthy)" >"$TMP/state-1.json"
printf '{"volumes":[%s],"nodes":[],"backupvolumes":[],"backuptargets":[],"systembackups":[],"orphans":[]}' \
  "$(volume vol-a detached unknown)" >"$TMP/state-2.json"

run_case() { # container image  -> metrics of scrape 2 on stdout
  local name=$1 image=$2 port
  docker rm -f "$name" >/dev/null 2>&1
  cp "$TMP/state-1.json" "$TMP/state.json"
  port=$((19500 + RANDOM % 400))
  docker run -d --name "$name" -p 127.0.0.1:$port:9572 \
    -v "$TMP/mock.py:/srv/mock.py:ro" -v "$TMP/state.json:/state.json:ro" \
    -e PORT=9572 -e LONGHORN_API=http://127.0.0.1:8099/v1 --entrypoint sh "$image" \
    -c 'python /srv/mock.py & exec python /srv/exporter.py' >/dev/null || return 1
  for _ in $(seq 30); do
    sleep 1
    curl -sf "127.0.0.1:$port/metrics" | grep -q '^longhorn_volume_ready{name="vol-b"' && break
  done
  # Overwrite in place: the single-file bind mount follows the inode, so the mock
  # republishes the new volume set on the next scrape.
  cp "$TMP/state-2.json" "$TMP/state.json"
  for _ in $(seq 10); do
    sleep 1
    curl -s "127.0.0.1:$port/metrics" | grep -q 'name="vol-a",robustness="unknown"' && break
  done
  curl -s "127.0.0.1:$port/metrics"
}

report() { # label metrics
  local label=$1 metrics=$2
  local volb vola
  volb=$(grep -c '^longhorn_volume_[a-z_]*{[^}]*name="vol-b"' <<<"$metrics")
  vola=$(grep -c '^longhorn_volume_info{[^}]*name="vol-a"' <<<"$metrics")
  printf '%s: vol-b series after deletion=%s (want 0), vol-a volume_info series=%s (want 1)\n' \
    "$label" "$volb" "$vola"
  [ "$volb" = 0 ] && [ "$vola" = 1 ]
}

echo "== build $SRC -> $IMAGE:test"
# The Dockerfile is under docker/ here, so build needs -f. Without this the build looks for
# $SRC/Dockerfile, does not find one, and the failure reads like a broken exporter.
DOCKERFILE="$SRC/docker/Dockerfile"
[ -f "$DOCKERFILE" ] || DOCKERFILE="$SRC/Dockerfile"
docker build -q -f "$DOCKERFILE" -t "$IMAGE:test" "$SRC" >/dev/null || { echo BUILD FAILED; exit 1; }

old=$(run_case lx-old "$OLD"); report old "$old"; old_ok=$?
new=$(run_case lx-new "$IMAGE:test"); report new "$new"; new_ok=$?

echo
if [ "$old_ok" = 0 ]; then echo "UNEXPECTED: pre-fix digest already cleared stale series; this test is no longer testing anything"; exit 1; fi
if [ "$new_ok" != 0 ]; then echo "FAIL: rebuilt image still exports stale series"; exit 1; fi
echo "PASS: pre-fix digest leaks, rebuilt image clears"
