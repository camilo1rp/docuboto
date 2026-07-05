# ADR-022: Perfil v2 del índice electrónico — metadatos RT-15 por documento

**Estado**: Aceptado
**Fecha**: 2026-07-02
**Autores**: Giampiero (mantenedor principal)
**Relacionados**: [ADR-016](ADR-016-firma-electronica.md) (firma XAdES-B del índice), E15 (índice electrónico), RT-15 (`documentos/requisitosTecnicos/15-disenio-indice-electronico.md`)

---

## Contexto

El índice electrónico (E15) que OrpycaMCP firma al cierre (ADR-016 Inc.1) emitía un XML **básico** (`urn:orpycamcp:indice:v1`): por documento solo identificador, título, tipología, fecha, `valorHuella`, `funciónResumen` y orden. El Acuerdo AGN 001/2024 art. 4.3.2.3 (RT-15, cobertura R.1.34) exige más metadatos por documento — entre ellos **formato** (MIME), **tamaño** en bytes y **foliación** —; la auditoría de conformidad de Inc.1 marcó que se estaba firmando un índice metadatológicamente incompleto y que no debía rotularse "conforme completo".

Los datos de formato/tamaño/folios viven en `document-service` (`anexos.mime_type`/`file_size`/`folios`); archive-service, que vincula radicados a expedientes, no los tiene. La fecha de incorporación ya existía como `expediente_radicados.added_at` pero se emitía con el nombre semánticamente impreciso `<fechaArchivado>`.

## Decisión

**Introducir el perfil v2 del índice (`urn:orpycamcp:indice:v2`, `xsdVersion="v2"`) que emite los metadatos RT-15 capturables por documento, capturándolos al vincular el radicado.**

1. **Captura al vincular (no consulta inter-servicio).** `POST /api/v1/expedientes/{id}/radicados` acepta `formato`/`tamano_bytes`/`folios` opcionales por radicado y los persiste en `expediente_radicados` (migración `016`) — **mismo patrón que `content_hash`** ya aportado al vincular. Se descartó que archive-service consultara document-service en build/cierre (introduciría acoplamiento inter-servicio y superficie de fallo en el momento de firma). Trade-off aceptado: confía en el emisor; los radicados ya vinculados quedan con estos campos en `NULL`.

2. **Bump de namespace v1 → v2.** El cambio no es puramente aditivo (se renombra `<fechaArchivado>`→`<fechaIncorporacion>` y se introduce **omisión** de elementos ausentes), así que un mismo namespace describiría dos estructuras. Nuevo `NS`/`XSD_VERSION` como única fuente de verdad (atributo XML + fila de BD); XSD documental en `services/archive-service/schemas/indice-v2.xsd` (contrato, no gate de runtime). Los índices v1 ya firmados quedan **inmutables**: la verificación **re-hashea el `xml` almacenado**, no reconstruye.

3. **`NULL → se omite el elemento`** (no elemento vacío). La ausencia comunica "metadato no declarado" — el estado honesto de un radicado vinculado antes de este cambio — en vez de afirmar falsamente un valor vacío.

4. **`content_hash` de idempotencia incluye los nuevos campos** (con prefijo de longitud/netstring para que un `formato` con delimitadores no colisione el canon). Así, corregir formato/tamaño/folios de un radicado en un expediente **abierto** dispara `version+1`.

5. **No sobre-declarar (RF-FIR-15).** v2 = perfil **"ampliado (RT-15 parcial)"**, NO "conforme completo": siguen faltando `orden_documental` estable (E02), `listaControlAcceso` y `<contexto>` (fuera de alcance, otro incremento). No se introduce bandera `conforme_completo`. El `estado` del índice sigue siendo de **firma** (`vigente|pendiente_firma|firmado`), no de completitud.

## Consecuencias

**Positivas:**
- El índice firmado incluye los metadatos RT-15 capturables; el snapshot por versión (`expediente_indice_item`) los congela (inmutabilidad probatoria por versión).
- Cero acoplamiento inter-servicio nuevo; consistente con el patrón `content_hash`.
- Endurecimiento colateral: `verify` ahora contrasta `xml_sha256(xml_almacenado)` (`huella_indice_ok`) — antes una alteración del XML de un índice **no firmado** quedaba indetectada. La regeneración deja asiento `archive.indice_regenerated` en `audit_log` con `xsd_version` (traza forense del salto v1→v2, ADR-010). `regenerate` gana guard `assert_open` (409 `expediente_not_open`; el cierre pasa `assert_open=False` para su regeneración final legítima).

