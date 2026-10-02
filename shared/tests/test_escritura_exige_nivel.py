# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Una escritura no puede exigir el nivel de una lectura.

`docs/es/api.md` declara qué significa el nivel CRUD de un permiso concedido:
**1 leer · 2 editar · 3 crear/borrar**. Y `require_permission(perm)` tiene
`min_crud=1` por defecto, así que un endpoint de escritura que omite el
parámetro queda abierto a quien tiene el permiso **en modo lectura**.

Destapado en el barrido negativo B13 (septiembre de 2026), pulsando de verdad:
el usuario `consulta` —`USUA_PERM_EXPEDIENTE` con nivel 1, creado precisamente
para ser el control negativo— **editó los metadatos de un expediente**. El
propio router lo delataba por pares: excluir un radicado exigía nivel 3 y
vincularlo no exigía nada; cerrar un expediente exigía 3 y crearlo no. El gate
estaba puesto en una sola dirección de cada par.

Y la documentación afirmaba lo contrario de lo que el sistema hacía —«decidir
solo por presencia del permiso ofrece acciones de escritura a quien lo tiene en
modo lectura, que el backend niega con 403»—, que es justo la frase por la que
un espejo RBAC de interfaz se fía del backend.

**Qué vigila este test.** No que las 135 escrituras exijan nivel: eso sería un
barrido que nadie ha decidido, sobre autorización de instalaciones existentes.
Vigila que la lista de las que NO lo exigen **no crezca**. Las de abajo son
excepciones DECLARADAS, no aprobadas: quedan escritas para que se vean, con el
mismo criterio que `018_permisos_inertes_declarados.sql` usó con los permisos
que no restringen nada. Un endpoint de escritura nuevo que nazca a nivel 1
rompe este test; y arreglar uno de los de la lista obliga a quitarlo de la lista, que es
como se sabe que la deuda baja.

