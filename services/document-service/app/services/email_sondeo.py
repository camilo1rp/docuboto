# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Sondeo programado de los buzones (E19 Increment 3).

Hasta aquí la ingesta era **a demanda**: alguien pulsaba «consultar el buzón».
Eso sirve para una ventanilla atendida, pero no para una entidad que recibe
correspondencia fuera de horario y quiere encontrarla ya en la bandeja.

**Tres cosas que este worker tiene que hacer bien, y por qué:**

1. **No duplicar trabajo entre réplicas.** Con dos instancias del servicio, las
   dos sondearían el mismo buzón a la vez y en modo automático radicarían el
   mismo correo dos veces — la unicidad de `email_mensaje` lo impediría, pero se
   habrían descargado los mensajes dos veces y peleado por la reserva. Cada
   cuenta se toma con un **advisory lock** de PostgreSQL: quien no lo consigue,
   pasa de largo sin esperar.

2. **No castigar a un buzón caído.** Una cuenta que falla entra en **espera
   creciente**: reintentar cada minuto contra un servidor que rechaza las
   credenciales solo llena el registro y puede bloquear la cuenta en el
   proveedor. La espera se reinicia en cuanto vuelve a funcionar.

3. **No radicar sin responsable.** En modo automático nadie pulsa, así que la
   cuenta declara bajo qué funcionario radica (`responsable_user_id`, migración
   024). No es un tecnicismo: es quien responde de lo que entre por ese buzón sin
   revisión previa (Ac. AGN 060/2001 arts. 2 y 13).

El worker es **opt-in** (`EMAIL_SONDEO_ENABLED`) y arranca desactivado: un
proceso que se conecta solo a buzones y crea documentos oficiales no debe
encenderse porque sí al desplegar.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

import asyncpg

from app.core.config import settings
from app.services.email_bandeja_service import EmailBandejaService
from app.services.email_cuenta_service import ErrorDeCuenta

logger = logging.getLogger(__name__)

# Espacio de claves de los advisory locks del sondeo. Un número fijo distinto del
# que usan otros servicios evita que dos usos sin relación se bloqueen entre sí
# por coincidir el hash de la clave.
_ESPACIO_LOCK = 0x4D41494C  # "MAIL"

ESPERA_BASE_SEGUNDOS = 60
ESPERA_MAXIMA_SEGUNDOS = 3600


@dataclass
class EstadoCuenta:
    """Cuánto hay que esperar antes de volver a intentar con esta cuenta."""

    fallos: int = 0
    proximo_intento: float = 0.0

    def espera(self) -> int:
        """Retroceso exponencial acotado: 1, 2, 4… minutos, hasta una hora.

        Acotado porque una cuenta mal configurada no debe dejar de intentarse
        nunca —se arreglará y nadie va a reiniciar el servicio— pero tampoco
        debe seguir golpeando cada minuto.
        """
        return min(ESPERA_BASE_SEGUNDOS * (2**self.fallos), ESPERA_MAXIMA_SEGUNDOS)


@dataclass
class ResultadoSondeo:
    cuentas_vistas: int = 0
    cuentas_omitidas: int = 0
    nuevos: int = 0
    radicados: list[str] = field(default_factory=list)
    fallos: int = 0


