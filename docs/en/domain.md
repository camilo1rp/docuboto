# Document Domain

This section documents the entities, business rules and flows of the Orfeo SGD (*Sistema de Gestión Documental* — Document Management System) that OrpycaMCP reimplements. It is the mandatory reference before implementing any functionality.

> The rules described here come from the analysis of the original Orfeo source code (multiple installations: ICETEX, Bogotá Limpia, Argoik, Veeduría, FondeCund). Use the `orfeo-domain-analyst` agent to dig deeper into any behavior before coding it.

---

## Regulatory framework

OrpycaMCP implements document management according to the Colombian standards of the AGN (Archivo General de la Nación — General Archive of the Nation):

| Standard | What it regulates |
|---|---|
| **Ley 594 of 2000** (Law 594/2000) | General Archives Law — requires documents to be organized by TRD |
| **Acuerdo AGN 060/2001** (AGN Agreement 060/2001) | Official communications — radicación, distribution, numbering |
| **Decreto 1080/2015** (Decree 1080/2015) | Unified archival regulations (Single Decree of the Culture Sector) |
| **NTC-ISO 15489** | International records management standard |

These standards directly define how radicación, expedientes and the TRD must work in the system.

---

## Document management lifecycle

```
Production/Reception → Radicación → Distribution → Processing → Archiving → Final disposition
```

Each stage has specific entities and rules in OrpycaMCP.

---

## Domain entities

### Radicado

The officially registered document. It is the central entity of the SGD.

**What does it do?** It assigns a unique and immutable official number to the document, registers its entry into the institutional system, and links it to a document type, dependencia (organizational unit) and owner.

**Why does it exist?** AGN Agreement 060/2001 requires that every official communication entering or leaving a public entity be numbered, dated and traced. Without a radicado, the document has no legal existence in the system.

**How is it used?**
```bash
POST /api/v1/documents/
{
  "subject": "Solicitud de información pública - Decreto 2232",
  "document_type_id": "uuid-tipo-documental",
  "dependencia_id": "uuid-dependencia-destino",
  "sender_name": "Juan García",
  "sender_entity": "Ciudadano",
  "pages": 3
}
# Response:
# { "tracking_number": "2024-DEMO-E-000001", "id": "uuid", ... }
```

**What can go wrong?**
- Sequence conflict: assignment is atomic (`SELECT FOR UPDATE`), the system retries
- Document type invalid for the tenant's TRD: error 422
- The assigned number **can never be modified or reassigned**

**How to contribute?** `services/document-service/app/services/tracking_service.py`

#### Radicado types

| Code | Name | Description | Standard |
|---|---|---|---|
| **E** | Entrada (Incoming) | Communication received from external third parties | AGN 060 Art. 3 |
| **S** | Salida (Outgoing) | Communication sent to external third parties | AGN 060 Art. 3 |
| **I** | Interno (Internal) | Communication between dependencias of the same entity | Ley 594 |

#### Radicado number format

```
{YEAR} - {ENTITY_CODE} - {TYPE} - {SEQUENCE:06d}

Example: 2024-ICETEX-E-001234
         2024-ICETEX-S-000087
         2024-ICETEX-I-002341
```

- The sequence is **per year + type + entity** — it is never shared between types
- It resets to 000001 every January 1st
- Assignment is **atomic** (transaction with a lock on the `tracking_sequences` table)

---

### Expediente

Logical grouping of radicados related to the same subject or case.

**What does it do?** It organizes the documents of a complete proceeding into a single documentary unit, allowing the entire history of a case to be viewed in one place.

**Why does it exist?** Law 594/2000 and the AGN standards require documents to be organized into coherent documentary units (expedientes) to facilitate consultation and final disposition.

**Expediente states:**
```
Open ──► Closed ──► Transferred to the central archive
```

**Rules:**
- An expediente cannot be closed without a TRD configured for its serie (series)
- The transfer requires the document inventory (AGN form E-7)
- A radicado can belong to only one active expediente

---

### TRD — Tabla de Retención Documental (Document Retention Schedule)

Archival instrument that classifies documents and defines their retention period and final disposition.

**What does it do?** It hierarchically organizes the entity's documents and determines how long they are kept and what happens to them upon expiry.

