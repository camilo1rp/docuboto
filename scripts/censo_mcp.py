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
    python3 scripts/censo_mcp.py --openapi /tmp/oa [--detalle F4]
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
    # OpenAPI agregado y la superficie del propio mcp-server —`/mcp/*` y
    # `/assistant/*`— entran aquí. No son capacidades que exponer: el comodín ES
    # la puerta por la que pasan todas las demás, y contarlo como operación
    # pendiente sería contar el pasillo como si fuera una habitación. Aparecieron
    # al quitarle al censo su punto ciego: antes no se veían, y el script las
    # dejaba «SIN CLASIFICAR», que es su forma de pedir una decisión.
    ("SISTEMA (sellado / no expuesto)", (r"/internal/", r"-internal$", r"^/api/v1/tenants", r"/health", r"^/api/v1/public/", r"^/api/v1/storage/", r"^/api/v1/\{path\}$", r"^/api/v1/openapi\.json$", r"^/api/v1/mcp/", r"^/api/v1/assistant/")),
    ("F1 Ventanilla y radicación", (r"^/api/v1/documents", r"^/api/v1/terceros", r"^/api/v1/anulaciones", r"^/api/v1/batch/documents", r"^/api/v1/import", r"^/api/v1/export")),
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
    pares = re.findall(r'method="([A-Z]+)",\s*\n\s*path="([^"]+)"', bloque)
    return {(m, normalizar(p)) for m, p in pares}


def flujo_de(ruta: str) -> str:
    for nombre, patrones in FLUJOS:
        if any(re.search(p, ruta) for p in patrones):
            return nombre
    return "SIN CLASIFICAR"


def main() -> int:
    ap = argparse.ArgumentParser(description="Censo del API por flujos frente al catálogo MCP")
    ap.add_argument("--openapi", default="/tmp/oa.json")
    ap.add_argument("--detalle", help="Muestra las operaciones de un flujo (por su prefijo, p. ej. F4)")
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
    detalle: dict[str, list[tuple[str, str, str, bool]]] = collections.defaultdict(list)

    for metodo, ruta, resumen in ops:
        f = flujo_de(ruta)
        esta = (metodo, normalizar(ruta)) in cub
        total[f] += 1
        hechas[f] += int(esta)
        detalle[f].append((metodo, ruta, resumen, esta))

    if args.detalle:
        for nombre, _ in FLUJOS:
            if not nombre.startswith(args.detalle):
                continue
            print(f"\n===== {nombre} =====")
            for metodo, ruta, resumen, esta in sorted(detalle[nombre], key=lambda x: x[1]):
                print(f" {'✓' if esta else ' '} {metodo:6} {ruta:56} {resumen[:70]}")
        return 0

    print(f"{'flujo':38} {'ops':>4} {'tools':>6}  cobertura")
    for nombre, _ in FLUJOS:
        t, c = total[nombre], hechas[nombre]
        barra = "█" * int(12 * c / t) if t else ""
        print(f"{nombre:38} {t:4} {c:6}  {barra:12} {(100 * c / t) if t else 0:3.0f}%")
    print(f"{'TOTAL':38} {sum(total.values()):4} {sum(hechas.values()):6}")
    sin = total.get("SIN CLASIFICAR", 0)
    if sin:
        # Una operación sin flujo no es un detalle: significa que el mapa dejó de
        # describir el sistema, y un censo sobre un mapa incompleto tranquiliza
        # sin fundamento.
        print(f"\n¡{sin} operaciones SIN CLASIFICAR! Revisa las reglas de FLUJOS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
