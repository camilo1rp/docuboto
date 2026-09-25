# ADR-023 — WORM / Object-Lock de preservación (MinIO) y hoja de ruta AIP/PREMIS

**Estado**: Aceptado (Increment A). Ratificado por `orfeo-architect`; auditado APTO por `tenant-security-auditor` y `archival-compliance-auditor`.

**Fecha**: 2026-07-06.

> **Actualizado por [ADR-027](ADR-027-sustitucion-almacen-objetos-rustfs.md) (2026-09-25).**
> El motor ya **no es MinIO**: archivó su proyecto de código abierto y retiró sus imágenes de
> todos los registros. El almacén es **RustFS**, elegido por una batería de conformidad WORM
> contra un S3 real. **Lo que este ADR decide no cambia** —Object Lock en modo COMPLIANCE por
> objeto, retención derivada de la TRD, gate de disposición—: cambia quién lo implementa, y
> desde ADR-027 esa garantía se **verifica** (read-back) en vez de suponerse.

**Contexto normativo**: Ley 594/2000 (Ley General de Archivos), Acuerdo AGN 060/2001, ISO 14721 (OAIS), RFC 8493 (BagIt), PREMIS v3.

---

## Contexto

E10 tenía plan de preservación + eventos PREMIS-lite funcionales, pero el **empaquetado AIP era un stub textual en BD**, el **PREMIS un placeholder** (namespace inventado, sin agent, sin XSD) y **no existía inmutabilidad de infraestructura**: el índice electrónico firmado (E15/E06) — el artefacto de mayor valor legal — se subía a MinIO por el flujo normal, con solo la inmutabilidad *lógica* de la aplicación. Un SGDEA conforme exige **inalterabilidad garantizada** de los documentos de archivo durante su período de retención.

`miniopy-async` (1.23.5, ya en uso) soporta Object-Lock completo, pero con una **restricción dura**: el object lock **solo puede habilitarse al crear el bucket**. Los buckets `orpycamcp-{slug}-documents` existentes no pueden convertirse retroactivamente.

## Decisión

Se divide el trabajo en tres incrementos; **este ADR cubre el Increment A** y declara B y C como hoja de ruta (no deuda oculta).

- **Increment A — Fundación WORM + índice firmado protegido** (implementado): bucket de preservación WORM por tenant, protección del índice firmado bajo retención derivada de la TRD, y gate de disposición. Entrega la garantía de inmutabilidad sobre el artefacto crítico por sí solo.
- **Increment B — AIP real + PREMIS v3** (siguiente): `build_bagit` real (bytes de MinIO, fixity recalculada, Bag RFC 8493 serializado zip), PREMIS v3 conforme (namespace oficial, object/event/agent, XSD con lxml), Bag subido al bucket WORM.
- **Increment C — diferido (roadmap)**: validación PDF/A con veraPDF (dependencia Java externa) + REPLICA (segunda copia geográfica).

### Increment A — contrato

1. **Bucket WORM por tenant** `orpycamcp-{slug}-preservation`, con `slug` derivado **exclusivamente del claim JWT validado**, nunca del body (envenenar el WORM de otro tenant es **irreversible**). Provisioning idempotente: no existe → `make_bucket(object_lock=True)`; existe con lock → OK; existe **sin** lock → **error ruidoso** (no se continúa con garantía falsa; el lock no se puede reactivar).
2. **Retención por-objeto** (`Retention(mode, retain_until)` en cada `put_object`), no retención default de bucket — cada expediente tiene una retención TRD distinta. **La retención la aporta el caller** (archive/workflow, dueños de la TRD); storage-service **no** invierte la dependencia llamando a archive. `retain_until` se valida (presente, futuro, dentro de un piso `PRESERVATION_MIN_RETENTION_DAYS` y un techo sensato); la derivación desde `retencion_anios + fecha_inicio` usa **aritmética de años calendario** (no `timedelta` ingenuo, para no desfasar por bisiestos).
3. **Modo COMPLIANCE por defecto** (inmutable de verdad, ni el root borra antes de expirar), configurable (`PRESERVATION_RETENTION_MODE`) para test-safety; **guard de arranque fail-closed**: cualquier `app_env` que no sea un entorno de no-producción reconocido se trata como producción y **rechaza el arranque** si el modo no es COMPLIANCE (un `app_env` mal escrito no degrada la garantía en silencio).
4. **Endpoint** `POST /api/v1/preservacion/proteger-indice` (gateado `USUA_PERM_EXPEDIENTE`, `min_crud=3` — umbral de disposición): verifica **fixity** (recalcula el SHA-256 sobre los bytes y lo contrasta con el hash **registrado en la subida original** — el llamador no declara ningún hash; detecta corrupción antes de fijar el objeto; mismatch → evento `FIJACION` fallo + `audit_log` `file.fixity_fallo` + **no** sube), sube al bucket WORM con `Retention`/`legal_hold`, registra en `preservacion_worm_objeto`, y emite eventos PREMIS (`FIJACION`, `WORM`) + `audit_log`.
5. **Gate de disposición**: `purge_for_disposition` consulta `preservacion_worm_objeto` y **bloquea** el borrado de cualquier objeto del expediente mientras haya una fila con `retain_until` futuro **o** `legal_hold=true` (`WormLockActiveError` + `audit_log`).
6. **Legal hold** para conservación total / expedientes en litigio (retención indefinida, independiente de `retain_until`).

