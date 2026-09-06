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
# ═══════════════════════════════════════════════════════════════════════════
# Entorno de pruebas completo, de cero, en un comando.
# ═══════════════════════════════════════════════════════════════════════════
#
# ## Por qué RECREAR y no restaurar un snapshot
#
# Un `pg_dump` sería más rápido. No sirve, por tres razones y la primera manda:
#
#   1. `public.audit_log` es **append-only por Ley 594** y ni el `--purge` del
#      seed lo toca. La única forma de dejarlo limpio es recrear la base. Un
#      snapshot congelaría la cadena tal y como estuviera — y en esta
#      instalación estaba contaminada con 44.408 filas de 7.910 tenants de test
#      que el tier de integración había ido dejando y que NADIE puede borrar.
#   2. Sembrar por la API hace que cada documento **nazca con su asiento y su
#      índice**. Un snapshot puede contener estados que el sistema ya no sabe
#      producir, y entonces se prueba contra una ficción.
#   3. Un snapshot caduca contra las migraciones. Resembrar siempre casa con el
#      código que hay delante.
#
# El precio es el tiempo. Se paga a propósito.
#
# ## Qué DESTRUYE
#
# `docker compose down -v` borra los volúmenes: Postgres (incluido el schema
# `keycloak`, que se reimporta solo desde `realm-dev.json`), MinIO y Redis.
# Todo lo que hubiera en la instalación se pierde. Por eso pide confirmación
# tecleada y no acepta un simple «sí»: la frase nombra el acto.
#
# ## Uso
#
#     ./scripts/reset-entorno.sh                # tamaño por defecto
#     ./scripts/reset-entorno.sh --radicados 400 --expedientes 30
#     ./scripts/reset-entorno.sh --si           # sin preguntar (CI)
#
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

GATEWAY="http://localhost:19080"
RADICADOS=180
EXPEDIENTES=12
TERCEROS=40
CONFIRMADO=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --radicados)   RADICADOS="$2"; shift 2 ;;
    --expedientes) EXPEDIENTES="$2"; shift 2 ;;
    --terceros)    TERCEROS="$2"; shift 2 ;;
    --gateway)     GATEWAY="$2"; shift 2 ;;
    --si)          CONFIRMADO=1; shift ;;
    -h|--help)     sed -n '11,45p' "$0"; exit 0 ;;
    *) echo "Opción desconocida: $1" >&2; exit 2 ;;
  esac
done

azul()  { printf '\n\033[1;34m▶ %s\033[0m\n' "$1"; }
verde() { printf '\033[0;32m   ✓ %s\033[0m\n' "$1"; }
rojo()  { printf '\033[0;31m✗ %s\033[0m\n' "$1" >&2; }

# ── Confirmación ───────────────────────────────────────────────────────────
# La frase nombra el acto y el objeto. Un «sí» genérico confirma «una acción»,
# no ESTA acción: quien lo teclea de memoria en el terminal equivocado no se
# entera de que está borrando la base de otro sitio.
if [[ "$CONFIRMADO" -ne 1 ]]; then
  cat <<'AVISO'

  ┌──────────────────────────────────────────────────────────────────────┐
  │  Esto DESTRUYE los volúmenes de Postgres, MinIO y Redis.             │
  │  Se pierde todo: documentos, expedientes, usuarios y el audit_log.   │
  │  Es irreversible y no hay copia.                                     │
  └──────────────────────────────────────────────────────────────────────┘

AVISO
  read -r -p '  Escribe "borrar el entorno" para continuar: ' RESPUESTA
  if [[ "$RESPUESTA" != "borrar el entorno" ]]; then
    rojo "Cancelado (no se ha tocado nada)."
    exit 1
  fi
fi

INICIO=$(date +%s)

azul "1/8 · Derribando la pila y sus volúmenes"
docker compose --profile dev --profile assistant down -v --remove-orphans
verde "volúmenes eliminados"

azul "2/8 · Secretos y material criptográfico de desarrollo"
# Idempotente: respeta lo que ya esté en .env y los pares ya generados.
./scripts/dev-secrets.sh >/dev/null

