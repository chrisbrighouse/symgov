"""Which PostgreSQL image the disposable-database harnesses start.

Eight test modules start a throwaway PostgreSQL with `docker run`, and each
carried its own copy of the image tag. `postgres:16-alpine` remains the
default, so a machine that already has it behaves exactly as before.

`SYMGOV_TEST_POSTGRES_IMAGE` overrides it, for a machine where that tag is
absent and cannot be pulled -- on the 2026-09-23 development box Docker Hub
resolves the manifest and then stalls indefinitely at `Pulling fs layer`, for
a 3 MB `alpine:3.20` as readily as for Postgres, so it is blob egress rather
than image size. Without an override every `*_postgresql` module there fails
at its fixture with `subprocess.TimeoutExpired` on `docker run`, which reads
like a fixture defect and is not one.

The readiness window widens for any non-alpine image because the Debian
image's `initdb` is far slower; 60 seconds is comfortable for alpine and too
short for `postgres:16`.
"""
from __future__ import annotations

import os

DEFAULT_POSTGRES_IMAGE = "postgres:16-alpine"

POSTGRES_IMAGE = (
    os.environ.get("SYMGOV_TEST_POSTGRES_IMAGE", "").strip() or DEFAULT_POSTGRES_IMAGE
)

POSTGRES_READY_TIMEOUT = 60 if "alpine" in POSTGRES_IMAGE else 300

# Migrating a disposable database to head took 7m17s on `postgres:16` on
# 2026-09-23, against a 240-second subprocess timeout that every harness here
# had inherited from alpine. The floor below is applied with `max`, so a
# harness that already allowed longer keeps its own value.
ALEMBIC_TIMEOUT = 240 if "alpine" in POSTGRES_IMAGE else 1200
