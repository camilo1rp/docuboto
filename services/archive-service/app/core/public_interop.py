# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Interruptor de la interoperabilidad pública ANÓNIMA (OAI-PMH multinivel de expedientes, E11).

**Por qué existe.** Estos endpoints cuelgan de `/api/v1/public/`, que el gateway
declara público sin autenticación, y su único recorte es `nivel_seguridad = 1`…
que es el DEFAULT de la columna y del schema de entrada. La polaridad efectiva es
«público salvo que alguien se acuerde de clasificar», y por ahí se cosecha número,
asunto, dependencia y fecha, y con ello el cuadro de clasificación completo.

Publicar el registro de correspondencia de una entidad es una decisión de esa
entidad —el esquema de publicación del art. 12 de la Ley 1712—, no un efecto
secundario de desplegar el software. De ahí el default `False`.

**404, no 403.** Con el interruptor apagado la respuesta es indistinguible de la de
una ruta que no existe. Un 403 confirmaría que detrás hay un servicio de cosecha
apagado, que es información que no hace falta dar; y es el mismo criterio de
omisión-indistinguible que ya aplica el recorte de reservados de estos endpoints.

**Guard, no `include_router` condicional.** Montar el router solo cuando el flag
está activo tiene el mismo efecto externo, pero se decide al importar el módulo y
por tanto no se puede ejercitar en un test sin reconstruir la aplicación entera —
es decir, la rama apagada quedaría sin cobertura, que es justo la que importa.
"""

from fastapi import HTTPException, status

from app.core.config import settings


def require_public_interop() -> None:
    """404 si la entidad no ha habilitado la interoperabilidad pública anónima."""
    if not settings.public_interop_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": "No encontrado", "status": 404},
        )
