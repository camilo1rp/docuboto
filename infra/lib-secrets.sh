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
#
# lib-secrets.sh — El paquete de secretos sellado (ADR-053 §5).
#
# Se carga con `.` desde seal-secrets.sh, backup.sh y verify-restore-semanal.sh.
#
# Qué entra: el fichero de entorno (todas las KEK y contraseñas viven ahí) y
# `secrets/signing`, `secrets/tsa` y `secrets/ca-users-public`. Perder
# SIGNING_KEY_ENCRYPTION_KEY hace irrecuperables las claves privadas custodiadas;
# perder el sello de la entidad, que nada vuelva a firmarse con él.
#
# Con qué: `gpg --symmetric` (AES-256, S2K iterado con SHA-512), con una
# contraseña que el operador guarda FUERA del servidor. gpg ya es la herramienta
# de cifrado del respaldo (BACKUP_GPG_RECIPIENT) y, a diferencia de
# `openssl enc`, autentica el contenido (MDC): un paquete alterado no se abre.
# `--no-symkey-cache` impide que gpg-agent recuerde la contraseña en el servidor.
#
# Por qué no en cada respaldo: una contraseña que no está en el servidor no la
# puede teclear un cron. El operador sella al instalar y después de cada cambio de
# secretos; cada respaldo copia el último paquete y comprueba que NINGÚN secreto
# es más nuevo que él. Si lo es, el respaldo se completa pero sale con 6.

# Rutas de los directorios de secretos, relativas a la raíz del proyecto.
SECRETS_DIRS=(secrets/signing secrets/tsa secrets/ca-users-public)

_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi
}

# Las entradas que existen: el fichero de entorno y los directorios presentes.
# $1 = raíz del proyecto, $2 = fichero de entorno.
secrets_inputs() {
    printf '%s\n' "$2"
    local d
    for d in "${SECRETS_DIRS[@]}"; do
        [ -d "$1/$d" ] && printf '%s\n' "$1/$d"
    done
    return 0
}

# Sella el paquete. $1 raíz, $2 fichero de entorno, $3 paquete de salida,
# $4 fichero con la contraseña (puede ser /dev/fd/N). Escribe a un temporal y
# solo lo renombra si se vuelve a abrir con la misma contraseña y su contenido
# coincide con lo sellado.
seal_secrets_bundle() (
    raiz="$1" env="$2" salida="$3" clave="$4"
    [ -s "$env" ] || { echo "ERROR: el fichero de entorno '$env' no existe o está vacío." >&2; exit 1; }
    umask 077
    # Se lee una vez: $4 puede ser una tubería (/dev/fd/N) y aquí se usa dos veces.
    pass="$(cat "$clave")"
    etapa="$(mktemp -d)"
    # shellcheck disable=SC2064
    trap "rm -rf '$etapa'" EXIT
    mkdir -p "$etapa/bundle/infra" "$etapa/bundle/secrets"
    cp -p "$env" "$etapa/bundle/infra/.env"
    for d in "${SECRETS_DIRS[@]}"; do
        [ -d "$raiz/$d" ] && cp -Rp "$raiz/$d" "$etapa/bundle/secrets/"
    done
    (cd "$etapa/bundle" && find . -type f ! -name MANIFEST -print | LC_ALL=C sort | while IFS= read -r f; do _sha256 "$f"; done) \
        > "$etapa/bundle/MANIFEST"
    tar -C "$etapa/bundle" -cf - . \
      | gpg --batch --yes --quiet --no-symkey-cache --pinentry-mode loopback \
            --passphrase-file <(printf '%s' "$pass") --symmetric \
            --cipher-algo AES256 --s2k-mode 3 --s2k-digest-algo SHA512 --s2k-count 65011712 \
            --output "$etapa/bundle.tar.gpg" \
      || { echo "ERROR: gpg no pudo sellar el paquete." >&2; exit 1; }
    mkdir -p "$etapa/check"
    open_secrets_bundle "$etapa/bundle.tar.gpg" <(printf '%s' "$pass") "$etapa/check" \
      || { echo "ERROR: el paquete recién sellado no se abre." >&2; exit 1; }
    cmp -s "$etapa/bundle/MANIFEST" "$etapa/check/MANIFEST" \
      || { echo "ERROR: el paquete recién sellado no coincide con lo sellado." >&2; exit 1; }
    mkdir -p "$(dirname "$salida")"
    cp "$etapa/bundle.tar.gpg" "$salida.tmp" && chmod 600 "$salida.tmp" && mv "$salida.tmp" "$salida"
)

