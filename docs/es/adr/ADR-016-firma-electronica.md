# ADR-016: Firma electrónica nativa (hash + identidad + sello de tiempo)

**Estado**: Aceptado
**Fecha**: 2026-06-21
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

El SGDEA debe permitir **firmar** documentos: dejar constancia verificable de quién aprobó/suscribió un radicado o anexo, cuándo, y sobre qué contenido exacto. La Ley 527/1999 (comercio electrónico) reconoce la firma electrónica y el mensaje de datos; el Decreto 2364/2012 define la firma electrónica como métodos que identifican al firmante e indican su aprobación, con fiabilidad apropiada al fin.

Los Orfeo legados (9342, orfeo7) integran **PortableSigner** (Java) para firma PKCS#7 embebida en PDF con certificado X.509. Esto exige una JVM, gestión de certificados y manejo del binario PDF — fricción operativa alta y dependencia de un stack ajeno (Java) en un proyecto FastAPI/Python.

El sistema ya tiene los cimientos para una firma electrónica verificable: **identidad autenticada** (Keycloak → `X-User-Id`/`X-Username` inyectados por el gateway, ADR-013), **integridad por SHA-256** de los anexos (E07) y **auditoría inmutable** encadenada (ADR-008).

## Decisión

**Implementar la firma electrónica de forma nativa como un registro verificable: identidad del firmante + hash SHA-256 del contenido firmado + sello de tiempo + motivo. Sin dependencia de Java/PortableSigner en F3.**

