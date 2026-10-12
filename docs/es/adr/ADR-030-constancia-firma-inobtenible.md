# ADR-030: La constancia de «firma inobtenible» es prueba, no una anotación

**Estado**: Aceptado
**Fecha**: 2026-10-09
**Autores**: Giampiero (mantenedor principal); remediación de los dictámenes de `archival-compliance-auditor` y `tenant-security-auditor`
**Relacionados**: [ADR-016](ADR-016-firma-electronica.md) (firma electrónica), [ADR-023](ADR-023-worm-object-lock-preservacion.md) (WORM), [ADR-010](ADR-010-libreria-compartida.md) (auditoría inmutable)

---

## Contexto

El acto «firma inobtenible» (AREA-07-10, migración `059`) permite declarar que una parte del acta
de entrega (`elabora`, `entrega`, `recibe`) no puede firmar. Su primera versión dejaba una fila
inmutable en la base y un asiento en `audit_log`. Los dos auditores la devolvieron con un hallazgo
**alto** y varios medios:

- La constancia **no estaba firmada**, no tenía **Object-Lock** y su soporte era un **texto libre**
  (`referencia_documental`): ante el AGN era una afirmación de la entidad sobre sí misma, no una
  prueba de por qué el acta carece de una firma.
- Solo se impedía que declarara **la parte afectada**; quien envió, recibió o elaboró la
  transferencia podía declarar la excepción de otra parte.
- Dos partes con excepción y ninguna firma se reportaban `firmada_con_excepcion`: un acta con **cero**
  firmas personales decía «firmada».
- No había salida si la excepción se declaraba por error.
- El `fundamento` (puede aludir a salud o fallecimiento) se devolvía completo a cualquier lector del acta.
- `firma_personal_pendiente` leía la política XAdES del **entorno**, no la de la entidad.

## Decisión

1. **La constancia es un XML canónico** (`urn:orpycamcp:acta-firma-excepcion:v1`) que cubre la huella del
   acta congelada, la transferencia, el rol, la parte, el motivo, la **huella** del fundamento (nunca el
   texto: el artefacto se replica y se conserva para siempre), el radicado/anexo de soporte, el
   declarante y el instante. Se firma con el **sello institucional XAdES** (nivel T **solo si la TSA está
   configurada y responde**; el nivel efectivo queda en `xades_level` y la verificación lo advierte) por el mismo camino que el
   acta (`objeto_tipo=acta_transferencia`) y, **además**, con la firma personal XAdES del declarante
   cuando envía `totp_code` y tiene credencial activa; es **obligatoria** si la política de la entidad
   (`firma_personal_require_xades`, parámetro de tenant) es `todos`, o `clasificados` con expediente de
   nivel >= 2.
2. **Sin sello no hay constancia**: la firma se obtiene **antes** de escribir y, si no hay sello, el acto
   responde 503 sin dejar nada. Una constancia «pendiente de firma» es justo lo que no sirve.
3. **WORM con la retención del acta**: seis `tipo` en storage (`acta_firma_excepcion_<rol>` y
   `acta_firma_excepcion_personal_<rol>`, instrumentos de control), bookkeeping en la misma transacción de
   la constancia, protección post-commit best-effort con reconciliación y renovación CT (mismo patrón que
   las firmas personales, `057`/`060`).
4. **El soporte es un radicado (y opcionalmente un anexo) existente y visible para el declarante**, resuelto
   por el servidor contra document-service con el token interno y el clearance del declarante (422 único
   para «no existe» y «no lo puede ver»; 503 si document-service no responde).
5. **Conflicto de interés**: ni quien declara ni quien revoca puede ser `enviada_por`, `decidida_por` ni
   `creada_por` de la transferencia (403 `declarante_es_parte_de_la_transferencia`, con asiento de
   denegación). Lo repite un trigger en el motor.
6. **Etiqueta honesta**: `firmada_con_excepcion` exige que al menos una de las dos partes haya firmado
   personalmente; si ninguna firmó es **`sellada_con_excepcion`**. Se eligió esta etiqueta y no rechazar la
   segunda excepción porque, si las dos partes no pueden firmar, el acta no puede quedar sin salida.
