# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""OAI-PMH 2.0 (E11, RF-INT-02) sobre los radicados PÚBLICOS de un tenant.

Protocol Version 2.0, `metadataPrefix=oai_dc` (Dublin Core). Cosecha selectiva por
`from`/`until`/`set` (set = doc_type). Todo el recorte de seguridad vive en
`OaiRepository` (`nivel_seguridad=1`, no anulado): este servicio nunca ve un registro
reservado, por lo que la omisión es indistinguible (completeListSize/sets/GetRecord
derivan del mismo filtro). Referencia: http://www.openarchives.org/OAI/2.0/
"""

import base64
import binascii
from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree as ET

from app.repositories.oai_repository import OaiRepository

OAI_NS = "http://www.openarchives.org/OAI/2.0/"
OAI_DC_NS = "http://www.openarchives.org/OAI/2.0/oai_dc/"
DC_NS = "http://purl.org/dc/elements/1.1/"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

_PAGE = 100
_GRANULARITY = "YYYY-MM-DDThh:mm:ssZ"

for _p, _u in (("", OAI_NS), ("oai_dc", OAI_DC_NS), ("dc", DC_NS), ("xsi", XSI_NS)):
    ET.register_namespace(_p, _u)

_DOC_TYPE_LABEL = {"E": "Entrada", "S": "Salida", "I": "Interno"}


class OaiError(Exception):
    """Error de protocolo OAI-PMH (se serializa como <error code=...>)."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _xml_safe(value: str) -> str:
    """Elimina caracteres de control C0 no válidos en XML 1.0 (salvo \\t \\n \\r) que
    sí pueden vivir en un TEXT de Postgres — evita romper el XML de la cosecha (Seg #3)."""
    return "".join(c for c in value if c in "\t\n\r" or ord(c) >= 0x20)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def encode_token(offset: int, req: dict[str, str]) -> str:
    """resumptionToken opaco que reanuda la cosecha. Datos públicos → no cifrado."""
    raw = "|".join([str(offset), req.get("from", ""), req.get("until", ""),
                     req.get("set", ""), req.get("metadataPrefix", "oai_dc")])
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_token(token: str) -> tuple[int, dict[str, str]]:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        offset_s, frm, until, setspec, prefix = raw.split("|")
        offset = int(offset_s)
        if offset < 0:  # un OFFSET negativo reventaría en Postgres → 500. Seg #2.
            raise ValueError("offset negativo")
        req = {"metadataPrefix": prefix}
        if frm:
            req["from"] = frm
        if until:
            req["until"] = until
        if setspec:
            req["set"] = setspec
        return offset, req
    except (ValueError, binascii.Error) as exc:
        raise OaiError("badResumptionToken", "resumptionToken inválido") from exc


