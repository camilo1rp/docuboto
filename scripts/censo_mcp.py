#!/usr/bin/env python3
# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Censo del API por flujos de trabajo frente al catálogo del MCP.

Responde «¿qué le falta al MCP?» **contando**, que es la única forma de
responderla sobre 367 operaciones. Un recuento a mano se equivoca, y se equivoca
de forma tranquilizadora: en este proyecto ya se dio por bueno un catálogo de
1.135 municipios tras verificar cuatro casos conocidos.

Agrupa por **flujo de trabajo** y no por servicio a propósito. El mapa de
servicios dice quién sirve cada endpoint; lo que hay que decidir es qué necesita
alguien que está haciendo un trabajo, y un trámite de ventanilla toca cuatro
servicios distintos.

**El punto ciego que este censo tuvo, y cómo se quitó** (2026-09-17): el
agregador de OpenAPI del gateway compone la documentación de siete de los once
servicios —faltan knowledge, signature y el propio mcp-server—, y eso no dejaba
el total corto: lo dejaba MAL. El circuito de firma entero, trece operaciones, no
figuraba como hueco sino que no figuraba, y la tabla publicaba un porcentaje
calculado sobre un universo que no lo contenía. Un número que nadie sospecha es
peor que ninguno.

Ahora `--openapi` admite un DIRECTORIO con el esquema de cada servicio, que
`scripts/censo-esquemas.sh` reúne preguntándoselo a cada app. Con el agregado
sigue funcionando, con su punto ciego declarado.

Uso:
    ./scripts/censo-esquemas.sh                      # los once, en /tmp/oa/
    python3 scripts/censo_mcp.py --openapi /tmp/oa [--detalle F4] [--huecos F4]

**Excluido a propósito frente a hueco real** (C22). Una sola columna «cobertura»
mezclaba dos cosas que piden respuestas opuestas: lo que se DECIDIÓ no ofrecer
(ejecutar el borrado, conceder permisos, firmar con segundo factor…) y lo que
simplemente falta. Sobre lo primero no hay nada que hacer; sobre lo segundo sí.
`EXCLUIDAS_A_PROPOSITO` declara lo primero, una entrada por grupo de rutas, cada
una con su motivo y la referencia donde la decisión está escrita; el resto de lo
que no tiene herramienta es un hueco. La cobertura se calcula sobre
`operaciones − excluidas`. `shared/tests/test_las_exclusiones_del_mcp_casan_con_rutas_reales.py`
vigila que cada entrada case con rutas reales y que no contradiga a `NO_OFRECIDAS`
(services/mcp-server/app/services/scope.py) ni al catálogo.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CATALOGO = RAIZ / "services" / "mcp-server" / "app" / "tools" / "catalog.py"