Como storage-service no tenía RBAC, se añadió `app/core/clearance.py` (twin de archive-service) y `tenant_schema()` en `database.py`; el gate usa el permiso **ya sembrado** `USUA_PERM_EXPEDIENTE` (sin sembrar permisos nuevos). Migración tenant `006_preservacion_worm.sql` (tabla + índice), validada idempotente en Postgres 15.

## Tensión WORM ↔ disposición TRD

El período WORM **deriva de la retención TRD** y la **disposición final solo ocurre tras expirar**. No es un conflicto: es exactamente lo que exige Ley 594/2000 + Acuerdo AGN 060/2001 — COMPLIANCE *hace cumplir* la ley. Como retención WORM y disparo de disposición derivan de la **misma** TRD, en el caso normal expiran juntos; el bloqueo del gate es la **red de seguridad** contra eliminación prematura (bug de derivación o disposición manual anticipada), no el camino feliz. `legal_hold` cubre conservación total y litigio.

## Estrategia de test

Los tests de storage-service **mockean el cliente MinIO por completo**; **ningún test escribe objetos COMPLIANCE reales** (un objeto COMPLIANCE es genuinamente imborrable y rompería la repetibilidad de la suite). Se asertan las **llamadas** (`make_bucket(object_lock=True)`, `put_object` con el `Retention` correcto, derivación de `retain_until`, nombre de bucket desde el claim, gate de purge, idempotencia del provisioning). El modo configurable existe precisamente para esta test-safety y para el guard de prod.

## Consecuencias

**Positivas**: inmutabilidad de infraestructura real sobre el índice firmado; la fixity amarra preservación con firma; el gate impide eliminación prematura (conformidad legal); aislamiento WORM por tenant; primer RBAC en storage-service. **Negativas / límites**: COMPLIANCE es irreversible — un `retain_until` mal derivado deja objetos imborrables por años (mitigado: COMPLIANCE solo en prod, cotas de retención, derivación desde la TRD como única autoridad); versioning implícito cambia la semántica de delete (delete markers); AIP real + PREMIS v3 (Increment B) y veraPDF/REPLICA (Increment C) siguen pendientes. **Frontera de confianza**: la correspondencia `expediente_id ↔ file_id` la garantiza **archive-service** antes de llamar; storage valida que el `file_id` exista en el tenant pero no modela expedientes.

---

## Addendum — Increment B (2026-07-06): AIP real (BagIt RFC 8493) + PREMIS v3

**Estado**: Aceptado. Auditado APTO por `tenant-security-auditor` (tras remediar 2 Alta + 5 Media) y `archival-compliance-auditor`.

### Decisión

**Convertir el AIP de un stub textual en BD en un paquete OAIS real**: BagIt RFC 8493 serializado (ZIP) con los **bytes reales** de los documentos y **fixity recalculada**, PREMIS v3 (namespace oficial) validado estructuralmente, subido al **bucket WORM** del Increment A y cubierto por el gate de disposición.

