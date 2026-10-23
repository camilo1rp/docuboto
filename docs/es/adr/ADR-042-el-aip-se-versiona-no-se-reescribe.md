# ADR-042: El AIP se versiona, no se reescribe — cierre de la deuda XAdES-LTA de B10

**Estado**: Aceptado
**Fecha**: 2026-10-20
**Autores**: Giampiero (mantenedor principal); tanda C-6
**Relacionados**: [ADR-034](ADR-034-xmldsig-conforme-firma-verificable-por-terceros.md) §5 (renovación del `ArchiveTimeStamp`; enmendado por este ADR), [ADR-023](ADR-023-worm-object-lock-preservacion.md) (WORM; la raíz `tipo='aip'` y la cascada), [ADR-021](ADR-021-reclamacion-deadletter-eventos.md) (por qué no hay consumidor), [ADR-035](ADR-035-estado-de-la-entidad-cumplimiento-en-el-borde.md) §8 (la suspensión no detiene la conservación), ETSI EN 319 132-1 §5.5, EN 319 102-1 §5.6.2.5, RFC 6960

---

## Contexto

B10 (§8 de ADR-034) renueva el `ArchiveTimeStamp` de una firma XAdES-LTA antes de que caduque el certificado de su TSA. Quedaron abiertos cinco puntos, de los que dependía que la renovación **conservara** algo y no solo existiera:

| # | Hecho verificado en el código | Consecuencia |
|---|---|---|
| H1 | `firma.lta_renovada` se emitía best-effort, sin outbox y sin consumidor | Una renovación podía no avisar a nadie, para siempre |
| H2 | El AIP embebe el XML vigente **solo al empaquetar**; si signature fallaba, empaquetaba el ORIGINAL en silencio | Un AIP con el sello viejo sin que nada lo viera (la lectura «opcional» convierte un error en vacío) |
| H3 | `expediente_aip` tenía un AIP por expediente | Un AIP empaquetado antes de la renovación no se podía rehacer |
| H4 | `get_by_expediente_and_tipo(…, 'aip')` tomaba el **más reciente** y la cascada de retención **desciende** por `renueva_a` | Con dos versiones, la fase de renovación de retención extendería la nueva y **la anterior vencería**: el defecto de «identidad WORM por tipo pierde la 2.ª» |
| H5 | La revocación de la TSA anterior solo se obtenía por CRL | Una TSA cuya CA publica solo OCSP no se podía renovar |
| H6 | `/verify` no evaluaba el `TimeStampValidationData`; el pin de un ATS renovado usaba `certificates[0]` de un `SET OF` | Una cadena con un TSVD vacío o inservible validaba; el orden de un conjunto decidía a qué TSA se anclaba |
| H7 | El margen de renovación miraba solo el `notAfter` de la TSA, no el de su CA | La cadena se rompía por la CA sin que el job lo previera |
| H8 | `firma_sello_archivo.estado`/`ultimo_aviso` no tenían endpoint | Un plazo que corre sin que nadie lo vea |

H4 era **bloqueante**: versionar el AIP sin corregirla habría **acortado** la retención efectiva del AIP previo.

## Decisión

### 1. Barrido de conciliación, no consumidor del evento

El AIP se concilia con un **barrido** de archive-service (fase 11 de `indice_reconciliation.py`, `run_once_aip_reempaquetado`) que compara, para cada AIP empaquetado con índice firmado, la huella del XML embebido (`expediente_aip.indice_xml_sha256`) con la del que representa HOY a la firma (`POST /api/v1/signature/internal/artefactos-vigentes`, lotes de ≤200 `firma_id`).

| | Consumidor de `firma.lta_renovada` | Barrido (**elegido**) |
|---|---|---|
| Pérdida | Redis caído en `_completar_worm` = renovación perdida para siempre | No hay mensaje que perder: el estado se compara en cada pasada |
| Infra | archive no tiene consumidor de streams: grupo, PEL, DLQ y replay nuevos | Una fase más con su advisory lock y backoff ya probados |
| H2 | No lo cubre (un AIP con el original por un fallo transitorio nunca recibe evento) | Lo cubre: cualquier AIP con índice embebido ≠ vigente se reempaqueta |
| Latencia | Segundos | ≤ un intervalo (24 h): irrelevante, la renovación ocurre ≥ 90 días antes del vencimiento |