7. **Revocar** es una fila nueva append-only (`..._excepcion_revocacion`), con asiento atómico, nivel 3 y sin
   poder ser parte. El trigger de exclusión mutua firma<->excepción solo cuenta excepciones **no
   revocadas**: la parte puede volver a firmar. **Una excepción revocada no se re-declara** (la
   constancia y su revocación son un par de evidencia cerrado y el artefacto WORM es singleton por
   `(expediente, tipo)`).
8. **El fundamento completo solo sale a `USUA_PERM_ADMIN` nivel >= 3**; el resto ve motivo, referencia y
   `fundamento_sha256`.
9. **La política XAdES es la de la entidad**: `/acta/verificacion` lee `firma_personal_require_xades` de
   `sgd_param_admin` (el entorno solo respalda si la entidad no la definió; un valor corrupto = `todos`).
10. **Las advertencias de `/verify`** de signature (`advertencias[]`) se propagan a `/indice/verify` y a
    `/acta/verificacion`.

## Consecuencias

**Positivas**: la constancia es verificable por terceros (XAdES-T + huella del acta + Object-Lock; *desde ADR-034 — antes de él, la firma XAdES no la verificaba un validador independiente*) y no
depende de que la base se conserve intacta; el texto sensible no vive en artefactos replicados.

**Negativas / límites**:
- Declarar exige que **signature-service esté disponible** y que el declarante tenga `PERM_FIRMA` >= 2
  (el sello se estampa a su nombre).
- Las constancias anteriores a la migración `061` quedan **sin firma** (se declaran así: `firmada=false`,
  `constancias_sin_firma` en la verificación, `valida=false`); no se firman retroactivamente.
- Una excepción declarada por error y revocada no admite una segunda declaración para ese rol.

---

## Addendum FIRMA-2 (2026-10-09) — cadena de constancias, revocación como prueba

Sustituye el punto 7 en lo que dice «una excepción revocada no se re-declara».

1. **Re-declaración encadenada.** Tras revocar se puede declarar de nuevo para el mismo rol: la nueva
   constancia lleva `ordinal = n+1` y `sustituye_a` = la revocada (`UNIQUE (transferencia_id, rol, ordinal)`
   y `UNIQUE (sustituye_a)`: cadena lineal). Solo si la revocación anterior está **sellada y todos sus
   artefactos protegidos WORM**; si no, 409 `revocacion_sin_proteger`. Si la parte firmó durante la
   ventana revocada, la cadena se cierra (409 `parte_ya_firmo`): la firma prevalece. Lo repite el trigger.
2. **La revocación es prueba**: XML canónico (`urn:orpycamcp:acta-firma-excepcion-revocacion:v1`) ligado a
   la huella de la constancia revocada, sello institucional y firma personal de quien revoca si tiene
   certificado activo; WORM con su propia reconciliación y renovación. Sin sello: 503 sin asiento.
3. **Firma personal siempre que haya certificado activo** (declarar y revocar), no solo por política.
4. **Constancia v2 autosuficiente** (`urn:orpycamcp:acta-firma-excepcion:v2`): número de radicado y
   SHA-256 del anexo de soporte (anexo obligatorio), nombre y cargo de declarante y parte como
   instantánea resuelta por el servidor. Datos personales de una persona afectada (fallecimiento,
   incapacidad) quedan en un artefacto WORM perpetuo: es deliberado (la constancia debe identificar a
   quién no pudo firmar) y su lectura se limita a quien lee el acta.
5. **El fundamento se huella con HMAC-SHA256 por entidad**, clave derivada por HKDF en signature-service
   (propósito `fundamento_excepcion`, sin secreto nuevo); un SHA-256 plano de un texto corto es atacable
   por diccionario. Se verifica recalculando contra la columna, el XML y el asiento.
6. Las constancias legadas (pre-061) son ordinal 1 sin firma; se revocan (revocación firmada) y encadenan.

---

## Addendum FIRMA-2, ronda de auditoría (2026-10-09) — verificable por terceros, canónico, anclado