class OaiService:
    def __init__(self, repo: OaiRepository, *, base_url: str, tenant: str) -> None:
        self._repo = repo
        self._base_url = base_url
        self._tenant = tenant

    # --- envoltura común ---------------------------------------------------
    def _root(self, request_attrs: dict[str, str]) -> tuple[ET.Element, ET.Element]:
        root = ET.Element(f"{{{OAI_NS}}}OAI-PMH")
        root.set(
            f"{{{XSI_NS}}}schemaLocation",
            f"{OAI_NS} http://www.openarchives.org/OAI/2.0/OAI-PMH.xsd",
        )
        ET.SubElement(root, f"{{{OAI_NS}}}responseDate").text = _fmt(_now())
        req = ET.SubElement(root, f"{{{OAI_NS}}}request", request_attrs)
        req.text = self._base_url
        return root, req

    @staticmethod
    def _serialize(root: ET.Element) -> bytes:
        return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")

    def error_xml(self, request_attrs: dict[str, str], code: str, message: str) -> bytes:
        # Regla OAI-PMH 2.0 (XML Response): en badVerb/badArgument el <request> lleva
        # SOLO el baseURL, sin atributos; en cualquier otro código refleja los args de
        # la petición. (Conformidad #3/#4.)
        attrs = {} if code in ("badVerb", "badArgument") else request_attrs
        root, _ = self._root(attrs)
        err = ET.SubElement(root, f"{{{OAI_NS}}}error", {"code": code})
        err.text = message
        return self._serialize(root)

    def _identifier(self, tracking_number: str) -> str:
        return f"oai:{self._tenant}:{tracking_number}"

    def _header(self, parent: ET.Element, row) -> None:
        h = ET.SubElement(parent, f"{{{OAI_NS}}}header")
        ET.SubElement(h, f"{{{OAI_NS}}}identifier").text = self._identifier(row["tracking_number"])
        ET.SubElement(h, f"{{{OAI_NS}}}datestamp").text = _fmt(row["registered_at"])
        ET.SubElement(h, f"{{{OAI_NS}}}setSpec").text = row["doc_type"]

    def _metadata(self, parent: ET.Element, row) -> None:
        md = ET.SubElement(parent, f"{{{OAI_NS}}}metadata")
        dc = ET.SubElement(md, f"{{{OAI_DC_NS}}}dc")
        dc.set(
            f"{{{XSI_NS}}}schemaLocation",
            f"{OAI_DC_NS} http://www.openarchives.org/OAI/2.0/oai_dc.xsd",
        )
        # Mapeo radicado → Dublin Core (RF-INT-06). El registro es PÚBLICO, así que
        # su `subject` es divulgable.
        pairs = [
            ("title", row["subject"]),
            ("identifier", row["tracking_number"]),
            ("type", _DOC_TYPE_LABEL.get(row["doc_type"], row["doc_type"])),
            ("date", _fmt(row["registered_at"])),
            ("publisher", row["origin_dept"] or row["tenant_code"]),
            ("source", row["tracking_number"]),
            ("language", "spa"),
        ]
        for tag, val in pairs:
            if val:
                ET.SubElement(dc, f"{{{DC_NS}}}{tag}").text = _xml_safe(str(val))

    # --- verbos ------------------------------------------------------------
    async def identify(self, request_attrs: dict[str, str]) -> bytes:
        root, _ = self._root(request_attrs)
        idn = ET.SubElement(root, f"{{{OAI_NS}}}Identify")
        earliest = await self._repo.earliest_public()
        fields = {
            "repositoryName": f"OrpycaMCP SGDEA — {self._tenant}",
            "baseURL": self._base_url,
            "protocolVersion": "2.0",
            "adminEmail": f"admin@{self._tenant}",
            "earliestDatestamp": _fmt(earliest) if earliest else "1970-01-01T00:00:00Z",
            "deletedRecord": "no",
            "granularity": _GRANULARITY,
        }
        for tag, val in fields.items():
            ET.SubElement(idn, f"{{{OAI_NS}}}{tag}").text = val
        return self._serialize(root)

    async def list_metadata_formats(self, request_attrs: dict[str, str], identifier: str | None) -> bytes:
        # Si se pide para un identificador concreto, debe ser PÚBLICO (si no,
        # idDoesNotExist — no se confirma la existencia de un reservado).
        if identifier is not None:
            tn = self._parse_identifier(identifier)
            if tn is None or await self._repo.get_public(tn) is None:
                raise OaiError("idDoesNotExist", "Identificador desconocido")
        root, _ = self._root(request_attrs)
        lmf = ET.SubElement(root, f"{{{OAI_NS}}}ListMetadataFormats")
        fmt = ET.SubElement(lmf, f"{{{OAI_NS}}}metadataFormat")
        ET.SubElement(fmt, f"{{{OAI_NS}}}metadataPrefix").text = "oai_dc"
        ET.SubElement(fmt, f"{{{OAI_NS}}}schema").text = "http://www.openarchives.org/OAI/2.0/oai_dc.xsd"
        ET.SubElement(fmt, f"{{{OAI_NS}}}metadataNamespace").text = OAI_DC_NS
        return self._serialize(root)

    async def list_sets(self, request_attrs: dict[str, str]) -> bytes:
        root, _ = self._root(request_attrs)
        ls = ET.SubElement(root, f"{{{OAI_NS}}}ListSets")
        for doc_type in await self._repo.sets_public():
            s = ET.SubElement(ls, f"{{{OAI_NS}}}set")
            ET.SubElement(s, f"{{{OAI_NS}}}setSpec").text = doc_type
            ET.SubElement(s, f"{{{OAI_NS}}}setName").text = _DOC_TYPE_LABEL.get(doc_type, doc_type)
        return self._serialize(root)

    async def get_record(self, request_attrs: dict[str, str], identifier: str, prefix: str) -> bytes:
        if prefix != "oai_dc":
            raise OaiError("cannotDisseminateFormat", "Solo oai_dc")
        tn = self._parse_identifier(identifier)
        row = await self._repo.get_public(tn) if tn else None
        if row is None:
            raise OaiError("idDoesNotExist", "Identificador desconocido")
        root, _ = self._root(request_attrs)
        gr = ET.SubElement(root, f"{{{OAI_NS}}}GetRecord")
        rec = ET.SubElement(gr, f"{{{OAI_NS}}}record")
        self._header(rec, row)
        self._metadata(rec, row)
        return self._serialize(root)

    async def list_records(
        self, request_attrs: dict[str, str], harvest: dict[str, str], *, headers_only: bool
    ) -> bytes:
        offset, req = self._resume(harvest)
        from_dt, until_dt = self._parse_range(req)
        set_spec = req.get("set")
        if req.get("metadataPrefix", "oai_dc") != "oai_dc":
            raise OaiError("cannotDisseminateFormat", "Solo oai_dc")

        total = await self._repo.count_public(from_dt, until_dt, set_spec)
        if total == 0:
            raise OaiError("noRecordsMatch", "Sin registros para el criterio")
        rows = await self._repo.list_public(
            offset=offset, limit=_PAGE, from_dt=from_dt, until_dt=until_dt, set_spec=set_spec
        )
        root, _ = self._root(request_attrs)
        verb = "ListIdentifiers" if headers_only else "ListRecords"
        container = ET.SubElement(root, f"{{{OAI_NS}}}{verb}")
        for row in rows:
            if headers_only:
                self._header(container, row)
            else:
                rec = ET.SubElement(container, f"{{{OAI_NS}}}record")
                self._header(rec, row)
                self._metadata(rec, row)
        self._resumption(container, offset=offset, page=len(rows), total=total, req=req)
        return self._serialize(root)

    # --- helpers de paginación / parsing -----------------------------------
    def _resume(self, harvest: dict[str, str]) -> tuple[int, dict[str, str]]:
        token = harvest.get("resumptionToken")
        if token is not None:
            return decode_token(token)
        return 0, harvest

    def _resumption(
        self, container: ET.Element, *, offset: int, page: int, total: int, req: dict[str, str]
    ) -> None:
        next_offset = offset + page
        rt = ET.SubElement(container, f"{{{OAI_NS}}}resumptionToken")
        # completeListSize cuenta SOLO lo público (el filtro está en el repo) → no
        # delata cuántos registros reservados existen (canal lateral cerrado).
        rt.set("completeListSize", str(total))
        rt.set("cursor", str(offset))
        # Token vacío en la última página (regla OAI); con valor si quedan más.
        rt.text = encode_token(next_offset, req) if next_offset < total else ""

    def _parse_identifier(self, identifier: str) -> str | None:
        prefix = f"oai:{self._tenant}:"
        if not identifier.startswith(prefix):
            return None
        return identifier[len(prefix):] or None

    @staticmethod
    def _parse_range(req: dict[str, str]) -> tuple[datetime | None, datetime | None]:
        fr = _parse_oai_date(req.get("from"))
        un = _parse_oai_date(req.get("until"))
        # OAI-PMH: mezclar granularidades de from/until es badArgument.
        if fr is not None and un is not None and fr[1] != un[1]:
            raise OaiError("badArgument", "granularidad de from/until no coincide")
        from_dt = fr[0] if fr else None  # 'from' inclusivo desde el inicio (día/segundo)
        until_dt: datetime | None = None
        if un is not None:
            # 'until' es "menor o IGUAL" e inclusivo de TODO el rango a su granularidad
            # (Conf #7): día → hasta el fin del día; segundo → inclusivo de ese segundo.
            # El repo filtra `registered_at < until_dt` (límite superior EXCLUSIVO).
            until_dt = un[0] + (timedelta(days=1) if un[1] else timedelta(seconds=1))
        return from_dt, until_dt


def _parse_oai_date(value: str | None) -> tuple[datetime, bool] | None:
    """Devuelve (datetime, es_solo_día) o None. Lanza badArgument si el formato no es
    OAI (segundos `YYYY-MM-DDThh:mm:ssZ` o día `YYYY-MM-DD`)."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC), False
    except ValueError:
        pass
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC), True
    except ValueError as exc:
        raise OaiError("badArgument", f"Fecha inválida: {value}") from exc