El evento **se queda** como señal informativa, **sin consumidor por diseño**. Una cadena de renovación sin eslabón vigente aún (`pendiente_worm`) no se concilia: la vista `firma_artefacto_vigente` solo ve `vigente`/`caducado`.

**Clave de idempotencia, única y en la base:** archive `expediente_aip_version UNIQUE (expediente_id, indice_xml_sha256)` y storage `UNIQUE (sustituye_a) WHERE sustituye_a IS NOT NULL` en `aip`: un AIP no puede tener dos sucesores aunque dos réplicas corran a la vez.

### 2. El AIP nuevo se construye desde el bag anterior, no desde document-service

`POST /api/v1/preservacion/aip-reempaquetar-internal` (storage, `X-Internal-Token`, en `RUTAS_DE_CONSERVACION`) descarga el ZIP anterior por `version_id`, verifica su `manifest-sha256` y `tagmanifest`, y construye un bag **idéntico salvo el índice**, que se sustituye por el XML vigente. Repetir el empaquetado normal puede cambiar el contenido (radicados excluidos o añadidos tras el cierre) y, en una entidad suspendida, esperaría la reactivación mientras el plazo corre; releer el propio objeto no toca document ni exige inventario.

- **El índice nuevo no puede ser un XML cualquiera:** su fila WORM tiene que ser `tipo='firma_lta_renovada'` y su cadena `renueva_a` subir hasta el WORM `indice` del MISMO expediente (`422 indice_no_es_renovacion_del_embebido`).
- **PREMIS v3 regenerado** (validado contra el XSD de la LoC): relación `derivation` / `has source` con el `aip_id` anterior y evento `packing` con `linkingObjectIdentifier` al AIP anterior (rol `source`) y al nuevo índice (rol `outcome`). Los eventos `validation` PDF/A se **reutilizan** de la versión anterior: nada se revalida.
- **Retención: nunca menor.** `retain_until = max(toda la cadena de AIP del expediente, el del AIP anterior)`, mismo modo y `legal_hold`.
- **El AIP anterior no se toca**: sigue en el bucket bajo COMPLIANCE; su fila `aip` y su fila WORM permanecen.
- Idempotente: reintento con el mismo `indice_xml_sha256` → `200` con el mismo AIP; con otro → `409 aip_ya_sustituido`.
- Archive, en UNA transacción: `INSERT expediente_aip_version` (v+1) + `UPDATE expediente_aip` (`aip_vigente_id`, `indice_xml_sha256`) + asiento `archive.aip_reempaquetado`. Un fallo no toca `estado`: backoff propio y un solo asiento al agotar.

### 3. Corrección de H4: la raíz de retención del AIP

`get_by_expediente_and_tipo` para `tipo='aip'` devuelve la **raíz** (`renueva_a IS NULL`, la más antigua), y la cascada existente (`_extender_cadena_renovada`) extiende todas las versiones. `ux_pwo_aip_raiz` (`UNIQUE (expediente_id) WHERE tipo='aip' AND renueva_a IS NULL`) vuelve imposible la ambigüedad en la base. `expediente_aip.aip_worm_object_id` sigue apuntando a la **primera** versión (identidad de retención); `aip_vigente_id` apunta a la última (la de consulta). `GET /api/v1/preservacion/aip/{expediente_id}` devuelve la cabeza de la cadena y se añade `…/versiones`.

### 4. H2: tri-estado, nunca el original en silencio

`signature_client.artefacto_vigente` pasa a `Vigente | SinRenovacion | Error`. Un `Error` en `intentar_empaquetar` es backoff (`artefacto_vigente_no_resuelto`); ya no se empaqueta con el original. `mark_empaquetado` guarda `indice_xml_sha256` e inserta la versión 1 en la misma transacción.

### 5. OCSP de la TSA cuando su CA no publica CRL