**Why does it exist?** Law 594/2000 Art. 24 requires all public entities to develop and apply a TRD approved by the AGN or the competent archival authority.

**Hierarchical structure:**
```
Entity
└── Sección (Section, e.g.: General Directorate)
    └── Subsección (Subsection, e.g.: Administrative Subdirectorate)
        └── Serie documental (documentary series, e.g.: Contracts)
            └── Subserie (subseries, e.g.: Service provision contracts)
                └── Tipo documental (document type, e.g.: Contract, Start Record, Report)
```

**Final disposition:**
| Code | Name | Description |
|---|---|---|
| CT | Total Preservation | Kept permanently |
| E | Destruction | Destroyed when retention expires |
| MT | Microfilming + Destruction | Microfilmed before destruction |
| S | Selection | A representative sample is selected |

**Critical rule:** Without a TRD configured for a serie, the expediente that uses it cannot be closed.

---

### Anexo

Binary file attached to a radicado, stored in MinIO.

**What does it do?** It preserves the physical document (PDF, image, scanned letter) linked to the radicado, with integrity guaranteed by checksum.

**Why does it exist?** The electronic document has the same legal value as the physical one when it meets the integrity and authenticity requirements (Ley 527 of 1999 — Electronic Commerce).

**How is it used?**
```bash
POST /api/v1/storage/annexes/
Content-Type: multipart/form-data

radicado_id: uuid
file: [archivo.pdf]

# Response:
# { "annexe_id": "uuid", "filename": "oficio_123.pdf",
#   "sha256": "a1b2c3...", "size_bytes": 204800,
#   "download_url": "..." }
```

**Integrity:** The SHA-256 is computed at upload time and stored. Any download can be verified against this value to detect corruption.

---

### Dependencia

Organizational unit of the institution (department, area, office).

**What does it do?** It represents the organizational structure of the entity. Each radicado has an origin dependencia and a destination one.

**Why does it exist?** Document distribution occurs between dependencias. The TRD is also organized by sections that correspond to dependencias.

**Key attributes:**
- Unique code within the entity
- Owner (head of the dependencia)
- Parent dependencia (for the organic hierarchy)
- Status: active / inactive

---

### Flujo (Distribution)

Record of each movement of the document between dependencias.

**What does it do?** It traces the document's complete journey from its radicación to its archiving, recording who held it, when, what action they took and to whom they sent it.

**Why does it exist?** AGN Agreement 060/2001 requires full traceability of official communications. It also makes it possible to measure response times and detect bottlenecks.

**Typical lifecycle of a flujo:**
```
Radicado E-000001
  → Front desk (radicado) 2024-01-15 09:00
  → General Directorate (distributed) 2024-01-15 09:05
  → Legal Subdirectorate (sent for opinion) 2024-01-16 10:30
  → General Directorate (with opinion, for response) 2024-01-18 14:00
  → Front desk (response registered as S-000042) 2024-01-19 11:00
```

---

### Recipient

Person or entity that receives an outgoing radicado.

**Rule:** An outgoing radicado can have **multiple recipients** (copy to several). Each recipient has its own dispatch and confirmation record.

---

## Compliance and lifecycle entities (F3–F6)

### Electronic index — índice electrónico (E15)
Functional equivalent of **page numbering (foliación) + control sheet (hoja de control)** (AGN Agreement 001/2024). An **append-only versioned XML** document per expediente, with the SHA-256 hash of each document (`valor_huella`), its immutable original order, and the hash of the index itself (`xml_sha256`). It is created at **version 0** when the expediente is opened and is regenerated automatically when radicados are linked/excluded. **Rule:** once the index is signed at closing, the expediente is immutable. (The XAdES signature at closing depends on the `signature-service`, pending.)

### Electronic signature — firma electrónica (E06)
Verifiable record of who signed a radicado/anexo and when: identity (Keycloak) + **SHA-256 hash** of the content + timestamp + reason. A signature verifies if its hash matches the current content (tampering detection). It is an **electronic** signature (Decree 2364/2012); the **digital** PKI/X.509 signature (XAdES/PAdES/TSA) is planned in a dedicated `signature-service` (ADR-016).

