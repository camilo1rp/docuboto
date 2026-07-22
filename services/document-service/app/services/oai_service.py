# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""OAI-PMH 2.0 (E11, RF-INT-02/INT-06) sobre los radicados PÚBLICOS de un tenant.

Protocol Version 2.0. Dos formatos de diseminación (RF-INT-06): `oai_dc` (Dublin Core
simple) y `ead` (ISAD(G) codificado en EAD 2002 — descripción archivística normalizada,
un componente `level="item"` por radicado). Cosecha selectiva por `from`/`until`/`set`
(set = doc_type). Todo el recorte de seguridad vive en `OaiRepository`
(`nivel_seguridad=1`, no anulado): este servicio nunca ve un registro reservado, por lo
que la omisión es indistinguible (completeListSize/sets/GetRecord derivan del mismo
filtro), sea cual sea el formato. Referencias: http://www.openarchives.org/OAI/2.0/ ·
ISAD(G) 2ª ed. · EAD 2002 (http://www.loc.gov/ead/).
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
EAD_NS = "urn:isbn:1-931666-22-9"  # EAD 2002 (Encoded Archival Description)

# Formatos de diseminación soportados (RF-INT-06). El prefijo → (namespace, schema).
_FORMATS = {
    "oai_dc": (OAI_DC_NS, "http://www.openarchives.org/OAI/2.0/oai_dc.xsd"),
    "ead": (EAD_NS, "http://www.loc.gov/ead/ead.xsd"),
}

_PAGE = 100
_GRANULARITY = "YYYY-MM-DDThh:mm:ssZ"

for _p, _u in (("", OAI_NS), ("oai_dc", OAI_DC_NS), ("dc", DC_NS), ("xsi", XSI_NS),
               ("ead", EAD_NS)):
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

    def _write_metadata(self, parent: ET.Element, row, prefix: str) -> None:
        """Despacha al constructor del formato pedido (ambos sobre el MISMO row
        público → el recorte de seguridad es idéntico sea cual sea el prefijo)."""
        if prefix == "ead":
            self._metadata_ead(parent, row)
        else:
            self._metadata_dc(parent, row)

    def _metadata_dc(self, parent: ET.Element, row) -> None:
        md = ET.SubElement(parent, f"{{{OAI_NS}}}metadata")
        dc = ET.SubElement(md, f"{{{OAI_DC_NS}}}dc")
        dc.set(
            f"{{{XSI_NS}}}schemaLocation",
            f"{OAI_DC_NS} http://www.openarchives.org/OAI/2.0/oai_dc.xsd",
        )
        # Mapeo radicado → Dublin Core simple. El registro es PÚBLICO, así que
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

    def _metadata_ead(self, parent: ET.Element, row) -> None:
        """Mapeo radicado → EAD 2002 con los 6 elementos OBLIGATORIOS de ISAD(G)/NTC 4095
        para intercambio (RF-INT-06). Un radicado es una *unidad documental simple* →
        `level="item"` (convención: el radicado es la unidad; sus anexos son parte de
        ella — no se eleva a `file`). Fragmento EAD autocontenido por registro (patrón
        OAI-EAD).

        ISAD(G) → EAD:
          3.1.1 Código de referencia   → <unitid countrycode="CO" repositorycode>  (país+repo+ref local, para intercambio)
          3.1.2 Título                 → <unittitle>     (asunto — público)
          3.1.3 Fecha(s)               → <unitdate normal>  (registered_at, AAAA-MM-DD)
          3.1.4 Nivel de descripción   → @level="item"
          3.1.5 Volumen y soporte      → <physdesc>      (1 doc. electrónico)
          3.2.1 Nombre del productor   → <origination>   (dependencia origen / tenant)
          3.4.1 Condiciones de acceso  → <accessrestrict>  (público, Ley 1712/2014)
          3.7.2 Reglas o convenciones  → <descrules>     (ISAD(G) 2ª ed. / NTC 4095)

        DEUDA declarada (roadmap, no incumplimiento — ISAD(G) 2.2/2.4): la descripción
        es monolvel (item); el contexto multinivel fondo→sección→serie→expediente vive
        en archive-service (TRD/CCD, Ac. AGN 001/2024) y se inyectará en un fast-follow.
        """
        md = ET.SubElement(parent, f"{{{OAI_NS}}}metadata")
        ead = ET.SubElement(md, f"{{{EAD_NS}}}ead")
        ead.set(
            f"{{{XSI_NS}}}schemaLocation",
            f"{EAD_NS} http://www.loc.gov/ead/ead.xsd",
        )
        tn = _xml_safe(str(row["tracking_number"]))
        title = _xml_safe(str(row["subject"])) if row["subject"] else tn
        # Código de repositorio para el identificador de intercambio (país + repo). El
        # tenant_code (o el slug) es divulgable: ya viaja en la URL y en el identificador OAI.
        repo_code = _xml_safe(str(row["tenant_code"] or self._tenant))
        # eadheader — identidad del fragmento + reglas de descripción (3.7.2).
        header = ET.SubElement(ead, f"{{{EAD_NS}}}eadheader")
        ET.SubElement(
            header, f"{{{EAD_NS}}}eadid",
            {"countrycode": "CO", "mainagencycode": repo_code},
        ).text = _xml_safe(self._identifier(row["tracking_number"]))
        fd = ET.SubElement(header, f"{{{EAD_NS}}}filedesc")
        ts = ET.SubElement(fd, f"{{{EAD_NS}}}titlestmt")
        ET.SubElement(ts, f"{{{EAD_NS}}}titleproper").text = title
        pd = ET.SubElement(header, f"{{{EAD_NS}}}profiledesc")
        ET.SubElement(pd, f"{{{EAD_NS}}}descrules").text = "ISAD(G) 2ª ed.; NTC 4095 (ICONTEC)."
        # archdesc — la descripción ISAD(G) del radicado.
        arch = ET.SubElement(ead, f"{{{EAD_NS}}}archdesc", {"level": "item"})
        did = ET.SubElement(arch, f"{{{EAD_NS}}}did")
        # 3.1.1 Código de referencia completo para intercambio: país (ISO 3166) + repo + ref local.
        ET.SubElement(
            did, f"{{{EAD_NS}}}unitid",
            {"countrycode": "CO", "repositorycode": repo_code},
        ).text = tn
        ET.SubElement(did, f"{{{EAD_NS}}}unittitle").text = title
        # 3.1.3 Fecha — @normal + texto en la misma granularidad de día (AAAA-MM-DD).
        iso_day = row["registered_at"].astimezone(UTC).strftime("%Y-%m-%d")
        ET.SubElement(did, f"{{{EAD_NS}}}unitdate", {"normal": iso_day}).text = iso_day
        doc_label = _xml_safe(str(_DOC_TYPE_LABEL.get(row["doc_type"], row["doc_type"])))
        ET.SubElement(did, f"{{{EAD_NS}}}physdesc").text = f"1 documento electrónico ({doc_label})"
        origin = row["origin_dept"] or row["tenant_code"]
        if origin:
            ET.SubElement(did, f"{{{EAD_NS}}}origination").text = _xml_safe(str(origin))
        lm = ET.SubElement(did, f"{{{EAD_NS}}}langmaterial")
        ET.SubElement(lm, f"{{{EAD_NS}}}language", {"langcode": "spa"}).text = "Español"
        ET.SubElement(did, f"{{{EAD_NS}}}repository").text = _xml_safe(self._tenant)
        # 3.4.1 Condiciones de acceso — afirma positivamente el carácter público de lo
        # diseminado (Ley 1712/2014); ancla la divulgación en vez de omitirla.
        ar = ET.SubElement(arch, f"{{{EAD_NS}}}accessrestrict")
        ET.SubElement(ar, f"{{{EAD_NS}}}p").text = "Información pública (Ley 1712 de 2014)."

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
        for prefix, (namespace, schema) in _FORMATS.items():
            fmt = ET.SubElement(lmf, f"{{{OAI_NS}}}metadataFormat")
            ET.SubElement(fmt, f"{{{OAI_NS}}}metadataPrefix").text = prefix
            ET.SubElement(fmt, f"{{{OAI_NS}}}schema").text = schema
            ET.SubElement(fmt, f"{{{OAI_NS}}}metadataNamespace").text = namespace
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
        if prefix not in _FORMATS:
            raise OaiError("cannotDisseminateFormat", "Formato no soportado (oai_dc, ead)")
        tn = self._parse_identifier(identifier)
        row = await self._repo.get_public(tn) if tn else None
        if row is None:
            raise OaiError("idDoesNotExist", "Identificador desconocido")
        root, _ = self._root(request_attrs)
        gr = ET.SubElement(root, f"{{{OAI_NS}}}GetRecord")
        rec = ET.SubElement(gr, f"{{{OAI_NS}}}record")
        self._header(rec, row)
        self._write_metadata(rec, row, prefix)
        return self._serialize(root)

    async def list_records(
        self, request_attrs: dict[str, str], harvest: dict[str, str], *, headers_only: bool
    ) -> bytes:
        offset, req = self._resume(harvest)
        from_dt, until_dt = self._parse_range(req)
        set_spec = req.get("set")
        prefix = req.get("metadataPrefix", "oai_dc")
        if prefix not in _FORMATS:
            raise OaiError("cannotDisseminateFormat", "Formato no soportado (oai_dc, ead)")

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
                self._write_metadata(rec, row, prefix)
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
