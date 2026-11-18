# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Doble de tenant-service con política de tratamiento vigente (B2, ADR-031)."""

from __future__ import annotations


class TenantConPolitica:
    """Responde SOLO la lectura estricta de parámetros que la ventanilla exige."""

    def __init__(self, version: str = "2026-01") -> None:
        self._version = version

    async def get_params_estricto(self, *, tenant_slug: str) -> dict:
        return {
            "politica_tratamiento_version": self._version,
            "politica_tratamiento_url": "https://entidad.example/privacidad",
            "politica_tratamiento_texto": "Aviso de privacidad de prueba",
        }


ENTIDAD = TenantConPolitica()