Corrige el punto 5 del addendum anterior y completa los puntos 1-2 tras la revisión de
`archival-compliance-auditor` y `tenant-security-auditor`. Migración archive `063`.

1. **El fundamento se huella con un compromiso con sal por registro** (sustituye al HMAC con clave de
   plataforma). `compromiso = SHA-256(sal_32B || texto_utf8)`, sal aleatoria (`secrets.token_bytes(32)`)
   por registro, guardada en la base junto al fundamento (columna `fundamento_sal`, **no WORM**).
   El compromiso va en la columna, en el XML (`<fundamentoCompromiso algoritmo="SHA-256-salted">`) y en el
   asiento; la sal **nunca** va a `audit_log`, al XML ni a una respuesta que no sea de administración nivel 3.
   Razón: el HMAC con clave raíz de la plataforma no lo puede verificar un tercero (el AGN) sin entregarle
   la clave de **toda** la entidad, y renombrar el tenant o perder el secreto raíz lo rompía; con sal por
   registro, la entidad entrega texto + sal de **un** registro y cualquiera recalcula con `sha256sum`.
   Sin sal el compromiso no se ataca por diccionario. La verificación del fundamento (administración nivel 3)
   recalcula **localmente** contra columna, XML y asiento; signature-service deja de intervenir.
2. **Versionado (decisión).** Constancia **v3** (`urn:orpycamcp:acta-firma-excepcion:v3`) y revocación
   **v3** (`...-revocacion:v3`, con `causa`). Las filas v2 existentes se escribieron con HMAC y la v2 nunca
   salió de desarrollo (datos de prueba): se **conservan** como legadas (columnas `fundamento_hmac*` de la
   062 intactas, la tabla es append-only), no se reescriben ni se verifican por compromiso, y su
   verificación responde `verificable=false` (`constancia_sin_compromiso_salado`). Los CHECK v2 de la 063
   exigen HMAC para v2 y compromiso+sal (32 bytes)+algoritmo para v3. Los endpoints
   `hmac-fundamento[/verificar]` y los 503 `huella_fundamento_no_disponible` desaparecen.
3. **Canonicalización.** Lo que se firma, se guarda y se huella es la **C14N exclusiva**
   (`http://www.w3.org/2001/10/xml-exc-c14n#`, lxml) del XML sin firmar; `constancia_sha256` /
   `revocacion_sha256` son el SHA-256 de esos bytes y el XAdES firma ese mismo contenido. Un tercero que
   reciba el XML firmado, le quite la `ds:Signature` envolvente y lo canonicalice obtiene la misma
   huella. `GET /acta/verificacion` coteja los **enlaces** de la cadena —huella guardada vs. XML,
   contenido de cada artefacto firmado vs. huella, `sustituyeA`/`revocacionSha256` vs. el eslabón anterior,
   `constanciaSha256`/`causa`/compromiso de la revocación vs. la constancia— y reporta el estado WORM de
   cada eslabón; un enlace roto fuerza `valida=false` con su motivo (`cadena_motivos`, `enlace_motivo`).
4. **Ancla común de retención.** Todos los eslabones de la cadena de un (transferencia, rol) se protegen con
   el mayor entre el sellado del acta y el **último acto** de la cadena (no con la fecha de cada acto), y al
   protegerse un eslabón nuevo se **alinean** (renovación monotónica, con asiento) los anteriores ya
   protegidos. Así el eslabón 1 no caduca antes que su revocación ni que la constancia que lo sustituye.
5. **Salida de `agotado`.** `POST .../acta/firma-inobtenible/{excepcion_id}/worm/reintentar` (constancia) y
   `.../{excepcion_id}/revocacion/worm/reintentar` (revocación): administración **nivel 3**, repone a
   pendiente los artefactos agotados (protección o renovación) con asiento atómico y reintenta la
   protección; devuelve el estado real. En desarrollo, con la preservación apagada, seguirá fallando:
   es lo correcto.