# El orden es el del trámite, no el alfabético: quien lee esto está siguiendo un
# documento por el sistema. La primera regla que casa gana.
FLUJOS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Los gemelos `…-internal` (D-02) son disparos de SISTEMA sellados con
    # `X-Internal-Token`: cuentan como no expuestos igual que `/internal/*`, y
    # dejarlos en su flujo hacía que un servicio bien cerrado pareciera un hueco.
    #
    # `/api/v1/storage/*` entra aquí ENTERO salvo `upload`: el gateway no
    # enruta el resto (verificado en `proxy.py` — «SOLO /upload se expone»), así
    # que contarlos como cobertura pendiente del MCP inflaba el denominador con
    # operaciones que no son alcanzables ni deben serlo. Y `upload` tampoco es
    # una tool: subir un fichero por un chat exigiría que el modelo aportara los
    # bytes.
    # El proxy comodín del gateway (`/api/v1/{path}` en sus cinco métodos), su
    # OpenAPI agregado y la superficie del propio mcp-server —`/mcp/*`— entran
    # aquí. No son capacidades que exponer: el comodín ES
    # la puerta por la que pasan todas las demás, y contarlo como operación
    # pendiente sería contar el pasillo como si fuera una habitación. Aparecieron
    # al quitarle al censo su punto ciego: antes no se veían, y el script las
    # dejaba «SIN CLASIFICAR», que es su forma de pedir una decisión.
    ("SISTEMA (sellado / no expuesto)", (r"/internal/", r"-internal$", r"^/api/v1/tenants", r"/health", r"^/api/v1/public/", r"^/api/v1/storage/", r"^/api/v1/\{path\}$", r"^/api/v1/openapi\.json$", r"^/api/v1/mcp/")),
    ("F1 Ventanilla y radicación", (r"^/api/v1/documents", r"^/api/v1/terceros", r"^/api/v1/anulaciones", r"^/api/v1/desclasificaciones", r"^/api/v1/batch/documents", r"^/api/v1/import", r"^/api/v1/export")),
    ("F2 Trámite y reparto", (r"^/api/v1/workflows", r"^/api/v1/workflow/")),
    ("F3 Respuesta y despacho", (r"^/api/v1/borradores", r"^/api/v1/plantillas", r"^/api/v1/envios", r"^/api/v1/fuid")),
    ("F4 Expediente", (r"^/api/v1/expedientes", r"^/api/v1/expediente-metadata", r"^/api/v1/batch/expedientes")),
    ("F5 Archivo físico", (r"^/api/v1/ubicaciones", r"^/api/v1/unidades", r"^/api/v1/prestamos")),
    ("F6 Transferencias", (r"^/api/v1/transferencias",)),
    ("F7 Disposición y eliminación", (r"^/api/v1/eliminacion",)),
    # `signature` entra aquí y hasta 2026-09-17 no entraba en ningún sitio: el
    # agregado del gateway no lo componía, así que sus 21 operaciones no eran un
    # hueco, es que no existían para el censo. F8 declaraba «11 operaciones» y
    # publicaba un 55 % calculado sin ellas.
    ("F8 Preservación y firma", (r"^/api/v1/preservacion", r"^/api/v1/storage", r"^/api/v1/archive/", r"^/api/v1/signature")),
    ("F9 Instrumentos archivísticos", (r"^/api/v1/trd", r"^/api/v1/tvd", r"^/api/v1/tipos-documentales", r"^/api/v1/metadata", r"^/api/v1/pinar")),
    ("F10 Correo institucional", (r"^/api/v1/email",)),
    ("F11 Consulta y reportes", (r"^/api/v1/search", r"^/api/v1/reports", r"^/api/v1/knowledge")),
    ("F12 Administración y seguridad", (r"^/api/v1/auth", r"^/api/v1/audit", r"^/api/v1/dependencias", r"^/api/v1/config", r"^/api/v1/catalogos", r"^/api/v1/business-days")),
    ("F13 Notificaciones e integración", (r"^/api/v1/notification", r"^/api/v1/webhooks")),
)


_ID = r"\{[^/]+\}"
_NO = "NO_OFRECIDAS: "
_SELLADA = "SELLADA: "
_FLUJOS_MD = "docs/es/mcp-flujos.md"
_PRESERVACION = "services/storage-service/app/routers/preservacion.py"

