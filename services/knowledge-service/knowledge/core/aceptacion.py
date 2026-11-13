# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Declaración que el administrador acepta, y cuándo se levanta la suspensión.

La huella se vuelve a comprobar aquí. No se fía de la que trae tenant-service.
La suspensión se levanta solo cuando cada nivel habilitado tiene esa huella.
Un nivel que la entidad no transmite no cuenta, aunque quede una fila vieja.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, TypeGuard

from knowledge.core import paso
from knowledge.core.config import settings
from knowledge.core.firma_capabilities import verificar

_SHA = re.compile(r"^[0-9a-f]{64}$")
_HOSTING = frozenset({"installation", "hosted"})
_RETENCION = frozenset({"none", "limited"})
_NIVELES = {"public": 1, "reserved": 2, "classified": 3}
_CODIGO = {numero: codigo for codigo, numero in _NIVELES.items()}
_KID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_GUARDAR = """
INSERT INTO intelligence_level_acceptance (level, capabilities_sha256)
VALUES ($1, $2)
ON CONFLICT (level) DO UPDATE
   SET capabilities_sha256 = EXCLUDED.capabilities_sha256,
       accepted_at = now()
"""

_NIVELES_SQL = """
SELECT intelligence_levels
  FROM public.tenants
 WHERE slug = $1
"""

_ACEPTADOS = """
SELECT level, capabilities_sha256
  FROM intelligence_level_acceptance
"""

_FIJAR = """
UPDATE intelligence_state
   SET capabilities_sha256 = $1,
       send_suspended = false,
       updated_at = now()
 WHERE id = 1
"""

_OLVIDAR_NIVEL = """
DELETE FROM intelligence_level_acceptance
 WHERE level = ANY($1::smallint[])
"""


@dataclass(frozen=True)
class Declaracion:
    ok: bool
    motivo: str
    vista: dict[str, Any] | None


def vista(statement: dict[str, Any]) -> dict[str, Any] | None:
    """La parte que el administrador tiene que nombrar. None si la forma no cuadra."""
    provider = statement.get("provider")
    if not isinstance(provider, dict):
        return None
    identificador = provider.get("id")
    nombre = provider.get("name")
    if not _texto(identificador, 64) or not _texto(nombre, 200):
        return None
    crudos = statement.get("subprocessors")
    rutas = statement.get("routing")
    if not isinstance(crudos, list) or not isinstance(rutas, list):
        return None
    subencargados: list[dict[str, str]] = []
    ids: set[str] = set()
    for fila in crudos:
        if not isinstance(fila, dict):
            return None
        sid = fila.get("id")
        sname = fila.get("name")
        if not _texto(sid, 64) or not _texto(sname, 200) or sid in ids:
            return None
        ids.add(sid)
        subencargados.append({"id": sid, "name": sname})
    routing: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for fila in rutas:
        normal = _ruta(fila, ids, vistos)
        if normal is None:
            return None
        routing.append(normal)
    return {
        "provider": {"id": identificador, "name": nombre},
        "subprocessors": subencargados,
        "routing": routing,
    }


def rutas_de(normal: dict[str, Any], habilitados: Iterable[int]) -> list[dict[str, Any]]:
    """La ruta de cada nivel que la entidad transmite, con el nombre del subencargado.

    Un nivel habilitado que la declaración no trae no aparece: no hay ruta que nombrar.
    """
    permitidos: set[int] = set()
    for valor in habilitados:
        try:
            numero = int(valor)
        except (TypeError, ValueError):
            continue
        if numero in (1, 2, 3):
            permitidos.add(numero)
    crudos = normal.get("subprocessors")
    rutas = normal.get("routing")
    if not isinstance(crudos, list) or not isinstance(rutas, list):
        return []
    nombres: dict[str, str] = {}
    for fila in crudos:
        if isinstance(fila, dict) and isinstance(fila.get("id"), str):
            nombres[fila["id"]] = str(fila.get("name") or "")
    salida: list[dict[str, Any]] = []
    for fila in rutas:
        if not isinstance(fila, dict):
            return []
        nivel = _NIVELES.get(str(fila.get("level")))
        if nivel is None or nivel not in permitidos:
            continue
        alias = fila.get("model_aliases")
        sub_ids = fila.get("subprocessors")
        hosting = fila.get("hosting")
        if hosting not in _HOSTING or not isinstance(alias, list) or not alias:
            return []
        if not isinstance(sub_ids, list):
            return []
        subencargados: list[dict[str, str]] = []
        for sid in sub_ids:
            nombre = nombres.get(sid) if isinstance(sid, str) else None
            if not nombre:
                return []
            subencargados.append({"id": sid, "name": nombre})
        salida.append(
            {
                "level": nivel,
                "hosting": hosting,
                "model_aliases": list(alias),
                "subprocessors": subencargados,
            }
        )
    return salida