**Negativas / límites:**
- Los radicados **ya vinculados** no tienen los metadatos (NULL → omitidos); solo los vínculos nuevos que los aporten. No hay backfill en este incremento (evolutivo: un futuro `PATCH .../radicados/{id}/metadatos` o fallback a document-service).
- **Bump de versión esperado al desplegar**: el primer `regenerate` de cada expediente **abierto** crea `version+1` (por el cambio de XML v2 + canon), aunque el conjunto documental no cambie. Es la migración natural v1→v2 del índice vivo, perezosa (no se fuerza rebuild masivo). Los expedientes cerrados/firmados no se tocan.
- El dato lo aporta el emisor → no se valida veracidad (acotado por `CHECK >= 0` y `max_length`).

## Alternativas consideradas

- **Consulta inter-servicio a document-service** (Opción B): cubriría automáticamente los radicados existentes, pero introduce un cliente HTTP nuevo, timeouts y superficie de fallo en el momento de cierre/firma. Descartada por acoplamiento.
- **Mantener v1 con elementos aditivos**: evita un namespace nuevo, pero el mismo namespace describiría dos estructuras distintas (por el renombre de fecha + la omisión). Descartada por ambigüedad de esquema.

## Seguimiento (no bloqueante)

- **Gap RBAC pre-existente** (descubierto en este incremento): las mutaciones de `expedientes.py` no aplicaban `require_permission('USUA_PERM_EXPEDIENTE')` y el api-gateway no aplica gate por ruta. **✅ CERRADO** (commit posterior, sección Seguridad del CHANGELOG): los **12 endpoints de `expedientes.py`** + los de `batch.py` ahora exigen el permiso (fail-closed), y se corrigió de paso un **bug de aislamiento cross-tenant** en la tarea batch detached (reutilización de la conexión request-scoped → `acquire_tenant()`). **Residual (misma clase de gap):** mutaciones de config archivística (`trd.py`/`tipos_documentales.py`/`metadata.py`) sin gate — la TRD gobierna retención (Ley 594). **✅ CERRADO** (commit posterior, sección Seguridad del CHANGELOG): las 6 mutaciones exigen **`USUA_PERM_TRD`** (permiso ya sembrado, sin migración; menor privilegio) y ahora escriben en `audit_log`; las lecturas GET quedan abiertas (radicación). **Residual restante (service-wide, deuda de diseño):** `clearance.py` concedía con `crud > 0` sin distinguir lectura/escritura/disposición (mínimo privilegio, RF-SEG-03). **✅ CERRADO** (commit posterior, sección Seguridad del CHANGELOG): el gate gana el parámetro `min_crud` (default 1, retrocompatible, comparación escalar `>=`) en las 5 copias de `clearance.py`, y las operaciones de **disposición** de archive (cerrar/transferir/desvincular expediente, transferencias FUID, `delete_tipo`, `update_trd_serie`, y el `PATCH` que transiciona a `closed`/`transferred`) exigen **`crud>=3`** (Crear/Borrar). **Residual restante:** accesos **denegados** no se registraban en `audit_log` (service-wide). **✅ CERRADO** (commit posterior, sección Seguridad del CHANGELOG): helper compartido `append_denial` (`action="acceso.denegado"`, best-effort) invocado antes del `raise 403` de permiso en las 5 `clearance.py` + `auth/authz.py`, con regla anti-ruido (solo usuario identificado + tenant válido). **Con esto el hilo RBAC queda cerrado end-to-end** (gates en expedientes/batch/config, mínimo privilegio por operación, y trazabilidad de denegaciones). **Residual restante (no-RBAC):** el flujo formal de **aprobación/convalidación de TRD** (Comité, actas, Acuerdo AGN 004/2019) queda para F5/PINAR; y cuando exista eliminación documental final con acta, debe ir a nivel `crud` 4/5.
- Completar RT-15 hacia "conforme completo": `orden_documental` estable (E02), `listaControlAcceso`, `<contexto>`, historial de acceso, fecha de declaración. **✅ PARCIAL — perfil v3 (ver addendum abajo):** cerrados `orden_documental` estable, `listaControlAcceso` (por nivel) y `<contexto>`; **quedan** historial de acceso, fecha de declaración, ACL por roles y dependencia productora (por eso v3 es "conforme EXTENDIDO", no "completo").