# El sello institucional y la TSA. Sin ellos el cierre de expediente deja el
# índice en `pendiente_firma` para siempre — un camino muerto que se lee como
# avería cuando en realidad es material que falta.
[[ -d secrets/signing/_dev ]] || ./scripts/gen-dev-signing-cert.sh >/dev/null
[[ -d secrets/tsa/_dev ]]     || ./scripts/gen-dev-tsa-cert.sh >/dev/null

# ANTES de `up`, y esto no es cosmético: `docker-compose.yml` monta
# `./secrets/ca-users-public` en auth-service. Si el directorio no existe cuando
# Docker crea el bind, lo crea él **como root** —Docker corre como UID 0 y un
# bind-mount no traduce UIDs— y entonces `gen-user-cert.sh`, que corre en el
# HOST, no puede escribir el espejo de la sub-CA. El fallo llegaría en el paso 7
# y parecería un problema de la CA. Es exactamente la trampa que documenta el
# CLAUDE.md, y la única defensa es crear el directorio antes.
mkdir -p secrets/ca-users-public
verde "secretos, sello y TSA listos; espejo de la sub-CA preparado"

azul "3/8 · Levantando la pila (perfiles dev + assistant)"
docker compose --profile dev --profile assistant up -d --build
docker build -q -t orpycamcp-scripts:dev -f scripts/Dockerfile scripts/ >/dev/null
verde "contenedores arriba e imagen de guiones construida"

# ── El nombre de la red ────────────────────────────────────────────────────
# Se DERIVA de la que compose acaba de crear, no se escribe a mano: depende del
# nombre del proyecto (el del directorio, o COMPOSE_PROJECT_NAME), así que un
# clon en otra carpeta tendría otro y esto fallaría con un «network not found»
# que no explica nada.
RED="$(docker network ls --format '{{.Name}}' | grep -m1 'orpycamcp-net$' || true)"
if [[ -z "$RED" ]]; then
  rojo "No encuentro la red de la pila (…orpycamcp-net). ¿Levantó bien el paso 3?"
  exit 1
fi

azul "4/8 · Esperando a que responda lo que la siembra necesita"
# Esperar al gateway NO basta, y esto costó un reset entero: su `/health` no
# depende de Keycloak, así que contestaba a los 10 s mientras Keycloak seguía
# importando el realm sobre una base recién creada. La siembra arrancaba y moría
# con un `ConnectError` que no nombra a nadie.
#
# Dos correcciones, y la segunda es la de fondo:
#   · se comprueba lo que el seed usa PRIMERO —el realm de Keycloak— además del
#     gateway y de auth-service;
#   · se comprueba DESDE DENTRO de la red, por los mismos nombres y puertos que
#     usará la siembra. Una comprobación de disponibilidad que recorre otra ruta
#     que aquello a lo que da paso no prueba nada: desde el host, por el puerto
#     publicado, es otro camino.
espera_dependencias() {
  docker run --rm --network "$1" orpycamcp-scripts:dev -c '
import sys, time, httpx
urls = [
    "http://keycloak:8080/realms/orpycamcp/.well-known/openid-configuration",
    "http://auth-service:8001/health",
    "http://api-gateway:8080/health",
]
limite = time.time() + 600
pendientes = list(urls)
while pendientes and time.time() < limite:
    for u in list(pendientes):
        try:
            if httpx.get(u, timeout=5.0).status_code == 200:
                print(f"   listo: {u}")
                pendientes.remove(u)
        except Exception:
            pass
    if pendientes:
        time.sleep(5)
if pendientes:
    print("NO responden a tiempo: " + ", ".join(pendientes), file=sys.stderr)
    sys.exit(1)
'
}

espera_dependencias "$RED" || { rojo "Las dependencias no respondieron. Mira: docker compose logs keycloak"; exit 1; }
verde "gateway, auth y el realm de Keycloak responden"

# Los guiones corren DENTRO de la red: sus valores por defecto ya son nombres
# internos (`postgres:5432`, `keycloak:8080`) porque hablan con Postgres por
# asyncpg y con Keycloak por su API de administración. El repo se monta `:ro`;
# `secrets/perfiles-csr` es lo ÚNICO con escritura, con `--user` para que lo que
# salga sea del usuario y no de root.
GW_INTERNO="http://api-gateway:8080"
CSR_DIR="$RAIZ/secrets/perfiles-csr"
mkdir -p "$CSR_DIR"

