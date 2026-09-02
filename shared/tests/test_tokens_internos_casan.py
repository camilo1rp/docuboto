# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Guardián: el token que un servicio ENVÍA es el que el destinatario VALIDA.

── El defecto que este guardián existe para impedir ─────────────────────────

`signature-service` consultaba la huella vigente de un borrador a
`document-service` mandando `SIGNING_KEY_INTERNAL_TOKEN` —el secreto de
auth-service— en la cabecera que `document-service` valida contra su
`INTERNAL_SERVICE_TOKEN`. Son dos valores distintos, así que **todas** las
consultas respondían 401.

El efecto no era una fuga: era peor de encontrar. El cliente traduce cualquier
fallo a `HuellaNoVerificableError`, el gate lo convierte en `503` fail-closed, y
el circuito de aprobación entero —abrir, dar visto bueno, firmar— quedaba
inservible con un mensaje que acusa a la red. Es la misma familia que la
«cabecera que nadie produce»: un mecanismo que reporta funcionamiento sin
comprobar nada.

**Ningún test unitario podía cazarlo**: todos sustituyen la llamada de red, y un
sustituto no valida tokens. Apareció al abrir el navegador, y por eso el
guardián vive aquí, sobre el `docker-compose.yml`, que es donde los dos extremos
se ven a la vez.

── Cómo se decide, y por qué no es una lista a mano ─────────────────────────

Se comparan los VALORES resueltos, no los nombres: un servicio puede llamar a su
variable `DOCUMENT_INTERNAL_TOKEN` y el destinatario `INTERNAL_SERVICE_TOKEN`, y
eso es correcto mientras el valor por defecto coincida. Lo que no puede ocurrir
es que dos extremos de la misma puerta resuelvan a secretos distintos.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "docker-compose.yml"

_SERVICIO = re.compile(r"^  ([a-z0-9-]+):\s*$")
_ENV = re.compile(r"^\s*-\s*([A-Z_][A-Z0-9_]*)=(.+?)\s*$")
_DEFAULT = re.compile(r"^\$\{[A-Z_][A-Z0-9_]*:-(.*)\}$")


def _entorno_por_servicio() -> dict[str, dict[str, str]]:
    """`{servicio: {VARIABLE: valor-por-defecto}}` leído del compose de dev."""
    entorno: dict[str, dict[str, str]] = {}
    actual: str | None = None
    for linea in COMPOSE.read_text(encoding="utf-8").splitlines():
        m = _SERVICIO.match(linea)
        if m:
            actual = m.group(1)
            entorno.setdefault(actual, {})
            continue
        if actual is None:
            continue
        m = _ENV.match(linea)
        if m:
            nombre, crudo = m.group(1), m.group(2)
            d = _DEFAULT.match(crudo)
            entorno[actual][nombre] = d.group(1) if d else crudo
    return entorno


#: `(emisor, variable del emisor)` → `(receptor, variable que el receptor valida)`.
#: Se añade un par el día que un servicio empieza a llamar a otro por una puerta
#: interna; el guardián no puede adivinar quién llama a quién, pero sí impedir
#: que el par declarado se desalinee.
PARES: tuple[tuple[str, str, str, str], ...] = (
    ("signature-service", "DOCUMENT_INTERNAL_TOKEN", "document-service", "INTERNAL_SERVICE_TOKEN"),
    ("signature-service", "STORAGE_INTERNAL_TOKEN", "storage-service", "INTERNAL_SERVICE_TOKEN"),
    ("document-service", "STORAGE_INTERNAL_TOKEN", "storage-service", "INTERNAL_SERVICE_TOKEN"),
    ("archive-service", "STORAGE_INTERNAL_TOKEN", "storage-service", "INTERNAL_SERVICE_TOKEN"),
)


def test_el_compose_se_puede_leer() -> None:
    """Anti-vacuidad: si la extracción se rompe, todo lo demás pasaría vacío."""
    entorno = _entorno_por_servicio()
    assert "document-service" in entorno, "no se reconoció ningún servicio"
    assert entorno["document-service"], "no se leyó ninguna variable de entorno"


@pytest.mark.parametrize(("emisor", "var_emisor", "receptor", "var_receptor"), PARES)
def test_los_dos_extremos_de_una_puerta_interna_resuelven_al_mismo_secreto(
    emisor: str, var_emisor: str, receptor: str, var_receptor: str
) -> None:
    entorno = _entorno_por_servicio()

    enviado = entorno.get(emisor, {}).get(var_emisor)
    assert enviado is not None, (
        f"{emisor} no declara {var_emisor} en docker-compose.yml. Sin ella el "
        f"cliente manda una cadena vacía y {receptor} responde 401 a TODAS las "
        "llamadas: fail-closed, pero el camino queda inservible con un mensaje "
        "que acusa a la red."
    )
    validado = entorno.get(receptor, {}).get(var_receptor)
    assert validado is not None, (
        f"{receptor} no declara {var_receptor}: no hay contra qué validar."
    )
    assert enviado == validado, (
        f"{emisor}.{var_emisor}={enviado!r} NO coincide con "
        f"{receptor}.{var_receptor}={validado!r}. Todas las llamadas por esa "
        "puerta responderán 401 y el camino degradará a 503 acusando a la red."
    )