- Una firma es una fila en `signatures` (por tenant) que vincula: `radicado_id` (y `anexo_id` opcional), `signer_id`/`signer_name` (de los claims del gateway), `content_hash` (SHA-256 del contenido firmado — para un anexo, el checksum ya calculado en E07), `reason`, `signed_at`.
- **Verificación**: una firma es válida para un contenido si su `content_hash` coincide con el hash actual de ese contenido. Si el documento cambia, su hash cambia y la firma deja de verificar → se detecta la alteración.
- **No repudio razonable**: la identidad proviene de Keycloak (autenticación fuerte) y el acto queda además en la auditoría inmutable (ADR-008). Es firma **electrónica** (Decreto 2364/2012), no firma **digital** certificada (PKI X.509).
- **Extensibilidad**: la firma criptográfica avanzada (PKCS#7/PAdES embebida en PDF, certificados X.509, sello de tiempo TSA) se modela como un **proveedor de firma enchufable** y se difiere; el contrato de la API de firma no cambia al añadirlo.

## Consecuencias

**Positivas:**
- **Sin JVM ni binarios**: implementación 100% Python/PostgreSQL, coherente con el stack y el despliegue sencillo.
- Verificable y auditable: el hash detecta alteración del contenido; la auditoría inmutable (ADR-008) registra el acto de firma.
- Reutiliza identidad (ADR-013) e integridad SHA-256 (E07) ya existentes; cero infraestructura nueva.
- Cubre la firma electrónica reconocida por el Decreto 2364/2012 para la mayoría de los trámites internos.

**Negativas / límites:**
- **No es firma digital con certificado X.509**: no hay validación de cadena de confianza ni sello de tiempo cualificado (TSA). Trámites que exijan firma digital cualificada requerirán el proveedor PKI (diferido).
- La fortaleza del no repudio depende de la robustez de la autenticación (Keycloak) y de la custodia de la auditoría.
- El `content_hash` lo aporta quien firma (o se toma del checksum del anexo); el sistema verifica coincidencia, no la semántica del contenido.

## Alternativas consideradas

- **PortableSigner / PKCS#7 en PDF (Java)**: descartado en F3 por la dependencia de JVM, la gestión de certificados y el acoplamiento al formato PDF; se conserva como proveedor enchufable futuro para firma digital cualificada.
- **Firmar solo con un flag de "aprobado"**: descartado; no ata la aprobación a un contenido concreto (no detecta alteración) ni deja hash verificable.
- **Servicio de firma dedicado**: innecesario en F3; la firma vive junto al documento (document-service), su objeto natural. Si se añade PKI/TSA con dependencias pesadas, podrá extraerse a un servicio sin cambiar el contrato.

## Relacionados

- [ADR-008](ADR-008-auditoria-inmutable.md) — el acto de firma se registra en la auditoría inmutable.
- [ADR-013](ADR-013-autorizacion-resuelta-en-bd.md) — la identidad del firmante proviene de los claims de Keycloak (gateway).
- E07 — el SHA-256 del anexo es el `content_hash` a firmar/verificar.

---

## Addendum — Incremento 1 (2026-07-02): XAdES-B del índice electrónico + sello institucional

**Estado**: Aceptado — realiza el *proveedor de firma enchufable* que la Decisión (§Extensibilidad) difería.

### Contexto adicional

El **índice electrónico** del expediente (E15, Acuerdo AGN 001/2024 art. 4.3.2.4) debe quedar **firmado al cierre** del expediente como instrumento que da fe de su integridad para la transferencia primaria/secundaria y el FUID. La firma nativa (HMAC de servicio) no es un formato de firma **interoperable** ni verificable por terceros: un índice que se transfiere a otra entidad necesita una firma **XML-DSig/XAdES** con certificado X.509 que cualquier validador estándar pueda comprobar.

### Decisión

Introducir un **proveedor de firma enchufable** (`app/providers/`, signature-service) con dos implementaciones tras un `Protocol` común (`SignerProvider`: `prepare`/`sign`/`check_status`/`download`):

- **`nativa`** (`NativeSigner`) — envuelve la firma HMAC preexistente (ADR-016 base) **sin cambiar** su comportamiento ni sus datos. Sigue siendo el proveedor para todos los objetos que no son índice.
- **`xades_local`** (`LocalSigner`, librería `signxml`) — **XAdES-B enveloped real**: RSA-SHA256, digest SHA-256, canonicalización **exclusiva**, `SignedProperties` (SigningTime + certificado). Se activa **solo** cuando `objeto_tipo == "indice"` **y** `settings.signer_provider == "xades_local"` (default, con kill-switch operativo `SIGNER_PROVIDER=nativa`). Cualquier otro caso cae a `nativa`.

**Sello institucional (persona jurídica, Ley 527/1999 art. 16.3)**: el índice se sella con el **certificado institucional del tenant**, no con un certificado personal del funcionario. El acto se **atribuye a la persona** que cierra el expediente vía `PERM_FIRMA` + `X-User-Id` validado + `audit_log` (RF-FIR-11). La clave es de la institución; el no-repudio del *acto* proviene de la identidad autenticada y la auditoría, igual que en la firma nativa.

**Custodia de clave (RT-16)**: la clave privada del sello **nunca** se persiste en BD, ni se loguea, ni se devuelve en respuestas/eventos. Solo se persiste el **certificado público** (`firma.certificado_pem`) y su **huella SHA-256** (`firma.certificado_fingerprint`). El par por tenant se resuelve desde un directorio montado como **secreto Docker read-only** (`SIGNER_CERT_DIR`, `app/core/keys.py::load_seal`), aislado por slug saneado contra path traversal.

**Aislamiento criptográfico por tenant (fail-closed)**: si el tenant no tiene sello propio montado, `load_seal` **falla cerrado** (el índice queda `pendiente_firma`, 503) salvo que `SIGNER_ALLOW_DEV_SEAL=true` (default **False**, solo desarrollo) habilite el par compartido `_dev/`. En producción, ausencia de sello ≠ firmar con clave compartida entre tenants.

**Cierre no bloqueante + reintento (decisión del usuario, RT-16)**: si el sellado falla al cierre (sello no aprovisionado, signature-service inalcanzable, subida a MinIO fallida), el cierre del expediente **no se bloquea** — el índice queda en **`estado='pendiente_firma'`** (nuevo estado, migración archive `015`), se emite el audit `archive.indice_firma_pendiente` (traza de no-conformidad, ADR-010) y puede reintentarse vía `POST /api/v1/expedientes/{id}/indice/firmar` (idempotente: 409 si ya `firmado`). **Un índice `pendiente_firma` es bloqueante para transferir** (`closed→transferred`, en las tres rutas: transferencia dedicada, PATCH genérico y recepción E12), aunque no lo sea para cerrar.

**Verificación atada a la firma concreta**: `POST /api/v1/signature/verify` valida criptografía + cobertura de la referencia raíz + vigencia del certificado + coincidencia de huella con el sello registrado, **y** compara el SHA-256 del XML firmado recibido contra `firma.signed_xml_sha256` (migración `010`) para que un `signed_xml` de *otro* índice firmado con el mismo sello del tenant no se reporte válido para un `firma_id` ajeno.

### Conformidad declarada (RF-FIR-15: no sobre-declarar)

En el Incremento 1 **no hay CA acreditada ONAC** integrada: toda firma XAdES-B se marca `nivel_conformidad="xades_b_no_acreditado"` y `acreditado=false`. **Nunca** se reporta como firma cualificada. El `SigningTime` proviene del reloj del servidor, **no** de una TSA RFC 3161: no se declara "sellado de tiempo" ni fecha cierta oponible a terceros mientras no exista XAdES-T.

### Consecuencias

**Positivas**: firma del índice **interoperable** y verificable por validadores XAdES estándar; contrato de la API de firma **no cambió** (la firma nativa sigue intacta); aislamiento de clave por tenant fail-closed; el estado `pendiente_firma` hace **observable** el fallo de sellado en vez de ocultarlo.

**Negativas / límites (deuda de roadmap declarada, NO incumplimiento)**:
- Sin **TSA/XAdES-T** (RFC 3161) → sin fecha cierta oponible; diferido a F4.
- Sin **XAdES-LT/LTA** (material de validación a largo plazo, CRL/OCSP) → los niveles quedan reservados en el CHECK de `firma.xades_level`.
- Sin **CA acreditada ONAC** → todo `no_acreditado`.
- El índice sellado aún **omite metadatos RT-15** exigibles (formato, tamaño, foliación, fecha de incorporación): no rotular como "conforme completo" hasta cerrar RT-15.
- `POST /verify` **no es público**: por el gateway exige JWT (no está en `PUBLIC_PATHS`) y el tenant sale del token. Era un oráculo de existencia para cualquier usuario autenticado de la entidad (`firma_id` son UUID aleatorios, impacto bajo); desde AREA-04-10 (octubre de 2026) aplica el **no-read-up** —la firma de un objeto por encima del clearance del llamante da el mismo `404` que un UUID inexistente, con el nivel en el `WHERE`— y la verificación pública de verdad va por `/api/v1/public/` con código de verificación (E13).

### Nota de implementación (signxml + C14N exclusiva)

`signxml` (verificado contra 5.0.1) falla la verificación de las referencias XAdES adicionales (`SignedProperties`, `KeyInfo`) con `InvalidDigest` cuando el firmante usa canonicalización **exclusiva**, porque esas referencias no llevan `<ds:Transforms>` explícito y el verificador cae al C14N por defecto (1.1) en vez del configurado al firmar. **Workaround**: pasar `expect_config` con `default_reference_c14n_method` igualado al algoritmo exclusivo usado al firmar (`app/providers/local.py`, documentado en el código).

### Relacionados (Inc.1)

- E15 — índice electrónico XML (archive-service) que este incremento firma.
- Acuerdo AGN 001/2024 art. 4.3.2.4 — firma del índice al cierre, obligatoria para transferencia.
- Ley 527/1999 art. 16.3 — sello de persona jurídica.
- [ADR-021](ADR-021-reclamacion-deadletter-eventos.md) — el evento `firma.indice.firmado` se publica best-effort en `orpycamcp.signature.events`.

---

## Addendum — Incremento 2 (2026-07-02): 2FA por OTP-correo en la firma personal (RF-FIR-13)

**Estado**: Aceptado — realiza el segundo factor en el **acto de firma** para la firma **personal** (cadena de firma). Complementario al Inc.1: el sello institucional del índice (actuación automatizada, RF-FIR-14) queda **EXENTO** del 2FA.

### Contexto adicional

RF-FIR-13 (Ley 527/1999 art. 7; DUR 1074/2015 Cap. 47 arts. 47.4.1/47.6/47.8/47.9): toda firma personal de actos de fondo debe exigir un **segundo factor verificado en el acto de firma** (no solo en el login), ligado por un `challenge_id` de un solo uso al `hash_documento`, para asegurar el vínculo **firmante ↔ voluntad ↔ documento**. La auditoría conserva tipo de factor, `challenge_id`, momento y resultado — **nunca el OTP**.

### Decisión

Segundo factor = **OTP de un solo uso enviado por correo** (decisión del usuario). signature-service es el **dueño del challenge**. NO TOTP/WebAuthn/step-up de Keycloak (diferidos a un incremento de mayor aseguramiento).

- **Tabla `firma_challenge`** (migración tenant `011`) por tenant: liga `(solicitud_id, firmante_id, hash_documento)`, persiste **solo el HMAC-SHA256 del OTP** (con `signature_secret`, nunca el OTP en claro), `estado` (`pendiente→verificado→consumido` | `expirado`/`bloqueado`), `intentos`/`max_intentos`, `expira_at`, `canal_masked`. Índice único parcial impide dos challenges activos por acto+documento.
- **`POST /api/v1/signature/requests/{id}/challenge`** — solo el firmante del turno activo emite su reto (403 si no; 404 neutro si la solicitud no es visible; **422 `payload_no_fijado`** si la solicitud no tiene `payload_sha256` estable — Q1). Genera OTP de 6 dígitos (CSPRNG), lo envía por correo (auth-service `/me` → email; notification-service `/send`), **fail-closed** (canal caído → 503 + rollback, sin challenge huérfano). Cooldown de reenvío 30s (429).
- **`POST /requests/{id}/sign`** exige `challenge_id`+`otp`; **sin challenge verificado → 409 `2fa_requerido`**. Verificación con `hmac.compare_digest` en la **misma transacción** del lock de turno; el consumo del challenge y el avance del turno son atómicos (anti-replay/anti-TOCTOU). OTP incorrecto → 401 con `intentos_restantes` (el contador se persiste de forma durable); al agotar `max_intentos` (5) → `bloqueado`.
- **`POST /batch`** (BREAKING, Q2): `items:[{solicitud_id, challenge_id, otp}]` — un OTP por documento (no reutilizable entre documentos); por-ítem no aborta el lote.

### Seguridad y conformidad declarada (RF-FIR-15: no sobre-declarar)

El OTP **nunca** se persiste/loguea/audita/devuelve en claro. Fuerza bruta cerrada por HMAC (secreto del servidor) + cap de intentos + TTL 5 min. `hash_documento` cierra reuso cross-documento; el check `solicitud_id` cierra reuso cross-acto (mismo payload en dos solicitudes). El 2FA es **no desactivable** para actos sobre información **RESERVADA/CLASIFICADA** (`nivel_seguridad ≥ 2`), aunque el flag global `firma_2fa_required` esté en `False` (solo conmutable para PUBLICA/tests). El factor `otp_email` es de **posesión de canal** — más débil que TOTP/WebAuthn — y se declara tal cual: es firma **electrónica** (Ley 527 art. 7), no digital/cualificada. La auditoría del acto (`firma.turno.firmado`) es autocontenida: liga firmante + `hash_documento` + `challenge_id` consumido + `consumido_at`.

### Consecuencias

**Positivas**: vínculo firmante↔voluntad↔documento verificable y auditado; contrato de la firma nativa/XAdES intacto; el gate vive por frontera de endpoint (el sello del índice no lo toca). **Negativas / límites (roadmap declarado)**: OTP-correo depende de un canal externo (el OTP viaja por SMTP, inherente al 2FA por correo); factores de mayor aseguramiento (TOTP/WebAuthn/step-up OIDC) diferidos; el 2FA exige que la solicitud tenga `payload_sha256` estable (documentos con hash dinámico no son firmables por la vía personal hasta fijarlo).

### Seguimiento cerrado — fuga del OTP en notification-service (2026-07-02)

El residual detectado en Inc.2 (el OTP viajaba en el cuerpo del correo y notification-service **persistía** ese cuerpo en la tabla `notifications`, exponiéndolo en BD/backups durante la ventana de validez) quedó **cerrado**: `NotificationSend` estrena un flag `sensible` (default `False`, retrocompatible); cuando es `True`, el correo se **envía por SMTP con el body real** pero se **persiste redactado** (`[contenido sensible omitido]`, y `last_error` genérico si el envío falla) en la fila, en el hash Redis y en el historial (migración notification `008` añade la columna `sensible`). signature-service marca el envío del OTP como `sensible=True`. Con esto el invariante «el OTP en claro nunca se persiste» se sostiene **end-to-end**: la única copia persistente sigue siendo el HMAC en `firma_challenge`.

### Relacionados (Inc.2)

- RF-FIR-13 (spec E06 §12) — 2FA en el acto de firma; RF-FIR-11 (atribución del JWT) y RF-FIR-12 (PERM_FIRMA + auditoría) que lo condicionan.
- [ADR-013](ADR-013-autorizacion-resuelta-en-bd.md) — el firmante se deriva del JWT validado, nunca del cuerpo.
- Servicios consumidos sin cambios: auth-service `/me` (email), notification-service `/send` (correo).

---

## Addendum — Incremento 4 (2026-07-02): XAdES-T — sello de tiempo RFC 3161 del índice

**Estado**: Aceptado — eleva el XAdES-B del índice (Inc.1) a **XAdES-T** añadiendo un sello de tiempo verificable sobre la firma.

### Contexto adicional

El XAdES-B de Inc.1 usa `SigningTime` del **reloj del servidor**, que no es oponible por sí solo frente a terceros. Un **sello de tiempo RFC 3161** de una autoridad de sellado (TSA) da **trazabilidad temporal verificable** de que la firma existía en un instante dado.

### Decisión

**Añadir un `xades:SignatureTimeStamp` (token RFC 3161) sobre el `ds:SignatureValue`, firmado por una TSA local in-process; `signxml` no lo soporta nativo, así que se post-procesa el XML firmado.**

- **Post-proceso** (`providers/local.py`): tras firmar XAdES-B con `XAdESSigner`, se canonicaliza el `ds:SignatureValue` (exc-c14n 1.0), se sella con la TSA y se inserta `<xades:UnsignedProperties>/<UnsignedSignatureProperties>/<SignatureTimeStamp>/<EncapsulatedTimeStamp>` con el token DER en base64. Es una propiedad **NO firmada** → **no invalida** el XAdES-B subyacente (no toca `SignedInfo`/`SignedProperties`).
- **TSA local in-process** (`core/tsa.py` + `core/keys.py::load_tsa_seal`): par auto-firmado con **EKU `id-kp-timeStamping`** (crítico, RFC 3161), montado como secreto Docker read-only (patrón idéntico al sello institucional de Inc.1; una TSA **única del despliegue**, no per-tenant). El token `TimeStampToken` (CMS `SignedData`, `eContentType=id-ct-TSTInfo`) se construye con `asn1crypto` (`tsp`+`cms`) y se firma RSA-SHA256 con `cryptography`. La clave privada de la TSA **nunca** se persiste/loguea. `scripts/gen-dev-tsa-cert.sh` genera el par dev.
- **Config**: `signer_timestamp_enabled` (default `True`), `tsa_url` (vacío → TSA local; puesto → **cliente remoto RFC 3161 REAL** (`app/core/tsa.py::_request_remote_timestamp`: POST HTTP, comprueba nonce, EKU y token; antes fue un stub y ya no lo es), que si la TSA no responde o el token no es conforme falla explícito con `TsaUnavailableError`; acreditado = `tsa_url` **y** `tsa_acreditada`), `tsa_cert_dir`, `tsa_allow_dev_seal`. **Fail-closed**: con timestamp habilitado y sin TSA disponible, la firma falla (503 `timestamp_authority_unavailable` → el índice queda `pendiente_firma`, consistente con Inc.1); kill-switch `signer_timestamp_enabled=False` produce XAdES-B como antes.
- **Persistencia**: migración tenant `012` añade `tsa_token`, `tsa_timestamp`, `tsa_cert_fingerprint`, `tsa_acreditado` a `firma` (el token TSA es **público**, no secreto). El CHECK de `xades_level` ya reservaba `'XAdES-T'`.
- **Verificación** (`verify_xades` + `/verify`): si hay `SignatureTimeStamp`, valida que el `messageImprint` del `TSTInfo` cubre el `ds:SignatureValue` actual, la firma CMS del token, la EKU/vigencia del cert TSA, **y ata el cert TSA al `firma.tsa_cert_fingerprint` persistido** (pinning — sin él un cert auto-firmado arbitrario con EKU timeStamping se reportaría válido). Expone `sello_tiempo_valido`/`tsa_timestamp` en `VerifyResponse`.

### Conformidad declarada (RF-FIR-15: no sobre-declarar)

`xades_level="XAdES-T"` pero **`acreditado=False`** y `nivel_conformidad="xades_b_no_acreditado"`: la TSA local **no es acreditada ONAC**, así que da **fecha cierta verificable, no acreditada/cualificada**. No se rotula "sellado de tiempo acreditado" ni "fecha cierta oponible" en respuestas/UI/docs.

### Consecuencias

**Positivas**: trazabilidad temporal verificable (mejora sobre el `SigningTime` del reloj de Inc.1); XAdES-B intacto (propiedad no firmada); verify ata el sello a la `firma_id` y a la TSA registrada; self-contained en dev (sin red). **Negativas / límites (roadmap)**: TSA local **no acreditada** → no cualificada (XAdES-T acreditado requiere una TSA ONAC vía `TSA_URL`, cliente remoto diferido); **XAdES-LT/LTA** (CRL/OCSP, archivado a largo plazo) siguen diferidos. Seguimiento (Baja): endurecer el parser del fragmento `SignatureTimeStamp` en verify; incluir los detalles del sello (`tsa_timestamp`/`tsa_acreditado`) en el audit del acto y en `FirmaResponse`; coherencia temporal `genTime`↔ventana de vigencia del sello institucional.

### Relacionados (Inc.4)

- RFC 3161 (Time-Stamp Protocol); ETSI EN 319 132 (perfil XAdES-T).
- E06 tasks T-08 (cliente TSA) — realizado el modo local **y el cliente remoto `TSA_URL`** (RFC 3161 real, `tests/test_tsa_remota.py`); lo que falta es **contratar la TSA acreditada** (externo), no código.

---

## Addendum — Incremento 5 (2026-07-05): XAdES-LT/LTA del índice con CA local de desarrollo (material de validación a largo plazo, no acreditado)

**Estado**: Aceptado. Ratificado por `orfeo-architect`; auditado APTO por `tenant-security-auditor` y `archival-compliance-auditor`.

### Decisión

**Escalar la firma del índice de XAdES-T a XAdES-LT/LTA cuando el sello del tenant dispone de material de CA**, insertando (post-proceso lxml, patrón de Inc.4) `xades:CertificateValues` + `xades:RevocationValues` (LT) y `xadesv141:ArchiveTimeStamp` (LTA), con una **CA local de desarrollo** que emite el sello y produce la CRL. Todo **no acreditado** (la CA es dev, no ONAC), sin sobre-declarar (RF-FIR-15).

**Línea roja de seguridad — la clave privada de la CA NUNCA entra a signature-service.** `scripts/gen-dev-ca.sh` retiene `ca.key` en `./secrets/ca-authority/` (chmod 600), directorio que **ningún** `docker-compose.yml` monta en el contenedor (solo se montan `./secrets/signing` y `./secrets/tsa` en modo `:ro`). El script copia **solo** los artefactos **públicos** `ca.crt` + `crl.der` al dir del sello del tenant. El servicio nunca lee `ca.key`; persiste solo certs públicos, fingerprints y tokens (el XML firmado en MinIO es la fuente de verdad del material LT/LTA).

### Estructura y cobertura

- **Orden ETSI EN 319 132 dentro de `xades:UnsignedSignatureProperties`** (refactor de `_insert_signature_timestamp` para crear el contenedor una sola vez): 1) `xades:SignatureTimeStamp` (v1.3.2, T, ya existía); 2) `xades:CertificateValues` (v1.3.2, LT) — `EncapsulatedX509Certificate` = **solo la CA** (el cert del sello ya está en `ds:KeyInfo`, EN 319 132 prohíbe duplicarlo); 3) `xades:RevocationValues/CRLValues/EncapsulatedCRLValue` (v1.3.2, LT) = `crl.der`; 4) `xadesv141:ArchiveTimeStamp` (**v1.4.1**, LTA, siempre el último). *Nota de implementación:* los hijos del `ArchiveTimeStamp` (`CanonicalizationMethod`/`EncapsulatedTimeStamp`) permanecen en v1.3.2 según las XSD reales de `signxml`; solo el contenedor usa v1.4.1.
- **Perfil de ArchiveTimeStamp simplificado (no acreditado):** el token RFC 3161 (misma TSA local de Inc.4) sella un **conjunto cubierto fijo y determinista** — C14N exclusiva de `ds:SignedInfo ‖ ds:SignatureValue ‖ ds:KeyInfo ‖ xades:SignedProperties ‖ xades:SignatureTimeStamp ‖ xades:CertificateValues ‖ xades:RevocationValues`, snapshot **antes** de insertar el propio ArchiveTimeStamp. Como `SignedInfo` ya contiene el digest del documento *enveloped*, manipular documento/firma/certs/CRL queda bajo el sello. **Requisito no negociable:** build y verify comparten **una única** función de canonicalización (`_archive_ts_covered_bytes`) — cualquier no-determinismo (prefijos NS, whitespace) rompería la verificación. No es plenamente EN 319 132; se documenta como perfil simplificado.

