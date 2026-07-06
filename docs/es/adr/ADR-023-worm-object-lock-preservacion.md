# ADR-023 — WORM / Object-Lock de preservación (MinIO) y hoja de ruta AIP/PREMIS

**Estado**: Aceptado (Increment A). Ratificado por `orfeo-architect`; auditado APTO por `tenant-security-auditor` y `archival-compliance-auditor`.

**Fecha**: 2026-07-06.

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

### Límites (Increment C, diferido)

Validación **PDF/A con veraPDF** (dependencia Java externa) + evento `VALIDACION_PDFA`; **REPLICA** (segunda copia geográfica); XSD PREMIS oficial completo + `xsi:type`. **76 tests verdes** (MinIO 100% mockeado).

## Relacionados

- ISO 14721 (OAIS); RFC 8493 (BagIt); PREMIS v3; Ley 594/2000; Acuerdo AGN 060/2001.
- [ADR-022](ADR-022-indice-electronico-v2-metadatos.md) (índice electrónico), [ADR-016](ADR-016-firma-electronica.md) (firma del índice), [ADR-015](ADR-015-trd-ccd-modelo.md) (TRD/retención).
