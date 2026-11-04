# longhorn_exporter

Prometheus exporter for [Longhorn](https://longhorn.io) that reads the Longhorn REST API instead of the
built-in metrics endpoints.

**Why not the built-in endpoints?** Longhorn v1.12 serves its per-component metrics on IPv6-only sockets
(`engine-manager-metrics-*`, `backing-image-manager-metrics-*`, `longhorn-manager` on 8443). In an
IPv4-only cluster those targets are unreachable, and the `longhorn-manager` endpoint also requires
client certificates. This exporter polls the read-only REST API through the in-cluster
`longhorn-frontend` service and renders Prometheus text, so it works in any cluster and needs no
extra network access.

## Configuration

| Environment | Default | Purpose |
| --- | --- | --- |
| `LONGHORN_API` | `http://longhorn-frontend.longhorn-system.svc.cluster.local/v1` | Longhorn API base URL |
| `LONGHORN_TOKEN` | – | optional bearer token, sent as `Authorization: Bearer` |
| `PORT` | `9572` | port serving `/metrics` |
| `LONGHORN_DEBUG` | – | set to log each polled endpoint |

Every polled endpoint is reported as `longhorn_exporter_endpoint_up{endpoint="volumes|nodes|..."}` so a
dashboard can tell "Longhorn API degraded" from "volume healthy".

## Metrics

Volumes: `longhorn_volume_info`, `_size_bytes`, `_replicas`, `_unhealthy_replica_count`, `_ready`,
`_robustness`, `_condition{condition="..."}`, `_expanding`, `_purge_in_progress`, `_restoring`,
`_last_backup_timestamp_seconds`.

Nodes and disks: `longhorn_node_ready`, `_schedulable`, `_allow_scheduling`, `_eviction_requested`,
`_condition{condition="..."}`, `_storage_maximum_bytes`, `_storage_reserved_bytes`,
`_storage_available_bytes`, `_storage_scheduled_bytes`, `longhorn_node_disk_size_bytes`,
`_disk_ready`, `_disk_schedulable`, `_disk_storage_available_bytes`, `_disk_storage_reserved_bytes`,
`_disk_storage_scheduled_bytes`, `_disk_scheduled_replicas`.

Backups and system: `longhorn_backup_volume_available`, `_starting`, `_restoring`,
`longhorn_backup_target_info`, `longhorn_system_backup_info`, `longhorn_orphan_count`.

Exporter: `longhorn_exporter_endpoint_up`, `longhorn_exporter_scrape_duration_seconds`.

## Build

```
./build-tag-and-push.sh          # sets DOCKER_HOST if you build against a remote daemon
```

Produces `initialed85/longhorn-exporter:latest` (`linux/amd64`, `python:3.13-slim-trixie`, pinned
`requests` + `prometheus_client`). CI publishes the same image on every push to `main` via
`.woodpecker/build.yaml`, tagged with the commit sha as well as `latest`.

## Test

Two halves, because this exporter has bitten the fleet once already by shipping metrics that were
stale rather than wrong.

```sh
python3 -m unittest test_exporter   # fast, no docker, runs in CI
./acceptance-test.sh                # heavier, local only: real containers
```

`test_exporter.py` replaces `exporter.get` with a fake and reproduces the bug in-process: two
volumes, a scrape, then one deleted and one flipped `attached/healthy` -> `detached/unknown`. It
asserts the survivor exports exactly one `longhorn_volume_info` series and the deleted volume
exports none. Run it against the pre-fix `exporter.py` and four of the five tests fail — it is a
regression gate, not a description of current behaviour.

`acceptance-test.sh` builds this source into a container, runs it next to a pinned **pre-fix
registry digest** against a mock Longhorn API, and requires the old digest to leak (9 series for a
deleted volume) and the new build to clear (0). The old digest is pinned rather than `:latest`
because `:latest` is the thing that regressed: a test that flips its own expectation once a good
build lands is not a test. It cannot run in the Kubernetes backend — `docker run -p` publishes on
the daemon host, and the CI step would then curl its own `127.0.0.1` — which is why the CI gate is
the unit test.

## Deploy

```yaml
spec:
  template:
    spec:
      containers:
        - name: longhorn-exporter
          image: initialed85/longhorn-exporter:latest
          ports:
            - name: metrics
              containerPort: 9572
```

The image's `ENTRYPOINT` is `python /srv/exporter.py`; set `LONGHORN_API` if Longhorn is not in the
same cluster. The exporter only issues `GET` requests; it cannot mutate Longhorn state.

Both clusters scrape it: `clusters/dev/applications/observability/` (where the CI deploy step stamps
the commit-sha tag into `kustomization.yaml`) and `clusters/prod/applications/observability/`
(plain manifests, so prod tracks `:latest` and only picks a new build up when the pod restarts).

No license.
