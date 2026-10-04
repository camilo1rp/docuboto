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

#### Radicado status: moved by the workflow (ADR-028)

`registered → distributed → in_progress → closed`, plus `anulado`. **Status is not edited**: workflow-service writes it in the same transaction as the step, as in Orfeo, where distributing is a status change.

| Act | Resulting status |
|---|---|
| Register (no distribution) | `registered` |
| Assign, transfer, return, roll back, reassign (the step is left `pending`) | `distributed` |
| `marcar_leido` **by the holder** of the step | `in_progress` |
| Complete a step without marking it as read | `distributed` (kept; declared) |
| **Finish processing** (explicit act by the holder, with a comment) | `closed` |
| Annulment (written by document-service) | `anulado` |

`closed` and `anulado` are **terminal**: no processing act moves them (`409 radicado_terminal`). The only way to close is "Finish processing"; completing the last step does not close. **Holder** = has the step assigned, or the step belongs to their dependencia and nobody holds it. **Outgoing** and **internal** radicados have no outward distribution: their status only moves when a step touches them. Annulment does not cancel the active step (declared). See [ADR-028](adr/ADR-028-el-flujo-mueve-el-estado-del-radicado.md).


#### Current location of the radicado (ADR-029)

`dest_dept` / `dest_dept_code` are the **registration** dependencia: who the document was addressed to, a legal snapshot fixed at registration that does not change. **Where the radicado is now** is a different thing —in Orfeo, `radi_depe_actu` / `radi_usua_actu`— and the workflow writes it.

| Situation | Current location |
|---|---|
| No steps (just registered) | The registration dependencia (destination; failing that, origin) |
| There is an active step | The dependencia (and holder) of the highest-numbered step |
| No active step | The step that **closed most recently**, in any status (not the highest-numbered one: after a rollback the cancelled step has the highest number but closed earlier) |
| `closed` | The dependencia where it was finished (frozen) |
| `anulado` | The dependencia where it was when annulled (annulment does not rewrite it) |

The step stores the dependencia **name** and `dependencias.nombre` is not unique: the code is only resolved when the name matches **one** active dependencia; otherwise it is `null` and the name is the snapshot. The location is **not public**: neither verification by code, nor OAI-PMH, nor CMIS expose it, and who holds the procedure (`flujo_usuario`) only appears in the authenticated detail. It can be filtered and counted (`?ubicacion_dept_code=`; the "Current location" report, which excludes closed and annulled). See [ADR-029](adr/ADR-029-ubicacion-actual-del-radicado.md).
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

**The TRD is a validated instrument, not a configuration table ([ADR-025](adr/ADR-025-trd-instrumento-convalidado-versionado.md)).** Every version of a serie is an **append-only** row: a substantive change (retention, disposition, name…) is never applied in place except over a `borrador` version — it creates a new version (`version+1`). An expediente pins, **at closure**, the specific version that governs it (`trd_serie_id` is frozen forever at that moment); later versions of the same serie **never** retroactively alter the disposition calendar of an already closed expediente.

The AGN Agreement 001/2024 circuit (which compiles 004/2019), modelled as the instrument's state:

```
[ new serie ] ──► borrador ──approve──► aprobada ──validate──► convalidada
                     ▲                     │                        │
                     └─────return──────────┘   (derogates the       │
                       (reason mandatory,       previous current    │
                        the Committee record     one, re-points     │
                        becomes VOID)            orphans)           ▼
 [ pre-existing series ] ──► migrada ──ratify (real act)──► convalidada
                                 │                                  │
                                 └────────── derogate ──────────────┴──► derogada
```

| State | Governs a **closure** | Governs **effective destruction** | Mutable content |
|---|---|---|---|
| `borrador` | No | No | Yes (the only state editable in place) |
| `aprobada` | No (Committee approval, a necessary but insufficient condition) | No | No |
| `convalidada` | Yes | Yes | No (except `pdfa_profile`; the act is immutable at engine level — migration 038) |
| `migrada` | Yes, **declaring it** (`serie_estado: "migrada"` in the snapshot; expediente flagged `trd_revision_requerida`) | No | No (except `pdfa_profile`) |
| `derogada` | No | Only if the frozen snapshot shows it **was validated** before being derogated (`acto_administrativo` present) — never if it derogated a `migrada` that never went through the circuit | No |

