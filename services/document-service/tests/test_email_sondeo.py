# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Sondeo programado de buzones (E19 Increment 3).

Se prueba la lógica de decisión —a quién le toca, cuánto se espera tras un fallo,
qué cuentas quedan fuera— que es donde un error se manifiesta como «el correo no
llega» y nadie sabe por qué.
"""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.services.email_cuenta_service import ErrorDeCuenta
from app.services.email_sondeo import (
    ESPERA_BASE_SEGUNDOS,
    ESPERA_MAXIMA_SEGUNDOS,
    EstadoCuenta,
    SondeoDeCorreo,
)

RESPONSABLE = uuid.uuid4()


def _cuenta(**kw):
    base = {
        "id": uuid.uuid4(),
        "nombre": "Ventanilla",
        "modo": "asistida",
        "responsable_user_id": None,
        "carpeta_origen": "INBOX",
    }
    base.update(kw)
    return base


class ConexionFalsa:
    """Doble de `asyncpg.Connection` que registra los locks pedidos."""

    def __init__(self, cuentas, *, lock_concedido=True):
        self._cuentas = cuentas
        self.lock_concedido = lock_concedido
        self.locks_pedidos = 0
        self.locks_soltados = 0

    async def fetch(self, *a, **kw):
        return self._cuentas

    async def fetchval(self, consulta, *a):
        if "advisory_lock" in consulta:
            self.locks_pedidos += 1
            return self.lock_concedido
        return None

    async def execute(self, consulta, *a):
        if "advisory_unlock" in consulta:
            self.locks_soltados += 1
        return "OK"


def test_la_espera_crece_pero_no_sin_limite() -> None:
    """Una cuenta caída no debe golpearse cada minuto, ni dejar de intentarse.

    Sin tope, un buzón mal configurado seguiría llamando eternamente y podría
    hacer que el proveedor bloquee la cuenta. Sin reintento, se arreglaría la
    configuración y nadie volvería a probar hasta reiniciar el servicio.
    """
    estado = EstadoCuenta()
    esperas = []
    for _ in range(12):
        estado.fallos += 1
        esperas.append(estado.espera())

    assert esperas[0] == ESPERA_BASE_SEGUNDOS * 2
    assert esperas == sorted(esperas), "la espera debe crecer, no oscilar"
    assert max(esperas) == ESPERA_MAXIMA_SEGUNDOS
    assert esperas[-1] == ESPERA_MAXIMA_SEGUNDOS


@pytest.mark.asyncio
async def test_un_fallo_aplaza_la_cuenta_y_el_exito_lo_reinicia(monkeypatch) -> None:
    cuenta = _cuenta()
    conn = ConexionFalsa([cuenta])
    sondeo = SondeoDeCorreo("demo")

    fallo = AsyncMock(side_effect=ErrorDeCuenta("conexion_fallida", "no conecta", status=502))
    monkeypatch.setattr(
        "app.services.email_bandeja_service.EmailBandejaService.sincronizar", fallo
    )
    r = await sondeo.una_vuelta(conn, ahora=0.0)
    assert r.fallos == 1
    estado = sondeo._estados[cuenta["id"]]
    assert estado.fallos == 1 and estado.proximo_intento > 0

    # En la vuelta siguiente, antes de que toque, se omite sin ni siquiera pedir
    # el lock: reintentar contra un servidor caído solo llena el registro.
    locks_antes = conn.locks_pedidos
    r2 = await sondeo.una_vuelta(conn, ahora=1.0)
    assert r2.cuentas_omitidas == 1 and r2.cuentas_vistas == 0
    assert conn.locks_pedidos == locks_antes

    # Cuando toca y funciona, la espera se reinicia del todo.
    monkeypatch.setattr(
        "app.services.email_bandeja_service.EmailBandejaService.sincronizar",
        AsyncMock(return_value={"nuevos": 2, "en_bandeja": 2, "hay_mas": False, "carpeta": "INBOX"}),
    )
    r3 = await sondeo.una_vuelta(conn, ahora=estado.proximo_intento + 1)
    assert r3.nuevos == 2 and r3.fallos == 0
    assert sondeo._estados[cuenta["id"]].fallos == 0
    assert sondeo._estados[cuenta["id"]].proximo_intento == 0.0


@pytest.mark.asyncio
async def test_si_otra_replica_tiene_la_cuenta_se_pasa_de_largo(monkeypatch) -> None:
    """Sin el lock, dos réplicas descargarían el mismo buzón a la vez.

    Y se usa la variante que NO espera: con la bloqueante, una réplica atascada
    en un buzón lento dejaría a la otra parada ahí en vez de atender las demás.
    """
    conn = ConexionFalsa([_cuenta()], lock_concedido=False)
    sincronizar = AsyncMock()
    monkeypatch.setattr(
        "app.services.email_bandeja_service.EmailBandejaService.sincronizar", sincronizar
    )

    r = await SondeoDeCorreo("demo").una_vuelta(conn, ahora=0.0)
    assert r.cuentas_omitidas == 1 and r.cuentas_vistas == 0
    sincronizar.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_lock_se_suelta_aunque_la_cuenta_falle(monkeypatch) -> None:
    """Un lock que no se suelta deja la cuenta muerta para siempre en todas las
    réplicas, y el síntoma es «ese buzón dejó de traer correo»."""
    conn = ConexionFalsa([_cuenta()])
    monkeypatch.setattr(
        "app.services.email_bandeja_service.EmailBandejaService.sincronizar",
        AsyncMock(side_effect=ErrorDeCuenta("x", "y", status=502)),
    )
    await SondeoDeCorreo("demo").una_vuelta(conn, ahora=0.0)
    assert conn.locks_pedidos == 1
    assert conn.locks_soltados == 1


@pytest.mark.asyncio
async def test_una_cuenta_caida_no_impide_sondear_las_demas(monkeypatch) -> None:
    """Si el buzón de PQRS está caído, el de notificaciones judiciales entra."""
    mala, buena = _cuenta(nombre="PQRS"), _cuenta(nombre="Judicial")
    conn = ConexionFalsa([mala, buena])

    async def _sincronizar(self, cuenta_id, **kw):
        if cuenta_id == mala["id"]:
            raise ErrorDeCuenta("credenciales_rechazadas", "no", status=502)
        return {"nuevos": 3, "en_bandeja": 3, "hay_mas": False, "carpeta": "INBOX"}

    monkeypatch.setattr(
        "app.services.email_bandeja_service.EmailBandejaService.sincronizar", _sincronizar
    )
    r = await SondeoDeCorreo("demo").una_vuelta(conn, ahora=0.0)
    assert r.fallos == 1
    assert r.nuevos == 3, "la cuenta sana debía sondearse igual"
    assert r.cuentas_vistas == 2


def test_la_consulta_de_candidatas_excluye_lo_que_no_puede_sondear() -> None:
    """Las cuentas PERSONALES quedan fuera, y no es un olvido.

    Su credencial no se guarda —se le pide al funcionario en cada sesión—, así
    que un proceso de fondo no puede abrirlas. Incluirlas produciría un fallo por
    cuenta y por vuelta, para siempre.
    """
    import inspect

    fuente = inspect.getsource(SondeoDeCorreo._cuentas_candidatas)
    assert "tipo = 'institucional'" in fuente
    assert "activa = true" in fuente
    # Y una cuenta OAuth2 sin autorizar tampoco: no hay token que canjear.
    assert "oauth_refresh_cifrado IS NOT NULL" in fuente
