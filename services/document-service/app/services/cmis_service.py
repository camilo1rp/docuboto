# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Perfil CMIS 1.1 MÍNIMO de lectura (E11, RF-INT-02) — Browser Binding (JSON).

Alcance (spec §3/§66): `repositoryInfo`, `getObject` por id, `getChildren` de la raíz
y `getContentStream` por id. **Solo lectura**; sin escritura, versionado, navegación
de carpetas anidadas ni CMIS-SQL (Roadmap). Los objetos son los radicados **públicos**
del tenant mapeados a `cmis:document` (objectId = número de radicado); la raíz es una
`cmis:folder` sintética.

Reutiliza el chokepoint `_PUBLIC` de `OaiRepository` (mismo recorte que OAI-PMH): el
perfil CMIS NUNCA expone metadatos ni bytes de un reservado. `getObject`/`getContent`
sobre un id reservado → 404 (indistinguible de inexistente); `getChildren`/`numItems`
solo cuentan público (Ley 1712/2014 arts. 18-19; RF-SEG-08; spec §5 inv. 11).
"""

from datetime import datetime

from app.repositories.oai_repository import OaiRepository

_MAX_ITEMS = 100
ROOT_ID = "root"


class CmisNotFound(Exception):
    """Objeto CMIS inexistente o no público → 404 objectNotFound (indistinguible)."""


def _prop(pid: str, ptype: str, value: object) -> dict:
    return {"id": pid, "localName": pid, "type": ptype, "cardinality": "single", "value": value}


def _millis(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class CmisService:
    def __init__(self, repo: OaiRepository, *, tenant: str, base_url: str = "") -> None:
        self._repo = repo
        self._tenant = tenant
        self._base_url = base_url.rstrip("/")
        self._repo_id = f"orpyca:{tenant}"

    # --- repositoryInfo ----------------------------------------------------
    def repository_info(self) -> dict:
        info = {
            "repositoryId": self._repo_id,
            "repositoryName": f"OrpycaMCP SGDEA — {self._tenant}",
            "repositoryDescription": "Perfil CMIS mínimo de lectura (solo radicados públicos)",
            "vendorName": "Orpyca",
            "productName": "OrpycaMCP",
            "productVersion": "1.0",
            "rootFolderId": ROOT_ID,
            # URLs del Browser Binding (CMIS 1.1 §5.1): sin ellas un cliente generico
            # no puede navegar. repositoryUrl = servicios de repositorio; rootFolderUrl
            # = navegacion de objetos (getObject/getChildren/getContentStream).
            "repositoryUrl": self._base_url,
            "rootFolderUrl": f"{self._base_url}/root",
            "cmisVersionSupported": "1.1",
            "capabilities": {
                # Perfil de solo-lectura: todo lo de escritura/consulta deshabilitado.
                "capabilityContentStreamUpdatability": "none",
                "capabilityChanges": "none",
                "capabilityRenditions": "none",
                "capabilityGetDescendants": False,
                "capabilityGetFolderTree": False,
                "capabilityQuery": "none",
                "capabilityJoin": "none",
                "capabilityACL": "none",
                "capabilityPWCUpdatable": False,
                "capabilityMultifiling": False,
                "capabilityUnfiling": False,
                "capabilityVersionSpecificFiling": False,
                "capabilityAllVersionsSearchable": False,
            },
        }
        return {self._repo_id: info}

    # --- objetos -----------------------------------------------------------
    def _document_object(self, row) -> dict:
        props = {
            "cmis:objectId": _prop("cmis:objectId", "id", row["tracking_number"]),
            "cmis:baseTypeId": _prop("cmis:baseTypeId", "id", "cmis:document"),
            "cmis:objectTypeId": _prop("cmis:objectTypeId", "id", "cmis:document"),
            "cmis:name": _prop("cmis:name", "string", row["subject"]),
            "cmis:createdBy": _prop("cmis:createdBy", "string", row["origin_dept"] or row["tenant_code"]),
            "cmis:creationDate": _prop("cmis:creationDate", "datetime", _millis(row["registered_at"])),
            "cmis:lastModificationDate": _prop(
                "cmis:lastModificationDate", "datetime", _millis(row["registered_at"])
            ),
        }
        return {"properties": props}

    def _root_folder(self) -> dict:
        props = {
            "cmis:objectId": _prop("cmis:objectId", "id", ROOT_ID),
            "cmis:baseTypeId": _prop("cmis:baseTypeId", "id", "cmis:folder"),
            "cmis:objectTypeId": _prop("cmis:objectTypeId", "id", "cmis:folder"),
            "cmis:name": _prop("cmis:name", "string", "Radicados públicos"),
            "cmis:path": _prop("cmis:path", "string", "/"),
        }
        return {"properties": props}

    async def get_object(self, object_id: str) -> dict:
        if object_id == ROOT_ID:
            return self._root_folder()
        row = await self._repo.get_public(object_id)
        if row is None:  # reservado/anulado/inexistente → indistinguible
            raise CmisNotFound(object_id)
        return self._document_object(row)

    async def get_children(self, *, max_items: int, skip_count: int) -> dict:
        max_items = max(1, min(max_items, _MAX_ITEMS))
        skip_count = max(0, skip_count)
        total = await self._repo.count_public(None, None, None)
        rows = await self._repo.list_public(
            offset=skip_count, limit=max_items, from_dt=None, until_dt=None, set_spec=None
        )
        return {
            "objects": [{"object": self._document_object(r)} for r in rows],
            "hasMoreItems": skip_count + len(rows) < total,
            "numItems": total,
        }

    async def get_content_anexo(self, object_id: str):
        """Devuelve el Record del anexo público a servir (file_id/filename/…), o
        lanza CmisNotFound. El streaming de bytes lo hace el router (storage_client)."""
        anexo = await self._repo.get_public_anexo(object_id)
        if anexo is None:
            raise CmisNotFound(object_id)
        return anexo