### Degradación observable y binding por tenant

- **Sin `ca.crt`/`crl.der` en el dir del tenant** → la firma se queda en **XAdES-T** (patrón "omitir en legacy"), el `xades_level` refleja el nivel **realmente alcanzado** (nunca el intencionado), y se emite un audit dedicado **`firma.lt_downgrade_no_ca`** (nunca silenciosa). `signer_lt_enabled` (default `False`, opt-in + kill-switch) activa el escalado; `signer_lt_require` (default `False`) fuerza **`503 lt_material_required`** cuando falta material CA (despliegues de conformidad estricta). Defaults del lado de la disponibilidad.
- **Aislamiento multi-tenant en el plano PKI:** `verify()` ancla `seal→CA` leyendo el dir **del tenant que firma** (pinning `ca_fingerprint`, migración 013) — un sello emitido por la CA del tenant B **falla** para el tenant A. No hay ancla CA global en código.

### Verificación (`verify_xades` extendido)

Fail-closed en todo: (1) cadena `seal→CA` (`Certificate.verify_directly_issued_by`, vigencias, `CA:TRUE`/`keyCertSign`); (2) **revocación real** — firma de la CRL contra la CA (fail-closed si inválida) y serial del sello **no** revocado (fail-closed si lo está); `crl_next_update` se comprueba y **expone**, sin hard-fail si caducó en modo no acreditado (las CRL de dev caducan); (3) `ArchiveTimeStamp` — reconstruye el conjunto cubierto con el helper compartido, `messageImprint`, token CMS RFC 3161, EKU crítico, pinning por fingerprint. Nivel reportado `XAdES-LT` o `XAdES-LTA` según lo presente y verificado. Retrocompatibilidad: firmas B/T previas verifican sin cambios.