class SondeoDeCorreo:
    """Recorre las cuentas activas de un tenant y las pone al día."""

    def __init__(self, tenant_slug: str) -> None:
        self._tenant = tenant_slug
        self._estados: dict[uuid.UUID, EstadoCuenta] = {}

    async def una_vuelta(self, conn: asyncpg.Connection, *, ahora: float) -> ResultadoSondeo:
        """Sondea las cuentas que toquen. Devuelve lo que pasó, sin lanzar.

        Un fallo de una cuenta no impide sondear las demás: si el buzón de PQRS
        está caído, el de notificaciones judiciales debe seguir entrando.
        """
        resultado = ResultadoSondeo()
        for fila in await self._cuentas_candidatas(conn):
            cuenta_id = fila["id"]
            estado = self._estados.setdefault(cuenta_id, EstadoCuenta())
            if ahora < estado.proximo_intento:
                resultado.cuentas_omitidas += 1
                continue
            if not await self._tomar_lock(conn, cuenta_id):
                # Otra réplica la está sondeando ahora mismo. No se espera: se
                # pasa de largo y se verá en la vuelta siguiente.
                resultado.cuentas_omitidas += 1
                continue
            try:
                await self._sondear_una(conn, fila, resultado, estado, ahora=ahora)
            finally:
                await self._soltar_lock(conn, cuenta_id)
            resultado.cuentas_vistas += 1
        return resultado

    async def _cuentas_candidatas(self, conn: asyncpg.Connection) -> list[asyncpg.Record]:
        """Cuentas activas que el sondeo puede trabajar por su cuenta.

        Se excluyen las **personales**: su credencial no está guardada —se pide
        al funcionario en cada sesión— así que un proceso de fondo no puede ni
        debe abrirlas.
        """
        return await conn.fetch(
            """
            SELECT id, nombre, modo, responsable_user_id, carpeta_origen
              FROM email_cuenta
             WHERE activa = true
               AND tipo = 'institucional'
               AND (auth_tipo = 'password' OR oauth_refresh_cifrado IS NOT NULL)
             ORDER BY nombre
            """
        )

    async def _sondear_una(
        self,
        conn: asyncpg.Connection,
        fila: asyncpg.Record,
        resultado: ResultadoSondeo,
        estado: EstadoCuenta,
        *,
        ahora: float,
    ) -> None:
        cuenta_id = fila["id"]
        servicio = EmailBandejaService(conn, self._tenant)
        # El sondeo actúa como el responsable declarado; en modo asistida no hay
        # ninguno y basta con traer, que no requiere autoría.
        actor = fila["responsable_user_id"] or uuid.UUID(int=0)
        try:
            traido = await servicio.sincronizar(
                cuenta_id, actor=actor, es_admin=True, password=None
            )
        except ErrorDeCuenta as exc:
            estado.fallos += 1
            estado.proximo_intento = ahora + estado.espera()
            resultado.fallos += 1
            logger.warning(
                "sondeo de «%s» fallido (%s); siguiente intento en %ss",
                fila["nombre"], exc.codigo, estado.espera(),
            )
            return

        estado.fallos = 0
        estado.proximo_intento = 0.0
        resultado.nuevos += int(traido["nuevos"])

        if fila["modo"] == "automatica":
            await self._radicar_pendientes(conn, fila, servicio, resultado)

    async def _radicar_pendientes(
        self,
        conn: asyncpg.Connection,
        fila: asyncpg.Record,
        servicio: EmailBandejaService,
        resultado: ResultadoSondeo,
    ) -> None:
        """Radica lo pendiente bajo la autoría del responsable declarado.

        La reserva se toma igual que la tomaría una persona: si un funcionario
        está trabajando un correo cuando entra el sondeo, el automático **no se lo
        quita**. Modo automático significa «no hace falta que alguien lo abra»,
        no «el proceso manda sobre quien lo abrió».
        """
        from app.services.document_service import DocumentService

        responsable = fila["responsable_user_id"]
        pendientes = await servicio._repo.bandeja(fila["id"], limite=25)
        for correo in pendientes:
            if correo["reservado_vigente"]:
                continue
            try:
                await servicio.reservar(correo["id"], fila["id"], actor=responsable)
                creado = await servicio.radicar(
                    correo["id"],
                    fila["id"],
                    actor=responsable,
                    es_admin=True,
                    password=None,
                    datos={},
                    servicio=DocumentService(conn, self._tenant, None, None),
                )
            except ErrorDeCuenta as exc:
                # Un correo que no se puede radicar no detiene a los demás: queda
                # en la bandeja para que alguien lo mire.
                resultado.fallos += 1
                logger.warning(
                    "radicación automática de un correo de «%s» fallida: %s",
                    fila["nombre"], exc.codigo,
                )
                continue
            resultado.radicados.append(str(creado["tracking_number"]))

    # -- exclusión entre réplicas -----------------------------------------

    async def _tomar_lock(self, conn: asyncpg.Connection, cuenta_id: uuid.UUID) -> bool:
        """`pg_try_advisory_lock`, que NO espera: o se tiene o se sigue.

        Con la versión bloqueante, una réplica que tarde en un buzón lento
        dejaría a la otra parada en la misma cuenta en vez de atender las demás.
        """
        tomado = await conn.fetchval(
            "SELECT pg_try_advisory_lock($1, $2)", _ESPACIO_LOCK, self._clave(cuenta_id)
        )
        return bool(tomado)

    async def _soltar_lock(self, conn: asyncpg.Connection, cuenta_id: uuid.UUID) -> None:
        await conn.execute(
            "SELECT pg_advisory_unlock($1, $2)", _ESPACIO_LOCK, self._clave(cuenta_id)
        )

    def _clave(self, cuenta_id: uuid.UUID) -> int:
        """UUID → entero de 32 bits con signo, que es lo que acepta la función.

        Se toman los 31 bits bajos: dos cuentas distintas podrían colisionar y
        una esperaría a la otra sin necesidad, lo cual solo cuesta una vuelta.
        Confundir el espacio de claves con el de otro servicio sería peor, y de
        eso protege `_ESPACIO_LOCK`.
        """
        return cuenta_id.int & 0x7FFFFFFF


async def bucle_de_sondeo(pool: asyncpg.Pool, tenant_slug: str) -> None:
    """Corre indefinidamente. Pensado para `asyncio.create_task` en el lifespan.

    Cada excepción inesperada se registra y el bucle **continúa**: un worker que
    muere en silencio deja la ingesta parada sin que nadie se entere, y el
    síntoma sería «no llega correo», indistinguible de un buzón vacío.
    """
    sondeo = SondeoDeCorreo(tenant_slug)
    intervalo = max(30, settings.email_sondeo_intervalo_segundos)
    logger.info("sondeo de correo activo para «%s» cada %ss", tenant_slug, intervalo)
    while True:
        try:
            async with pool.acquire() as conn:
                await conn.execute(f'SET search_path = "tenant_{tenant_slug}", public')
                resultado = await sondeo.una_vuelta(
                    conn, ahora=asyncio.get_running_loop().time()
                )
            if resultado.nuevos or resultado.radicados:
                logger.info(
                    "sondeo: %s correo(s) nuevo(s), %s radicado(s) automáticamente",
                    resultado.nuevos, len(resultado.radicados),
                )
        except asyncio.CancelledError:
            logger.info("sondeo de correo detenido")
            raise
        except Exception:  # noqa: BLE001 — ver el docstring: el bucle no muere
            logger.exception("vuelta de sondeo fallida; se reintenta")
        await asyncio.sleep(intervalo)
