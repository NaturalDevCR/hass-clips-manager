ARG BUILD_ARCH=amd64
FROM ghcr.io/home-assistant/${BUILD_ARCH}-base:3.20

RUN apk add --no-cache ffmpeg python3 py3-pip \
    && python3 -m pip install --no-cache-dir --break-system-packages \
        "fastapi==0.115.14" "uvicorn[standard]==0.32.1" "PyYAML==6.0.2" "pydantic==2.11.7"

COPY src /opt/cinema-collections-worker/src
COPY rootfs /

ENV PYTHONPATH=/opt/cinema-collections-worker/src
ENV CINEMA_COLLECTIONS_OPTIONS=/data/options.json

RUN chmod 0755 /usr/bin/cinema-collections-worker

# The Home Assistant base image owns process supervision and the permissions of
# Supervisor-mounted /data and /media. No host port is published by the App.
CMD ["/usr/bin/cinema-collections-worker"]