# Operaciones del API que el MCP NO ofrece **por decisión**, no por falta de trabajo.
#
# (método, regex de ruta, motivo, referencia). El método es una regex completa
# (`PATCH|DELETE`); la ruta se compara sin barra final. La referencia es donde la
# decisión está ESCRITA, y es lo que impide que esta lista sea una opinión más:
#
# - `NO_OFRECIDAS: <operación>` — prefijo de una entrada de `NO_OFRECIDAS`
#   (services/mcp-server/app/services/scope.py), que es lo que el catálogo MCP le
#   dice a la persona. Si la entrada no existe, el guardián lo acusa.
# - `SELLADA: <fichero>` — rutas de servicio a servicio (`X-Internal-Token`, D-02)
#   que el gateway no puede abrir a una persona. El guardián exige que TODA ruta
#   casada lleve `require_internal_token` en el router.
# - `docs/es/mcp-flujos.md (…)` — decisión que la página declara fuera.
#
# Cada patrón se verificó contra el decorador del router, no contra el recuerdo.
# Lo que NO está aquí es un hueco aunque «parezca» de la misma familia (p. ej.
# `signature/sign-xml`, `acta/firmar`, `pinar/…/aprobar`):
# nadie lo declaró, y declararlo es una decisión, no una deducción del censo.
EXCLUIDAS_A_PROPOSITO: tuple[tuple[str, str, str, str], ...] = (
    # --- Actos con persona presente / de custodia -------------------------------
    ("POST", rf"^/api/v1/eliminacion/lotes/{_ID}/ejecutar$",
     "único acto que destruye documentos de archivo; exige persona presente",
     _NO + "Ejecutar el borrado de un lote"),
    ("POST", rf"^/api/v1/expedientes/{_ID}/legal-holds$",
     "fijar una retención legal es un acto con acta y responsable",
     _NO + "Fijar o levantar una retención legal"),
    ("POST", rf"^/api/v1/expedientes/{_ID}/legal-holds/{_ID}/levantar$",
     "levantar una retención legal es un acto con acta y responsable",
     _NO + "Fijar o levantar una retención legal"),
    ("POST", r"^/api/v1/preservacion/legal-hold-internal/(fijar|\{[^/]+\}/levantar)$",
     "gemelo sellado que orquesta archive-service para el legal hold",
     _SELLADA + _PRESERVACION),
    ("POST", rf"^/api/v1/tvd/{_ID}/valoracion$",
     "valoración del Comité sobre la TVD: exige el acta del Comité",
     _NO + "Registrar la valoración del Comité"),
    ("POST", rf"^/api/v1/tvd/{_ID}/valoracion/expedientes/{_ID}/seleccion$",
     "selección (disposición S) de un expediente: acto del Comité, con su fundamento",
     _NO + "Registrar la valoración del Comité"),
    ("POST", rf"^/api/v1/desclasificaciones/{_ID}/aprobar$",
     "aprobar exige ver el documento y el cliente MCP no lo muestra; rechazar y retirar sí se ofrecen",
     _NO + "Aprobar la desclasificación de un radicado"),
    ("POST", r"^/api/v1/preservacion/proteger-(indice|artefacto)$",
     "sellar en WORM exige un file_id que el llamante no puede conocer; no se deshace",
     _NO + "Sellar un artefacto en WORM"),
    ("POST", r"^/api/v1/preservacion/aip$",
     "empaquetado manual del AIP: acto de reconciliación del sistema",
     _NO + "Empaquetar manualmente el AIP"),
    ("GET", rf"^/api/v1/preservacion/aip/{_ID}(/versiones)?$",
     "lectura sellada; la mediada de archive-service (/expedientes/{id}/aip) es la que se ofrece",
     _SELLADA + _PRESERVACION),
    ("GET", r"^/api/v1/preservacion/eventos$",
     "lectura sellada; la mediada de archive-service (/expedientes/{id}/preservacion/eventos) es la que se ofrece",
     _SELLADA + _PRESERVACION),
    ("POST", r"^/api/v1/notifications/send$",
     "lo llama signature-service para el segundo factor; sin el sello cualquier token enviaría correo como la institución",
     _SELLADA + "services/notification-service/app/routers/notifications.py"),
    # --- Firma: segundo factor o documento delante ------------------------------
    ("POST", rf"^/api/v1/signature/requests/{_ID}/(sign|vobo|reject)$",
     "firmar consume un segundo factor; aprobar/rechazar exige haber visto el documento",
     _NO + "Firmar un turno de una cadena"),
    # Pedir el reto, firmar en lote y la firma personal suelta son el mismo acto que
    # firmar un turno: consumen el segundo factor (decisión 2026-10-22, triage C22).
    ("POST", rf"^/api/v1/signature/requests/{_ID}/challenge$",
     "pide el código de un solo uso del segundo factor de la firma",
     _NO + "Firmar un turno de una cadena"),
    ("POST", r"^/api/v1/signature/batch$",
     "firma en lote: consume el segundo factor y compromete varios documentos a la vez",
     _NO + "Firmar un turno de una cadena"),
    ("POST", r"^/api/v1/signature/sign-personal$",
     "firma personal con segundo factor",
     _NO + "Firmar un turno de una cadena"),
    ("POST", r"^/api/v1/signature/verify$",
     "exige el XML firmado completo como entrada; por un chat habría que inventarlo",
     _NO + "Comprobar la integridad de una firma"),
    ("POST", rf"^/api/v1/transferencias/{_ID}/acta/firmar-personal$",
     "firma personal con segundo factor (TOTP)",
     _NO + "Firmar personalmente el acta de una transferencia"),
    # --- Redacción y estructura que se hacen viendo ----------------------------
    ("POST", rf"^/api/v1/borradores/{_ID}/previsualizar$",
     "devuelve un PDF como cuerpo; no cabe en una conversación",
     _NO + "Ver el PDF de un borrador"),
    ("POST", r"^/api/v1/plantillas(/importar)?$",
     "redactar plantillas es edición (marcadores, alcance, membrete)",
     _NO + "Ver el PDF de un borrador"),
    ("DELETE", rf"^/api/v1/plantillas/{_ID}$",
     "borrar plantillas es administración de contenido",
     _NO + "Ver el PDF de un borrador"),
    ("PUT", rf"^/api/v1/plantillas/{_ID}/alcance$",
     "el alcance de una plantilla se edita viéndola",
     _NO + "Ver el PDF de un borrador"),
    ("POST", r"^/api/v1/pinar/planes$",
     "formular un PINAR es planeación con el Comité",
     _NO + "Armar la estructura de un plan PINAR"),
    ("PATCH", rf"^/api/v1/pinar/planes/{_ID}$",
     "formular un PINAR es planeación con el Comité",
     _NO + "Armar la estructura de un plan PINAR"),
    ("POST", rf"^/api/v1/pinar/planes/{_ID}/(objetivos|proyectos|aspectos|instrumentos)$",
     "formular un PINAR es planeación con el Comité",
     _NO + "Armar la estructura de un plan PINAR"),
    ("PUT", rf"^/api/v1/pinar/planes/{_ID}/priorizacion$",
     "priorizar es decisión del Comité",
     _NO + "Armar la estructura de un plan PINAR"),
    ("DELETE", rf"^/api/v1/pinar/planes/{_ID}/instrumentos/{_ID}$",
     "formular un PINAR es planeación con el Comité",
     _NO + "Armar la estructura de un plan PINAR"),
    ("PATCH|DELETE", rf"^/api/v1/pinar/(objetivos|proyectos|aspectos)/{_ID}$",
     "formular un PINAR es planeación con el Comité",
     _NO + "Armar la estructura de un plan PINAR"),
    ("PUT", rf"^/api/v1/pinar/proyectos/{_ID}/aspectos$",
     "formular un PINAR es planeación con el Comité",
     _NO + "Armar la estructura de un plan PINAR"),
    # --- La frontera de autorización y las credenciales ------------------------
    ("POST", r"^/api/v1/auth/(groups|users)$",
     "crear personas y grupos decide qué puede hacer todo el mundo",
     _NO + "Conceder o retirar permisos"),
    ("PATCH|DELETE", rf"^/api/v1/auth/groups/{_ID}$",
     "modificar grupos mueve la frontera de autorización",
     _NO + "Conceder o retirar permisos"),
    ("PUT", rf"^/api/v1/auth/groups/{_ID}/(clearance|permissions/{_ID})$",
     "conceder permisos o subir el nivel de un grupo ensancha los límites desde dentro",
     _NO + "Conceder o retirar permisos"),
    ("POST|DELETE", rf"^/api/v1/auth/groups/{_ID}/members/{_ID}$",
     "meter o sacar a alguien de un grupo mueve la frontera de autorización",
     _NO + "Conceder o retirar permisos"),
    ("POST", rf"^/api/v1/auth/users/{_ID}/urd$",
     "habilitar a una persona en una unidad de reparto es una habilitación atribuida",
     _NO + "Conceder o retirar permisos"),
    ("DELETE", rf"^/api/v1/auth/users/{_ID}/urd/{_ID}$",
     "retirar una habilitación de reparto es una habilitación atribuida",
     _NO + "Conceder o retirar permisos"),
    ("POST", r"^/api/v1/auth/me/totp/(enroll|activate|recovery-codes)$",
     "el secreto y los códigos se revelan UNA vez; no deben quedar en una conversación",
     _NO + "Dar de alta o revocar credenciales personales"),
    ("DELETE", r"^/api/v1/auth/me/(totp|signing-key)$",
     "revocar una credencial propia es un acto personal con sesión propia",
     _NO + "Dar de alta o revocar credenciales personales"),
    ("POST", r"^/api/v1/auth/me/signing-key/enroll$",
     "alta de la llave de firma: material criptográfico de un solo uso",
     _NO + "Dar de alta o revocar credenciales personales"),
    ("POST", rf"^/api/v1/auth/users/{_ID}/totp/revoke$",
     "revocar el segundo factor de otra persona es el paso previo a suplantarla",
     _NO + "Dar de alta o revocar credenciales personales"),
    ("POST", rf"^/api/v1/auth/admin/users/{_ID}/signing-key/revoke$",
     "revocar la llave de firma de otra persona",
     _NO + "Dar de alta o revocar credenciales personales"),
    ("POST|GET", r"^/api/v1/auth/(token|refresh|logout|validate)$",
     "mecánica de sesión: el servidor ya viaja con el token de quien pregunta",
     _FLUJOS_MD + " (F12: mecánica de sesión)"),
    # --- La regla con la que se mide todo lo demás -----------------------------
    ("POST", r"^/api/v1/config/holidays(/seed)?$",
     "un festivo añadido corre los plazos que ya están andando",
     _NO + "Cambiar el calendario de festivos"),
    ("DELETE", rf"^/api/v1/config/holidays/{_ID}$",
     "un festivo retirado corre los plazos que ya están andando",
     _NO + "Cambiar el calendario de festivos"),
    ("PUT", rf"^/api/v1/config/(params/{_ID}|asistente)$",
     "los parámetros de la entidad son la regla con la que se cuentan los términos",
     _NO + "Cambiar el calendario de festivos"),
    ("POST", rf"^/api/v1/catalogos/{_ID}$",
     "un catálogo cerrado deja de servir si se le añade desde una conversación",
     _NO + "Añadir o modificar valores de los catálogos"),
    ("PATCH|DELETE", rf"^/api/v1/catalogos/{_ID}/{_ID}$",
     "un catálogo cerrado deja de servir si se modifica desde una conversación",
     _NO + "Añadir o modificar valores de los catálogos"),
    ("POST", r"^/api/v1/dependencias(/\{[^/]+\}/mapeos-nombre)?$",
     "la estructura de dependencias decide a quién le llegan los documentos",
     _NO + "Añadir o modificar valores de los catálogos"),
    ("PATCH|DELETE", rf"^/api/v1/dependencias/{_ID}$",
     "la estructura de dependencias decide a quién le llegan los documentos",
     _NO + "Añadir o modificar valores de los catálogos"),
    # --- Credenciales de buzón y canales de salida ------------------------------
    ("GET|POST", r"^/api/v1/email/apps-oauth$",
     "configurar la aplicación OAuth de la entidad es mover credenciales",
     _NO + "Configurar los buzones de correo"),
    ("POST", r"^/api/v1/email/cuentas$",
     "dar de alta un buzón es mover credenciales",
     _NO + "Configurar los buzones de correo"),
    ("POST", rf"^/api/v1/email/cuentas/{_ID}/(probar|oauth/iniciar|oauth/canjear)$",
     "probar, iniciar o canjear la autorización de un buzón mueve credenciales",
     _NO + "Configurar los buzones de correo"),
    ("POST", r"^/api/v1/webhooks$",
     "abre un canal de salida permanente hacia un tercero",
     _NO + "Dar de alta o retirar un canal de salida"),
    ("DELETE", rf"^/api/v1/webhooks/{_ID}$",
     "retira un canal de salida configurado por la entidad",
     _NO + "Dar de alta o retirar un canal de salida"),
    # --- Binarios: no caben en el resultado de una herramienta ------------------
    ("GET", rf"^/api/v1/documents/{_ID}/anexos/{_ID}/download$",
     "descarga de binario por su ruta mediada; no se devuelve como resultado de una herramienta",
     _FLUJOS_MD + " (Qué queda FUERA: descargas de binarios)"),
    ("GET", rf"^/api/v1/expedientes/{_ID}/export\.zip$",
     "descarga de un artefacto binario, no una consulta",
     _FLUJOS_MD + " (F4: exportar el ZIP son descargas de artefactos)"),
)

