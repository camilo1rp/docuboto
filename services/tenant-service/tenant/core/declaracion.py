# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Qué tiene que nombrar la declaración del administrador (ADR-045 §4).

Los nombres salen de la vista que devuelve knowledge-service, ya comprobada.
No hay un nombre de proveedor escrito en este módulo.
"""

from __future__ import annotations

from typing import Any

CODIGOS = {1: "public", 2: "reserved", 3: "classified"}


def fila_de(vista: dict[str, Any], nivel: int) -> dict[str, Any] | None:
    """La ruta de ese nivel, o None si el proveedor no la declara."""
    codigo = CODIGOS.get(nivel)
    if codigo is None:
        return None
    for fila in vista.get("routing") or []:
        if isinstance(fila, dict) and fila.get("level") == codigo:
            return fila
    return None


def exigidos_proveedor(vista: dict[str, Any]) -> list[str]:
    """Proveedor y cada subencargado, id y nombre, tal como vienen."""
    provider = vista["provider"]
    piezas = [provider["name"], provider["id"]]
    for sub in vista.get("subprocessors") or []:
        piezas.extend((sub["name"], sub["id"]))
    return _unicos(piezas)


def exigidos_nivel(vista: dict[str, Any], nivel: int) -> list[str] | None:
    """Ruta, modelos y subencargados de un nivel. None si no está declarado."""
    fila = fila_de(vista, nivel)
    if fila is None:
        return None
    nombres = {sub["id"]: sub["name"] for sub in vista.get("subprocessors") or []}
    piezas = [fila["hosting"], *fila["model_aliases"]]
    for sid in fila["subprocessors"]:
        piezas.append(sid)
        if sid in nombres:
            piezas.append(nombres[sid])
    return _unicos(piezas)


def faltan(texto: str, exigidos: list[str]) -> list[str]:
    """Los nombres que la declaración no contiene, en el orden exigido."""
    return [pieza for pieza in exigidos if pieza not in texto]


def ruta_admitida(fila: dict[str, Any]) -> bool:
    """Un modelo alojado solo vale si no retiene ni entrena con los datos."""
    if fila.get("hosting") == "installation":
        return True
    return (
        fila.get("prompt_and_output_retention") == "none"
        and fila.get("trains_on_customer_data") is False
    )


def _unicos(piezas: list[str]) -> list[str]:
    salida: list[str] = []
    for pieza in piezas:
        if pieza and pieza not in salida:
            salida.append(pieza)
    return salida
