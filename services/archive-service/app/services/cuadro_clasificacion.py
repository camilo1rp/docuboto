# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cuadro de clasificación documental DERIVADO (C-4/C8, ADR-040).

Fondo > sección > subsección > serie > subserie. Nada de esto se captura dos
veces: la estructura es el organigrama (E14, ``dependencias``), las series son
la TRD vigente y lo único que las une es la dependencia productora que declara
cada versión de la serie.

``armar_cuadro`` es una función PURA (sin BD, sin reloj): recibe lo que el
servicio leyó en una sola transacción y devuelve la respuesta. Así se prueba
cada regla de derivación sin levantar nada, y el servicio solo consulta.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from app.schemas.archive import (
    CuadroAviso,
    CuadroClasificacionResponse,
    CuadroFondo,
    CuadroNodo,
    CuadroSerie,
    CuadroSinSeccion,
    CuadroSubserie,
)

#: Estados de un instrumento que aún no gobierna (en trámite). Las subseries se
#: agrupan con la serie de su MISMA clase: una subserie vigente no cuelga de un
#: borrador de su serie ni al revés.
_EN_TRAMITE = frozenset({"borrador", "aprobada"})


class OrganigramaConCicloError(Exception):
    """El organigrama tiene un ciclo (A -> B -> A): no hay raíz que derive un cuadro."""

    def __init__(self, codigos: Sequence[int]) -> None:
        super().__init__(f"organigrama con ciclo: {sorted(codigos)}")
        self.codigos = sorted(codigos)


class OrganigramaDemasiadoProfundoError(Exception):
    """Una cadena de `parent_id` supera el tope de profundidad sin llegar a la raíz."""

    def __init__(self, codigos: Sequence[int]) -> None:
        super().__init__(f"organigrama demasiado profundo: {sorted(codigos)}")
        self.codigos = sorted(codigos)


def nodos_desde_arbol(
    filas: Sequence[Mapping[str, Any]], *, profundidad_max: int
) -> dict[int, dict[str, Any]]:
    """Convierte la subida por el organigrama (una fila por nodo y ancestro, ordenadas
    por origen y profundidad) en ``{codigo: {codigo, sigla, nombre, activo, ruta}}``.

    Un ciclo o una cadena que no llega a la raíz NO se trunca ni se omite: se lanza,
    porque un cuadro sin esas ramas parecería un cuadro sin ellas."""
    cadenas: dict[int, list[Mapping[str, Any]]] = {}
    for f in filas:
        cadenas.setdefault(int(f["origen_codigo"]), []).append(f)

    ciclos: list[int] = []
    profundos: list[int] = []
    nodos: dict[int, dict[str, Any]] = {}
    for origen, cadena in cadenas.items():
        cadena = sorted(cadena, key=lambda r: r["prof"])
        if any(r["es_ciclo"] for r in cadena):
            ciclos.append(origen)
            continue
        if cadena[-1]["tiene_padre"]:
            # Terminó sin ciclo pero con padre pendiente: se cortó por el tope.
            profundos.append(origen)
            continue
        propio = cadena[0]
        nodos[origen] = {
            "codigo": origen,
            "sigla": propio["sigla"],
            "nombre": propio["nombre"],
            "activo": propio["activo"],
            "ruta": [
                {"codigo": int(r["codigo"]), "sigla": r["sigla"], "nombre": r["nombre"]}
                for r in reversed(cadena)
            ],
        }
    if ciclos:
        raise OrganigramaConCicloError(ciclos)
    if profundos:
        raise OrganigramaDemasiadoProfundoError(profundos)
    return nodos


def _codigos(ruta: Sequence[Mapping[str, Any]] | None) -> list[int] | None:
    if ruta is None:
        return None
    return [int(n["codigo"]) for n in ruta]


