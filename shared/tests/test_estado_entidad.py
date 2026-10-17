# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""`orpycamcp_common.tenant.estado_entidad` / `veredicto_entidad` (B9-1, ADR-035)."""

import pytest
from orpycamcp_common.tenant import (
    ERROR_ESTADO_NO_DISPONIBLE,
    ERRORES_ENTIDAD,
    EstadoEntidad,
    estado_entidad,
    veredicto_entidad,
)


class _Conn:
    def __init__(self, fila=None, error: Exception | None = None) -> None:
        self.fila, self.error, self.consultas = fila, error, 0

    async def fetchrow(self, sql: str, *args: object):
        self.consultas += 1
        if self.error:
            raise self.error
        return self.fila


def _fila(status="active", prov="active", schema=True) -> dict:
    return {"status": status, "provisioning_status": prov, "hay_schema": schema}


@pytest.mark.parametrize(
    ("fila", "esperado"),
    [
        (_fila(), EstadoEntidad.OPERATIVA),
        (_fila(status="suspended"), EstadoEntidad.INACTIVA),
        (_fila(status="inactive"), EstadoEntidad.INACTIVA),
        (None, EstadoEntidad.INACTIVA),
        (_fila(prov="pending"), EstadoEntidad.NO_PROVISIONADA),
        (_fila(prov="provisioning_failed"), EstadoEntidad.NO_PROVISIONADA),
        (_fila(schema=False), EstadoEntidad.NO_PROVISIONADA),
    ],
)
async def test_estado_por_fila(fila, esperado) -> None:
    assert await estado_entidad(_Conn(fila), "demo") is esperado


async def test_un_slug_mal_formado_es_inactivo_sin_consultar() -> None:
    conn = _Conn(_fila())
    assert await estado_entidad(conn, 'x"; --') is EstadoEntidad.INACTIVA
    assert conn.consultas == 0


async def test_estado_entidad_no_captura_el_fallo_del_registro() -> None:
    with pytest.raises(RuntimeError):
        await estado_entidad(_Conn(error=RuntimeError("caído")), "demo")


async def test_veredicto() -> None:
    assert await veredicto_entidad(_Conn(_fila()), "demo") is None
    assert await veredicto_entidad(_Conn(_fila(status="suspended")), "demo") == ERRORES_ENTIDAD[
        EstadoEntidad.INACTIVA
    ]
    assert (
        await veredicto_entidad(_Conn(error=RuntimeError("x")), "demo")
        == ERROR_ESTADO_NO_DISPONIBLE
    )
    assert ERROR_ESTADO_NO_DISPONIBLE[0] == 503