### Conformidad declarada (RF-FIR-15)

`acreditado` **siempre `False`** (CA de dev, no ONAC). `nivel_conformidad` gana los valores `xades_lt_no_acreditado`/`xades_lta_no_acreditado` (migración 013, CHECK aditivo). La **CRL vacía** (0 revocados) es honesta; su procedencia se marca con `revocation_provenance="dev"` ("material de revocación de desarrollo, no de una PKI acreditada"). **Gap conocido documentado, no ocultado:** la TSA local es self-signed y no tiene material de validación propio (cadena/revocación) — una de las razones legítimas de `acreditado=False`; un B-LT estrictamente conformante también lo exigiría.

### Persistencia y API

Migración tenant **`013_lt_lta_material.sql`** (aditiva, nullable, validada idempotente en Postgres 15; no toca `firma_xades_level_check` que ya reservaba LT/LTA desde 008): `lt_material_present`, `ca_fingerprint`, `crl_fingerprint`, `crl_this_update`, `crl_next_update`, `archive_timestamp_present`, `archive_timestamp_at`, `archive_tsa_cert_fingerprint`, `revocation_provenance`, `archive_timestamp_token` (opcional, conveniencia). Enum `SignatureFormat` gana `XADES_LT`. `FirmaResponse`/`VerifyResponse` exponen `xades_level` + `lt_material_present` + `archive_timestamp_present` + `crl_next_update` + `revocation_provenance`. **Atomicidad** (remediación Media): el INSERT en `firma` y sus `audit.append` (`firma.creada`, `firma.lt_downgrade_no_ca`) van en un único `conn.transaction()` — la firma y su traza inmutable son atómicas (RF-FIR-14).

