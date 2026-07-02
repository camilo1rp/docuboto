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
- `POST /verify` es **público** (oráculo de existencia por-tenant de bajo impacto: `firma_id` son UUID aleatorios); aceptado por contrato.

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
