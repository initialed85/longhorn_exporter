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

Produces `initialed85/longhorn-exporter:latest` (`linux/amd64`, Python 3.13 slim, `requests` +
`prometheus_client`).

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

The image's `CMD` is `python /srv/exporter.py`; set `LONGHORN_API` if Longhorn is not in the same
cluster. The exporter only issues `GET` requests; it cannot mutate Longhorn state.

No license.