6. **Privacidad (Ley 1581 de 2012).** `incapacidad` es un dato de salud (art. 5, dato sensible): dentro del
   XML y del asiento se escribe el término neutro `impedimento_sobreviniente`; la categoría exacta queda
   solo en la base. **Base legal del tratamiento**: art. 10 lit. a (información requerida por una entidad
   pública en ejercicio de sus funciones legales), con la finalidad de dejar constancia de por qué una
   parte no pudo firmar el acta (Ley 594/2000 art. 11; Ac. AGN 003/2015). **Clasificación Ley 1712 de 2014**:
   información clasificada por protección de datos de salud (art. 18 lit. a). Estos artefactos
   (constancia, revocación, fundamento, sal) **están excluidos de OAI-PMH, EAD y CMIS**: ningún módulo de
   interoperabilidad los lee (lo fija `tests/test_firma2_ronda_auditoria.py`) y el XML nunca se sirve por
   archive-service.
7. **Revocación con `causa`** de vocabulario cerrado (`error_de_declaracion`, `parte_puede_firmar`,
   `otro`), obligatoria en el cuerpo y dentro del XML firmado; el detalle libre sigue en `fundamento`.
8. **Trazabilidad de la lectura.** Leer el fundamento en claro (`GET /acta`, `GET /acta/verificacion` para
   administración nivel 3, y la verificación del fundamento) deja el asiento
   `transferencia.acta_fundamento_leido` (quién, qué constancias, por qué puerta; sin texto ni sal), **antes**
   de servirlo: sin asiento no hay lectura. El asiento de declarar ya no lleva el número del radicado de
   soporte (quedan el id y la huella del anexo).
9. **Salvedades declaradas.**
   - El nombre con `@fuente="usuario"` (sin certificado de firma) es el *username* del catálogo: **no
     identifica a una persona natural**; solo el CN de un certificado activo (`fuente="certificado"`) lo hace.
   - El anexo de soporte es un anexo ordinario y **puede eliminarse por su TRD**; la constancia conserva su
     id y su SHA-256. Propuesta futura: bloqueo de disposición mientras exista una constancia vigente
     que lo cite.
   - Una revocación **legada** (anterior a la 062) sigue definitiva. Una «regularización» que permita
     re-declarar sobre ella queda como propuesta (roadmap), no implementada.

---

## Addendum FIRMA-2, ronda 3 (2026-10-09) — sobre de revelación del fundamento

Cierra la reserva Media de `archival-compliance-auditor`: el texto del fundamento y su sal vivían
**solo en la base** (tabla append-only, sin Object-Lock, fuera del AIP y de la transferencia). Perder la
base perdía el fundamento entero, no solo la posibilidad de verificarlo. Migraciones archive `064` y
storage `021`.

1. **Sobre de revelación.** Por cada constancia v3 y cada revocación v3, archive-service genera un XML
   `urn:orpycamcp:acta-firma-excepcion-sobre:v1` con `{id, acto, texto, salHex, compromiso, algoritmo}`
   (C14N exclusiva; `acto` = `constancia` | `revocacion`, `id` = el del acto), lo **sella con el sello
   institucional** (XAdES-T) y lo guarda como **objeto propio** en el almacén. En la fila del acto quedan
   solo `sobre_firma_id`, `sobre_minio_key`, `sobre_sha256` y `sobre_xades_level` (el XML del sobre no se
   duplica en la base). Quien reciba el sobre recalcula `SHA-256(sal || texto)` y lo coteja con el
   `fundamentoCompromiso` del XML principal.
2. **Mismo flujo, antes de la transacción.** Se sella justo después del sello de la constancia y **antes**
   de consumir el TOTP de la firma personal. Si el sello falla: 503 `sellado_sobre_no_obtenido` (o
   `sello_institucional_no_disponible`) y **nada** queda escrito (ni fila ni asiento). Un carácter de control
   no admitido por XML 1.0 en el fundamento responde 422 `fundamento_caracteres_invalidos` (el sobre se
   construye con lxml para conservar un `\r` interno como `&#xD;`: el compromiso cubre los bytes exactos).
