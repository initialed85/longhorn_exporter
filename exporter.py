import os
import time
import traceback
from datetime import datetime

import requests
from prometheus_client import REGISTRY, Gauge, start_http_server

API = os.environ.get("LONGHORN_API", "http://longhorn-frontend.longhorn-system.svc.cluster.local/v1")
TOKEN = os.environ.get("LONGHORN_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

V_INFO = Gauge("longhorn_volume_info", "Volume metadata", ["name", "state", "robustness", "access_mode", "image"], registry=REGISTRY)
V_SIZE = Gauge("longhorn_volume_size_bytes", "Volume size", ["name"], registry=REGISTRY)
V_ROBUSTNESS = Gauge("longhorn_volume_robustness", "1 healthy, 2 degraded, 3 error, 0 unknown", ["name"], registry=REGISTRY)
V_READY = Gauge("longhorn_volume_ready", "1 when attached and healthy", ["name"], registry=REGISTRY)
V_REPLICAS = Gauge("longhorn_volume_replicas", "Desired replica count", ["name"], registry=REGISTRY)
V_UNHEALTHY = Gauge("longhorn_volume_unhealthy_replica_count", "Unhealthy replicas", ["name"], registry=REGISTRY)
V_CONDITION = Gauge("longhorn_volume_condition", "Volume condition status", ["name", "condition", "status"], registry=REGISTRY)
V_PURGING = Gauge("longhorn_volume_purge_in_progress", "A replica purge is running", ["name"], registry=REGISTRY)
V_LAST_BACKUP = Gauge("longhorn_volume_last_backup_timestamp_seconds", "Most recent backup", ["name"], registry=REGISTRY)
V_RESTORING = Gauge("longhorn_volume_restoring", "Restore in progress", ["name", "state"], registry=REGISTRY)
V_EXPANDING = Gauge("longhorn_volume_expanding", "Expansion in progress", ["name"], registry=REGISTRY)

N_READY = Gauge("longhorn_node_ready", "Longhorn node ready", ["name"], registry=REGISTRY)
N_SCHEDULABLE = Gauge("longhorn_node_schedulable", "Longhorn node schedulable", ["name"], registry=REGISTRY)
N_ALLOW_SCHEDULING = Gauge("longhorn_node_allow_scheduling", "Longhorn node allows scheduling", ["name"], registry=REGISTRY)
N_EVICTING = Gauge("longhorn_node_eviction_requested", "Longhorn node eviction requested", ["name"], registry=REGISTRY)
N_CONDITION = Gauge("longhorn_node_condition", "Longhorn node condition status", ["name", "condition", "status"], registry=REGISTRY)
N_RESERVED = Gauge("longhorn_node_storage_reserved_bytes", "Storage reserved across disks", ["name"], registry=REGISTRY)
N_SCHEDULED = Gauge("longhorn_node_storage_scheduled_bytes", "Storage scheduled across disks", ["name"], registry=REGISTRY)
N_AVAILABLE = Gauge("longhorn_node_storage_available_bytes", "Storage available across disks", ["name"], registry=REGISTRY)
N_MAXIMUM = Gauge("longhorn_node_storage_maximum_bytes", "Storage capacity across disks", ["name"], registry=REGISTRY)
D_SIZE = Gauge("longhorn_node_disk_size_bytes", "Disk capacity", ["node", "disk"], registry=REGISTRY)
D_AVAILABLE = Gauge("longhorn_node_disk_storage_available_bytes", "Disk space available", ["node", "disk"], registry=REGISTRY)
D_RESERVED = Gauge("longhorn_node_disk_storage_reserved_bytes", "Disk space reserved", ["node", "disk"], registry=REGISTRY)
D_SCHEDULED = Gauge("longhorn_node_disk_storage_scheduled_bytes", "Disk space scheduled", ["node", "disk"], registry=REGISTRY)
D_SCHEDULABLE = Gauge("longhorn_node_disk_schedulable", "Disk schedulable and allowing scheduling", ["node", "disk"], registry=REGISTRY)
D_READY = Gauge("longhorn_node_disk_ready", "Disk ready", ["node", "disk"], registry=REGISTRY)
D_REPLICAS = Gauge("longhorn_node_disk_scheduled_replicas", "Replicas scheduled on disk", ["node", "disk"], registry=REGISTRY)

B_AVAILABLE = Gauge("longhorn_backup_volume_available", "Backup volume has backups", ["name", "last_backup"], registry=REGISTRY)
B_STARTING = Gauge("longhorn_backup_volume_starting", "Backup starting", ["name"], registry=REGISTRY)
B_RESTORING = Gauge("longhorn_backup_volume_restoring", "Restore running", ["name"], registry=REGISTRY)
BT_INFO = Gauge("longhorn_backup_target_info", "Backup target", ["name", "url", "status"], registry=REGISTRY)
SB_INFO = Gauge("longhorn_system_backup_info", "System backup", ["name", "state"], registry=REGISTRY)
ORPHAN_COUNT = Gauge("longhorn_orphan_count", "Orphaned files", ["node", "type"], registry=REGISTRY)

ENDPOINT_UP = Gauge("longhorn_exporter_endpoint_up", "Longhorn API endpoint reachable", ["endpoint"], registry=REGISTRY)
SCRAPE_SECONDS = Gauge("longhorn_exporter_scrape_duration_seconds", "API fetch time", registry=REGISTRY)

ROBUSTNESS = {"healthy": 1, "degraded": 2, "error": 3}


def get(path):
    r = requests.get(f"{API}/{path}", headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json().get("data", [])


def conditions(obj):
    raw = obj.get("conditions") or {}
    if isinstance(raw, dict):
        return {k: v.get("status", "") for k, v in raw.items()}
    return {c.get("type"): c.get("status") for c in raw}


def clear(*gauges):
    """Drop every label set of these gauges before the scrape repopulates them.

    prometheus_client keeps a child per label set for the life of the process, so a
    volume that leaves the API (deleted, restored then renamed) or changes a label value
    (state attached -> detached) keeps exporting its last value forever. Restarting the
    pod was the only way to clear those rows; the fleet runs on :latest and restarts
    rarely, so each collector clears its own gauges first.
    """
    for gauge in gauges:
        gauge.clear()


def volumes():
    clear(V_INFO, V_SIZE, V_ROBUSTNESS, V_READY, V_REPLICAS, V_UNHEALTHY, V_CONDITION,
          V_PURGING, V_LAST_BACKUP, V_RESTORING, V_EXPANDING)
    for v in get("volumes"):
        name = v["name"]
        robustness = v.get("robustness", "")
        V_INFO.labels(name, v.get("state", ""), robustness, v.get("accessMode", ""), v.get("image", "")).set(1)
        V_SIZE.labels(name).set(int(v.get("size", 0)))
        V_ROBUSTNESS.labels(name).set(ROBUSTNESS.get(robustness, 0))
        V_READY.labels(name).set(1 if v.get("state") == "attached" and robustness == "healthy" else 0)
        V_REPLICAS.labels(name).set(int(v.get("numberOfReplicas", 0)))
        V_UNHEALTHY.labels(name).set(int(v.get("unhealthyReplicaCount", 0)))
        for cond, status in conditions(v).items():
            V_CONDITION.labels(name, cond, status).set(1)
        V_PURGING.labels(name).set(1 if any(p.get("isPurging") for p in (v.get("purgeStatus") or [])) else 0)
        stamps = [b.get("completedAt", "") for b in (v.get("backupStatus") or []) if b.get("completedAt")]
        if stamps:
            last = max(stamps)
            V_LAST_BACKUP.labels(name).set(datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp())
        for r in (v.get("restore") or []):
            V_RESTORING.labels(name, r.get("state", "")).set(1)
        V_EXPANDING.labels(name).set(1 if (v.get("expand") or {}).get("isExpanding") else 0)


def nodes():
    clear(N_READY, N_SCHEDULABLE, N_ALLOW_SCHEDULING, N_EVICTING, N_CONDITION,
          N_RESERVED, N_SCHEDULED, N_AVAILABLE, N_MAXIMUM,
          D_SIZE, D_AVAILABLE, D_RESERVED, D_SCHEDULED, D_SCHEDULABLE, D_READY, D_REPLICAS)
    for n in get("nodes"):
        name = n["name"]
        conds = conditions(n)
        N_READY.labels(name).set(1 if conds.get("Ready") == "True" else 0)
        N_SCHEDULABLE.labels(name).set(1 if conds.get("Schedulable") == "True" else 0)
        N_ALLOW_SCHEDULING.labels(name).set(1 if n.get("allowScheduling") else 0)
        N_EVICTING.labels(name).set(1 if n.get("evictionRequested") else 0)
        for cond, status in conds.items():
            N_CONDITION.labels(name, cond, status).set(1)
        reserved = scheduled = available = maximum = 0
        for disk, d in (n.get("disks") or {}).items():
            reserved += int(d.get("storageReserved", 0))
            scheduled += int(d.get("storageScheduled", 0))
            available += int(d.get("storageAvailable", 0))
            maximum += int(d.get("storageMaximum", 0))
            dconds = conditions(d)
            D_SIZE.labels(name, disk).set(int(d.get("storageMaximum", 0)))
            D_AVAILABLE.labels(name, disk).set(int(d.get("storageAvailable", 0)))
            D_RESERVED.labels(name, disk).set(int(d.get("storageReserved", 0)))
            D_SCHEDULED.labels(name, disk).set(int(d.get("storageScheduled", 0)))
            D_SCHEDULABLE.labels(name, disk).set(1 if dconds.get("Schedulable") == "True" and d.get("allowScheduling") else 0)
            D_READY.labels(name, disk).set(1 if dconds.get("Ready") == "True" else 0)
            D_REPLICAS.labels(name, disk).set(len(d.get("scheduledReplica") or {}))
        N_RESERVED.labels(name).set(reserved)
        N_SCHEDULED.labels(name).set(scheduled)
        N_AVAILABLE.labels(name).set(available)
        N_MAXIMUM.labels(name).set(maximum)


def backupvolumes():
    clear(B_AVAILABLE, B_STARTING, B_RESTORING)
    for b in get("backupvolumes"):
        last = max((e.get("name", "") for e in (b.get("lastBackup") or [])), default="")
        B_AVAILABLE.labels(b["name"], last).set(1 if b.get("available") else 0)
        B_STARTING.labels(b["name"]).set(1 if b.get("starting") else 0)
        B_RESTORING.labels(b["name"]).set(1 if b.get("restoring") else 0)


def backuptargets():
    clear(BT_INFO)
    for t in get("backuptargets"):
        BT_INFO.labels(t["name"], t.get("url", ""), t.get("status", "")).set(1)


def systembackups():
    clear(SB_INFO)
    for s in get("systembackups"):
        SB_INFO.labels(s["name"], s.get("state", "")).set(1)


def orphans():
    clear(ORPHAN_COUNT)
    counts = {}
    for o in get("orphans"):
        counts[(o.get("nodeID", ""), o.get("type", ""))] = counts.get((o.get("nodeID", ""), o.get("type", "")), 0) + 1
    for (node, typ), count in counts.items():
        ORPHAN_COUNT.labels(node, typ).set(count)


COLLECTORS = {
    "volumes": volumes,
    "nodes": nodes,
    "backupvolumes": backupvolumes,
    "backuptargets": backuptargets,
    "systembackups": systembackups,
    "orphans": orphans,
}


class LonghornCollector:
    def collect(self):
        start = time.time()
        for endpoint, fn in COLLECTORS.items():
            try:
                fn()
                ENDPOINT_UP.labels(endpoint).set(1)
            except Exception as exc:
                print(f"longhorn {endpoint} error: {exc}")
                ENDPOINT_UP.labels(endpoint).set(0)
                if os.environ.get("LONGHORN_DEBUG"):
                    traceback.print_exc()
        SCRAPE_SECONDS.set(time.time() - start)
        return []


if __name__ == "__main__":
    REGISTRY.register(LonghornCollector())
    port = int(os.environ.get("PORT", "9572"))
    httpd, _ = start_http_server(port)
    print(f"longhorn-exporter :{port} api={API}")
    httpd.serve_forever()

