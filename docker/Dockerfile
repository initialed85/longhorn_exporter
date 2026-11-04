# Source of docker.io/initialed85/longhorn-exporter, the adapter both clusters scrape
# (clusters/{dev,prod}/applications/observability/longhorn-exporter.yaml). Longhorn v1.12
# serves metrics on IPv6-only sockets, unreachable in these IPv4-only clusters, so this
# reads the Longhorn REST API through the in-cluster longhorn-frontend proxy.
#
# Deps are pinned deliberately. The deployments run `:latest` with imagePullPolicy: Always
# and only rebuild when this repo changes, so an unpinned `pip install` means an image
# rebuilt next month is not the image that passed today's tests. Pin here, bump here.
#
# `slim-trixie` rather than `slim`: `slim` floats with Debian's current stable, so the same
# tag silently changes distro underneath a rebuild.
FROM python:3.13-slim-trixie

RUN pip install --no-cache-dir --root-user-action=ignore prometheus_client==0.26.0 requests==2.34.2

COPY exporter.py /srv/exporter.py

ENV PORT=9572
EXPOSE 9572

# ENTRYPOINT, not CMD: the deployments run runAsNonRoot (65534) with
# readOnlyRootFilesystem: true and drop all capabilities, so this container has exactly one
# job and nothing to write to. A CMD can be dropped by a `docker run image some-args` and
# then the exporter silently does not start.
ENTRYPOINT ["python", "/srv/exporter.py"]
