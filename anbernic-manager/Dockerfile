# Anbernic Manager
#
# Multi-stage build: compile Skyscraper (Gemba/skyscraper fork, the
# actively maintained continuation of muldjord/skyscraper -- also what
# nixpkgs packages) from source against Qt6, then assemble a slim runtime
# image with just the shared libraries it needs plus the Python app.
#
# Build steps here were verified against a real build (not copied from
# docs): older Qt6 builds of upstream muldjord/skyscraper fail to compile
# against current Qt6 (QPair/std::pair mismatch) -- the Gemba fork fixes
# this and is what actually gets used in practice today.

FROM ubuntu:24.04 AS build
ARG SKYSCRAPER_VERSION=3.20.4
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential git ca-certificates \
      qt6-base-dev qt6-base-dev-tools qmake6 libqt6sql6-sqlite qt6-svg-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch ${SKYSCRAPER_VERSION} \
      https://github.com/Gemba/skyscraper.git /tmp/skyscraper
WORKDIR /tmp/skyscraper
RUN qmake6 && make -j"$(nproc)" && make install


FROM ubuntu:24.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      libqt6core6t64 libqt6gui6t64 libqt6network6t64 libqt6sql6t64 \
      libqt6sql6-sqlite libqt6xml6t64 libqt6dbus6t64 qt6-qpa-plugins \
      cifs-utils p7zip-full \
      python3 python3-pip \
      ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /usr/local/bin/Skyscraper /usr/local/bin/Skyscraper
COPY --from=build /usr/local/etc/skyscraper /usr/local/etc/skyscraper

ENV QT_QPA_PLATFORM=offscreen \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY app/requirements.txt /app/requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /app/requirements.txt
COPY app /app

EXPOSE 8099
ENTRYPOINT ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8099"]