`tsa.revocacion_de_tsa` prefiere la CRL (lo que ya funcionaba no cambia) y, si no hay o falla, pregunta al OCSP anunciado en el AIA del certificado (`POST application/ocsp-request`, CertID SHA-256, nonce de 32 bytes, sin redirecciones, tope de 64 KiB). `ocsp.verify_ocsp_tsa` exige: `SUCCESSFUL`; una `SingleResponse` cuyo CertID coincide (se **busca** entre todas); firmante = la propia CA o un delegado emitido directamente por ella (EKU `OCSPSigning`, `ocsp-nocheck`, vigente); algoritmos del catálogo; y frescura: `thisUpdate` no futuro, `nextUpdate` no vencido, nonce coincidente si lo trae y, si no, `producedAt` a ±5 min. `GOOD` → `ocsp`; `REVOKED` ANTES del sello → `TsaRevocadaError`, DESPUÉS → se renueva; `UNKNOWN` o cualquier fallo → sigue al siguiente medio; ni CRL ni OCSP → `RevocacionTsaNoDisponibleError` (fail-closed, un solo asiento por eslabón). La evidencia debe ser posterior al sello que cubre. `TSA_OCSP_ENABLED` (default `true`) es un kill-switch: con `false` el comportamiento es el de antes. El TSVD lleva `OCSPValues` (o `CRLValues`) y **la CA que verificó la revocación**, aunque el token de la TSA no la trajera.

### 6. `/verify` evalúa el `TimeStampValidationData`

Para cada eslabón `i ≥ 1` hay **un** TSVD entre el ATS `i-1` y el `i` (`tsvd_ausente`/`tsvd_multiple`) que trae el certificado de la TSA del `i-1` y el de su CA (`tsvd_sin_certificado_tsa`) y una revocación **emitida entre los dos sellos** (`genTime(i-1) ≤ thisUpdate/producedAt ≤ genTime(i)`): CRL con firma verificada contra la CA o respuesta OCSP en modo archivado. Revocada **antes** del sello `i-1` → `tsvd_tsa_revocada` (inválida); **después** → válida con el aviso `tsa_revocada_despues_del_sello`. Sin revocación y no autofirmada → `tsvd_sin_revocacion` (fail-closed). La TSA **autofirmada** sigue siendo su propia ancla (la huella ya está anclada): válida con el aviso `tsvd_tsa_autofirmada_sin_revocacion` (limitación declarada de ADR-034 §5, sin cambio de fondo). `VerifyResponse.archive_timestamps[]` expone `{ordinal, gen_time, tsa_fingerprint, vence_at, revocacion, valido}` sin datos del documento.

- **Algoritmos vigentes** (`app/core/algoritmos.py`, catálogo en código, no en el request): digests `sha256/384/512`, firma RSA con módulo ≥ 2048 y ECDSA P-256/P-384, por **admisión**. SHA-1 solo como identificador (`ESSCertID` v1, `CertID` de OCSP), nunca en una firma. **Regla temporal** (EN 319 102-1 §5.6.2.5, prueba de existencia): los algoritmos del ATS `i` y de su TSVD deben ser vigentes en el `genTime` del ATS `i+1`, y los del último, **hoy** (`algoritmo_no_vigente[i]:<alg>`). Cada entrada lleva su fecha de caducidad (`None` = vigente) para que la regla sea comprobable. Hoy el sistema solo produce SHA-256 + RSA-2048: el veredicto observable no cambia.
- **El pin sale del certificado que firmó** (`certificado_tsa_de_token`, por `sid`), no de `certificates[0]` (H6): un `SET OF` no tiene orden.
- **Lo primero que caduque de la TSA y de su CA** manda (H7): `vence_at = LEAST(tsa_cert_not_after, tsa_cadena_not_after)` en el margen de renovación, en `archive_timestamp_cadena_rota` y en `lta_vigente`.

### 7. Consulta de administración (H8)

