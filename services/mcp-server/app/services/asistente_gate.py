# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
Gate del asistente (ADR-037): bandera de la ENTIDAD + permiso de la PERSONA.

Toda ruta de `/api/v1/assistant/*` lo pasa tras resolver la identidad y ANTES de tocar
el SDK de Anthropic o Whisper:

1. La entidad tiene que haber activado el asistente (`asistente_habilitado` de
   `/auth/me`): 403 `asistente_deshabilitado`. Va primero y vale también para ROOT, porque
   es una decisión de tratamiento de datos de la entidad, no un permiso.
2. La persona tiene que tener `PERM_CONOCIMIENTO` nivel 1 («consultar con IA»): 403
   `forbidden`. ROOT lo tiene por definición.

Es defensa de borde: la autoridad sobre la bandera es knowledge-service, que la vuelve a
leer de la base en cada consulta. `shared/tests/test_conocimiento_mira_la_bandera.py`
exige que toda ruta de este router llame a `exigir_asistente`.
"""

from __future__ import annotations

from app.services.identity import Identity

PERMISO = "PERM_CONOCIMIENTO"
NIVEL_MINIMO = 1


class AsistenteDenegado(Exception):
    """La entidad no activó el asistente o la persona no tiene el permiso (403)."""

    status = 403

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


def exigir_asistente(identidad: Identity) -> None:
    """Lanza `AsistenteDenegado` salvo que la entidad lo active y la persona pueda usarlo."""
    if not identidad.asistente_habilitado:
        raise AsistenteDenegado(
            "asistente_deshabilitado",
            "Esta entidad no tiene activado el asistente ni la búsqueda semántica.",
        )
    if identidad.is_root:
        return
    nivel = identidad.levels.get(PERMISO, 0) if PERMISO in identidad.permissions else 0
    if nivel < NIVEL_MINIMO:
        raise AsistenteDenegado(
            "forbidden",
            # Mismo texto que `orpycamcp_common.rbac.mensaje_denegacion` (no se importa:
            # arrastra asyncpg y este servicio no tiene base de datos).
            f"Se requiere el permiso {PERMISO} (nivel {NIVEL_MINIMO} o superior)"
            if not nivel
            else f"Tiene el permiso {PERMISO} con nivel {nivel}, y esta acción requiere "
            f"nivel {NIVEL_MINIMO} o superior",
        )