def armar_cuadro(
    *,
    series: Sequence[Mapping[str, Any]],
    subseries: Sequence[Mapping[str, Any]],
    productoras: Mapping[uuid.UUID, Sequence[Mapping[str, Any]]],
    nodos: Mapping[int, Mapping[str, Any]],
    fondo: Mapping[str, Any],
    generado_at: datetime,
    incluir_vacias: bool = False,
    segun: str = "convalidado",
) -> CuadroClasificacionResponse:
    """Arma el cuadro.

    ``series``/``subseries``: filas de ``trd_series`` ya filtradas por vigencia
    (las subseries llevan ``parent_code``: se agrupan por el CÓDIGO del padre, no
    por ``parent_id``, que apunta a la fila-versión que era vigente al crearlas).
    ``productoras``: por ``trd_serie_id``, ``[{dependencia_codigo, ruta_convalidada}]``.
    ``nodos``: por código de dependencia, ``{codigo, sigla, nombre, activo, ruta}``
    con ``ruta`` de la raíz a la propia dependencia (inclusive).

    ``segun`` decide DÓNDE se ubica una serie con instantánea: ``convalidado`` (por omisión) la
    pone en la ruta que fijó la convalidación —lo que el Consejo aprobó—, y la ruta viva solo se
    dice como aviso; ``vivo`` la pone en la ruta vigente del organigrama. Sin instantánea
    (``migrada``/``borrador``) no hay otra cosa que la ruta viva.
    """
    secciones: list[CuadroNodo] = []
    # Clave = el CAMINO (códigos de la raíz al nodo), no el código: una reorganización puede dejar
    # la misma dependencia bajo dos padres (la ruta convalidada de unas series y la viva de otras).
    por_camino: dict[tuple[int, ...], CuadroNodo] = {}
    sin_seccion: list[CuadroSinSeccion] = []
    # Avisos acumulados: tipo -> dependencia -> series
    inactivas: dict[int, set[str]] = {}
    reorganizadas: dict[int, set[str]] = {}
    desconocidas: dict[int, set[str]] = {}

    def nodo_de(ruta: Sequence[Mapping[str, Any]]) -> CuadroNodo:
        """Crea (o recupera) la cadena de nodos de una ruta y devuelve el último."""
        padre: CuadroNodo | None = None
        camino: tuple[int, ...] = ()
        for i, paso in enumerate(ruta):
            codigo = int(paso["codigo"])
            camino = (*camino, codigo)
            nodo = por_camino.get(camino)
            if nodo is None:
                dato = nodos.get(codigo, paso)
                nodo = CuadroNodo(
                    codigo=codigo,
                    sigla=dato.get("sigla"),
                    nombre=str(dato["nombre"]),
                    activo=bool(dato.get("activo", True)),
                    nivel="seccion" if i == 0 else "subseccion",
                )
                por_camino[camino] = nodo
                (secciones if padre is None else padre.subsecciones).append(nodo)
            padre = nodo
        assert padre is not None  # una ruta vacía es un dato corrupto, no un caso
        return padre

    if incluir_vacias:
        for dato in nodos.values():
            nodo_de(dato["ruta"])

    # Subseries agrupadas por (código del padre, clase de vigencia).
    hijas: dict[tuple[str, bool], list[CuadroSubserie]] = {}
    for ss in subseries:
        clave = (str(ss["parent_code"]), ss["estado"] in _EN_TRAMITE)
        hijas.setdefault(clave, []).append(
            CuadroSubserie(
                code=ss["code"],
                name=ss["name"],
                version=ss["version"],
                estado=ss["estado"],
                disposition=ss["disposition"],
                archivo_gestion_years=ss.get("archivo_gestion_years"),
                archivo_central_years=ss.get("archivo_central_years"),
            )
        )
    padres_vistos: set[tuple[str, bool]] = set()

    for s in sorted(series, key=lambda r: (r["code"], r["version"])):
        clave_hijas = (str(s["code"]), s["estado"] in _EN_TRAMITE)
        padres_vistos.add(clave_hijas)
        mis_hijas = sorted(hijas.get(clave_hijas, []), key=lambda h: h.code)
        declaradas = list(productoras.get(s["id"], []))
        validas = [p for p in declaradas if int(p["dependencia_codigo"]) in nodos]
        for p in declaradas:
            if int(p["dependencia_codigo"]) not in nodos:
                desconocidas.setdefault(int(p["dependencia_codigo"]), set()).add(s["code"])

        if not declaradas:
            sin_seccion.append(_sin_seccion(s, "sin_productora"))
            continue
        if not validas:
            sin_seccion.append(_sin_seccion(s, "productora_desconocida"))
            continue

        for p in validas:
            cod = int(p["dependencia_codigo"])
            dato = nodos[cod]
            ruta_viva = dato["ruta"]
            congelada = p.get("ruta_convalidada")
            instantanea = _codigos(congelada)
            difiere = None if instantanea is None else instantanea != _codigos(ruta_viva)
            ubicacion = congelada if (segun == "convalidado" and congelada) else ruta_viva
            if difiere:
                reorganizadas.setdefault(cod, set()).add(s["code"])
            if not dato.get("activo", True):
                inactivas.setdefault(cod, set()).add(s["code"])
            nodo_de(ubicacion).series.append(
                CuadroSerie(
                    code=s["code"],
                    name=s["name"],
                    version=s["version"],
                    estado=s["estado"],
                    disposition=s["disposition"],
                    archivo_gestion_years=s.get("archivo_gestion_years"),
                    archivo_central_years=s.get("archivo_central_years"),
                    compartida=len(validas) > 1,
                    ruta_difiere_de_la_convalidada=difiere,
                    subseries=[h.model_copy() for h in mis_hijas],
                )
            )

    # Subseries cuyo padre no está en el cuadro: no se omiten, se dicen.
    for clave, huerfanas in sorted(hijas.items()):
        if clave in padres_vistos:
            continue
        for h in sorted(huerfanas, key=lambda x: x.code):
            sin_seccion.append(
                CuadroSinSeccion(
                    code=h.code,
                    name=h.name,
                    version=h.version,
                    estado=h.estado,
                    motivo="serie_padre_no_vigente",
                )
            )

    _ordenar(secciones)

    avisos: list[CuadroAviso] = []
    if not (fondo.get("nombre") or "").strip():
        avisos.append(CuadroAviso(tipo="entidad_sin_nombre"))
    for cod in sorted(inactivas):
        avisos.append(
            CuadroAviso(tipo="productora_inactiva", dependencia_codigo=cod, series=sorted(inactivas[cod]))
        )
    for cod in sorted(reorganizadas):
        avisos.append(
            CuadroAviso(
                tipo="organigrama_reorganizado_tras_convalidacion",
                dependencia_codigo=cod,
                series=sorted(reorganizadas[cod]),
            )
        )
    for cod in sorted(desconocidas):
        avisos.append(
            CuadroAviso(tipo="productora_desconocida", dependencia_codigo=cod, series=sorted(desconocidas[cod]))
        )

    nombre = (fondo.get("nombre") or "").strip() or None
    sigla = (fondo.get("sigla") or "").strip() or None
    return CuadroClasificacionResponse(
        fondo=CuadroFondo(nombre=nombre, sigla=sigla),
        secciones=secciones,
        sin_seccion=sin_seccion,
        avisos=avisos,
        generado_at=generado_at,
    )


def _sin_seccion(s: Mapping[str, Any], motivo: str) -> CuadroSinSeccion:
    return CuadroSinSeccion(
        code=s["code"],
        name=s["name"],
        version=s["version"],
        estado=s["estado"],
        motivo=motivo,
    )


def _ordenar(nodos: list[CuadroNodo]) -> None:
    nodos.sort(key=lambda n: n.codigo)
    for n in nodos:
        n.series.sort(key=lambda x: (x.code, x.version))
        _ordenar(n.subsecciones)
