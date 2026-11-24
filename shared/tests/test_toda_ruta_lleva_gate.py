# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Toda ruta de un servicio lleva un gate, o su ausencia está declarada con motivo.

Las reglas no negociables (CLAUDE.md, invariante 5) dicen que la autorización es
fail-closed. Hasta AREA-01-18 ningún test lo derivaba del árbol: `test_escritura_exige_nivel`
solo mira las rutas que YA tienen `require_permission` y su nivel, así que una
ruta que nacía sin gate no la veía nadie (`knowledge /search`, `/antecedentes` y
`/rag` estaban así y solo un censo manual lo supo).

**Qué vigila.** Cada ruta (`@router.get|post|put|patch|delete`) de
`services/*/app/routers/*.py` debe cumplir UNA de:

* llevar `require_permission`, `require_internal_token` o `require_public_interop`
  (en el decorador, en la firma, o en `APIRouter(dependencies=[...])`);
* ser un `health.py` / `openapi.py`, o colgar de un prefijo `/public/`;
* figurar en `EXCEPCIONES` con su MOTIVO. Son excepciones DECLARADAS, no
  aprobadas: se ven para poder discutirlas, como `SIN_NIVEL_DECLARADAS`.

La lista no puede crecer (una ruta nueva sin gate rompe el test) ni caducar
(arreglar una obliga a quitarla), y el censo debe ser > 100 rutas para que un
cambio de estilo del decorador no lo vacíe en silencio.

El motivo de CADA excepción no es decorativo: dice qué otro mecanismo la
protege (clearance en el WHERE, identidad del bearer, turno de la cadena,
permiso dinámico). Una excepción cuyo motivo ya no sea cierto se corrige aquí,
no se deja.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._compose import glob_src

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"

#: Nombres que, presentes en el decorador, la firma o el router, constituyen un gate.
GATES = frozenset({
    "require_permission",
    "require_any_permission",
    "require_internal_token",
    "_require_internal_token",
    "require_public_interop",
    "exigir_firma_proveedor",
})
_METODOS = frozenset({"get", "post", "put", "patch", "delete"})
# `metrics.py` (C18, ADR-041) está exento SOLO si declara únicamente `GET /metrics`: esa
# condición la verifica shared/tests/test_metricas_frontera.py, no esta exención.
_ARCHIVOS_EXENTOS = frozenset({"health.py", "openapi.py", "metrics.py"})

MOTIVOS = {
    "catalogo": (
        "Lectura de catálogo institucional (TRD/TVD, tipos, dependencias, parámetros): cualquier autenticado del tenant la necesita para operar; no devuelve datos de un radicado."
    ),
    "pre_auth": (
        "Emisión/renovación/cierre de sesión y validación del bearer: son previas a tener permisos (el gate ES la validación del token)."
    ),
    "propia": (
        "Opera solo sobre la identidad del propio bearer (/me, contexto, clearance propio, TOTP/clave de firma propios); no recibe el id de otra persona."
    ),
    "interno_por_bearer": (
        "Interno (no enrutado por el gateway): verifica el TOTP del dueño del bearer; su gate es la validación del JWT del firmante, no un token interno."
    ),
    "clearance": (
        "Lectura/solicitud sobre un radicado: el gate es el clearance no-read-up en el WHERE de la consulta (RF-SEG-08), no un permiso funcional."
    ),
    "firma": (
        "Verificación de firmas (pública por diseño del verificador) y lectura de una firma por id con clearance; sin efecto de escritura."
    ),
    "turno": (
        "Visto bueno: el gate es la pertenencia a la cadena y el turno (servicio), no un permiso de ruta."
    ),
    "workflow": (
        "Lectura de bandeja/historial: alcance por dependencia y clearance en el WHERE (ADR-028)."
    ),
    "permiso_dinamico": (
        "El permiso exigido es el `transaction_types.requires_permission` del tipo, resuelto en runtime por el servicio (con suelo PERM_TRAMITAR)."
    ),
}

