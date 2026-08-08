# Regulatory Framework

OrpycaMCP implements the requirements established by Colombian archival regulations. This section explains which standard originates each functionality of the system.

---

## Ley 594 of 2000 (Law 594/2000) — General Archives Law

The fundamental standard. It requires all Colombian public entities to:

- Organize their documents through **Tablas de Retención Documental (TRD — document retention schedules)**
- Guarantee the preservation of the documentary heritage
- Establish a **Programa de Gestión Documental (PGD — Document Management Program)**
- Apply the principle of **provenance** (documents are organized according to the unit that produces them)

**What OrpycaMCP implements from this law:**
- TRD module with a structure of secciones (sections), series and subseries
- Expediente states with transfer control
- Final disposition configurable per serie: CT, E, MT, S

---

## Ley 527 of 1999 and Decreto Único 1074 of 2015 — Electronic and digital signature

These grant **functional equivalence** and evidentiary value to electronic documents and their signatures. **Law 527/1999** defines the electronic signature (methods: keys, biometrics, passwords) and the digital signature (cryptography with a certificate from an accredited authority), plus the **functional equivalence** of the signature (art. 7) and of writing. Its current implementing regulation is **Single Decree 1074/2015, Ch. 47** (arts. 2.2.2.47.1–47.10, which **compile and extend** Decree 2364/2012: reliability 47.4, legal effects 47.5, signer obligations 47.6, security level 47.8) and **Ch. 48** + **Decree 333/2014** (certification entities / **ONAC** accreditation). **Decree 526/2021** enables the electronic signature of employment contracts.

> ⚠️ Cite **DUR 1074/2015**, not Decree 2364/2012 on its own. The full framework is extracted in `documentos/normativa-firma/`.

**What OrpycaMCP implements (or specifies) from this framework** (epic E06):
- Signing of the **electronic index at the closure** of the expediente (AGN Agreement 001/2024 art. 4.3.2.4) — XAdES-B/T/LT/LTA with the institutional seal is implemented; ONAC accreditation is not.
- **Verified attribution** of the signer (JWT identity), **2FA on the personal signing act** and an **electronic seal of the legal person** for self-signing of system-generated documents.
- Preservation of the evidence (signature + certificate + timestamp + countersignature) without altering the signed content.

---

## Acuerdo AGN 060 of 2001 (AGN Agreement 060/2001) — Official Communications

Establishes the guidelines for the **radicación (official registration), distribution and control of communications** in public entities.

Key points:

> *"Entities must establish systems for the control of communications received and sent"*

> *"Communications received shall be assigned a radicado number in the consecutive order in which they are received"*

> *"The radicado number cannot be amended or crossed out"*

**What OrpycaMCP implements from this agreement:**
- Radicación of Incoming (E), Outgoing (S) and Internal (I)
- Consecutive number per year and type, **immutable**
- Recording of the sender, date, destination dependencia and subject
- Traceability of the document's journey (flujos)

---

## Decreto 1080 of 2015 (Decree 1080/2015) — Single Decree of the Culture Sector

It compiled and unified all Colombian archival regulations. It includes the requirements for:

- **Organization of archives** by the document's lifecycle
- **Document inventories** in transfers (FUID form)
- **Document appraisal** to determine final disposition

---

## Ley 527 of 1999 (Law 527/1999) — Electronic Commerce and Electronic Document

It gives legal value to the electronic document when it complies with:

- **Integrity**: the document has not been altered
- **Authenticity**: its origin can be verified
- **Accessibility**: it can be consulted later

**What OrpycaMCP implements from this law:**
- SHA-256 checksum of each anexo to guarantee integrity
- Storage in MinIO with version control
- Traceability of who uploaded each file and when

---

## NTC-ISO 15489 — Records Management

International standard adopted in Colombia. It defines the principles of:

- **Authenticity**: the document is what it claims to be
- **Reliability**: the content is a complete representation of the act
- **Integrity**: it is complete and has not been altered
- **Availability**: it can be located and used when needed

---

## International applicability

Although the main reference framework is Colombian, the concepts are applicable to other Latin American countries with similar regulations:

| Country | Governing body | Equivalent standard |
|---|---|---|
| Colombia | AGN | Ley 594/2000 |
| Mexico | AGN México | General Archives Law 2019 |
| Peru | AGN Perú | Ley 25323 + Decreto 008-92-JUS |
| Argentina | AGN Argentina | Decreto 1571/81 |
| Chile | Archivo Nacional | Decreto con Fuerza de Ley 5200/1929 |
| Ecuador | ANE | National Archives System Law |

OrpycaMCP can be adapted to any regulation by adjusting the TRD configuration and the radicación fields.