`migrada` is the honest backfill state: "this serie existed before the validation model; the system does not know whether it was validated, nor under which act" — a validation nobody performed is never asserted (RF-FIR-15). **Ratification** (`migrada → convalidada`, with the real administrative act) creates no new version and clears the `trd_revision_requerida` flag from the expedientes closed under it. `aprobada` now has an honest exit besides `convalidar`: **`devolver`** (migration 038) returns it to `borrador` with a reason, for the ordinary case of "the validating body asks for adjustments" — previously the only door was to declare a validation that might not have taken place.

Effective destruction (final disposition; today with no job to execute it) requires the **frozen snapshot** left in `expedientes.disposition` at closure to describe a validated instrument: `serie_estado == "convalidada"`, or `"derogada"` with `acto_administrativo` present (D5, gate corrected in migration 038 — the original criterion read the LIVE row and would have blocked legitimate destructions of expedientes whose instrument was validated and later derogated by a subsequent update).

#### TVD — Documentary Appraisal Schedule for accumulated holdings ([ADR-026](adr/ADR-026-tvd-fondo-acumulado.md), Increment 1)

**Why does it exist?** An institution with **accumulated holdings** (*fondo acumulado*) — documentation already produced, piled up without archival criteria, typically from suppressed units or wound-up entities — has no TRD to apply (a TRD orders **future** production; accumulated holdings are already produced). AGN Agreement 001/2024 (which compiles 004/2019) subjects the TVD to the **same circuit** as the TRD: drafting, Committee approval, Council validation, RUSD registration, publication.

**It is the same instrument, with different content.** A TVD is **not** a table of its own: it is a `tipo_instrumento ∈ {TRD, TVD}` discriminator over the same `trd_series` (the reason: `expedientes.trd_serie_id` is the single seam from which nine points derive legal consequences, seven of them feeding WORM COMPLIANCE `retain_until` — irreversible; forking that seam is the design the ADR rejects). It shares with the TRD the full validation circuit, the states, the append-only rule and the final-disposition gate. It differs in that it has no **records-management phase** (the holdings are already in central/historical archive), its extreme dates (`fecha_extrema_inicial`/`fecha_extrema_final`) belong to the **holdings**, not to a closure, and its `justificacion_valoracion` **is the object** of the instrument — the reasoning that would be cited in a destruction record.

**The property that defines Increment 1 (all that is implemented today): a TVD does not yet govern any expediente.** The engine guarantees this with a trigger that rejects **any** expediente bound to a TVD row — zero WORM risk by construction. An institution can register, approve, validate, register in the RUSD and publish its TVD right now; binding accumulated-holdings expedientes to it and computing their retention calendar (from the extreme date, **never** from `closed_at` — computing from today would make documentation whose retention has already lapsed undeletable for decades) is Increment 2, not yet built.

---

### Anexo

Binary file attached to a radicado, stored in the S3 object store.

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
Functional equivalent of **page numbering (foliación) + control sheet (hoja de control)** (AGN Agreement 001/2024). An **append-only versioned XML** document per expediente, with the SHA-256 hash of each document (`valor_huella`), its immutable original order, and the hash of the index itself (`xml_sha256`). It is created at **version 0** when the expediente is opened and is regenerated automatically when radicados are linked/excluded. **Rule:** once the index is signed at closing, the expediente is immutable. Verifying the index checks each `valor_huella` against the document's current hash, **re-hashes the stored XML against `xml_sha256`** (`huella_indice_ok`, detecting alteration of the index itself) **and** validates the index's signature against the `signature-service` (XAdES-B with the institutional seal, ADR-016 Inc.1). **XAdES-T/LT/LTA** (TSA, CRL/OCSP) is implemented for the index seal (E06 Inc.4/5/7); what remains outstanding is ONAC accreditation, not the format.

