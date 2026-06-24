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