3. **Object-Lock con la retención de la cadena.** Tercer `artefacto` (`sobre`) de las tablas
   `transferencia_acta_firma_excepcion[_revocacion]_worm` (CHECK ampliado en la `064`): mismo ciclo que
   sello y firma personal —protección inicial post-commit best-effort, reconciliación con backoff,
   renovación CT, `.../worm/reintentar`— y **misma ancla común** de la cadena del rol (`_ancla_cadena`
   / `_alinear_cadena`). En storage son seis `tipo` nuevos (`acta_firma_{excepcion,revocacion}_sobre_<rol>`),
   **instrumentos de control** y **multi-artefacto** por `(expediente, tipo, file_id)`. Para re-declarar
   tras revocar, el trigger y el servicio exigen además el sobre de la revocación protegido.
4. **Acceso solo mediado.** `GET /transferencias/{id}/acta/firma-inobtenible/{excepcion_id}/sobre?acto=constancia|revocacion`:
   administración **nivel 3**, 404 neutro por no-read-up del expediente, y el asiento
   `transferencia.acta_fundamento_leido` con `via="sobre"` se escribe **antes** de servirlo (sin asiento no
   hay lectura). Nunca por OAI-PMH, EAD, CMIS ni la verificación pública; `GET /acta` y `/acta/verificacion`
   solo exponen metadatos (`sobre`, `sobre_sha256`, `worm_sobre_protegido`). La verificación del
   fundamento añade el sobre como **cuarta copia** (`sobre`, `revocacion_sobre`): si existe y no coincide o
   no se pudo leer, `coincide=false`.
5. **Cómo viaja en la transferencia secundaria al AGN.** El sobre es un **componente clasificado** (Ley 1712
   de 2014, art. 18 lit. a: datos de salud / fallecimiento): se entrega **junto a** la constancia y la
   revocación, en el mismo paquete, con nota en el FUID y en el acta de entrega que lo declare *clasificado,
   acceso restringido a la entidad receptora*; el AGN recibe texto y sal **de ese registro** (no la clave
   de toda la entidad) y puede verificar el compromiso con `sha256sum`. El empaquetado del sobre en el
   paquete de transferencia secundaria es un acto del procedimiento de entrega (la lectura mediada del
   punto 4 deja el asiento); no se publica ni se indexa.
6. **La copia de respaldo de la base NO sustituye al sobre.** Es una copia operativa, rotativa y sin
   Object-Lock ni sello; el sobre es la copia de **conservación**, inmutable y verificable. Las
   constancias y revocaciones **v2/legadas** y las v3 anteriores a la `064` quedan **sin sobre** (columnas
   `NULL`; la vista lo dice con `sobre=false`): no se sellan retroactivamente.
7. **Revocar ya no devuelve el fundamento ajeno.** La respuesta de `POST .../revocar` devolvía el
   `fundamento` y la sal de la constancia **original** (texto de otro declarante) sin asiento de lectura. Ahora
   los devuelve `null`; la vista completa queda en `GET /acta`, que sí asienta. (El `fundamento` de la propia
   revocación, que acaba de escribir quien revoca, se mantiene.)
8. **Orden y huérfanos.** El sobre se sube DESPUÉS de validar la firma personal (un TOTP inválido no deja
   copias del texto). Si la fila no llega a referenciarlo, archive lo purga por `/internal/purgar-huerfanos`
   de storage (que deja su asiento `file.purgado_huerfano`); si la purga falla, un barrido de reconciliación
   (misma barrida que el WORM de la constancia) recoge los ficheros `acta-transferencia-sobre-…` sin
   referencia en `sobre_minio_key` pasada una gracia (`SOBRE_HUERFANO_GRACIA_HORAS`, 1 h).
9. **Lectura a petición.** `GET /acta` y `/acta/verificacion` ya no devuelven el texto ni la sal (ni a admin
   nivel 3): `GET .../{excepcion_id}/fundamento?acto=` lo sirve con asiento `via=lectura`. La sal solo viaja
   dentro del sobre.

## Addendum B4 (2026-10-09) — punto 5: el paquete de entrega existe

El punto 5 decía que el sobre viaja «junto a» la constancia en la transferencia secundaria, pero no había
paquete que lo llevara: el AIP se sella al cerrar el expediente (antes de que existan constancias,
revocaciones y sobres, y ya está bajo COMPLIANCE), y el acta FUID congelada es probatoria y no se reescribe.