### Physical archive: Location, Conservation unit, Signatura (E17)
ArchivesSpace pattern (ADR-017): the **Location** (physical address, recursive hierarchy site→…→drawer) is separated from the **Conservation unit** (movable container: box/folder/file…). The **signatura topográfica** (topographic call number, `DEP01-E05-C0124`) locates the unit and is the intellectual↔physical bridge. A hybrid expediente is linked to its units with a folio range. Units are **lent** (`lent→returned→overdue`) with an append-only movement history.

### FUID — Formato Único de Inventario Documental (Single Document Inventory Form) (E17/E12)
Canonical inventory (Agreement 042/2002) derived from expediente↔unit↔signatura↔serie; a deliverable of every transfer.

### Document transfer — transferencia documental (E12)
Movement of the expediente between the three archives of the lifecycle: **primary** (management→central) and **secondary** (central→historical). Cycle `prepared→sent→received|rejected`. Upon **receipt**, the expediente moves to `transferred` and becomes **frozen** (it accepts no documents). It carries the FUID as a deliverable.

### Digital preservation — preservación digital (E10)
Versioned **Digital Preservation Plan** (target formats, number of copies, fixity periodicity) + immutable record of **PREMIS events** (ingest/fixity/migration/validation/WORM). It guarantees the five qualities over the long term (ISO 14721 OAIS). AIP/BagIt packaging, real WORM (MinIO Object Lock) and PDF/A validation (veraPDF) are planned and pending infrastructure.

### Postal dispatch — envío postal (E20)
Dispatch of an outgoing radicado through a **postal operator** (4-72/Servientrega) with a **tracking guide** number and delivery status (`registered→in_transit→delivered|returned|failed`), with history.

### Webhook (E11)
Per-tenant subscription to domain events: OrpycaMCP delivers the event to external systems via an **HMAC-SHA256 signed POST** (ADR-018).

---

## Critical business rules (summary)

| # | Rule | Consequence if violated |
|---|---|---|
| 1 | The radicado number is **immutable** | Loss of legal traceability |
| 2 | Number assignment is **atomic** | Duplicate numbers |
| 3 | An outgoing radicado can have **N recipients** | Loss of recipients |
| 4 | Every anexo has a **SHA-256** computed at upload | Integrity cannot be guaranteed |
| 5 | Without a configured TRD, **the expediente is not closed** | Non-compliance with Law 594 |
| 6 | The expediente has a **lifecycle** open→closed→transferred | Uncontrolled archiving |
| 7 | Each entity has its **isolated schema** in PostgreSQL | Data leakage between institutions |
| 8 | The **electronic index** is append-only versioned; after closing it is immutable | Loss of the expediente's legal validity |
| 9 | The expediente's **disposition** is derived from its TRD serie at closing | Improper destruction/preservation |
| 10 | Once a **transfer** is received, the expediente is **frozen** | Alteration of the transferred holdings |

---

## Glossary

| Term | Definition |
|---|---|
| **AGN** | Archivo General de la Nación — governing body for archival policy in Colombia |
| **CCD** | Cuadro de Clasificación Documental (Document Classification Scheme) — precedes the TRD |
| **FUID** | Formato Único de Inventario Documental (Single Document Inventory Form) — required in transfers |
| **PGD** | Programa de Gestión Documental (Document Management Program) — mandatory institutional plan |
| **Radicación** | The act of assigning an official number to a document |
| **Serie documental** | Documentary series — set of documents of the same type produced by the same function |
| **Subserie** | Subseries — subdivision of a documentary series |
| **Transferencia primaria** | Primary transfer — movement from the management archive to the central archive |
| **Transferencia secundaria** | Secondary transfer — movement from the central archive to the historical archive |
| **Índice electrónico** | Electronic index — versioned XML with the hash of each document of the expediente; equivalent to page numbering + control sheet (Agreement 001/2024) |
| **Signatura topográfica** | Topographic call number — code that unambiguously locates a conservation unit in the physical archive |
| **OAIS** | Open Archival Information System (ISO 14721) — long-term preservation model (SIP/AIP/DIP) |
| **PREMIS** | Preservation metadata standard (objects, events, agents, rights) |
| **WORM** | Write Once Read Many — immutable storage (MinIO Object Lock) for preservation |
| **PDF/A** | PDF profile for long-term archiving (validatable with veraPDF) |
| **Webhook** | Signed HTTP delivery of domain events to external systems |