### Consecuencias

**Positivas**: material de validación embebido (cadena + revocación real contra CRL) → verificable a largo plazo aunque expire el cert; archive-timestamp que protege el conjunto firmado; aislamiento PKI por tenant; degradación honesta y auditada. **Negativas / límites (roadmap)**: CA/TSA de **desarrollo, no ONAC** → sigue no acreditado; perfil de ArchiveTimeStamp **simplificado** (no plenamente EN 319 132 — un validador XAdES-LTA de terceros no reconocería el conjunto cubierto propio); la TSA carece de material de validación propio; **XAdES-LT/LTA acreditado** (CA + TSA ONAC) queda diferido. **197 tests verdes**; 6 hallazgos Baja de auditoría aceptados/documentados (p. ej. exponer `crl_expired` derivado, pinning contra `archive_tsa_cert_fingerprint`).

### Relacionados (Inc.5)

- ETSI EN 319 132 (perfiles XAdES-LT/LTA); RFC 5280 (CRL); RFC 3161 (ArchiveTimeStamp reusa el token).

---

## Addendum — Incremento 6 (2026-07-06): 2FA por TOTP (RFC 6238) en la firma personal

**Estado**: Aceptado. Ratificado por `orfeo-architect`; auditado APTO por `tenant-security-auditor` y `archival-compliance-auditor`.

