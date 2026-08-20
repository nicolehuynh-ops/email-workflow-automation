#!/usr/bin/env sh
set -eu

# Wrapper for the persistent local container. Usage mirrors the Python CLI:
# ./scripts/local-container.sh run --campaign example-campaign --mode review --snapshot /app/fixtures/example_snapshot.json
exec docker compose run --rm outreach "$@"
