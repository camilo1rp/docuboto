#!/usr/bin/env bash
# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
#
# ci-local.sh — Corre en local los trabajos de .github/workflows/ci.yml, para
# cuando no hay minutos de Actions. Es un espejo, no la fuente: si cambia ci.yml,
# cambia esto.
#
# Uso:
#   ./scripts/ci-local.sh lint shared smoke        # los trabajos que digas
#   ./scripts/ci-local.sh all                      # todos
#   INTEG_ONLY="auth archive" ./scripts/ci-local.sh integration
#   UNIT_ONLY="storage" ./scripts/ci-local.sh unit
#
# Trabajos: lint shared smoke npm-audit unit frontend verapdf docs integration worm
#
# Requisitos: uv, Docker, y Node 22 para npm-audit y frontend (el npm 11 de
# Node 24 rechaza el package-lock.json; CI usa Node 22). `lint` corre dentro de
# python:3.11-slim porque scripts/lint.sh necesita bash 4 y macOS trae 3.2.
# `integration` levanta un PostgreSQL y un Redis desechables (scripts/test-app.sh
# recrea la base antes de cada dominio), y `worm` un RustFS; se borran al terminar.
#
# Resultado: un PASS/FAIL por trabajo en $OUT/results.txt y el log completo en
# $OUT/logs/<trabajo>.log. Sale con 1 si algún trabajo falló.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Fuera del repo: los guardianes de shared/tests recorren el árbol y tropezarían
# con los venvs.
OUT="${OUT:-${TMPDIR:-/tmp}/docuboto-ci-local}"
mkdir -p "$OUT/venvs" "$OUT/logs"
: > "$OUT/results.txt"
cd "$REPO"
FAILED=0

venv() {  # venv <name> <pip args...>
    local v="$OUT/venvs/$1"; shift
    [ -x "$v/bin/python" ] || uv venv -q -p 3.11 "$v"
    uv pip install -q -p "$v/bin/python" "$@" >/dev/null
    echo "$v/bin"
}
result() { echo "$1 $2" >> "$OUT/results.txt"; echo "== $1: $2"; [ "$2" = PASS ] || FAILED=1; }
run() {  # run <name> <cmd...>
    local name="$1"; shift
    if ( "$@" ) > "$OUT/logs/$name.log" 2>&1; then result "$name" PASS; else result "$name" FAIL; fi
}

