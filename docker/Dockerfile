FROM python:3.13-slim

RUN pip install --no-cache-dir --root-user-action=ignore requests prometheus_client

COPY exporter.py /srv/exporter.py

ENV PORT=9572
EXPOSE 9572

CMD ["python", "/srv/exporter.py"]