EXCEPCIONES = {
    "pre_auth": frozenset({
        "auth::auth.py::GET /api/v1/auth/me",
        "auth::auth.py::POST /api/v1/auth/login",
        "auth::auth.py::POST /api/v1/auth/logout",
        "auth::auth.py::POST /api/v1/auth/password",
        "auth::auth.py::POST /api/v1/auth/refresh",
        # El emisor OIDC (ADR-048): descubrimiento y JWKS son públicos por
        # definición; /oauth/token autentica la cuenta de servicio con su secreto.
        "auth::oidc.py::GET /.well-known/jwks.json",
        "auth::oidc.py::GET /.well-known/openid-configuration",
        "auth::oidc.py::POST /oauth/token",
    }),
    "propia": frozenset({
        # Catálogo de causales de la Ley 1712 (C12): es la ley, no un dato de la entidad; solo
        # exige tenant válido (get_tenant_conn) y no lee ninguna tabla.
        "document::desclasificacion.py::GET /api/v1/documents/causales-reserva",
        "auth::clearance.py::GET /api/v1/auth/clearance",
        "auth::clearance.py::GET /api/v1/auth/clearance/check",
        "auth::clearance.py::GET /api/v1/auth/security-levels",
        "auth::context.py::GET /api/v1/auth/context",
        "auth::context.py::POST /api/v1/auth/context/switch",
        "auth::signing_key.py::DELETE /api/v1/auth/me/signing-key",
        "auth::signing_key.py::GET /api/v1/auth/me/signing-key",
        "auth::totp.py::DELETE /api/v1/auth/me/totp",
        "auth::totp.py::GET /api/v1/auth/me/totp",
        "auth::totp.py::POST /api/v1/auth/me/totp/activate",
        "auth::totp.py::POST /api/v1/auth/me/totp/enroll",
        "auth::totp.py::POST /api/v1/auth/me/totp/recovery-codes",
    }),
    "interno_por_bearer": frozenset({
    }),
    "permiso_dinamico": frozenset({
        "workflow::workflow.py::POST /api/v1/workflows/{radicado_id}/transactions",
    }),
    "turno": frozenset({
        "workflow::visto_bueno.py::GET /api/v1/workflows/{radicado_id}/vistos-buenos",
        "workflow::visto_bueno.py::POST /api/v1/workflows/{radicado_id}/vistos-buenos/decidir",
    }),
    "firma": frozenset({
        "signature::signature.py::GET /api/v1/signature/firmas/{firma_id}",
        "signature::signature.py::POST /api/v1/signature/verify",
    }),
    "clearance": frozenset({
        "document::anulacion.py::GET /api/v1/documents/{document_id}/anulacion",
        "document::documents.py::GET /api/v1/documents",
        "document::documents.py::GET /api/v1/documents/by-tracking/{tracking_number:path}",
        "document::documents.py::GET /api/v1/documents/{document_id}",
        "document::documents.py::GET /api/v1/documents/{document_id}/anexos",
        "document::documents.py::GET /api/v1/documents/{document_id}/anexos/{file_id}/download",
        "document::documents.py::GET /api/v1/documents/{document_id}/historial-acceso",
        "document::respuesta.py::GET /api/v1/documents/{document_id}/respuestas",
        "document::search.py::GET /api/v1/search",
    }),
    "workflow": frozenset({
        "workflow::workflow.py::GET /api/v1/workflows/inbox",
        "workflow::workflow.py::GET /api/v1/workflows/pending",
        "workflow::workflow.py::GET /api/v1/workflows/transaction-types",
        "workflow::workflow.py::GET /api/v1/workflows/{radicado_id}/current",
        "workflow::workflow.py::GET /api/v1/workflows/{radicado_id}/events",
        "workflow::workflow.py::GET /api/v1/workflows/{radicado_id}/history",
    }),
    "catalogo": frozenset({
        "archive::metadata.py::GET /api/v1/expediente-metadata/templates",
        "archive::tipos_documentales.py::GET /api/v1/tipos-documentales",
        "archive::tipos_documentales.py::GET /api/v1/tipos-documentales/{tipo_id}",
        "archive::trd.py::GET /api/v1/trd",
        "archive::trd.py::GET /api/v1/trd/cuadro",
        "archive::trd.py::GET /api/v1/trd/{serie_id}",
        "archive::trd.py::GET /api/v1/trd/{serie_id}/retention",
        "archive::tvd.py::GET /api/v1/tvd",
        "archive::tvd.py::GET /api/v1/tvd/{agrupacion_id}",
        "archive::tvd.py::GET /api/v1/tvd/{agrupacion_id}/retention",
        "document::metadata.py::GET /api/v1/metadata/templates",
        "document::metadata_elements.py::GET /api/v1/metadata/elements",
        "document::metadata_elements.py::GET /api/v1/metadata/elements/{element_id}",
        "tenant::catalogos.py::GET /api/v1/catalogos",
        "tenant::catalogos.py::GET /api/v1/catalogos/{catalogo}",
        "tenant::config.py::GET /api/v1/business-days/calculate",
        "tenant::config.py::GET /api/v1/config/asistente",
        "tenant::config.py::GET /api/v1/config/holidays",
        "tenant::config.py::GET /api/v1/config/params",
        "tenant::config.py::GET /api/v1/config/params/{key}",
        "tenant::dependencias.py::GET /api/v1/dependencias",
        "tenant::dependencias.py::GET /api/v1/dependencias/by-codigo/{codigo}",
        "tenant::dependencias.py::GET /api/v1/dependencias/tree",
        "tenant::dependencias.py::GET /api/v1/dependencias/{dependencia_id}",
    }),
}

DECLARADAS = {ruta: motivo for motivo, rutas in EXCEPCIONES.items() for ruta in rutas}


def _nombres(nodo: ast.AST) -> set[str]:
    hallados: set[str] = set()
    for n in ast.walk(nodo):
        if isinstance(n, ast.Name):
            hallados.add(n.id)
        elif isinstance(n, ast.Attribute):
            hallados.add(n.attr)
    return hallados


