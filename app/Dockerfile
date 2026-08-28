ARG BUILD_ARCH=amd64
FROM ghcr.io/home-assistant/${BUILD_ARCH}-base:3.21

RUN apk add --no-cache ffmpeg python3 py3-pip \
    && python3 -m pip install --no-cache-dir --break-system-packages \
        "fastapi==0.115.14" "uvicorn[standard]==0.32.1" "PyYAML==6.0.2" "pydantic==2.11.7"

COPY src /opt/cinema-collections-worker/src
COPY rootfs /

ENV PYTHONPATH=/opt/cinema-collections-worker/src
ENV CINEMA_COLLECTIONS_OPTIONS=/data/options.yaml

RUN chmod 0755 /usr/bin/cinema-collections-worker

# Home Assistant's base image provides the unprivileged abc account.  The
# Supervisor owns mounting /data and /media; no host port is published.
USER abc
CMD ["/usr/bin/cinema-collections-worker"]