**Index profile v4 — "fully conformant" (E15, [ADR-022](adr/ADR-022-indice-electronico-v2-metadatos.md)):** new indexes are generated in `urn:orpycamcp:indice:v4`, which **closes the 4 residues of v3** at capability level. It adds, **per document**, the **producing dependencia** (supplied at link time — the signing build is deterministic and does not call document-service; it is omitted on legacy documents lacking the datum) and the **declaration date** (= the date of incorporation into the expediente, the Orfeo `SGD_EXP_FECH` equivalent); and, **in the header**, an **access policy** (`no-read-up` + permission + minimum clearance — stable and portable across transfers, unlike a dump of roles) alongside the security level, an **attestation of the audit trail** (a reference to `public.audit_log`, embedding no live entries) and a per-instance **conformance marker**. **Reads** of the expediente/index are now **recorded** in the access trail (`audit_log`), closing the access history Orfeo never had. The v4 **content_hash canonicalises the whole tree** (v3 covered only `<documentos>`). It is "fully conformant" at the level of the **profile/capability**; completeness is **per instance** (a legacy expediente may omit the producing dependencia and its trail starts at instrumentation) and every index **self-declares it** in `<conformidad>` — RF-FIR-15, do not over-declare. The **live** datum of which roles may access is queried separately (`GET /expedientes/{id}/roles-autorizados`), **outside** the signed artifact.

**Index profile v3 — "extended conformance" (E15, [ADR-022](adr/ADR-022-indice-electronico-v2-metadatos.md)):** v3 indexes carry, per document: RT-15 metadata (format/size/foliation/date of incorporation, captured at link time), a **STABLE documentary order** (assigned at link time with an atomic `MAX+1`, **immutable**, never renumbered — the principle of original order, AGN 001/2024 art. 4.3.2.3), an **access control list** (the expediente's security level) and, in the header, **context** (`fechaPrimerUso` + series/subseries by name). **Excluding** a radicado is a **soft delete** (`estado='excluido'`): the ordinal is not reused and the index emits the excluded document flagged (`excluido="true"` + date/reason) → a **traceable gap**. Indexes in **v1/v2/v3 that are already signed remain immutable**. It is **"extended conformance"**, **not** "fully conformant": it lacks a per-document **access history**, the **declaration date**, **role-based ACLs** (which live in auth-service) and the **producing dependencia** (no field) — full conformance is not claimed while those are missing (RF-FIR-15).

### Electronic signature — firma electrónica (E06)
Verifiable record of who signed a radicado/anexo and when: identity (the signer's JWT, RF-FIR-11) + **SHA-256 hash** of the content + the server's timestamp + an HMAC `firma_blob` with a service secret (RF-FIR-12). A signature verifies if its recomputed HMAC matches (tampering detection). It is a **basic electronic** signature (Law 527/1999; DUR 1074/2015 Ch. 47); the **digital** PKI/X.509 signature (XAdES/PAdES/TSA) lives in the `signature-service` (ADR-016) — implemented for the institutional seal and for the per-user personal signature, **not ONAC-accredited**.

**Signature chain** (`firma_solicitud` + `firma_cadena`): a request groups the ordered signing turns (`orden` 1..N) for a documentary object. The **active turn** is the lowest pending order; signer N+1 can only act after turn N has been signed (RF-FIR-04). Turn and request states: `pendiente → firmado` or `rechazado` (a rejection stops the whole chain). The "signer's inbox" lists, across documents, the requests whose active turn belongs to the user. Every act (create/sign/reject) is recorded in `audit_log` (RF-FIR-12).

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
| 11 | Every radicado is born with a **document type and a department** (E/I → destination; S → origin; AGN 060 §5.2) | A document with no TRD series and no inbox |
| 12 | Every **classified or reserved** radicado carries its legal basis (never NULL: the database enforces it) | A row with no stated basis in the art. 20 index |
| 13 | The **level-change** event is born in the same transaction as the change (outbox) | Semantic index serving a stale level |
| 14 | Publishing a radicado **does not publish its attachments**; what came through the public window is not published ex officio | Disclosure of third-party data (Law 1581) |
| 15 | Public intake records the **personal-data authorisation** and is **idempotent** by key | A radicado without consent; two sequence numbers for one retry |

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
| **WORM** | Write Once Read Many — immutable storage (S3 Object Lock) for preservation |
| **PDF/A** | PDF profile for long-term archiving (validatable with veraPDF) |
| **Webhook** | Signed HTTP delivery of domain events to external systems |
