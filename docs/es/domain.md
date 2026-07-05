# Dominio Documental

Esta sección documenta las entidades, reglas de negocio y flujos del SGD Orfeo que OrpycaMCP reimplementa. Es la referencia obligatoria antes de implementar cualquier funcionalidad.

> Las reglas aquí descritas provienen del análisis del código fuente del Orfeo original (múltiples instalaciones: ICETEX, Bogotá Limpia, Argoik, Veeduría, FondeCund). Usar el agente `orfeo-domain-analyst` para profundizar en cualquier comportamiento antes de codificarlo.

---

## Marco normativo

OrpycaMCP implementa la gestión documental según las normas colombianas del AGN (Archivo General de la Nación):

| Norma | Qué regula |
|---|---|
| **Ley 594 de 2000** | Ley General de Archivos — obliga a organizar documentos por TRD |
| **Acuerdo AGN 060/2001** | Comunicaciones oficiales — radicación, distribución, numeración |
| **Decreto 1080/2015** | Normativa archivística unificada (Decreto Único Sector Cultura) |
| **NTC-ISO 15489** | Estándar internacional de gestión de documentos |

Estas normas definen directamente cómo deben funcionar la radicación, los expedientes y la TRD en el sistema.

---

## Ciclo de gestión documental

```
Producción/Recepción → Radicación → Distribución → Trámite → Archivo → Disposición final
```

Cada etapa tiene entidades y reglas específicas en OrpycaMCP.

---

## Entidades del dominio

### Radicado

El documento registrado oficialmente. Es la entidad central del SGD.

**¿Qué hace?** Asigna un número oficial único e inmutable al documento, registra su entrada al sistema institucional y lo vincula a un tipo documental, dependencia y responsable.

**¿Por qué existe?** El Acuerdo AGN 060/2001 obliga a que toda comunicación oficial que ingresa o sale de una entidad pública quede numerada, fechada y trazada. Sin radicado, el documento no tiene existencia legal en el sistema.

**¿Cómo se usa?**
```bash
POST /api/v1/documents/
{
  "subject": "Solicitud de información pública - Decreto 2232",
  "document_type_id": "uuid-tipo-documental",
  "dependencia_id": "uuid-dependencia-destino",
  "sender_name": "Juan García",
  "sender_entity": "Ciudadano",
  "pages": 3
}
# Respuesta:
# { "tracking_number": "2024-DEMO-E-000001", "id": "uuid", ... }
```

**¿Qué puede salir mal?**
- Conflicto de consecutivo: la asignación es atómica (`SELECT FOR UPDATE`), el sistema reintenta
- Tipo documental inválido para la TRD del tenant: error 422
- El número asignado **nunca puede modificarse ni reasignarse**

**¿Cómo contribuir?** `services/document-service/app/services/tracking_service.py`

#### Tipos de radicado

| Código | Nombre | Descripción | Norma |
|---|---|---|---|
| **E** | Entrada | Comunicación recibida de terceros externos | AGN 060 Art. 3 |
| **S** | Salida | Comunicación enviada a terceros externos | AGN 060 Art. 3 |
| **I** | Interno | Comunicación entre dependencias de la misma entidad | Ley 594 |

#### Formato del número de radicado

```
{AÑO} - {CÓDIGO_ENTIDAD} - {TIPO} - {CONSECUTIVO:06d}

Ejemplo: 2024-ICETEX-E-001234
         2024-ICETEX-S-000087
         2024-ICETEX-I-002341
```

- El consecutivo es **por año + tipo + entidad** — nunca se comparte entre tipos
- Se reinicia en 000001 cada 1 de enero
- La asignación es **atómica** (transacción con bloqueo en tabla `tracking_sequences`)

**Reclasificación de seguridad (E08).** El `nivel_seguridad` (1=PUBLICA, 2=RESERVADA, 3=CLASIFICADA) se fija al radicar pero puede **reclasificarse** después vía `PATCH /documents/{id}/security-level` (paridad con el *Cambio Nivel de Seguridad* de Orfeo). Es un cambio del **control de acceso**, no del contenido ni del número: la inmutabilidad del radicado queda intacta. Exige permiso `PERM_RECLASIFICAR`, respeta *no-read-up* (no se puede reclasificar lo que no se puede leer → `404` neutro) y *no-write-up* (no se puede fijar por encima del propio *clearance* → `403`), y **el acto debe motivarse** (motivo obligatorio + fundamento jurídico opcional), registrándose en el `audit_log` inmutable. El cambio se propaga por evento al `signature-service`, que re-sincroniza el nivel de las cadenas de firma del objeto.