`GET /api/v1/signature/admin/sellos-archivo` (+ `/resumen`), `USUA_PERM_ADMIN` a nivel 3. Último eslabón de cada firma, con `situaciones` (`por_vencer`, `vencido`, `sin_revocacion`, `pendiente_worm`, `rechazado`), orden declarado `vence_at ASC, firma_id`, `X-Total-Count` del mismo `WHERE`. **Clearance: no se ocultan filas, se acota el campo.** Un administrador sin nivel que no viera que un sello reservado va a caducar sufriría una pérdida de prueba invisible (el defecto de B9): la fila siempre se ve; `objeto_tipo`/`objeto_id` van en `null` con `objeto_reservado=true` cuando el objeto supera su clearance, decidido en la misma consulta. Content-free (sin asunto ni XML). Cada consulta deja un asiento (`firma.sellos_archivo_consultados`).

### 8. La suspensión no detiene esta conservación (ADR-035 §8)

`POST /api/v1/signature/internal/artefactos-vigentes` está en la lista de conservación de signature (`app/core/conservacion.py`, copia del patrón de storage: lista por admisión, `fullmatch`, solo `suspended`, `X-Internal-Token`, asiento `entidad.conservacion_admitida`) y `aip-reempaquetar-internal` en la de storage. La fase de archive recorre entidades `active` y `suspended`.

### 9. `409 aip_ya_existe` no es un fallo de fixity (auditoría de cierre del lote C)

Si storage empaquetó el AIP pero la respuesta se perdió, el reintento de archive recibe `409 aip_ya_existe`. Antes el cliente lo traducía como `fixity_mismatch` (que acusa una mutación de anexo que no ocurrió), entraba en backoff hasta agotarse y el AIP quedaba **huérfano para archive**. Ahora: storage incluye el **`aip_id` vigente** en el 409; el cliente de archive distingue `aip_ya_existe` de `fixity_mismatch` (`AipPackagingOutcome.ya_existe`) y **reconcilia con el `GET /preservacion/aip/{expediente}` interno** antes de registrar: solo si storage confirma un AIP vigente y es el mismo que informó el 409, se llama a `mark_empaquetado` con su id y su `retain_until` (asiento `archive.aip_empaquetado` con `reconciliado_con_storage = true`). Si no se puede confirmar (404, id distinto, storage caído), queda como reintento con motivo `aip_ya_existe_sin_reconciliar`, nunca como mutación de anexo. La decisión «200 idempotente cuando solo hay un AIP que archive no registró» se descartó: storage no sabe qué registró archive, y un 200 ahí esconde el caso en lugar de reconciliarlo.

## Consecuencias

- Un AIP ya no queda con el sello viejo: se **versiona** (v1 → v2 → …), cada versión conserva su propio objeto bajo COMPLIANCE y la retención de la cadena entera se extiende desde la raíz.
- Migraciones: signature `027` (columnas de revocación/vencimiento de la cadena de sellos), storage `023` (`aip.version`, `sustituye_a`, `motivo`, índices únicos), archive `074` (`expediente_aip_version` append-only, columnas de backoff). **Sin backfill ni rama de legado**: los AIP y renovaciones anteriores son corpus de prueba y se recrean; un AIP v1 de antes de C-6 sin `indice_xml_sha256` se trata como desconocido y se reempaqueta si la firma tiene ordinal > 0.
- Costo del barrido: O(AIP empaquetados con índice firmado) por pasada, en lotes de 200. Si pesara, la alternativa es un cursor incremental por `vigente_at` (descartada por ahora: añade un estado que también habría que conciliar).
- **Residuo declarado:** una OCSP sin nonce solo se acepta si está fresca (±5 min); un respondedor que reenvíe respuestas cacheadas más viejas no permite renovar (fail-closed, un asiento por eslabón). La TSA y las CA de desarrollo no están acreditadas: la conformidad que se exige a EU DSS es criptográfica y de integridad, no de cadena de confianza (igual que B7).
- Revisión obligatoria antes de integrar: `tenant-security-auditor` (endpoint de administración + acotado por clearance + ruta de conservación nueva en signature) y `archival-compliance-auditor` (PREMIS `packing`/`has source`, regla temporal de algoritmos, TSVD de EN 319 132-1 §5.5.1).