job_lint() {  # el mismo gate que CI, en Linux
    docker run --rm -v "$REPO:/repo" -w /repo -e LINT_IN_PROCESS=1 \
        -e MYPY_CACHE_DIR=/tmp/mypy -e RUFF_CACHE_DIR=/tmp/ruff python:3.11-slim bash -c '
        pip install -q --root-user-action=ignore "ruff>=0.4.0" "mypy==1.11.0" ./shared \
            -r services/docuboto/requirements.lock && ./scripts/lint.sh'
}
job_shared() {
    local B; B=$(venv shared ./shared pytest pytest-asyncio pyyaml packaging cryptography fastapi httpx)
    cd shared && CI=true "$B/pytest" tests/ -q --tb=short -p no:cacheprovider
}
app_venv() { venv app ./shared -r services/docuboto/requirements.lock; }
job_smoke() {
    local B; B=$(app_venv)
    cd services/docuboto && "$B/pytest" -q --tb=short -p no:cacheprovider tests/docuboto tests/storage/test_purge_worm_gate.py
}
job_npm_audit() { cd frontend && npm ci --ignore-scripts && npm audit --omit=dev --audit-level=high; }
job_unit() {
    local B; B=$(app_venv)
    PATH="$B:$PATH" ./scripts/test-app.sh unit ${UNIT_ONLY:-}
}
job_frontend() { cd frontend && npm ci && npm run lint && npm run check && npm run test:unit && npm run build; }
job_verapdf() {
    local B; B=$(app_venv)
    chmod +x services/docuboto/tests/storage/fakes/fake_verapdf.py
    cd services/docuboto && VERAPDF_BINARY="$REPO/services/docuboto/tests/storage/fakes/fake_verapdf.py" \
        PDFA_VALIDATOR=verapdf PRESERVATION_PDFA_VALIDATION_ENABLED=true \
        "$B/pytest" tests/storage/test_subprocess_verapdf_ci_smoke.py -q --tb=short -p no:cacheprovider
}
job_docs() {
    local B; B=$(venv docs mkdocs mkdocs-material mkdocs-static-i18n)
    "$B/mkdocs" build --strict --site-dir "$OUT/site"
}
job_integration() {  # un PostgreSQL y un Redis desechables; test-app.sh recrea la base por dominio
    local B port=55500 rport=55600 name=cilocal-pg-app rname=cilocal-redis-app rc=0
    B=$(app_venv)
    docker rm -f "$name" "$rname" >/dev/null 2>&1
    docker run -d --rm --name "$name" -e POSTGRES_USER=orpycamcp -e POSTGRES_PASSWORD=orpycamcp \
        -e POSTGRES_DB=orpycamcp_db -p "127.0.0.1:$port:5432" postgres:17-bookworm >/dev/null
    docker run -d --rm --name "$rname" -p "127.0.0.1:$rport:6379" redis:7-alpine >/dev/null
    for _ in $(seq 1 60); do docker exec "$name" pg_isready -U orpycamcp -d orpycamcp_db -q && break; sleep 1; done
    sleep 2
    DATABASE_URL="postgresql+asyncpg://orpycamcp:orpycamcp@localhost:$port/orpycamcp_db" RUN_INTEGRATION=1 \
        REDIS_URL="redis://localhost:$rport/0" PATH="$B:$PATH" \
        ./scripts/test-app.sh integration ${INTEG_ONLY:-} || rc=$?
    docker rm -f "$name" "$rname" >/dev/null 2>&1
    return $rc
}
job_worm() {
    local B name=cilocal-rustfs rc=0
    B=$(app_venv)
    docker rm -f "$name" >/dev/null 2>&1
    docker run -d --rm --name "$name" -e RUSTFS_ACCESS_KEY=orpycamcp -e RUSTFS_SECRET_KEY=orpycamcp_dev \
        -e RUSTFS_VOLUMES=/data -p 127.0.0.1:19400:9000 \
        rustfs/rustfs:1.0.0@sha256:8cc9801755448b71a786705ce76692c77e14936cccd87cf2fc31842e58f4d1ff >/dev/null
    for _ in $(seq 1 30); do curl -sf http://localhost:19400/health >/dev/null && break; sleep 2; done
    (cd services/docuboto && RUN_INTEGRATION=1 RUN_S3_WORM=1 MINIO_ENDPOINT=localhost:19400 \
        MINIO_ACCESS_KEY=orpycamcp MINIO_SECRET_KEY=orpycamcp_dev \
        "$B/pytest" -q tests/storage/integration/test_worm_object_lock_real_s3.py --tb=short -p no:cacheprovider) || rc=$?
    docker rm -f "$name" >/dev/null 2>&1
    return $rc
}

[ $# -gt 0 ] || { sed -n '15,19p' "$0"; exit 2; }
[ "$1" = all ] && set -- lint shared smoke npm-audit unit frontend verapdf docs integration worm
for j in "$@"; do
    case "$j" in
        lint) run lint job_lint ;;
        shared) run test-unit-shared job_shared ;;
        smoke) run test-security-smoke job_smoke ;;
        npm-audit) run npm-audit job_npm_audit ;;
        unit) run test-unit job_unit ;;
        frontend) run test-frontend job_frontend ;;
        verapdf) run storage-verapdf-subprocess job_verapdf ;;
        docs) run docs-build job_docs ;;
        integration) run test-integration job_integration ;;
        worm) run storage-worm-real job_worm ;;
        *) echo "trabajo desconocido: $j" >&2; exit 2 ;;
    esac
done
exit $FAILED