---

### Expediente

Agrupación lógica de radicados relacionados al mismo asunto o caso.

**¿Qué hace?** Organiza los documentos de un trámite completo en una unidad documental única, permitiendo ver toda la historia de un caso en un solo lugar.

**¿Por qué existe?** La Ley 594/2000 y las normas del AGN exigen que los documentos se organicen en unidades documentales coherentes (expedientes) para facilitar la consulta y disposición final.

**Estados del expediente:**
```
Abierto ──► Cerrado ──► Transferido al archivo central
```

**Reglas:**
- No se puede cerrar un expediente sin TRD configurada para su serie
- La transferencia requiere el inventario documental (formato E-7 del AGN)
- Un radicado puede pertenecer a un solo expediente activo

---

### TRD — Tabla de Retención Documental

Instrumento archivístico que clasifica los documentos y define su tiempo de retención y disposición final.

**¿Qué hace?** Organiza jerárquicamente los documentos de la entidad y determina cuánto tiempo se conservan y qué pasa con ellos al vencimiento.

**¿Por qué existe?** La Ley 594/2000 Art. 24 obliga a todas las entidades públicas a elaborar y aplicar TRD aprobada por el AGN o la autoridad archivística competente.

**Estructura jerárquica:**
```
Entidad
└── Sección (ej: Dirección General)
    └── Subsección (ej: Subdirección Administrativa)
        └── Serie documental (ej: Contratos)
            └── Subserie (ej: Contratos de prestación de servicios)
                └── Tipo documental (ej: Contrato, Acta de inicio, Informe)
```

**Disposición final:**
| Código | Nombre | Descripción |
|---|---|---|
| CT | Conservación Total | Se conserva permanentemente |
| E | Eliminación | Se destruye al vencer la retención |
| MT | Microfilmación + Eliminación | Se microfilma antes de eliminar |
| S | Selección | Se selecciona una muestra representativa |

**Regla crítica:** Sin TRD configurada para una serie, no se puede cerrar el expediente que la usa.

---

### Anexo

Archivo binario adjunto a un radicado, almacenado en MinIO.

**¿Qué hace?** Preserva el documento físico (PDF, imagen, oficio escaneado) vinculado al radicado, con garantía de integridad por checksum.

**¿Por qué existe?** El documento electrónico tiene el mismo valor legal que el físico cuando cumple los requisitos de integridad y autenticidad (Ley 527 de 1999 — Comercio Electrónico).

**¿Cómo se usa?**
```bash
POST /api/v1/storage/annexes/
Content-Type: multipart/form-data

radicado_id: uuid
file: [archivo.pdf]

# Respuesta:
# { "annexe_id": "uuid", "filename": "oficio_123.pdf",
#   "sha256": "a1b2c3...", "size_bytes": 204800,
#   "download_url": "..." }
```

**Integridad:** El SHA-256 se calcula en el momento del upload y se almacena. Cualquier descarga puede verificarse contra este valor para detectar corrupción.

---

### Dependencia

Unidad organizacional de la institución (departamento, área, oficina).

**¿Qué hace?** Representa la estructura organizacional de la entidad. Cada radicado tiene una dependencia origen y una destino.

**¿Por qué existe?** La distribución de documentos ocurre entre dependencias. La TRD también se organiza por secciones que corresponden a dependencias.

**Atributos clave:**
- Código único dentro de la entidad
- Responsable (jefe de dependencia)
- Dependencia padre (para jerarquía orgánica)
- Estado: activa / inactiva

---

### Flujo (Distribución)

Registro de cada movimiento del documento entre dependencias.

**¿Qué hace?** Traza el recorrido completo del documento desde su radicación hasta su archivo, registrando quién lo tuvo, cuándo, qué acción tomó y a quién lo envió.

**¿Por qué existe?** El Acuerdo AGN 060/2001 exige trazabilidad completa de las comunicaciones oficiales. También permite medir tiempos de respuesta y detectar cuellos de botella.