def test_ningun_par_reutiliza_el_token_de_auth_para_otro_servicio() -> None:
    """El defecto concreto que ocurrió, escrito para que no vuelva.

    `SIGNING_KEY_INTERNAL_TOKEN` es la puerta de auth-service. Aparecer como
    emisor hacia cualquier OTRO receptor significa que alguien lo reutilizó
    porque «ya estaba a mano».
    """
    infractores = [
        (emisor, receptor)
        for emisor, var_emisor, receptor, _ in PARES
        if var_emisor == "SIGNING_KEY_INTERNAL_TOKEN" and receptor != "auth-service"
    ]
    assert not infractores, (
        f"El token de auth-service se está usando para hablar con otro servicio: "
        f"{infractores}. Cada servicio valida el suyo."
    )


# ---------------------------------------------------------------------------
# Segundo eje: cada LLAMADA lleva el token del servicio AL QUE VA
# ---------------------------------------------------------------------------
#
# El guardián de arriba compara los VALORES de las variables de entorno en el
# compose, y por eso no vio el segundo defecto: las dos variables casaban
# perfectamente y la llamada usaba la que no era.
#
# Al corregir el token de la consulta de huella, el arreglo entró en la función
# EQUIVOCADA — `get_signing_key_revocation_status`, que habla con auth-service—.
# Resultado: la huella seguía con el token de auth (403 en todas las consultas,
# y con ellas 503 en abrir circuito, dar visto bueno y firmar) y, de propina, la
# comprobación de revocación, que estaba sana, pasó a mandar el token de
# document a auth-service. Un arreglo que rompe lo que funcionaba mientras deja
# roto lo que iba a arreglar.
#
# Aquí se ata cada `httpx.AsyncClient(base_url=settings.X_service_url)` con el
# `X-Internal-Token` que viaja dentro de esa misma función.

import ast

RAIZ_REPO = Path(__file__).resolve().parents[2]
CLIENTES_SIGNATURE = (
    RAIZ_REPO / "services" / "signature-service" / "app" / "core" / "http_clients.py"
)

#: `settings.<url>` → `settings.<token>` que ese destino valida.
DESTINO_ESPERA = {
    "auth_service_url": "signing_key_internal_token",
    "document_service_url": "document_internal_token",
    "notification_service_url": "notification_internal_token",
    "storage_service_url": "storage_internal_token",
}


def _atributos_de_settings(nodo: ast.AST) -> set[str]:
    """Nombres `settings.X` que aparecen dentro de un nodo del árbol."""
    encontrados = set()
    for hijo in ast.walk(nodo):
        if (
            isinstance(hijo, ast.Attribute)
            and isinstance(hijo.value, ast.Name)
            and hijo.value.id == "settings"
        ):
            encontrados.add(hijo.attr)
    return encontrados


def _funciones_con_destino_y_token():
    """(función, url de destino, tokens que manda) por cada llamada saliente."""
    arbol = ast.parse(CLIENTES_SIGNATURE.read_text(encoding="utf-8"))
    for fn in ast.walk(arbol):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        usados = _atributos_de_settings(fn)
        destinos = usados & set(DESTINO_ESPERA)
        tokens = {u for u in usados if u.endswith("_internal_token")}
        if len(destinos) == 1 and tokens:
            yield fn.name, destinos.pop(), tokens


def test_el_censo_ve_las_llamadas_salientes() -> None:
    """Anti-vacuidad: si el extractor se rompe, cae aquí y no en el de abajo."""
    assert len(list(_funciones_con_destino_y_token())) >= 3


def test_cada_llamada_manda_el_token_del_servicio_al_que_va() -> None:
    equivocadas = []
    for nombre, destino, tokens in _funciones_con_destino_y_token():
        esperado = DESTINO_ESPERA[destino]
        if esperado not in tokens:
            equivocadas.append(f"{nombre}() → {destino} manda {sorted(tokens)}, espera {esperado!r}")
    assert not equivocadas, (
        "llamadas que mandan el token de OTRO servicio; el destino responde 401/403 "
        "y el camino queda fail-closed pero inservible: " + "; ".join(equivocadas)
    )