### Decisión

**Elevar el segundo factor de la firma personal de OTP-correo (posesión de canal, Inc.2) a TOTP RFC 6238 (posesión de dispositivo), como factor alternativo más fuerte** — sin reemplazar el OTP-correo ni forzar TOTP en este incremento.

**D1 — el secreto TOTP vive en auth-service, no en signature-service.** Es una credencial de identidad durable por usuario (analogía con `password_migrated`), no un artefacto de firma; ubicarla en signature la scopearía mal (mañana sirve para step-up de login/admin) y ampliaría el blast radius. signature-service **nunca ve el secreto**: llama a un endpoint interno de verificación. Tabla nueva `auth_user_totp` (migración tenant `010`, separada de `auth_users`, único parcial `WHERE estado <> 'revocado'`): `secret_cifrado`/`secret_nonce` (AESGCM), `estado` (`pendiente_activacion`/`activo`/`revocado`), `last_timestep` (anti-replay), `failed_attempts`/`locked_until` (anti-brute-force), `algoritmo`/`digitos`/`periodo`/`key_version`.

### Cifrado y RFC 6238

- **AESGCM** (`cryptography.hazmat`, **no** Fernet) con **AAD = `tenant:{slug}:user:{id}:totp`** — ata el ciphertext a la fila/tenant: un ciphertext movido entre filas o tenants **falla** al descifrar. Nonce aleatorio de 12 bytes por registro; clave `TOTP_ENCRYPTION_KEY` (AES-256) **separada** de `signature_secret`, en config de auth (se añadió `cryptography>=42` explícito a auth + `requirements.lock` regenerado). El secreto se descifra **solo en memoria** en verify; nunca en logs/respuestas/eventos/audit; se revela **una vez** en el `otpauth://` de `enroll` (`Cache-Control: no-store`).
- **RFC 6238 en stdlib** (`hmac`/`hashlib`, sin `pyotp` — coherente con `otp.py` y el RFC 3161 a mano): HMAC-SHA1, 6 dígitos, periodo 30 s, secreto 20 bytes, base32. Verificado contra los **vectores del Apéndice B** de la RFC.

