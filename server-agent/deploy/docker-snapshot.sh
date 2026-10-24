#!/bin/sh
# Writes the container list for the SENTINEL agent (runs as root via a timer).
# The agent itself gets no Docker access, it only reads this file.
set -eu
OUT=/run/sentinel-agent/docker.json
command -v docker >/dev/null 2>&1 || exit 0
docker ps -a --no-trunc --format '{{json .}}' > "$OUT.tmp"
chgrp sentinel-agent "$OUT.tmp"
chmod 0640 "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