# Entradas de `NO_OFRECIDAS` que NO se pueden expresar como ruta: la decisión
# vive en un parámetro o en una condición de la cuenta y no en un endpoint.
# Declararlas aquí evita que el guardián las dé por «sin representar».
NO_OFRECIDAS_SIN_RUTA: dict[str, str] = {
    "Trabajar un buzón PERSONAL": (
        "es un parámetro (`password`) de rutas que SÍ se ofrecen "
        "(`sincronizar_buzon`, `radicar_correo`), no un endpoint propio"
    ),
}


def esta_excluida(metodo: str, ruta: str) -> tuple[str, str, str] | None:
    """(motivo, referencia, patrón) de la exclusión que cubre la operación, o None."""
    plana = ruta.rstrip("/") or "/"
    for m, patron, motivo, ref in EXCLUIDAS_A_PROPOSITO:
        if re.fullmatch(m, metodo) and re.search(patron, plana):
            return motivo, ref, patron
    return None


def normalizar(ruta: str) -> str:
    """`{lo_que_sea}` → `{x}` y sin barra final: dos plantillas que nombran su
    parámetro distinto son la MISMA ruta, y compararlas literalmente daría un
    hueco donde hay cobertura."""
    return re.sub(r"\{[^}]+\}", "{x}", ruta.rstrip("/")) or "/"


