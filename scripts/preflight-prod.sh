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
# preflight-prod.sh — la parte del checklist de seguridad del despliegue que NO se
# puede verificar en el repositorio (AREA-13-11): lo que depende del HOST. Lo que sí
# se puede (sin `ports:`, Redis con contraseña, credenciales obligatorias, imágenes
# fijadas) lo vigila `shared/tests/test_manifiesto_prod_seguro.py` en cada push.
#
# Uso, DESDE FUERA del servidor (otra máquina de la red o de Internet):
#     scripts/preflight-prod.sh sgd.entidad.gov.co
#
# Comprueba: (1) TLS válido y HTTP redirige a HTTPS; (2) los puertos internos
# (PostgreSQL 5432, Redis 6379, almacén de objetos 9000/9001, servicios 8001-8012)
# NO responden desde fuera. Solo LEE: no escribe nada ni modifica el host. El
# audit de PostgreSQL/Keycloak y la rotación de credenciales siguen siendo manuales
# (docs/es/deployment.md).
set -uo pipefail

HOST="${1:-}"
if [ -z "$HOST" ]; then
    echo "Uso: $0 <host-publico>" >&2
    exit 2
fi

FALLOS=0
fallo() { echo "  ✗ $1" >&2; FALLOS=$((FALLOS + 1)); }
bien()  { echo "  ✓ $1"; }

echo "── TLS"
if curl -sSf -o /dev/null --max-time 10 "https://$HOST/"; then
    bien "https://$HOST responde con un certificado válido"
else
    fallo "https://$HOST no responde o su certificado no valida"
fi
CODIGO="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://$HOST/" || true)"
case "$CODIGO" in
    301|302|307|308) bien "http://$HOST redirige a HTTPS ($CODIGO)" ;;
    000)             bien "http://$HOST no escucha (solo HTTPS)" ;;
    *)               fallo "http://$HOST responde $CODIGO en claro (debería redirigir)" ;;
esac

echo "── Puertos internos (no deben responder desde fuera)"
for PUERTO in 5432 6379 9000 9001 8001 8002 8003 8004 8005 8006 8007 8008 8009 8011 8012; do
    if (exec 3<>"/dev/tcp/$HOST/$PUERTO") 2>/dev/null; then
        fallo "el puerto $PUERTO está ABIERTO en $HOST"
    else
        bien "puerto $PUERTO cerrado"
    fi
done

if [ "$FALLOS" -gt 0 ]; then
    echo "" >&2
    echo "✗ $FALLOS comprobaciones fallaron: no dé el despliegue por seguro." >&2
    exit 1
fi
echo "✓ preflight del host superado"
