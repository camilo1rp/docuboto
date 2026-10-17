# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`atencion_publica`: qué se sirve SIN autenticar según el estado (N1, ADR-035)."""

import pytest
from orpycamcp_common.tenant import AtencionPublica, atencion_publica


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
        (_fila(), AtencionPublica.COMPLETA),
        (_fila(status="suspended"), AtencionPublica.SOLO_LECTURA),
        (_fila(status="inactive"), AtencionPublica.NINGUNA),
        (None, AtencionPublica.NINGUNA),
        (_fila(prov="pending"), AtencionPublica.NINGUNA),
        (_fila(status="suspended", prov="pending"), AtencionPublica.NINGUNA),
        (_fila(status="suspended", schema=False), AtencionPublica.NINGUNA),
        (_fila(prov="provisioning_failed"), AtencionPublica.NINGUNA),
        (_fila(schema=False), AtencionPublica.NINGUNA),
    ],
)
async def test_atencion_por_fila(fila, esperado) -> None:
    assert await atencion_publica(_Conn(fila), "demo") is esperado


async def test_un_slug_mal_formado_no_consulta_y_no_se_atiende() -> None:
    conn = _Conn(_fila())
    assert await atencion_publica(conn, 'x"; --') is AtencionPublica.NINGUNA
    assert conn.consultas == 0


async def test_no_captura_el_fallo_del_registro() -> None:
    with pytest.raises(RuntimeError):
        await atencion_publica(_Conn(error=RuntimeError("caído")), "demo")
