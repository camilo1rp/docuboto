# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Buzones de dependencia — el ÚNICO sitio donde se construye una dirección.

Motivo. El aviso de asignación (`workflow.step.created`) se dirigía a
``f"{to_dept}@orpycamcp.local"``, y `to_dept` es —por convención deliberada de
todo el sistema— el **nombre** de la dependencia, texto libre de hasta 100
caracteres: «Oficina Jurídica», «Ventanilla Única de Radicación». Eso produce
una parte local con espacios y tildes, que ningún servidor SMTP sin SMTPUTF8
acepta. En el tenant de demostración **fallaron los 25 avisos de asignación
emitidos, sin una sola excepción**, con el error del propio servidor:
«An address containing non-ASCII characters was provided».

No es un caso extremo: **es la norma**. Casi todo nombre de dependencia de una
entidad pública colombiana lleva espacio o tilde. El aviso que le dice a una
dependencia que tiene un documento que tramitar no se entregaba nunca.

Y había una incoherencia de fondo: la misma oficina recibía dos direcciones
distintas según el camino. `document.radicado.created` ya usaba
``dependencia-{codigo}@orpycamcp.local`` —deliverable— y el camino de flujos
inventaba otra a partir del nombre. Aquí se unifican: **una dependencia tiene
un buzón, no dos**.

Por qué un módulo y no un `if`: el defecto es de CLASE, no de sitio. Mientras
interpolar texto libre en una dirección sea posible, reaparece en el siguiente
`f"...@..."` que alguien escriba. Este es el punto de paso obligado, y
`test_buzones.py` censa mecánicamente que no haya otro.
"""

from __future__ import annotations

import logging
import re

import asyncpg

logger = logging.getLogger(__name__)

#: Dominio interno de los buzones de dependencia (entorno de despliegue).
DOMINIO_BUZONES = "orpycamcp.local"

#: Buzón de respaldo: la ventanilla única. Un aviso oficial que no se puede
#: encaminar va a la ventanilla —que es quien lo reencamina en la práctica—,
#: nunca a una dirección inventada que rebota y se pierde.
BUZON_VENTANILLA = f"ventanilla@{DOMINIO_BUZONES}"

#: `dependencias.codigo` es un INT del catálogo (`depe_codi`, 3-4 dígitos).
#: Se valida el CÓDIGO, no la dirección ya montada: `""` produce
#: `dependencia-@…`, que tiene forma válida y destinatario inexistente — un
#: rebote silencioso es peor que un error. Estricta a propósito: su trabajo es
#: RECHAZAR, no normalizar.
_CODIGO_RE = re.compile(r"^[0-9]{1,20}$")


def buzon_de_codigo(codigo: int | str) -> str:
    """Buzón canónico de una dependencia a partir de su CÓDIGO del catálogo.

    Misma forma que ya usaba el camino de radicación, para que la misma oficina
    tenga una sola dirección venga el aviso de donde venga.
    """
    if not _CODIGO_RE.match(str(codigo)):
        # Un `codigo` que no es el del catálogo es un dato corrupto, no una
        # dependencia rara: no se fabrica la dirección.
        raise ValueError(f"código de dependencia no apto para un buzón: {codigo!r}")
    return f"dependencia-{codigo}@{DOMINIO_BUZONES}"


def buzon_de_codigo_o_ventanilla(codigo: int | str | None) -> str:
    """Buzón por código, cayendo a la ventanilla si el código no sirve.

    Variante tolerante para el camino de radicación, que recibe el código en el
    sobre del evento y no puede permitirse una excepción: reventar aquí deja el
    mensaje en el PEL del stream y bloquea la entrega de TODO lo que venga
    detrás. Un aviso mal encaminado es un incidente; un consumidor parado es
    una avería.
    """
    if codigo is None:
        return BUZON_VENTANILLA
    try:
        return buzon_de_codigo(codigo)
    except ValueError:
        logger.warning(
            "código de dependencia %r no apto para un buzón: "
            "el aviso se encamina a la ventanilla única",
            codigo,
        )
        return BUZON_VENTANILLA


async def _codigo_por_nombre(conn: asyncpg.Connection, nombre: str) -> tuple[int | None, int]:
    """Resuelve un NOMBRE a `(codigo, candidatos)`; `codigo` solo si hay uno.

    Usa la función única `resolver_codigo_dependencia(nombre, true)` de
    tenant-service (migración 013), que mira activas, inactivas y nombres
    ANTERIORES a un renombre: un evento viejo que solo trae el nombre de antes
    sigue llegando a su dependencia. Si la función aún no existe en el schema
    (tenant sin migrar) se aplica el mismo criterio sobre `dependencias`:
    exactamente UN código distinto o nada. Nunca `fetchrow` sin más: con dos
    homónimos devolvía una fila arbitraria.
    """
    existe = await conn.fetchval(
        "SELECT to_regprocedure('resolver_codigo_dependencia(text,boolean)') IS NOT NULL"
    )
    if existe:
        fila = await conn.fetchrow("SELECT codigo, candidatos FROM resolver_codigo_dependencia($1, true)", nombre)
        if fila is None:
            return None, 0
        return fila["codigo"], int(fila["candidatos"] or 0)
    filas = await conn.fetch("SELECT DISTINCT codigo FROM dependencias WHERE nombre = $1", nombre)
    if len(filas) == 1:
        return filas[0]["codigo"], 1
    return None, len(filas)


async def resolver_buzon(
    conn: asyncpg.Connection, codigo: int | str | None, nombre: str | None
) -> str:
    """Buzón de una dependencia: el CÓDIGO manda; el nombre es respaldo.

    Orden (B5, ADR-032): código del evento -> nombre resuelto (con histórico)
    -> ventanilla. El nombre ambiguo (dos homónimos) o huérfano cae a la
    ventanilla con aviso: un aviso al buzón de OTRA dependencia es una fuga,
    uno a la ventanilla es solo un reencamine.
    """
    if codigo is not None:
        return buzon_de_codigo_o_ventanilla(codigo)
    if nombre:
        resuelto, candidatos = await _codigo_por_nombre(conn, nombre)
        if resuelto is not None:
            return buzon_de_codigo(resuelto)
        logger.warning(
            "dependencia %r sin correspondencia única en el catálogo (%d candidatos): "
            "el aviso se encamina a la ventanilla única",
            nombre,
            candidatos,
        )
        return BUZON_VENTANILLA
    logger.warning("aviso sin dependencia: se encamina a la ventanilla única")
    return BUZON_VENTANILLA


async def resolver_buzon_dependencia(conn: asyncpg.Connection, nombre: str | None) -> str:
    """Compatibilidad: resolución solo por NOMBRE (ver `resolver_buzon`)."""
    return await resolver_buzon(conn, None, nombre)