def _de_un_esquema(datos: dict) -> list[tuple[str, str, str]]:
    salida = []
    for ruta, item in datos.get("paths", {}).items():
        # El agregador duplica el prefijo: /api/v1/<servicio>/api/v1/<ruta real>.
        m = re.match(r"^/api/v1/[a-z-]+(/api/v1/.*)$", ruta)
        real = m.group(1) if m else ruta
        for metodo, op in item.items():
            if metodo in ("get", "post", "put", "patch", "delete"):
                salida.append((metodo.upper(), real, op.get("summary") or ""))
    return salida


def operaciones(openapi: pathlib.Path) -> list[tuple[str, str, str]]:
    """Las operaciones del API, de UN agregado o de un DIRECTORIO de esquemas.

    El directorio existe porque el agregado del gateway tiene un punto ciego: no
    compone la documentación de knowledge, signature ni del propio mcp-server.
    Y un censo ciego a un servicio no informa de menos — informa MAL: el circuito
    de firma entero, trece operaciones, no aparecía como hueco sino que **no
    aparecía**, y la tabla publicaba un porcentaje de cobertura calculado sobre
    un universo que no lo incluía. Eso es peor que no tener censo, porque nadie
    desconfía de un número.

    Se descubrió cruzando el catálogo contra los esquemas de cada servicio por
    separado: 391 operaciones frente a las 367 que veía el agregado.
    """
    if openapi.is_dir():
        vistos: dict[tuple[str, str], tuple[str, str, str]] = {}
        for f in sorted(openapi.glob("*.json")):
            for op in _de_un_esquema(json.loads(f.read_text(encoding="utf-8"))):
                vistos[(op[0], op[1])] = op
        return list(vistos.values())
    return _de_un_esquema(json.loads(openapi.read_text(encoding="utf-8")))