def _routers_de(arbol: ast.AST) -> dict[str, tuple[str, set[str]]]:
    """{variable: (prefijo, nombres de `dependencies=`)} de cada `APIRouter(...)`."""
    routers: dict[str, tuple[str, set[str]]] = {}
    for n in ast.walk(arbol):
        if (
            isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Call)
            and getattr(n.value.func, "id", None) == "APIRouter"
            and isinstance(n.targets[0], ast.Name)
        ):
            kw = {k.arg: k.value for k in n.value.keywords}
            prefijo = ""
            if isinstance(kw.get("prefix"), ast.Constant):
                prefijo = str(kw["prefix"].value)
            deps = _nombres(kw["dependencies"]) if "dependencies" in kw else set()
            routers[n.targets[0].id] = (prefijo, deps)
    return routers


def _censo_de(texto: str, clave: str) -> tuple[set[str], set[str]]:
    """(todas las rutas, las que NO llevan gate ni exención) de un archivo."""
    arbol = ast.parse(texto)
    routers = _routers_de(arbol)
    todas: set[str] = set()
    sin_gate: set[str] = set()
    for fn in ast.walk(arbol):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in fn.decorator_list:
            if not (
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and isinstance(d.func.value, ast.Name)
                and d.func.value.id in routers
                and d.func.attr in _METODOS
            ):
                continue
            prefijo, deps_router = routers[d.func.value.id]
            ruta = ""
            if d.args and isinstance(d.args[0], ast.Constant):
                ruta = str(d.args[0].value)
            completa = f"{clave}::{d.func.attr.upper()} {prefijo}{ruta}"
            todas.add(completa)
            if (_nombres(d) | _nombres(fn.args) | deps_router) & GATES:
                continue
            if "/public/" in f"{prefijo}{ruta}":
                continue
            sin_gate.add(completa)
    return todas, sin_gate


def _censo() -> tuple[set[str], set[str]]:
    todas: set[str] = set()
    sin_gate: set[str] = set()
    for ruta in sorted(glob_src(SERVICIOS, "*/app/routers/*.py")):
        if ruta.name in _ARCHIVOS_EXENTOS:
            continue
        t, s = _censo_de(ruta.read_text(encoding="utf-8"), f"{ruta.parts[-3]}::{ruta.name}")
        todas |= t
        sin_gate |= s
    return todas, sin_gate


def test_ninguna_ruta_nace_sin_gate() -> None:
    """Lo que importa: una ruta nueva sin gate ni excepción declarada rompe."""
    _, sin_gate = _censo()
    nuevas = sorted(sin_gate - DECLARADAS.keys())
    assert not nuevas, (
        "Estas rutas no llevan `require_permission`, `require_internal_token` ni "
        "cuelgan de `/public/`. Añade el gate, o declara la excepción en "
        "`EXCEPCIONES` con el mecanismo que SÍ la protege:\n  " + "\n  ".join(nuevas)
    )


def test_la_lista_de_excepciones_no_caduca() -> None:
    """Arreglar una excepción (o borrar la ruta) obliga a quitarla de la lista."""
    _, sin_gate = _censo()
    resueltas = sorted(DECLARADAS.keys() - sin_gate)
    assert not resueltas, (
        "Estas excepciones ya llevan gate (o dejaron de existir): quítalas de "
        "`EXCEPCIONES` para que la lista cuente la deuda real:\n  " + "\n  ".join(resueltas)
    )


def test_el_censo_encuentra_algo() -> None:
    """Anti-vacuidad: un cambio de estilo del decorador dejaría los tests de
    arriba en verde sobre un censo vacío."""
    todas, sin_gate = _censo()
    assert len(todas) > 100, f"el censo se quedó en {len(todas)} rutas: el patrón dejó de casar"
    assert sin_gate, "ninguna ruta sin gate: o el derivador está ciego o la lista dejó de ser cierta"


def test_cada_motivo_declarado_tiene_texto() -> None:
    assert set(EXCEPCIONES) == set(MOTIVOS)
    assert all(len(m) > 40 for m in MOTIVOS.values())


_SINTETICO = '''
router = APIRouter(prefix="/x")
admin = APIRouter(prefix="/a", dependencies=[Depends(require_permission("P"))])


@router.post("/abierta")
async def uno() -> None: ...


@router.post("/con-gate", dependencies=[Depends(require_permission("P"))])
async def dos() -> None: ...


@router.get("/firma")
async def tres(_: None = Depends(require_internal_token)) -> None: ...


@admin.get("/heredada")
async def cuatro() -> None: ...
'''


def test_el_censo_distingue_gate_de_su_ausencia() -> None:
    todas, sin_gate = _censo_de(_SINTETICO, "svc::r.py")
    assert len(todas) == 4
    assert sin_gate == {"svc::r.py::POST /x/abierta"}