### Endpoints y encaje

- **auth-service** (públicos, bearer del usuario): `POST /api/v1/auth/me/totp/enroll` (dos pasos: genera → `pendiente_activacion` → devuelve `otpauth://` una vez), `POST .../activate` `{code}`, `GET .../me/totp` (nunca el secreto), `DELETE .../me/totp` `{step_up_totp_code}`. **Step-up**: re-enrolar y desenrolar un factor **activo** exigen un código actual válido (consumido antes de la mutación) → sin ruta solo-sesión que swapee/despoje el 2.º factor.
- **auth-service** (interno, red Docker, **no** enrutado por el gateway): `POST /internal/totp/verify` `{code, contexto?}` — deriva la identidad del **bearer reenviado** (`validate_token → sub → get_user_by_keycloak_sub`), **nunca** de un `user_id` de cuerpo (cierra IDOR). Honra `locked_until` **antes** de comparar; avanza el anti-replay en un **único `UPDATE` condicional** (`WHERE last_timestep < Tm`) y decide el lockout en el mismo `UPDATE` server-side (evita el *lost update* del contador bajo concurrencia — remediación de la auditoría de seguridad). Ventana de skew ±1.
- **signature-service**: `FirmarRequest`/`LoteItem` ganan `totp_code`. `_gate_2fa` hace **selección determinista**: `totp_code` + TOTP activo → camino TOTP (verify vía auth, `contexto=solicitud_id` para correlacionar la evidencia entre servicios); `totp_code` sin TOTP activo → `422 totp_no_activo` (no cae en silencio al correo); ambos → prevalece TOTP; ninguno → `409 2fa_requerido`. **TOTP no toca `firma_challenge`** (ese modelo es del OTP pre-emitido); solo el payload de audit (`firma.turno_firmado factor_tipo='totp'`, `challenge_id=null`) y el dict de retorno del gate ganan `totp`. La regla `firma_2fa_required or nivel>=2 → 2FA` **no cambia**: TOTP es un **satisfactor** dentro del gate, nunca un bypass; el kill-switch sigue aplicando solo a PUBLICA(1).