def cubiertas() -> set[tuple[str, str]]:
    texto = CATALOGO.read_text(encoding="utf-8")
    inicio = texto.index("CATALOG: tuple[Tool, ...] = (")
    bloque = texto[inicio : texto.index("\n)\n", inicio)]
    # Entre `method=` y `path=` puede haber comentarios: `consultar_aip` y
    # `eventos_preservacion` explican ahí por qué apuntan a la ruta mediada, y la
    # versión que exigía las dos líneas contiguas las contaba como hueco.
    pares = re.findall(r'method="([A-Z]+)",\s*(?:#[^\n]*\s*)*path="([^"]+)"', bloque)
    return {(m, normalizar(p)) for m, p in pares}


def flujo_de(ruta: str) -> str:
    for nombre, patrones in FLUJOS:
        if any(re.search(p, ruta) for p in patrones):
            return nombre
    return "SIN CLASIFICAR"


def _elegido(nombre: str, seleccion: str) -> bool:
    """`F1` es F1 y no F10..F13: se compara el código entero, no el prefijo del texto."""
    codigo = nombre.split()[0].lower()
    sel = seleccion.lower()
    return sel == codigo or (len(sel) > 3 and nombre.lower().startswith(sel))


def main() -> int:
    ap = argparse.ArgumentParser(description="Censo del API por flujos frente al catálogo MCP")
    ap.add_argument("--openapi", default="/tmp/oa.json")
    ap.add_argument("--detalle", help="Muestra todas las operaciones de un flujo (p. ej. F4)")
    ap.add_argument(
        "--huecos",
        nargs="?",
        const="*",
        help="Lista SOLO los huecos reales (sin herramienta y sin exclusión declarada) de un flujo "
        "(p. ej. F4); sin valor, de todos",
    )
    args = ap.parse_args()

    ruta_oa = pathlib.Path(args.openapi)
    if not ruta_oa.exists():
        print(
            f"No existe {ruta_oa}. Reúne los esquemas primero:\n"
            "  ./scripts/censo-esquemas.sh            # los ONCE servicios → /tmp/oa/\n"
            "  python3 scripts/censo_mcp.py --openapi /tmp/oa\n"
            "\nO, con su punto ciego declarado (sin knowledge ni signature):\n"
            "  curl -s http://localhost:19080/api/v1/openapi.json -o /tmp/oa.json",
            file=sys.stderr,
        )
        return 1

    ops = operaciones(ruta_oa)
    cub = cubiertas()
    total: collections.Counter[str] = collections.Counter()
    hechas: collections.Counter[str] = collections.Counter()
    excluidas: collections.Counter[str] = collections.Counter()
    # (método, ruta, resumen, estado) con estado en {"tool", "excluida", "hueco"}
    detalle: dict[str, list[tuple[str, str, str, str, str]]] = collections.defaultdict(list)
    contradicciones: list[str] = []
    patrones_usados: set[str] = set()

    for metodo, ruta, resumen in ops:
        f = flujo_de(ruta)
        con_tool = (metodo, normalizar(ruta)) in cub
        exc = esta_excluida(metodo, ruta)
        # SISTEMA está excluido por construcción: no es una capacidad que exponer.
        sistema = f.startswith("SISTEMA")
        if exc:
            patrones_usados.add(exc[2])
        if con_tool and (exc or sistema):
            # Lo declarado fuera y ofrecido a la vez: una de las dos afirmaciones es falsa.
            why = exc[1] if exc else "SISTEMA (sellado / no expuesto)"
            contradicciones.append(f"{metodo} {ruta}  [{why}]")
        total[f] += 1
        if con_tool:
            hechas[f] += 1
            estado = "tool"
        elif exc or sistema:
            excluidas[f] += 1
            estado = "excluida"
        else:
            estado = "hueco"
        detalle[f].append((metodo, ruta, resumen, estado, exc[0] if exc else ""))

    if args.detalle or args.huecos is not None:
        solo_huecos = args.huecos is not None
        sel = args.huecos if solo_huecos else args.detalle
        marca = {"tool": "✓", "excluida": "–", "hueco": " "}
        for nombre, _ in FLUJOS:
            if sel != "*" and not _elegido(nombre, sel):
                continue
            filas = sorted(detalle[nombre], key=lambda x: x[1])
            if solo_huecos:
                filas = [r for r in filas if r[3] == "hueco"]
            print(f"\n===== {nombre}{' · huecos reales: ' + str(len(filas)) if solo_huecos else ''} =====")
            for metodo, ruta, resumen, estado, motivo in filas:
                print(f" {marca[estado]} {metodo:6} {ruta:56} {(motivo or resumen)[:70]}")
        if not solo_huecos:
            print("\n ✓ tiene herramienta   – excluida a propósito   (vacío) hueco real")
    else:
        enc = f"{'flujo':38} {'ops':>4} {'tools':>6} {'excl.':>6} {'huecos':>7}  cobertura sobre (ops − excluidas)"
        print(enc)
        for nombre, _ in FLUJOS:
            t, c, x = total[nombre], hechas[nombre], excluidas[nombre]
            h = t - c - x
            base = t - x
            barra = "█" * int(12 * c / base) if base else ""
            pct = f"{100 * c / base:3.0f}%" if base else "  — "
            print(f"{nombre:38} {t:4} {c:6} {x:6} {h:7}  {barra:12} {pct}")
        T, C, X = sum(total.values()), sum(hechas.values()), sum(excluidas.values())
        print(f"{'TOTAL':38} {T:4} {C:6} {X:6} {T - C - X:7}  {'':12} {100 * C / (T - X) if T > X else 0:3.0f}%")
        print("\nSolo huecos reales de un flujo: python3 scripts/censo_mcp.py --openapi DIR --huecos F4")

    sin = total.get("SIN CLASIFICAR", 0)
    if sin:
        # Una operación sin flujo no es un detalle: significa que el mapa dejó de
        # describir el sistema, y un censo sobre un mapa incompleto tranquiliza
        # sin fundamento.
        print(f"\n¡{sin} operaciones SIN CLASIFICAR! Revisa las reglas de FLUJOS.")
    huerfanas = [pat for _, pat, _, _ in EXCLUIDAS_A_PROPOSITO if pat not in patrones_usados]
    if huerfanas:
        # Aviso y no error: el censo puede estar leyendo un agregado sin algún servicio.
        # El error, con el árbol completo, lo da el guardián de shared/tests.
        print(f"\nAviso: {len(huerfanas)} exclusiones no casan con ninguna operación de ESTE esquema:", file=sys.stderr)
        for pat in huerfanas:
            print(f"  {pat}", file=sys.stderr)
    if contradicciones:
        print(
            f"\nERROR: {len(contradicciones)} operaciones declaradas fuera (o selladas) que SÍ tienen herramienta:",
            file=sys.stderr,
        )
        for c in contradicciones:
            print(f"  {c}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
