#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
env_file="$root/.env"

if [ -e "$env_file" ]; then
  printf 'Refusing to overwrite existing POC env: %s\n' "$env_file" >&2
  exit 1
fi

for forbidden in POC_POSTGRES_USER POC_POSTGRES_PASSWORD POC_POSTGRES_DB POC_CLICKHOUSE_USER POC_CLICKHOUSE_PASSWORD POC_REDIS_AUTH POC_MINIO_ROOT_USER POC_MINIO_ROOT_PASSWORD POC_LANGFUSE_WEB_PORT POC_NEXTAUTH_SECRET POC_NEXTAUTH_URL POC_SALT POC_ENCRYPTION_KEY POC_TELEMETRY_ENABLED POC_LANGFUSE_INIT_ORG_ID POC_LANGFUSE_INIT_ORG_NAME POC_LANGFUSE_INIT_PROJECT_ID POC_LANGFUSE_INIT_PROJECT_NAME POC_LANGFUSE_INIT_PROJECT_PUBLIC_KEY POC_LANGFUSE_INIT_PROJECT_SECRET_KEY POC_LANGFUSE_INIT_USER_EMAIL POC_LANGFUSE_INIT_USER_NAME POC_LANGFUSE_INIT_USER_PASSWORD; do
  if [ -n "${!forbidden:-}" ]; then
    printf 'Refusing to create POC env while %s is exported. Unset it first.\n' "$forbidden" >&2
    exit 1
  fi
done

random_hex() { openssl rand -hex "$1"; }

umask 077
cat >"$env_file" <<EOF
COMPOSE_PROJECT_NAME=langfuse-poc
POC_LANGFUSE_WEB_PORT=13000
POC_POSTGRES_USER=langfuse_poc
POC_POSTGRES_PASSWORD=$(random_hex 24)
POC_POSTGRES_DB=langfuse_poc
POC_CLICKHOUSE_USER=langfuse_poc
POC_CLICKHOUSE_PASSWORD=$(random_hex 24)
POC_REDIS_AUTH=$(random_hex 24)
POC_MINIO_ROOT_USER=langfusepoc
POC_MINIO_ROOT_PASSWORD=$(random_hex 24)
POC_LANGFUSE_INIT_ORG_ID=langfuse-poc-org
POC_LANGFUSE_INIT_ORG_NAME=Symgov Synthetic POC
POC_LANGFUSE_INIT_PROJECT_ID=langfuse-poc-project
POC_LANGFUSE_INIT_PROJECT_NAME=Symgov Synthetic Telemetry POC
POC_LANGFUSE_INIT_PROJECT_PUBLIC_KEY=pk-lf-$(random_hex 16)
POC_LANGFUSE_INIT_PROJECT_SECRET_KEY=sk-lf-$(random_hex 24)
POC_LANGFUSE_INIT_USER_EMAIL=poc-operator@example.invalid
POC_LANGFUSE_INIT_USER_NAME=POC Operator
POC_LANGFUSE_INIT_USER_PASSWORD=$(random_hex 24)
POC_NEXTAUTH_SECRET=$(random_hex 32)
POC_ENCRYPTION_KEY=$(random_hex 32)
POC_SALT=$(random_hex 16)
POC_NEXTAUTH_URL=http://127.0.0.1:13000
POC_TELEMETRY_ENABLED=false
EOF
chmod 600 "$env_file"
printf 'Created POC-only env file with mode 600: %s\n' "$env_file"