El censo se **deriva** de `services/*/app/routers/*.py` con AST-lite sobre el
decorador. Escribir la lista a mano ya dejó fuera tres de once servicios en
este proyecto.
"""

from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"

_DECORADOR = re.compile(r"@router\.(post|patch|put|delete)\(")
_RUTA = re.compile(r'@router\.\w+\(\s*\n?\s*"([^"]*)"')
_RUTA_ANY = re.compile(r'@\w+\.\w+\(\s*\n?\s*"([^"]*)"')
_PERMISO = re.compile(r'require_permission\(\s*\n?\s*"([^"]+)"')

#: Escrituras con gate que hoy NO exigen nivel mínimo. Declaradas, no
#: aprobadas — ver el docstring. Formato:
#: "<servicio>::<archivo>::<MÉTODO> <ruta>::<permiso>"
SIN_NIVEL_DECLARADAS = frozenset({
    "archive-service::batch.py::POST /expedientes::USUA_PERM_EXPEDIENTE",
    "archive-service::fisico.py::POST /prestamos/{prestamo_id}/devolver::USUA_PERM_EXPEDIENTE",
    "archive-service::fisico.py::POST /ubicaciones::USUA_PERM_EXPEDIENTE",
    "archive-service::fisico.py::POST /unidades/{unidad_id}/expedientes::USUA_PERM_EXPEDIENTE",
    "archive-service::fisico.py::POST /unidades/{unidad_id}/prestamos::USUA_PERM_EXPEDIENTE",
    "archive-service::fisico.py::POST /unidades::USUA_PERM_EXPEDIENTE",
    "archive-service::indice.py::POST /{expediente_id}/indice/firmar::USUA_PERM_EXPEDIENTE",
    "archive-service::indice.py::POST /{expediente_id}/indice/rebuild::USUA_PERM_EXPEDIENTE",
    "archive-service::metadata.py::POST ::USUA_PERM_TRD",
    "archive-service::tipos_documentales.py::PATCH /{tipo_id}::USUA_PERM_TRD",
    "archive-service::tipos_documentales.py::POST ::USUA_PERM_TRD",
    "archive-service::trd.py::POST ::USUA_PERM_TRD",
    "archive-service::tvd.py::POST ::USUA_PERM_TRD",
    "auth-service::signing_key.py::POST /enroll::PERM_FIRMA",
    "document-service::anulacion.py::POST /{document_id}/anulacion/aprobar::PERM_PANU_CODI",
    "document-service::anulacion.py::POST /{document_id}/anulacion/rechazar::PERM_PANU_CODI",
    "document-service::anulacion.py::POST /{document_id}/anulacion::PERM_SOL_ANULAR",
    "document-service::batch.py::POST /documents::PERM_RADI",
    # Reprocesar es retomar el MISMO acto que crear el lote, al mismo nivel que
    # radicar: su creador lo hace con PERM_RADI 1; un tercero necesita nivel 3, y
    # ese gate va en el WHERE del servicio (404 neutro), no en el decorador.
    "document-service::batch.py::POST /documents/{job_id}/reprocesar::PERM_RADI",
    "document-service::borradores.py::DELETE /api/v1/plantillas/{plantilla_id}::USUA_PERM_ADMIN",
    "document-service::borradores.py::PATCH /api/v1/borradores/{borrador_id}::PERM_RADI",
    "document-service::borradores.py::POST /api/v1/borradores/{borrador_id}/aprobar::PERM_RADI",
    "document-service::borradores.py::POST /api/v1/borradores/{borrador_id}/devolver::PERM_RADI",
    "document-service::borradores.py::POST /api/v1/borradores/{borrador_id}/previsualizar::PERM_RADI",
    "document-service::borradores.py::POST /api/v1/borradores/{borrador_id}/radicar::PERM_RADI",
    "document-service::borradores.py::POST /api/v1/borradores::PERM_RADI",
    "document-service::borradores.py::POST /api/v1/plantillas/importar::USUA_PERM_ADMIN",
    "document-service::borradores.py::POST /api/v1/plantillas/{plantilla_id}/combinar::PERM_RADI",
    "document-service::borradores.py::POST /api/v1/plantillas::USUA_PERM_ADMIN",
    "document-service::borradores.py::PUT /api/v1/plantillas/{plantilla_id}/alcance::USUA_PERM_ADMIN",
    "document-service::documents.py::PATCH /{document_id}::PERM_RADI",
    "document-service::documents.py::POST ::PERM_RADI",
    "document-service::metadata.py::POST ::USUA_PERM_TRD",
    "document-service::metadata_elements.py::PATCH /{element_id}::USUA_PERM_TRD",
    "document-service::metadata_elements.py::POST ::USUA_PERM_TRD",
    "document-service::respuesta.py::POST /{document_id}/respuesta::PERM_RESPUESTA_VINCULADA",
    "signature-service::signature.py::POST /sign-personal::PERM_FIRMA",
    "storage-service::storage.py::POST /upload::PERM_RADI",
    "tenant-service::catalogos.py::DELETE /{catalogo}/{item_id}::USUA_PERM_ADMIN",
    "tenant-service::catalogos.py::PATCH /{catalogo}/{item_id}::USUA_PERM_ADMIN",
    "tenant-service::catalogos.py::POST /{catalogo}::USUA_PERM_ADMIN",
    "tenant-service::config.py::DELETE /config/holidays/{holiday_id}::USUA_PERM_ADMIN",
    "tenant-service::config.py::POST /config/holidays/seed::USUA_PERM_ADMIN",
    "tenant-service::config.py::POST /config/holidays::USUA_PERM_ADMIN",
    "tenant-service::config.py::PUT /config/params/{key}::USUA_PERM_ADMIN",
    "tenant-service::dependencias.py::DELETE /{dependencia_id}::USUA_PERM_ADMIN",
    "tenant-service::dependencias.py::PATCH /{dependencia_id}::USUA_PERM_ADMIN",
    "tenant-service::dependencias.py::POST ::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::DELETE /aspectos/{aspecto_id}::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::DELETE /objetivos/{objetivo_id}::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::DELETE /planes/{plan_id}/instrumentos/{instrumento_id}::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::DELETE /proyectos/{proyecto_id}::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::PATCH /aspectos/{aspecto_id}::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::PATCH /objetivos/{objetivo_id}::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::PATCH /planes/{plan_id}::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::PATCH /proyectos/{proyecto_id}::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::POST /planes/{plan_id}/aprobar::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::POST /planes/{plan_id}/aspectos::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::POST /planes/{plan_id}/cerrar::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::POST /planes/{plan_id}/ejecutar::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::POST /planes/{plan_id}/instrumentos::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::POST /planes/{plan_id}/objetivos::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::POST /planes/{plan_id}/proyectos::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::POST /planes::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::POST /proyectos/{proyecto_id}/seguimiento::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::PUT /planes/{plan_id}/priorizacion::USUA_PERM_ADMIN",
    "tenant-service::pinar.py::PUT /proyectos/{proyecto_id}/aspectos::USUA_PERM_ADMIN",
    "workflow-service::rules.py::DELETE /{rule_id}::USUA_PERM_ADMIN",
    "workflow-service::rules.py::PATCH /{rule_id}::USUA_PERM_ADMIN",
    "workflow-service::rules.py::POST /evaluate/batch::USUA_PERM_ADMIN",
    "workflow-service::rules.py::POST /evaluate::USUA_PERM_ADMIN",
    "workflow-service::rules.py::POST ::USUA_PERM_ADMIN",
    "workflow-service::visto_bueno.py::POST /{radicado_id}/vistos-buenos::PERM_RADI",
    "workflow-service::workflow.py::PATCH /{step_id}/complete::PERM_TRAMITAR",
    "workflow-service::workflow.py::POST /assign::PERM_TRAMITAR",
    "workflow-service::workflow.py::POST /overdue/scan::USUA_PERM_ADMIN",
    "workflow-service::workflow.py::POST /reassign/cascade::USUA_PERM_ADMIN",
    "workflow-service::workflow.py::POST /transfer::PERM_TRAMITAR",
    "workflow-service::workflow.py::POST /{radicado_id}/devolver::PERM_TRAMITAR",
    "workflow-service::workflow.py::POST /{radicado_id}/rollback::PERM_TRAMITAR",
    # --- Declaradas al enseñar al censo a leer los gates de `APIRouter(dependencies=[...])`
    # (AREA-01-01). Estaban abiertas a nivel de lectura sin que el censo lo viera;
    # las de auth-service (RBAC, URD, 2FA/firma de OTRA persona) se corrigieron en
    # la misma tanda. Estas quedan declaradas, no aprobadas: subirlas a 2/3 es el
    # barrido siguiente (cadena de firma, DLQ, alta de terceros; los webhooks ya exigen nivel 3, OMI-03).
    "document-service::terceros.py::POST ::PERM_RADI",
    "notification-service::admin_deadletter.py::POST /{event_id}/discard::PERM_DLQ_ADMIN",
    "notification-service::admin_deadletter.py::POST /{event_id}/replay::PERM_DLQ_ADMIN",
    "signature-service::admin_deadletter.py::POST /{event_id}/discard::PERM_DLQ_ADMIN",
    "signature-service::admin_deadletter.py::POST /{event_id}/replay::PERM_DLQ_ADMIN",
    "signature-service::cadena.py::POST /batch::PERM_FIRMA",
    "signature-service::cadena.py::POST /requests/{solicitud_id}/challenge::PERM_FIRMA",
    "signature-service::cadena.py::POST /requests/{solicitud_id}/reject::PERM_FIRMA",
    "signature-service::cadena.py::POST /requests/{solicitud_id}/sign::PERM_FIRMA",
    "signature-service::cadena.py::POST /requests/{solicitud_id}/vobo::PERM_FIRMA",
    "signature-service::cadena.py::POST /requests::PERM_FIRMA",
    "workflow-service::admin_deadletter.py::POST /{event_id}/discard::PERM_DLQ_ADMIN",
    "workflow-service::admin_deadletter.py::POST /{event_id}/replay::PERM_DLQ_ADMIN",
})


_ROUTER = re.compile(r"^(\w+) = APIRouter\(", re.MULTILINE)
_DECORADOR_ANY = re.compile(r"@(\w+)\.(post|patch|put|delete)\(")


def _gates_de_routers(texto: str) -> dict[str, tuple[str, bool]]:
    """{variable_del_router: (permiso, ¿tiene min_crud?)} de cada
    `APIRouter(dependencies=[...require_permission(...)])` del archivo.

    Un gate de router protege TODAS las rutas del router sin que ningún decorador
    lo mencione: el censo anterior solo miraba el decorador y era ciego a él
    (AREA-01-01: `USUA_PERM_ADMIN` a nivel 1 bastaba para administrar RBAC).
    Se extrae la llamada con paréntesis balanceados: un `router = APIRouter(...)`
    de una línea no debe arrastrar las dependencias del `admin_router` siguiente.
    """
    gates: dict[str, tuple[str, bool]] = {}
    for m in _ROUTER.finditer(texto):
        profundidad, i = 1, m.end()
        while i < len(texto) and profundidad:
            profundidad += {"(": 1, ")": -1}.get(texto[i], 0)
            i += 1
        cuerpo = texto[m.end() : i]
        if "dependencies=" in cuerpo and "require_permission(" in cuerpo:
            p = _PERMISO.search(cuerpo)
            gates[m.group(1)] = (p.group(1) if p else "?", "min_crud=" in cuerpo)
    return gates


def _censo_de(texto: str, clave: str) -> set[str]:
    """Censo de UN archivo de router. `clave` = "<servicio>::<archivo>"."""
    hallados: set[str] = set()
    gates = _gates_de_routers(texto)
    lineas = texto.splitlines()
    i = 0
    while i < len(lineas):
        m = _DECORADOR_ANY.match(lineas[i])
        if not m or (m.group(1) != "router" and m.group(1) not in gates):
            i += 1
            continue
        gate_router = gates.get(m.group(1))
        metodo = m.group(2).upper()
        bloque: list[str] = []
        j = i
        # El decorador llega hasta la firma de la función que decora.
        while j < len(lineas) and not lineas[j].startswith(("async def", "def ")):
            bloque.append(lineas[j])
            j += 1
        txt = "\n".join(bloque)
        r = _RUTA_ANY.search(txt)
        permiso: str | None = None
        if "require_permission(" in txt:
            # El gate del propio endpoint manda sobre el del router.
            if "min_crud=" not in txt:
                p = _PERMISO.search(txt)
                permiso = p.group(1) if p else "?"
        elif gate_router is not None and not gate_router[1]:
            permiso = gate_router[0]
        if permiso is not None:
            hallados.add(f"{clave}::{metodo} {r.group(1) if r else ''}::{permiso}")
        i = j
    return hallados


def _censo() -> set[str]:
    """Toda escritura con gate (de endpoint o de router) y sin `min_crud`, derivada."""
    hallados: set[str] = set()
    for ruta in sorted(SERVICIOS.glob("*/app/routers/*.py")):
        hallados |= _censo_de(
            ruta.read_text(encoding="utf-8"), f"{ruta.parts[-4]}::{ruta.name}"
        )
    return hallados


def test_ninguna_escritura_nueva_nace_a_nivel_de_lectura() -> None:
    """Lo que importa: la lista no crece."""
    nuevas = sorted(_censo() - SIN_NIVEL_DECLARADAS)
    assert not nuevas, (
        "Estas escrituras exigen `require_permission` pero NO un `min_crud`, así "
        "que las puede ejecutar quien tenga el permiso en modo LECTURA (nivel 1). "
        "Añade `min_crud=2` (editar) o `min_crud=3` (crear/borrar) según lo que "
        "haga el endpoint. Si de verdad debe quedar a nivel 1, decláralo en "
        "`SIN_NIVEL_DECLARADAS` y di por qué:\n  " + "\n  ".join(nuevas)
    )


def test_la_lista_de_excepciones_no_caduca() -> None:
    """Y que al arreglar una, se quite de la lista.

    Sin esto la lista se vuelve un cementerio: seguiría pasando en verde
    mientras describe un sistema que ya no existe, y dejaría de decir cuánta
    deuda queda.
    """
    resueltas = sorted(SIN_NIVEL_DECLARADAS - _censo())
    assert not resueltas, (
        "Estas ya exigen un nivel mínimo (o dejaron de existir): quítalas de "
        "`SIN_NIVEL_DECLARADAS` para que la lista siga contando la deuda real:\n  "
        + "\n  ".join(resueltas)
    )


def test_el_censo_encuentra_algo() -> None:
    """Anti-vacuidad: si el derivador deja de casar con el decorador —un
    cambio de estilo, un `@router.post` partido en otra forma— los dos tests
    de arriba pasarían en verde sobre un censo vacío, que es la manera
    silenciosa de que un guardián deje de guardar.
    """
    assert len(_censo()) > 50, (
        "El censo derivado se quedó casi vacío: el patrón del decorador dejó de "
        "casar y este guardián ya no vigila nada."
    )


_ROUTER_SINTETICO = '''
router = APIRouter(
    prefix="/x",
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)


@router.put("/g/{g}/permissions/{p}")
async def uno() -> None: ...


@router.post(
    "/g",
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def dos() -> None: ...


@router.get("/lectura")
async def tres() -> None: ...
'''


def test_el_censo_ve_el_gate_de_router() -> None:
    """AREA-01-01: un gate a nivel de `APIRouter` sin `min_crud` cuenta para TODAS
    las escrituras del router que no declaren el suyo; las que sí lo declaran, y
    las lecturas, quedan fuera."""
    assert _censo_de(_ROUTER_SINTETICO, "svc::r.py") == {
        "svc::r.py::PUT /g/{g}/permissions/{p}::USUA_PERM_ADMIN"
    }


def test_el_censo_no_ve_gate_de_router_con_min_crud() -> None:
    texto = _ROUTER_SINTETICO.replace(
        'require_permission("USUA_PERM_ADMIN"))],\n)',
        'require_permission("USUA_PERM_ADMIN", min_crud=3))],\n)',
    )
    assert _censo_de(texto, "svc::r.py") == set()


def test_administrar_rbac_exige_nivel_3() -> None:
    """AREA-01-01: las escrituras de RBAC/URD/clearance de auth-service exigen
    `USUA_PERM_ADMIN` con `min_crud=3`: ya no figuran en el censo de las que se
    pueden ejecutar a nivel de lectura, y la fuente lo declara."""
    base = SERVICIOS / "auth-service" / "app" / "routers"
    fuera = [
        c for c in _censo() if c.startswith("auth-service::") and c.endswith("::USUA_PERM_ADMIN")
    ]
    assert not fuera, f"escrituras de auth-service a nivel de lectura: {fuera}"
    for archivo, esperadas in {
        "rbac.py": 7,
        "urd.py": 2,
        "clearance.py": 1,
    }.items():
        texto = (base / archivo).read_text(encoding="utf-8")
        assert texto.count('require_permission("USUA_PERM_ADMIN", min_crud=3)') >= esperadas, (
            f"{archivo}: faltan gates de escritura a nivel 3"
        )