guion() {
  docker run --rm \
    --network "$RED" \
    --user "$(id -u):$(id -g)" \
    -v "$RAIZ:/repo:ro" \
    -v "$CSR_DIR:/repo/secrets/perfiles-csr:rw" \
    -w /repo \
    orpycamcp-scripts:dev "$@"
}

azul "5/8 · Creando el tenant y aplicando sus migraciones"
# Sobre un volumen nuevo NO existe `tenant_demo`, y `seed_demo.py` da por hecho
# que sí —su propio mensaje de error lo dice—. Sin este paso la siembra muere
# con un 500 al pedir el primer token, después de haber creado el usuario en
# Keycloak: un estado a medias que se lee como «Keycloak está mal».
#
# Corre desde el HOST porque así está escrito `init-tenant.sh`, que orquesta
# `docker compose exec` sobre los servicios.
./scripts/init-tenant.sh demo "Demo" DEMO >/dev/null
verde "tenant «demo» inicializado con todas las migraciones"

azul "6/8 · Tenant y recorrido base (seed_demo)"
guion scripts/seed_demo.py --gateway "$GW_INTERNO"

azul "7/8 · Volumen y variedad (seed_corpus)"
guion scripts/seed_corpus.py --gateway "$GW_INTERNO" \
  --radicados "$RADICADOS" --expedientes "$EXPEDIENTES" --terceros "$TERCEROS"

azul "8/8 · Segundo factor y credenciales de firma por perfil"
# Fase 1 (contenedor): TOTP de todos y CSR de quien lleva credencial.
# `tee /dev/tty` NO: en CI (`--si`) o bajo `nohup` no hay terminal y el paso
# entero se cae justo al final, después de haber reconstruido todo. Se usa un
# fichero temporal, que funciona igual con y sin terminal.
SALIDA_FASE1="$(mktemp)"
trap 'rm -f "$SALIDA_FASE1"' EXIT
guion scripts/perfiles_prueba.py --enrolar \
  --gateway "$GW_INTERNO" --auth "http://auth-service:8001" \
  --csr-dir /repo/secrets/perfiles-csr | tee "$SALIDA_FASE1"
SECRETOS="$(sed -n 's/^SECRETOS_TOTP=//p' "$SALIDA_FASE1" | tail -1)"

# Firma en el HOST. Esta es la frontera que `gen-user-cert.sh` declara y que no
# se cruza: la clave de la sub-CA de usuarios no entra en ningún contenedor.
# Lo que cruzó fue un CSR, que es público.
for CSR in "$CSR_DIR"/*.csr; do
  [[ -e "$CSR" ]] || continue
  azul "     firmando $(basename "$CSR") con la sub-CA de usuarios (host)"
  ./scripts/gen-user-cert.sh "$CSR" >/dev/null
  verde "$(basename "${CSR%.csr}.crt") emitido"
done

# Fase 2 (contenedor): cargar los certificados y dejar a `archivista` revocado.
guion scripts/perfiles_prueba.py --activar \
  --gateway "$GW_INTERNO" --auth "http://auth-service:8001" \
  --csr-dir /repo/secrets/perfiles-csr --secretos "$SECRETOS"

# Los CSR y los .crt ya cumplieron: el certificado vive en auth-service y la
# privada nunca salió de ahí. Dejarlos sería material criptográfico olvidado en
# el árbol, que es como se acaba reutilizando uno viejo sin saberlo.
rm -f "$CSR_DIR"/*.csr "$CSR_DIR"/*.crt "$CSR_DIR"/*.user_id

FIN=$(date +%s)
printf '\n\033[1;32m══ Entorno reconstruido en %d min %02d s ══\033[0m\n' \
  $(( (FIN-INICIO) / 60 )) $(( (FIN-INICIO) % 60 ))
cat <<FIN

  Interfaz     http://localhost:19300
  MailHog      http://localhost:8025      (los OTP por correo llegan aquí)
  MinIO        http://localhost:19901
  Keycloak     http://localhost:19180/admin

  Los perfiles y sus secretos TOTP están en la tarjeta de arriba.
  El recorrido funcional completo: docs/es/plan-pruebas.md

FIN