**Ciclo típico de un flujo:**
```
Radicado E-000001
  → Ventanilla (radicado) 2024-01-15 09:00
  → Dirección General (distribuido) 2024-01-15 09:05
  → Subdirección Jurídica (enviado para concepto) 2024-01-16 10:30
  → Dirección General (con concepto, para respuesta) 2024-01-18 14:00
  → Ventanilla (respuesta radicada como S-000042) 2024-01-19 11:00
```

---

### Destinatario

Persona o entidad que recibe un radicado de salida.

**Regla:** Un radicado de salida puede tener **múltiples destinatarios** (copia a varios). Cada destinatario tiene su propio registro de envío y confirmación.

---

## Entidades de conformidad y ciclo de vida (F3–F6)

### Índice electrónico (E15)
Equivalente funcional de la **foliación + hoja de control** (Acuerdo AGN 001/2024). Documento **XML versionado append-only** por expediente, con la huella SHA-256 de cada documento (`valor_huella`), su orden original inmutable y la huella del propio índice (`xml_sha256`). Se crea en **versión 0** al abrir el expediente y se regenera automáticamente al vincular/excluir radicados. **Regla:** firmado el índice al cierre, el expediente es inmutable. La verificación del índice contrasta cada `valor_huella` con el hash actual del documento, **re-hashea el XML almacenado contra `xml_sha256`** (`huella_indice_ok`, detecta alteración del propio índice) **y** valida la firma del índice contra el `signature-service` (XAdES-B con sello institucional, ADR-016 Inc.1). La firma **XAdES-T/LT/LTA** (TSA, CRL/OCSP) sigue diferida (F4).

**Perfil v3 del índice — "conforme extendido" (E15, [ADR-022](adr/ADR-022-indice-electronico-v2-metadatos.md)):** los índices nuevos se generan en `urn:orpycamcp:indice:v3` con, por documento: metadatos RT-15 (formato/tamaño/foliación/fecha de incorporación, capturados al vincular), **orden documental ESTABLE** (asignado al vincular con `MAX+1` atómico, **inmutable**, nunca renumerado — principio de orden original AGN 001/2024 art. 4.3.2.3), **lista de control de acceso** (nivel de seguridad del expediente) y, en cabecera, **contexto** (`fechaPrimerUso` + serie/subserie por nombre). La **exclusión** de un radicado es **soft-delete** (`estado='excluido'`): el ordinal no se reutiliza y el índice emite el documento excluido marcado (`excluido="true"` + fecha/causal) → **hueco trazable**. Los índices **v1/v2/v3 ya firmados quedan inmutables**. Es **"conforme extendido"**, **no** "conforme completo": faltan **historial de acceso** por documento, **fecha de declaración**, **ACL por roles** (viven en auth-service) y **dependencia productora** (sin campo) — no se rotula conformidad total mientras falten (RF-FIR-15).

### Firma electrónica (E06)
Constancia verificable de quién suscribió un radicado/anexo y cuándo: identidad (JWT del firmante, RF-FIR-11) + **hash SHA-256** del contenido + sello de tiempo del servidor + `firma_blob` HMAC con secreto de servicio (RF-FIR-12). Una firma verifica si su HMAC recomputado coincide (detección de alteración). Es firma **electrónica básica** (Ley 527/1999; DUR 1074/2015 Cap. 47); la firma **digital** PKI/X.509 (XAdES/PAdES/TSA) está prevista en el `signature-service` (ADR-016, diferida F4).

**Cadena de firma** (`firma_solicitud` + `firma_cadena`): una solicitud agrupa los turnos ordenados (`orden` 1..N) de firma de un objeto documental. El **turno activo** es el de menor orden pendiente; el firmante N+1 solo puede actuar tras firmarse el turno N (RF-FIR-04). Estados de turno y solicitud: `pendiente → firmado` o `rechazado` (un rechazo detiene toda la cadena). La «bandeja del firmante» lista, cross-documento, las solicitudes cuyo turno activo es del usuario. Cada acto (crear/firmar/rechazar) se registra en `audit_log` (RF-FIR-12).

### Archivo físico: Ubicación, Unidad de conservación, Signatura (E17)
Patrón ArchivesSpace (ADR-017): la **Ubicación** (dirección física, jerarquía recursiva sede→…→gaveta) se separa de la **Unidad de conservación** (contenedor móvil: caja/carpeta/legajo…). La **signatura topográfica** (`DEP01-E05-C0124`) localiza la unidad y es el puente intelectual↔físico. Un expediente híbrido se vincula a sus unidades con rango de folios. Las unidades se **prestan** (`prestado→devuelto→vencido`) con historial de movimientos append-only.

