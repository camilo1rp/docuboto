# Document Lifecycle

In records management, every document passes through well-defined stages from its creation to its final disposition. OrpycaMCP manages this complete cycle.

---

## The three ages of a document

```
┌─────────────────────────────────────────────────────────┐
│  ARCHIVO DE GESTIÓN         │  Production and processing │
│  (Producing office)         │  Retention: 2-5 years      │
│                             │  Modules: Radicación,       │
│                             │  Flujos, Expedientes        │
├─────────────────────────────────────────────────────────┤
│  ARCHIVO CENTRAL            │  Closed documents           │
│  (Institutional repository) │  Retention: 5-20 years     │
│                             │  Module: Transfers          │
├─────────────────────────────────────────────────────────┤
│  ARCHIVO HISTÓRICO          │  Permanent value            │
│  (Heritage)                 │  Total Preservation (CT)   │
│                             │  or permanent deletion      │
└─────────────────────────────────────────────────────────┘
```

> **Glossary:** *Archivo de Gestión* (management/active archive), *Archivo Central* (central archive), *Archivo Histórico* (historical archive) — the three Colombian archival ages. *Radicación* (official registration), *Flujos* (distribution flows), *Expedientes* (case files).

---

## Flow of an Incoming document (Entrada, E)

The most common case: a citizen or external entity sends a communication.

```
1. RECEPTION
   The document arrives physically or by email.
   The front desk receives it and verifies that it is complete.

2. REGISTRATION (Radicación)
   The number is assigned: 2024-ENTIDAD-E-000001
   Recorded: sender, date, subject, document type, destination dependencia.
   Attachments (anexos) are digitized (PDF) → stored in the object store with SHA-256.

3. DISTRIBUTION (Flujo)
   The system or the operator sends the radicado to the responsible dependencia.
   Logged: who sent it, to whom, when.

4. PROCESSING
   The dependencia handles the document.
   It may reassign it, add observations, link it to an expediente.
   If a reply is required, a linked Outgoing (Salida, S) document is registered.

5. FILING IN AN EXPEDIENTE
   The radicado is linked to the expediente of the corresponding matter.
   The expediente groups all the documents of the same case.

6. CLOSURE AND TRANSFER
   When processing ends, the expediente is closed.
   According to the TRD, it is transferred to the central archive or deleted.
```

---

## Flow of an Outgoing document (Salida, S)

The institution generates and sends an official communication.

```
1. DRAFTING
   The officer drafts the document (templates may be used).

2. OUTGOING REGISTRATION
   The number is assigned: 2024-ENTIDAD-S-000042
   The recipients are recorded (one or several).
   The digitally signed files are attached.

3. DISPATCH
   The system records the dispatch method: physical mail, email, courier.
   The radicado receipt is generated to hand over to the recipient.

4. TRACEABILITY
   The record remains of when it was sent, to whom, and through which channel.
   The outgoing radicado can be linked to the incoming one that originated it.
```

---

## Incoming → Outgoing linking

OrpycaMCP allows linking an outgoing radicado to the incoming one that originated it, forming the complete thread of the communication:

```
E-000001 (Citizen's request)
    └── S-000042 (Entity's response)
        └── E-000156 (Citizen's reply to the response)
```

This linking is the basis for measuring **response times** and complying with legal deadlines.

---

## Legal response deadlines

OrpycaMCP can configure alerts based on the deadlines of the Administrative Procedure Code (CPACA — Ley 1437 de 2011, Colombia's Code of Administrative Procedure):

| Type of request | Legal deadline |
|---|---|
| Right of petition (information) | 15 business days |
| Right of petition (documents) | 10 business days |
| Complaint or claim | 15 business days |
| Consultation to authority | 30 business days |

---

## The expediente as a documentary unit

An expediente is not just a folder of documents. It is the **complete memory of a procedure**:

```
EXPEDIENTE: Contract 2024-001 — Office supplies
│
├── Radicado E-000023 — Invitation to quote
├── Radicado E-000089 — Quote from supplier A
├── Radicado E-000091 — Quote from supplier B
├── Radicado I-000045 — Comparative table of offers
├── Radicado I-000048 — Selection minutes
├── Radicado S-000067 — Notification to the selected supplier
├── Radicado E-000112 — Signed contract
└── Radicado I-000089 — Start minutes
```

**State:** Open → (when the contract ends) Closed → (according to TRD) Transferred to the central archive

---

## Final disposition according to TRD

When the retention period defined in the TRD (Tabla de Retención Documental — document retention schedule) expires, each expediente has one of these dispositions:

| Disposition | Code | What it means |
|---|---|---|
| Total Preservation | CT | Preserved permanently (historical or legal value) |
| Deletion | E | Destroyed following the document deletion procedure |
| Microfilming | MT | Digitized/microfilmed and then the paper is deleted |
| Selection | S | A representative sample is preserved, the rest is deleted |

The document deletion process requires a signed **Acta de Eliminación** (deletion minutes), which is also filed in the system.

---

## Closure, signature, and preservation (F3–F5)

Closing an expediente is not just a status change: it triggers three chained acts
that shield its long-term legal validity (full detail of each entity in
[Document domain](domain.md#compliance-and-lifecycle-entities-f3f6)):

```
Expediente closure
   │
   ├─► The electronic index XML is generated/signed (E15)
   │     XAdES-B signature with institutional seal → the expediente becomes immutable
   │
   ├─► The index (and, if applicable, the BagIt/PREMIS AIP) is uploaded to a
   │     bucket with WORM/Object-Lock (E10) under retention derived from the TRD
   │     → while retention is in effect, final disposition is BLOCKED
   │        (it cannot be deleted before its time)
   │
   └─► Once retention expires and the transfer (E12) occurs, the FUID is
         generated and the expediente moves to `transferred` (frozen)
```

This means final disposition (table above) does **not** happen by the mere passage
of time: the system verifies that WORM no longer retains the artifact before
allowing deletion or secondary transfer.