**Decisión.** Un **paquete de entrega** aparte, generado a petición —`POST /transferencias/{id}/paquete-entrega`—
y reproducible desde componentes que ya están bajo Object-Lock. Solo transferencias *secundarias* y *recibidas*;
administración nivel 3; no-read-up en el `WHERE` (404 neutro).

1. **Contenido (exacto).** `acta/` (acta congelada tal cual = el **inventario FUID**, + sello XAdES + firmas
   personales), `indice/indice-electronico.xades.xml` (el **índice electrónico firmado** de la versión que
   entregó el acta, bajo Object-Lock), `clasificado/` (toda la cadena de constancias y revocaciones, también las
   revocadas, con sus firmas personales y los sobres), `nota-entrega.xml` y `checksums.txt`. El **AIP** del
   expediente (BagIt + PREMIS con los documentos) **no viaja dentro del ZIP**: su volumen lo hace inviable y ya
   está sellado bajo COMPLIANCE; la nota lo **referencia** por `bucket`, `object_key`, `sha256`, `retain_until` y
   `retention_mode` (`<aip incluido_en_paquete="no">`) y quien recibe lo obtiene del almacén y lo coteja con esa
   huella. Sin AIP empaquetado con retención confirmada por el almacén, el paquete no se genera.
2. **La «nota en el FUID» es `nota-entrega.xml`** (`urn:orpycamcp:nota-entrega-transferencia:v1`). El acta no se
   edita: la nota la referencia por `acta_fuid_sha256` y declara cada componente (`ruta`, `sha256`, `acto`,
   `rol`, `ordinal`) como `clasificacion="clasificada"`, `fundamento="Ley 1712/2014 art. 18 lit. a"`,
   `acceso="restringido a la entidad receptora"`. Constancias v2/legadas llevan `sobre="ausente"`: no se
   sellan retroactivamente. La nota es determinista (sin hora ni autor, que viven en el asiento).
3. **Fail-closed.** Si algún componente (incluidos índice y AIP) no tiene Object-Lock confirmado, `409
   componentes_sin_object_lock` y no se descarga nada; las rutas `clasificado/...` no se enumeran (se agregan en
   `componentes_clasificados`, para no revelar cuántas excepciones hay). Un entorno sin preservación nunca produce paquete.
   **Fixity de egreso.** Antes de empaquetar se recalcula `sha256(acta_fuid_xml)` contra `acta_fuid_sha256`, el
   sello del acta debe firmar esa misma acta (C14N-SHA256 sin la firma envolvente) y cada componente se lee como
   bytes y se coteja con la huella que storage registró al subirlo. Una discrepancia es `409
   componente_integridad_discrepante` + asiento `transferencia.paquete_entrega_incidente`; sin huellas de captura,
   `503`. Los checksums del ZIP certifican la copia entregada; esta comprobación certifica el original.
4. **Asiento antes de servir.** `transferencia.acta_fundamento_leido` (`via="paquete"`, por cada constancia cuyo
   sobre viaja) y `transferencia.paquete_entrega_generado` (huella de la nota y de cada componente), en una
   sola transacción y antes del primer byte.
5. **Exclusión.** Ni OAI-PMH, ni EAD, ni CMIS leen las tablas de excepción (guardián por fuente en las pruebas).

**Límites declarados.** `nota-entrega.xml` no está sellada con XAdES (tocaría signature-service); su huella queda
anclada en `audit_log`. El paquete se arma en un fichero temporal (son XML pequeños).

## Nota ADR-034 (2026-10-11)

La conformidad XMLDSig de la firma (ADR-034) **no cambia** `constancia_sha256` ni `huella_de_firmado`: son huellas de la aplicación
sobre el XML sin `ds:Signature`. Cambia solo la `ds:Signature` que lo sella (con `Transforms` declarados). Las constancias selladas
antes de ADR-034 conservan sus huellas, pero su sello XAdES no verifica con un validador independiente y se regenera si hace falta.