# Abre el paquete en $3 y comprueba cada fichero contra su MANIFEST.
# $1 paquete, $2 fichero con la contraseña.
open_secrets_bundle() (
    set -o pipefail
    paquete="$1" clave="$2" destino="$3"
    gpg --batch --quiet --no-symkey-cache --pinentry-mode loopback \
        --passphrase-file "$clave" --decrypt "$paquete" 2>/dev/null \
      | tar -C "$destino" -xf - \
      || { echo "ERROR: el paquete no se abre (¿contraseña equivocada o paquete alterado?)." >&2; exit 1; }
    [ -s "$destino/MANIFEST" ] && [ -s "$destino/infra/.env" ] \
      || { echo "ERROR: al paquete le falta MANIFEST o infra/.env." >&2; exit 1; }
    (cd "$destino" && _sha256 -c --quiet MANIFEST >/dev/null 2>&1) \
      || { echo "ERROR: un fichero del paquete no coincide con su MANIFEST." >&2; exit 1; }
)

# El paquete más reciente de $1 (vacío si no hay).
latest_secrets_bundle() {
    find "$1" -maxdepth 1 -type f -name 'secrets-*.tar.gpg' 2>/dev/null | LC_ALL=C sort | tail -1
}

# Estado del último paquete frente a lo vivo: imprime OK, AUSENTE o DESACTUALIZADO.
# $1 directorio de paquetes, $2 raíz, $3 fichero de entorno.
secrets_bundle_state() {
    local paquete nuevo
    paquete="$(latest_secrets_bundle "$1")"
    if [ -z "$paquete" ]; then echo AUSENTE; return 0; fi
    nuevo="$(secrets_inputs "$2" "$3" | while IFS= read -r e; do find "$e" -newer "$paquete" -print; done | head -1)"
    if [ -n "$nuevo" ]; then echo DESACTUALIZADO; else echo OK; fi
}

# Sin la contraseña solo se puede ver la envoltura: un paquete simétrico de gpg con
# AES-256 (cipher 9) y S2K iterado (s2k 3). Lo demás exige abrirlo.
secrets_bundle_looks_sealed() {
    gpg --batch --pinentry-mode cancel --list-packets "$1" 2>/dev/null \
      | grep -q '^:symkey enc packet: .*cipher 9.*s2k 3'
}

# Abre el paquete y lo compara con lo vivo. $1 paquete, $2 contraseña, $3 raíz,
# $4 fichero de entorno. Sale con 1 si no se abre o si algo difiere.
compare_secrets_bundle() (
    paquete="$1" clave="$2" raiz="$3" env="$4"
    umask 077
    abierto="$(mktemp -d)"
    # shellcheck disable=SC2064
    trap "rm -rf '$abierto'" EXIT
    open_secrets_bundle "$paquete" "$clave" "$abierto" || exit 1
    cmp -s "$abierto/infra/.env" "$env" \
      || { echo "ERROR: el .env del paquete no es el vivo." >&2; exit 1; }
    for d in "${SECRETS_DIRS[@]}"; do
        if [ -d "$raiz/$d" ] || [ -d "$abierto/$d" ]; then
            diff -rq "$abierto/$d" "$raiz/$d" >/dev/null 2>&1 \
              || { echo "ERROR: $d del paquete no coincide con el vivo." >&2; exit 1; }
        fi
    done
)