async def leer(conn: Any, slug: str, cliente: Any) -> Declaracion:
    """Comprueba la firma y devuelve la vista. No transmite registros."""
    recibido = await paso.leer_declaracion(conn, slug, cliente)
    if not recibido.sale or not isinstance(recibido.cuerpo, dict):
        return Declaracion(False, recibido.motivo, None)
    pem, kid = _clave()
    if pem is None or kid is None:
        return Declaracion(False, "clave", None)
    veredicto = verificar(recibido.cuerpo, pem=pem, kid=kid)
    if not veredicto.ok or veredicto.huella is None:
        return Declaracion(False, veredicto.motivo, None)
    statement = recibido.cuerpo.get("statement")
    normal = vista(statement) if isinstance(statement, dict) else None
    if normal is None:
        return Declaracion(False, "forma", None)
    normal["sha256"] = veredicto.huella
    return Declaracion(True, "ok", normal)


async def aceptar(conn: Any, slug: str, cliente: Any, *, nivel: int, sha256: str) -> str:
    """Registra la huella del nivel. Vacío si quedó. Si no, el motivo.

    Lee los niveles ya confirmados. Quien llama tiene que haber confirmado
    el cambio de columna antes: esta conexión no ve una escritura ajena
    todavía abierta.
    """
    if nivel not in _CODIGO or not _SHA.fullmatch(sha256):
        return "nivel"
    declaracion = await leer(conn, slug, cliente)
    if not declaracion.ok or declaracion.vista is None:
        return declaracion.motivo
    if declaracion.vista["sha256"] != sha256:
        return "distinta"
    codigo = _CODIGO[nivel]
    if not any(fila["level"] == codigo for fila in declaracion.vista["routing"]):
        return "nivel"
    async with conn.transaction():
        await conn.execute(_GUARDAR, nivel, sha256)
        if await _todas(conn, slug, sha256):
            await conn.execute(_FIJAR, sha256)
    return ""


async def olvidar(conn: Any, niveles: list[int]) -> None:
    """Quita la aceptación de los niveles que la entidad ya no transmite."""
    if niveles:
        await conn.execute(_OLVIDAR_NIVEL, niveles)


def _ruta(fila: Any, ids: set[str], vistos: set[str]) -> dict[str, Any] | None:
    if not isinstance(fila, dict):
        return None
    nivel = fila.get("level")
    hosting = fila.get("hosting")
    aliases = fila.get("model_aliases")
    sub_ids = fila.get("subprocessors")
    retencion = fila.get("prompt_and_output_retention")
    entrena = fila.get("trains_on_customer_data")
    if nivel not in _NIVELES or nivel in vistos:
        return None
    if hosting not in _HOSTING or retencion not in _RETENCION or not isinstance(entrena, bool):
        return None
    if not isinstance(aliases, list) or not aliases or not all(_texto(a, 128) for a in aliases):
        return None
    if not isinstance(sub_ids, list) or not all(isinstance(s, str) and s in ids for s in sub_ids):
        return None
    vistos.add(nivel)
    return {
        "level": nivel,
        "hosting": hosting,
        "model_aliases": list(aliases),
        "subprocessors": list(sub_ids),
        "prompt_and_output_retention": retencion,
        "trains_on_customer_data": entrena,
    }


def _texto(valor: Any, tope: int) -> TypeGuard[str]:
    return isinstance(valor, str) and 1 <= len(valor) <= tope and valor.strip() == valor


def _clave() -> tuple[str | None, str | None]:
    pem = settings.ppi_capabilities_public_key.strip().replace("\\n", "\n")
    kid = settings.ppi_capabilities_kid.strip()
    if not pem or not _KID.fullmatch(kid):
        return None, None
    return pem, kid


async def _todas(conn: Any, slug: str, sha256: str) -> bool:
    fila = await conn.fetchrow(_NIVELES_SQL, slug)
    if fila is None:
        return False
    habilitados = {int(nivel) for nivel in (fila["intelligence_levels"] or [])}
    if not habilitados:
        return False
    filas = await conn.fetch(_ACEPTADOS)
    aceptados = {int(item["level"]): item["capabilities_sha256"] for item in filas}
    return all(aceptados.get(nivel) == sha256 for nivel in habilitados)
