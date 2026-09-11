#!/bin/sh
# Non-destructive ownership initialization for bind mounts and named volumes.
set -eu
mkdir -p /app/data/admin
chown -R 10001:10001 /app/data
