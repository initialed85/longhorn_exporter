"""Regression test for the stale-series fix.

prometheus_client keeps one child per label set for the life of the process. Before the
fix, a volume that left the API kept exporting its last value forever, and a volume whose
label values changed exported both the old label set and the new one. Only a pod restart
cleared them, and the fleet runs :latest and restarts rarely, so the dashboards quietly
filled with rows for volumes that no longer exist.

This is the scenario the fix was made for, exercised in-process: two volumes, a scrape,
then volume-b is deleted and volume-a flips attached/healthy -> detached/unknown.

It is deliberately a differential test in shape rather than in images: run it against the
pre-fix exporter.py and it fails on both assertions. That is what makes it a regression
test and not a description of the current code.

No cluster, no docker, no network: exporter.get is replaced with a fake, so this runs in
the CI test step, which has none of those.
"""

import unittest

import exporter
from prometheus_client import REGISTRY
from prometheus_client.exposition import generate_latest


def volume(name, state, robustness):
    """One Longhorn volume, shaped like the API returns it."""
    return {
        "name": name,
        "state": state,
        "robustness": robustness,
        "accessMode": "rwo",
        "image": "img",
        "size": "1073741824",
        "numberOfReplicas": 2,
        "unhealthyReplicaCount": 0,
        "conditions": {"Scheduled": {"status": "True", "type": "Scheduled"}},
        "purgeStatus": [],
        "backupStatus": [],
        "restore": [],
        "expand": {},
    }


class FakeAPI:
    """Stand-in for exporter.get: returns whatever the test currently wants served."""

    def __init__(self):
        self.data = {}

    def get(self, path):
        return self.data.get(path, [])

    def set_volumes(self, *volumes):
        self.data["volumes"] = list(volumes)


def scrape():
    """Run every collector once and render the registry, as a real scrape does.

    The collector is constructed but not registered on purpose: collect() mutates the
    gauges, then generate_latest renders their state. Registering it would make the
    registry call collect() a second time during rendering, and a test that scrapes twice
    would then depend on CollectorRegistry's collector ordering.
    """
    exporter.LonghornCollector().collect()
    return generate_latest(REGISTRY).decode()


def series(metrics, metric, needle):
    """Count the lines of `metric` whose labels contain `needle`."""
    return sum(
        1
        for line in metrics.splitlines()
        if line.startswith(metric + "{") and needle in line
    )


class StaleSeriesTest(unittest.TestCase):
    def setUp(self):
        # exporter.get is module-level and the collectors close over it by name, so
        # replacing it here is what the collectors actually call.
        self._real_get = exporter.get
        self.api = FakeAPI()
        exporter.get = self.api.get
        self.api.set_volumes(
            volume("vol-a", "attached", "healthy"),
            volume("vol-b", "attached", "healthy"),
        )

    def tearDown(self):
        # Clear the gauges with the fake still installed. Restoring the real get() first
        # would make this scrape reach for the in-cluster Longhorn API, which does not
        # resolve outside the cluster: the collector swallows the error, prints it, and
        # every test ends with five DNS retry timeouts in the log. Empty the fake instead
        # so the shared module-level gauges end up cleared and the next test starts clean.
        self.api.data.clear()
        scrape()
        exporter.get = self._real_get

    def test_both_volumes_export_before_the_change(self):
        """Guard the test itself: if the first scrape is empty the scenario is vacuous."""
        metrics = scrape()
        self.assertEqual(series(metrics, "longhorn_volume_ready", 'name="vol-b"'), 1)
        self.assertEqual(series(metrics, "longhorn_volume_ready", 'name="vol-a"'), 1)

    def test_deleted_volume_stops_exporting(self):
        scrape()
        self.api.set_volumes(volume("vol-a", "detached", "unknown"))
        metrics = scrape()

        stale = sum(
            1
            for line in metrics.splitlines()
            if 'name="vol-b"' in line and line.startswith("longhorn_volume_")
        )
        self.assertEqual(
            stale,
            0,
            "vol-b was deleted from the API but is still exporting "
            f"{stale} series; a dashboards row for a volume that no longer exists is "
            "exactly the bug this test exists for",
        )

    def test_changed_labels_replace_instead_of_accumulating(self):
        scrape()
        self.api.set_volumes(volume("vol-a", "detached", "unknown"))
        metrics = scrape()

        # volume_info carries state and robustness in its label set, so without a clear
        # the old (attached, healthy) child survives alongside the new one and the volume
        # appears twice in every grouped panel.
        count = series(metrics, "longhorn_volume_info", 'name="vol-a"')
        self.assertEqual(
            count,
            1,
            f"vol-a exports {count} longhorn_volume_info series; changing a label value "
            "must replace the series, not add a second one",
        )
        self.assertIn('longhorn_volume_info{access_mode="rwo"', metrics)
        self.assertIn('robustness="unknown"', metrics)
        self.assertNotIn('robustness="healthy"', metrics)

    def test_node_and_disk_gauges_clear_too(self):
        """The fix clears node/disk gauges as well; a removed disk must not linger.

        `disks` is a map keyed by disk name (nodes() iterates it with .items()), which is
        why this shape matters more than it looks: get().items() on a list is an
        AttributeError, and an exception here is silently caught by LonghornCollector.
        collect() prints the error and the scrape still returns 200 with stale rows.
        """
        def node(disks):
            return {
                "name": "node-1",
                "conditions": {"Ready": {"status": "True", "type": "Ready"}},
                "allowScheduling": True,
                "disks": disks,
            }

        def disk(allowed=True):
            return {
                "storageMaximum": 100,
                "storageAvailable": 60,
                "storageReserved": 10,
                "storageScheduled": 30,
                "allowScheduling": allowed,
                "conditions": {"Ready": {"status": "True", "type": "Ready"},
                               "Schedulable": {"status": "True", "type": "Schedulable"}},
                "scheduledReplica": {},
            }

        self.api.data["nodes"] = [node({"disk-a": disk(), "disk-b": disk()})]
        metrics = scrape()
        self.assertEqual(series(metrics, "longhorn_node_disk_size_bytes", 'disk="disk-b"'), 1)

        self.api.data["nodes"] = [node({"disk-a": disk()})]
        metrics = scrape()
        stale = series(metrics, "longhorn_node_disk_size_bytes", 'disk="disk-b"')
        self.assertEqual(stale, 0, f"disk-b was removed but still exports {stale} series")

        # A collector that raised would leave ENDPOINT_UP at 0 for that endpoint; asserting
        # 1 here proves the node path ran to completion on this data shape rather than
        # throwing into the except block.
        self.assertIn('longhorn_exporter_endpoint_up{endpoint="nodes"} 1.0', metrics)

    def test_empty_api_clears_everything(self):
        """A scrape where the API returns nothing must export no volume series at all."""
        scrape()
        self.api.set_volumes()
        metrics = scrape()
        self.assertEqual(series(metrics, "longhorn_volume_ready", "name="), 0)


if __name__ == "__main__":
    unittest.main()