- **Mapeo documento→objeto**: `AipDocInput` gana `file_id` (localizador real en `files`); `valor_huella` se **elimina** (la fixity se recalcula, no se confía en la provista).
- **Bag real** (`aip_builder.py`): por cada documento se descargan los bytes de MinIO por `object_key`, se **recalcula el SHA-256** y se contrasta con `files.sha256` — mismatch → aborta todo el empaquetado (409, `FIJACION` fallo, `audit_log`), no empaqueta contenido corrupto. Se arma `data/{nombre}` + `bagit.txt` + `bag-info.txt` (Payload-Oxum real) + `manifest-sha256.txt` + `premis.xml` + `indice-firmado.xml` (opcional) + `tagmanifest-sha256.txt` (que **cubre** `manifest-sha256.txt`, RFC 8493 §2.2.1), serializado ZIP. `aip_sha256` pasa a ser el SHA-256 del ZIP completo.
- **PREMIS v3**: namespace oficial `http://www.loc.gov/premis/v3`, entidades `object`/`event`/`agent`, validado contra un **perfil XSD local** (no el schema completo de la Library of Congress — vendorizar el oficial + emitir `xsi:type="premis:file"` queda para Increment C; la conformidad se declara como **validación estructural contra perfil v3**, no "conforme pleno", RF-FIR-15). Se añadió `lxml` a storage-service (lockfile regenerado).
- **WORM + gate**: el ZIP se sube al bucket de preservación con `Retention` (derivada de la TRD, mismo contrato que `proteger-indice`) + `legal_hold`, y se registra en `preservacion_worm_objeto` con `tipo='aip'` → el **gate de disposición del Increment A también cubre el AIP**. El registro (`insert_aip` + `worm_repo.insert` + eventos `INGESTA`/`FIJACION`/`WORM` ok + `audit_log`) va en una **única `conn.transaction()`** (atomicidad; la subida WORM, irreversible y externa a la BD, ocurre antes). Migración tenant `007` (aditiva/nullable, validada idempotente en PG15).
- **Endpoint** `POST /api/v1/preservacion/aip` gateado `USUA_PERM_EXPEDIENTE` `min_crud=3`; **todas** las lecturas/escrituras del router de preservación quedan gateadas de forma consistente (`GET /aip`, `GET /plan`, `GET /plan/versions`, `list_eventos` con `min_crud=1`; `PUT /plan`, `POST /eventos` con `min_crud=3`) — endurecimiento de una fuga de metadatos (GET exponía nombres/hashes/ubicación WORM de documentos clasificados) y de escrituras ungated a la traza PREMIS.

### Endurecimientos de la auditoría (remediados)