### Brecha de atomicidad cross-service (aceptada)

El avance anti-replay (`last_timestep`) se commitea en la tx de **auth**, no en la de firma de signature. Si la firma hace rollback tras un verify OK, el código queda quemado (~30 s) — **sobre-consumo fail-safe, no replay**. Mitigación: el verify es el último guard antes de `marcar_turno`, con trabajo mínimo después. `verify_totp` es fail-closed (timeout ~3 s → `503 canal_2fa_no_disponible` + rollback), misma postura que el canal de correo caído en Inc.2.

### Conformidad (RF-FIR-15)

TOTP es factor de **posesión de dispositivo** (más fuerte que el OTP-correo), pero la firma sigue siendo **firma electrónica no cualificada** (Ley 527/1999 art. 7): TOTP **no** la convierte en firma digital/acreditada. No se rotula lo contrario en respuestas/UI/docs. El *system-of-record* del 2.º factor es el `audit_log` **de signature-service** (`firma.turno_firmado`, atómico con la firma); la entrada de auth (`totp.verificado`, best-effort) es corroborante.

### Consecuencias

**Positivas**: segundo factor más fuerte, self-service, sin infra externa; secreto confinado a auth y cifrado con binding por tenant/usuario; anti-replay atómico + lockout; retrocompatibilidad total (el camino OTP-correo sigue intacto). **Negativas / límites (roadmap)**: **WebAuthn** (posesión + biometría/hardware) sigue pendiente; el forzado de TOTP por nivel/tenant (`totp_requerido_por_nivel`) quedó reservado y se **retiró** (T8, AREA-04-02): lo hace signature-service, que consulta `GET /internal/totp/status` y rechaza el OTP-correo (`409 totp_requerido`) cuando el actor ya tiene TOTP activo en un acto de nivel ≥ 2; doble control para desenrolar firmantes `nivel>=2` es evolutivo. **97+208 tests verdes** (auth+signature); auditoría de seguridad APTO (1 Media remediada: incremento atómico del contador; 1 Baja remediada: fuente canónica del `tenant_slug` para el AAD).

### Relacionados (Inc.6)

- RFC 6238 (TOTP), RFC 4226 (HOTP), RFC 4648 (base32); RFC 5116 (AEAD/AES-GCM).

## Addendum (2026-10-02, tanda T8): segundo factor coherente con el actor, claves por entidad

- **Identidad (AREA-04-05).** La firma se atribuye a `X-User-Id` pero el OTP sale al correo del **bearer**, el TOTP se verifica contra el **bearer** y la credencial personal es la del **bearer**. Se asumía que eran la misma persona porque el gateway los deriva del mismo token; el comentario lo llamaba «fail-safe» y era al revés (quien tiene el bearer recibe el código y completa él mismo una firma atribuida a otro) — alcanzable desde un servicio interno comprometido, no desde fuera. Ahora `/me` (con `X-Tenant-Slug`), `/internal/totp/verify` y `/internal/signing-key/status` dicen **de quién** son (`user_id`) y signature-service exige que coincida con el actor: `403 identidad_incoherente` con asiento `firma.identidad_incoherente`, antes de crear el desafío, de marcar el turno o de pedir un factor. Un auth-service que no devuelva `user_id` se trata como incoherente (fail-closed).
- **Factor más fuerte (AREA-04-02).** En nivel ≥ 2, con TOTP activo, el correo no vale (`409 totp_requerido`); fail-closed si no se puede consultar.
- **Huella y claves.** La huella de una cadena de `radicado`/`expediente` no la declara el cliente (AREA-04-09). El HMAC de la firma nativa y el del OTP usan claves **HKDF por (propósito, entidad)** derivadas de `signature_secret`, el mensaje ata entidad+tipo+objeto (firma) o `challenge_id` (OTP), y `key_version` (migración `020`; DEFAULT 0 = esquema anterior, que sigue verificando) dice con qué esquema se produjo cada fila (AREA-04-11).
- **Verificación.** `acreditado` sale de lo persistido y solo si la cripto valida (AREA-05-02); la revocación posterior a la firma no la invalida (AREA-04-13); el sello de tiempo se cruza con la vigencia del certificado firmante y con el ESSCertID (AREA-04-08).
- **Aún abierto / decisión de la entidad**: el gate `FIRMA_PERSONAL_REQUIRE_XADES` sigue en `off` (activarlo exige enrolar certificados antes; ahora el arranque lo advierte y un canal de credenciales caído deja asiento `firma.fallback_nativa_canal_caido`, AREA-04-12); un vocabulario cerrado de motivos de revocación (para invalidar hacia atrás por compromiso de clave); HSM/KMS externo.
