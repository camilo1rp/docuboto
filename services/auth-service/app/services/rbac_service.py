# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lógica de autorización (E08).

Reproduce el comportamiento del legado (plan-argoik/plan-icetex): Keycloak
autentica, la BD del tenant autoriza. ROOT se comprueba primero (RN-2/RN-3) y los
permisos se resuelven como MAX(crud) por nombre.
"""

import uuid

from app.repositories.rbac_repository import RBACRepository

# Permiso especial: bypass total (USUA_PERM_ROOT del legado).
ROOT_PERMISSION = "USUA_PERM_ROOT"


def has_permission(perms: dict[str, int], name: str, min_crud: int = 1) -> bool:
    """¿El conjunto de permisos satisface ``name`` con al menos ``min_crud``?

    ROOT primero: ``USUA_PERM_ROOT`` concede cualquier permiso.
    """
    if perms.get(ROOT_PERMISSION, 0) >= 1:
        return True
    return perms.get(name, 0) >= min_crud


class RBACService:
    def __init__(self, repo: RBACRepository) -> None:
        self._repo = repo

    async def resolve_effective_permissions(self, user_id: uuid.UUID) -> dict[str, int]:
        """Permisos efectivos del usuario. Si es root, embebe ``USUA_PERM_ROOT``.

        Solo se devuelven permisos con ``crud > 0`` (mitiga la inflación del JWT).
        """
        perms = await self._repo.resolve_permissions(user_id)
        if await self._repo.is_root(user_id):
            perms[ROOT_PERMISSION] = 5
        return perms
