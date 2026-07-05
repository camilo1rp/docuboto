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

---

## Addendum — Perfil v4 "conforme completo" (2026-07-05): dependencia productora + fecha de declaración + pista de auditoría + política de acceso

**Estado**: Aceptado — cierra los **4 residuales de v3** a nivel de capacidad, subiendo el perfil a "conforme completo" **sin sobre-declarar** (RF-FIR-15). Ratificado por `orfeo-architect`; auditado APTO por `archival-compliance-auditor` y `tenant-security-auditor`.

### Decisión

**Bump a `urn:orpycamcp:indice:v4` con dependencia productora + fecha de declaración por documento, y política de acceso + atestación de pista de auditoría + marcador de conformidad en cabecera.** XSD `schemas/indice-v4.xsd` publicado. Principio rector (fija el diseño de las 4 decisiones): **el `content_hash` cubre el árbol canónico completo; por tanto sólo entra al XML lo estable-por-expediente — todo lo mutable/operacional se atesta o se expone por endpoint, fuera de la firma.** (v4 cambia el alcance del hash: v3 hasheaba sólo `<documentos>`; los nuevos elementos viven en cabecera, así que v4 canonicaliza el documento entero.)

1. **R1 — Dependencia productora (Acuerdo AGN 001/2024 art. 4.3.2.3; "oficina productora" del FUID de Orfeo, `radi_depe_radi`).** Columnas nuevas `dependencia_productora_codigo`/`_nombre` en `expediente_radicados` (+ snapshot en `expediente_indice_item`, migración `019`), **aportadas al vincular** por el caller (Opción A, como formato/tamaño/folios) — el build de firma **NO** llama a document-service (debe ser determinista y auto-contenido; acoplarlo a otro servicio haría infirmable un artefacto legal si ese servicio está caído). Grano por-radicado. Emitido `<dependenciaProductora codigo="N">Nombre</dependenciaProductora>` sólo cuando ambos existen; **omitido** (minOccurs=0) en filas legacy (no backfilleable sin document-service). Regla de pareja (código y nombre juntos o ninguno) en `RadicadoLink` **y** `BatchExpedienteCreate` (`422`) + `CHECK` de BD como segunda barrera.
2. **R2 — Fecha de declaración.** `<fechaDeclaracion>` por documento = `added_at` del vínculo. El `orfeo-domain-analyst` confirmó que en Orfeo `SGD_EXP_FECH` (fecha del acto de incluir al expediente, distinta de `fech_radi`) **es** la fecha de declaración documental; en OrpycaMCP el archivado es un solo acto → **declaración ≡ incorporación**. Una sola fuente de verdad (`added_at`); la columna vacía `expediente_indice_item.fecha_declaracion` (mig. 008) queda deprecada, no se puebla.
3. **R3 — Historial de acceso (brecha real del legado: Orfeo nunca registró lecturas).** (a) Se **instrumentan las lecturas exitosas** de expediente/índice en `audit_log` (`archive.expediente_consultado`/`archive.indice_consultado`), con actor de identidad **validada** y **sólo** canal `api|ui|mcp` con **actor real** (se omite `canal=system` — regeneración/verify/jobs no ensucian la pista ni amplifican escrituras). (b) Endpoint `GET /expedientes/{id}/historial-acceso` (repositorio de solo-lectura `audit_read.py`, filtro `tenant_slug` **explícito** de defensa en profundidad). (c) En el índice se **atesta** `<pistaAuditoria disponible registro="public.audit_log" objetoTipo objetoRef cadenaVerificable>` — **no se incrustan entradas vivas**: hacerlo rompería la idempotencia del versionado (el hash cambiaría en cada lectura) y falsearía la semántica temporal (un v-N firmado en T no puede contener lecturas posteriores). `audit_log` (hash-chain, append-only) es la primitiva de integridad superior; el índice sólo la referencia.
4. **R4 — Control de acceso por roles (CORREGIDO respecto de la propuesta inicial).** Se **rechazó incrustar la matriz RBAC viva** (`<rolAutorizado>`) en el índice firmado: es config IAM mutable (churn del artefacto probatorio en cada reorg/alta de grupo), **no portable** (nombres de grupo locales carecen de sentido para un receptor AGN) y conflaciona capas. En su lugar se emite **`<politicaAcceso modelo="no-read-up" requiere="USUA_PERM_EXPEDIENTE" clearanceMinimo="N">`** dentro de `<listaControlAcceso>` (movida a **cabecera** en v4): política **estable, portable y derivada del propio expediente** (es lo que MoReq/SGDEA pide — categoría de seguridad + regla de derechos, no un dump de grupos). El dato **vivo** va a `GET /expedientes/{id}/roles-autorizados` (snapshot SQL de los grupos con el permiso y clearance suficiente), **fuera** de la firma.

### Marcador de conformidad y label (RF-FIR-15)

`<conformidad perfil="v4" productoraPresente="…" pistaAccesoDesde="…"/>` en cabecera (entra al hash: es estable). Distingue **perfil** de **instancia**: el perfil v4 **soporta** los 4 elementos ("completo" a nivel de capacidad), pero la completitud es **per-instancia** — un expediente creado tras la instrumentación con productora en todos sus documentos es genuinamente completo; uno legacy omite productora y su pista arranca en `pistaAccesoDesde`. **No se declara "residuales: none" en seco**: cada índice **auto-declara** su completitud en `<conformidad>` (mecanismo anti-sobre-declaración codificado, verificable por máquina). `productoraPresente="false"` sobre **conjunto vacío** (índice v0 o todos excluidos) — no proyecta conformidad positiva sobre un expediente sin documentos. Label documental: **"CONFORME COMPLETO — perfil v4, aplicabilidad prospectiva."**

### Consecuencias

**Positivas**: los 4 residuales cerrados a nivel de capacidad; con R4 corregido **ningún elemento v4 es mutable-en-vivo → cero churn continuo** (sólo la ola única de regeneración v3→v4); pista de acceso auditable (cierra la brecha del legado); artefacto portable en transferencias AGN. **Negativas / límites**: (a) productora **no backfilleable** en radicados legacy sin un cliente a document-service (diferido); (b) la pista de acceso de expedientes previos arranca en la instrumentación (imposible retroactivamente por definición); (c) instrumentar lecturas añade una escritura hash-chained por GET autenticado (contención potencial en expedientes muy leídos — se prioriza corrección sobre throughput; mitigado al omitir `canal=system` y accesos sin actor); (d) leer `public.audit_log` desde archive por SQL acopla a la forma de tabla de auth-service (mitigado: solo-lectura, columnas como contrato estable ADR-010, filtro `tenant_slug`). Migración `019` (validada idempotente en Postgres 15; `CHECK` de pareja código/nombre); v1/v2/v3/v4 firmados inmutables, `verify` agnóstico por versión. **203 tests verdes** (+43). **Comportamiento al desplegar**: el primer `regenerate` de un expediente abierto crea `version+1` (v3→v4).