---

## Addendum — Perfil v3 "conforme extendido" (2026-07-03): orden_documental estable + soft-delete + ACL + contexto

**Estado**: Aceptado — completa RT-15 hasta donde hay fuente de datos en archive-service, sin sobre-declarar "conforme completo".

### Decisión

**Bump a `urn:orpycamcp:indice:v3` ("conforme extendido") con orden documental estable, exclusión trazable (soft-delete), lista de control de acceso por nivel y bloque de contexto.**

1. **`orden_documental` estable (Acuerdo AGN 001/2024 art. 4.3.2.3, principio de orden original).** Nueva columna en `expediente_radicados`, asignada **al vincular** con `MAX(orden_documental)+1` por expediente de forma **atómica** (`pg_advisory_xact_lock` + misma tx que el INSERT), **inmutable** — ya no es el `enumerate(1..N)` recalculado de v2. El análisis del Orfeo legacy (orfeo-domain-analyst) confirmó que **Orfeo nunca implementó un ordinal estable** (usaba `row_number()` recalculado que renumeraba al excluir) — no hay paridad que preservar, es una brecha del legado.
2. **Exclusión = soft-delete (hueco trazable).** `unlink` pasó de **DELETE físico** a `UPDATE estado='excluido'` (+ `excluido_at`, `excluido_causal`), como el Orfeo legacy (`estado=2`). El `orden_documental` **nunca se reutiliza ni renumera**; el índice emite el documento excluido como `<documento excluido="true">` con su ordinal preservado + `<fechaExclusion>`/`<causalExclusion>`, dejando el **hueco visible y trazable**. **Todas** las vistas de "documentos vigentes" (listados, foliado, conteos, clearance) filtran `estado='vinculado'`; solo el índice y `verify` ven ambos (para mostrar/verificar el hueco).
3. **`<listaControlAcceso>`** por documento: el `nivel_seguridad` del expediente (PUBLICA/RESERVADA/CLASIFICADA, heredado). Se expuso `nivel_seguridad` en el modelo/row-mapper (antes write-only). Es **cobertura mínima honesta** — la ACL por roles/dependencias vive en auth-service, no integrada.
4. **`<contexto>`**: `fechaPrimerUso` (min `added_at` de vinculados) + `<serie>`/`<subserie>` por **nombre** (lookup TRD jerárquico por `parent_id`), no el UUID de v2.

### No sobre-declarar (RF-FIR-15)

v3 = **"conforme extendido"**, NO "conforme completo". El disclaimer del `index_builder` lista los **4 residuales sin fuente en archive**: (1) historial de acceso por documento, (2) fecha de declaración, (3) ACL por roles/dependencias (solo se emite el nivel), (4) dependencia productora. No hay bandera `conforme_completo`.

### Consecuencias

**Positivas**: orden original inmutable y hueco trazable (AGN-conforme); contexto legible (serie/subserie por nombre); ACL de nivel. **Endurecimiento colateral** (auditoría de seguridad, NO_APTO inicial): las lecturas del índice (`GET /{id}/indice`, `/indice.xml`, `/indice/versions`) **no tenían `require_permission` ni clearance** (gap RBAC pre-existente en `indice.py`, no cubierto por el hilo RBAC de `expedientes.py`) → ahora exigen `USUA_PERM_EXPEDIENTE` + **no-read-up** por clearance (RF-SEG-08); el soft-delete deja asiento en `audit_log` con el actor (quién excluyó). **Negativas / límites**: 4 residuales pendientes para "conforme completo" (arriba); re-vincular un radicado excluido asigna un ordinal nuevo (el hueco anterior permanece). Migración `018`; v1/v2/v3 firmados inmutables. **Comportamiento esperado al desplegar**: el primer `regenerate` de un expediente abierto crea `version+1` (v2→v3, como v1→v2).