### FUID — Formato Único de Inventario Documental (E17/E12)
Inventario canónico (Acuerdo 042/2002) derivado de expediente↔unidad↔signatura↔serie; entregable de toda transferencia.

### Transferencia documental (E12)
Paso del expediente entre los tres archivos del ciclo vital: **primaria** (gestión→central) y **secundaria** (central→histórico). Ciclo `preparada→enviada→recibida|rechazada`. Al **recibir**, el expediente pasa a `transferred` y queda **congelado** (no admite documentos). Lleva el FUID como entregable.

### Preservación digital (E10)
**Plan de Preservación Digital** versionado (formatos destino, nº de copias, periodicidad de fijación) + registro inmutable de **eventos PREMIS** (ingesta/fijación/migración/validación/WORM). Garantiza las cinco cualidades a largo plazo (ISO 14721 OAIS). El empaquetado AIP/BagIt, el WORM real (MinIO Object Lock) y la validación PDF/A (veraPDF) están previstos y pendientes de infraestructura.

### Envío postal (E20)
Despacho de un radicado de salida por un **operador postal** (4-72/Servientrega) con número de **guía** y estado de entrega (`registrado→en_transito→entregado|devuelto|fallido`), con historial.

### Webhook (E11)
Suscripción por tenant a eventos de dominio: OrpycaMCP entrega el evento a sistemas externos por **POST firmado HMAC-SHA256** (ADR-018).

---

## Reglas de negocio críticas (resumen)

| # | Regla | Consecuencia si se viola |
|---|---|---|
| 1 | El número de radicado es **inmutable** | Pérdida de trazabilidad legal |
| 2 | La asignación del número es **atómica** | Números duplicados |
| 3 | Un radicado de salida puede tener **N destinatarios** | Pérdida de destinatarios |
| 4 | Todo anexo tiene **SHA-256** calculado en el upload | No se puede garantizar integridad |
| 5 | Sin TRD configurada, **no se cierra el expediente** | Incumplimiento Ley 594 |
| 6 | El expediente tiene **ciclo de vida** open→closed→transferred | Archivo sin control |
| 7 | Cada entidad tiene su **schema aislado** en PostgreSQL | Fuga de datos entre instituciones |
| 8 | El **índice electrónico** es versionado append-only; tras el cierre es inmutable | Pérdida de validez legal del expediente |
| 9 | La **disposición** del expediente se deriva de su serie TRD al cerrar | Eliminación/conservación indebida |
| 10 | Recibida una **transferencia**, el expediente queda **congelado** | Alteración del acervo transferido |

---

## Glosario

| Término | Definición |
|---|---|
| **AGN** | Archivo General de la Nación — ente rector de la política archivística en Colombia |
| **CCD** | Cuadro de Clasificación Documental — antecede a la TRD |
| **FUID** | Formato Único de Inventario Documental — requerido en transferencias |
| **PGD** | Programa de Gestión Documental — plan institucional obligatorio |
| **Radicación** | Acto de asignar un número oficial a un documento |
| **Serie documental** | Conjunto de documentos del mismo tipo producidos por la misma función |
| **Subserie** | Subdivisión de una serie documental |
| **Transferencia primaria** | Paso del archivo de gestión al archivo central |
| **Transferencia secundaria** | Paso del archivo central al archivo histórico |
| **Índice electrónico** | XML versionado con la huella de cada documento del expediente; equivalente a foliación + hoja de control (Acuerdo 001/2024) |
| **Signatura topográfica** | Código que localiza inequívocamente una unidad de conservación en el archivo físico |
| **OAIS** | Open Archival Information System (ISO 14721) — modelo de preservación a largo plazo (SIP/AIP/DIP) |
| **PREMIS** | Estándar de metadatos de preservación (objetos, eventos, agentes, derechos) |
| **WORM** | Write Once Read Many — almacenamiento inmutable (MinIO Object Lock) para preservación |
| **PDF/A** | Perfil de PDF para archivo a largo plazo (validable con veraPDF) |
| **Webhook** | Entrega HTTP firmada de eventos de dominio a sistemas externos |

<!-- TODO: translate to English -->