**Alta**: (1) **colisión de nombres de payload** — dos documentos con igual `nombre` se sobrescribían en el ZIP mientras el manifest contaba ambos → Bag inválido y pérdida silenciosa de contenido, **irreparable bajo WORM COMPLIANCE**; cerrado con saneo de `nombre` (rechaza `/`, `\`, `..`, control chars) + unicidad (422) + `Payload-Oxum`/`num_files` derivados de las entradas reales; (2) **`GET /aip` sin RBAC** → fuga del inventario de documentos; gateado. **Media**: tagmanifest que no cubría el manifest; sobre-declaración de conformidad PREMIS; `AipResponse` que rompía la retrocompat con filas stub (campos WORM → `Optional`); rastro de eventos incompleto; no-atomicidad del registro.

### Límites (Increment C, diferido en el momento de B)

Validación **PDF/A con veraPDF** (dependencia Java externa) + evento `VALIDACION_PDFA`; **REPLICA** (segunda copia); XSD PREMIS oficial completo + `xsi:type` (→ cerrado en C1a). **76 tests verdes** (MinIO 100% mockeado).

---

## Addendum — Increment C1a (2026-07-06): PREMIS v3 conformidad plena

**Estado**: Aceptado. Auditado APTO por `archival-compliance-auditor`.

**Cierra el gap de honestidad de B**: el AIP validaba su `premis.xml` contra un **perfil XSD local propio** (validar contra un schema que escribimos nosotros no prueba conformidad). C1a lo hace **conforme pleno**: valida contra el **`premis.xsd` OFICIAL de la Library of Congress** (vendorizado en `services/storage-service/schemas/premis-v3-loc.xsd`, 52.845 B, `targetNamespace=http://www.loc.gov/premis/v3`, **autocontenido** — la referencia a `xlink` fue eliminada en PREMIS v3).

- El elemento `<object>` emite ahora **`xsi:type="premis:file"`** (mecanismo oficial de tipado por sustitución: `objectComplexType` es `abstract` y `file`/`representation`/`bitstream` lo extienden) y se **elimina el elemento `<objectCategory>`** (era de PREMIS v2, no existe en v3). El `nsmap` del root gana los prefijos `premis` y `xsi`. `_SCHEMA_PATH` reapunta al XSD oficial; el perfil local `premis-v3.xsd` se **elimina** (`git rm`).
- **Criterio binario de "conforme pleno"** (ratificado): el `premis.xml` emitido (`object file` + `event` ingestion/fixity check + `agent`) pasa `assertValid` contra el schema **normativo**. La **ausencia** de `rights`/`environment`/`relationship`/`significantProperties`/`preservationLevel` **es conforme** (opcionales en el oficial; `object file` solo exige `objectIdentifier` + `objectCharacteristics`) — no un gap. Notable: **cero contenido semántico nuevo obligatorio** — la estructura de B ya era conforme salvo el tipado del `object`.
- **Sin migración, sin cambios de endpoint/RBAC/acceso a datos** — puro cambio de emisión XML + XSD vendorizado. **79 tests verdes** (incluye un test que valida contra el XSD oficial y uno negativo que confirma que `assertValid` rechaza estructura inválida). **Deuda de roadmap (no incumplimiento)**: `formatName` lleva el MIME sin `formatRegistry`/PRONOM; vocabularios controlados y entidades opcionales (rights, preservationLevel) quedan como refinamiento futuro. **REPLICA** (C1b) y **veraPDF/PDF-A** (C2) siguen pendientes.

---

## Addendum — Increment C1b (2026-07-06): REPLICA del AIP

**Estado**: Aceptado. Auditado APTO por `tenant-security-auditor` y `archival-compliance-auditor`.

**Segunda copia inmutable del AIP** para durabilidad de preservación (OAIS *multiple copies*, `preservacion_plan.num_copias`).

- **Disparo por `num_copias`** (copias totales = 1 primaria + N réplicas): `num_copias<=1` (o sin plan) → **cero réplicas** (respeta el plan de copia única); `>=2` → **una** réplica en C1b. Si `num_copias>2` con solo target local, el déficit se registra honestamente en el evento (`copias_solicitadas` vs `copias_hechas`); el fan-out multi-réplica es roadmap.
- **Target configurable** (`app/services/aip_replica_service.py::replicate_aip`): default **local** — segundo bucket del mismo MinIO `orpycamcp-{slug}-preservation-replica` (nombre **solo del claim JWT**), provisionado con `object_lock=True` y `copy_object` server-side con la **misma `Retention`/`legal_hold`** que el primario (réplica tan inmutable como el original); si `MINIO_SECONDARY_ENDPOINT` está configurado → **cross-site** a un segundo MinIO (segundo cliente + `put_object`). Feature-flag `PRESERVATION_REPLICA_ENABLED` (default `false`).
- **Best-effort NON-fatal** (crítico): la réplica se intenta **después** de subir el AIP primario a WORM y **antes** de la transacción crítica; `replicate_aip` **nunca propaga excepciones** — un fallo de I/O de MinIO se captura y se refleja como `ReplicaResult(ok=False)`, de modo que un fallo de durabilidad opcional **jamás revierte** el AIP primario ya sellado. El resultado se registra como **`preservacion_evento` tipo `REPLICA`** (`resultado=ok|fallo`) dentro de la tx — **único registro**: **no** se registra en `preservacion_worm_objeto` (evitar migrar el `CHECK` y no contaminar el gate de disposición; la inmutabilidad la da el object-lock del bucket réplica) ni se retrofitea el `premis.xml` sellado (la réplica del *objeto*-AIP es un evento de repositorio, no de contenido).
- **Honestidad (RF-FIR-15)**: la réplica local (mismo MinIO) se etiqueta explícitamente como *"réplica local (segundo bucket), no cross-site"* en la respuesta (`replicado`/`replica_target`/`replica_copias_hechas`) y el `detail` del evento; la cross-site real queda como capacidad de prod activable por config. **Sin migración**. **87 tests verdes** (incluye el fallo de réplica best-effort no-fatal con el AIP primario intacto, y bucket réplica del claim vs body).
- **Baja aceptadas (roadmap/edge, no bloqueantes)**: sin re-verificación de fixity post-réplica en el camino cross-site; `str(exc)` crudo de MinIO en el `detail`/respuesta (acotar); sin validación de que `MINIO_SECONDARY_ENDPOINT` difiera del primario (réplica ilusoria si coinciden); límite de 5 GiB de `copy_object` server-side (usar `compose_object` para AIP grandes).

---

## Addendum — Increment C2a (2026-07-06): validación PDF/A — contrato + honestidad (sin veraPDF real)

**Estado**: Aceptado. Auditado APTO por `archival-compliance-auditor` y `tenant-security-auditor`.

**Cablea el paso de validación PDF/A del AIP (OAIS, validación en ingesta) de forma honesta**, dejando la interfaz lista para que **C2b** enchufe veraPDF real. No existe un validador PDF/A puro-Python maduro (veraPDF es Java) y no se bloatea la imagen `slim` de dev con una JVM para una feature off-by-default — el validador real y su imagen de deploy son C2b.

- **Interfaz pluggable** (`app/services/pdfa/`): `PdfaValidator` (Protocol, `async validate(pdf_path, *, profile) -> PdfaResult`), `StubPdfaValidator` (**default, honesto**: siempre `outcome="not_evaluated"`, `validator_agent="stub/validator_unavailable"` — **no finge**), factory que si `PDFA_VALIDATOR=verapdf` pero el binario no está degrada a stub con warning (no crashea), y `run_pdfa_validation` (materializa a tempfile, **nunca lanza**).
- **Migración `008` — habilita la honestidad**: `preservacion_evento.resultado` era `CHECK IN ('ok','fallo')`; el honesto `no_evaluado` no cabía (forzarlo a `ok` implica conformidad, a `fallo` no-conformidad — ambos mienten). La migración amplía la columna a `VARCHAR(16)` y el CHECK a `('ok','fallo','no_evaluado')` (idempotente, validada en Postgres 15). Sin columnas nuevas (`detalle` JSONB + `agente` cubren perfil/versión/outcome).
- **Encaje** (`empaquetar_aip`): detecta PDFs por **magic bytes `%PDF`** (no por `mime_type`, que lo declara el cliente), corre la validación **fuera de `conn.transaction()`** (un subprocess JVM futuro no puede correr con la tx abierta ni hacer rollback del AIP), y emite un `preservacion_evento` `VALIDACION_PDFA` por documento (`resultado` ok|fallo|**no_evaluado**) + un evento PREMIS **`validation`** (`eventOutcome` passed|failed|**"not evaluated"**) + un software Agent por validador — todo **sigue validando contra el XSD oficial** (`premis-v3-loc.xsd`, C1a) sin tocarlo.
- **Advisory por defecto** (ratificado): un PDF válido no-PDF/A **no bloquea** el AIP (el documento ya está radicado con número inmutable; el remedio archivístico es **migrar el formato**, no rechazar). No hay modo estricto en el empaquetado. `PDFA_PROFILE` (default `2b`) es **política declarada**, no implícita. **Honestidad (RF-FIR-15)**: `"not evaluated" ≠ "passed"`; el wording deja claro que la validación no se realizó (validador no disponible). **95 tests verdes**; **veraPDF real (subprocess endurecido + imagen de deploy + CI) = C2b, roadmap**. Baja aceptadas: `%PDF` exigido en offset 0; `pdfa_profile` no cotejado con `preservacion_plan.formatos_destino`; `object_ref=file_id` en el evento vs `document_id` en el PREMIS (cross-ref).

## Relacionados

- ISO 14721 (OAIS); RFC 8493 (BagIt); PREMIS v3; Ley 594/2000; Acuerdo AGN 060/2001.
- [ADR-022](ADR-022-indice-electronico-v2-metadatos.md) (índice electrónico), [ADR-016](ADR-016-firma-electronica.md) (firma del índice), [ADR-015](ADR-015-trd-ccd-modelo.md) (TRD/retención).
