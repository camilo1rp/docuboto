# Marco Normativo

OrpycaMCP implementa los requerimientos establecidos por la normativa archivística colombiana. Esta sección explica qué norma origina cada funcionalidad del sistema.

---

## Ley 594 de 2000 — Ley General de Archivos

La norma fundamental. Obliga a todas las entidades públicas colombianas a:

- Organizar sus documentos mediante **Tablas de Retención Documental (TRD)**
- Garantizar la conservación del patrimonio documental
- Establecer un **Programa de Gestión Documental (PGD)**
- Aplicar el principio de **procedencia** (los documentos se organizan según la unidad que los produce)

**Qué implementa OrpycaMCP de esta ley:**
- Módulo TRD con estructura de secciones, series y subseries
- Estados del expediente con control de transferencia
- Disposición final configurable por serie: CT, E, MT, S

---

## Acuerdo AGN 060 de 2001 — Comunicaciones Oficiales

Establece las pautas para la **radicación, distribución y control de las comunicaciones** en entidades públicas.

Puntos clave:

> *"Las entidades deberán establecer sistemas para el control de las comunicaciones recibidas y enviadas"*

> *"A las comunicaciones recibidas se les asignará un número de radicado en el orden consecutivo en que se reciban"*

> *"El número de radicado no puede enmendarse ni tacharse"*

**Qué implementa OrpycaMCP de este acuerdo:**
- Radicación de Entrada (E), Salida (S) e Interno (I)
- Número consecutivo por año y tipo, **inmutable**
- Registro del remitente, fecha, dependencia destino y asunto
- Trazabilidad del recorrido del documento (flujos)

---

## Decreto 1080 de 2015 — Decreto Único Sector Cultura

Compiló y unificó toda la normativa archivística colombiana. Incluye los requisitos de:

- **Organización de archivos** por ciclo vital del documento
- **Inventarios documentales** en transferencias (Formato FUID)
- **Valoración documental** para determinar disposición final

---

## Ley 527 de 1999 — Comercio Electrónico y Documento Electrónico

Da valor legal al documento electrónico cuando cumple con:

- **Integridad**: el documento no ha sido alterado
- **Autenticidad**: se puede verificar su origen
- **Accesibilidad**: puede ser consultado posteriormente

**Qué implementa OrpycaMCP de esta ley:**
- Checksum SHA-256 de cada anexo para garantizar integridad
- Almacenamiento en MinIO con control de versiones
- Trazabilidad de quién cargó cada archivo y cuándo

---

## NTC-ISO 15489 — Gestión de Documentos

Estándar internacional adoptado en Colombia. Define los principios de:

- **Autenticidad**: el documento es lo que dice ser
- **Fiabilidad**: el contenido es una representación completa del acto
- **Integridad**: está completo y no ha sido alterado
- **Disponibilidad**: puede ser localizado y utilizado cuando se necesite

---

## Aplicabilidad internacional

Aunque el marco de referencia principal es colombiano, los conceptos son aplicables a otros países latinoamericanos con normativas similares:

| País | Ente rector | Norma equivalente |
|---|---|---|
| Colombia | AGN | Ley 594/2000 |
| México | AGN México | Ley General de Archivos 2019 |
| Perú | AGN Perú | Ley 25323 + Decreto 008-92-JUS |
| Argentina | AGN Argentina | Decreto 1571/81 |
| Chile | Archivo Nacional | Decreto con Fuerza de Ley 5200/1929 |
| Ecuador | ANE | Ley del Sistema Nacional de Archivos |

OrpycaMCP puede adaptarse a cualquier normativa ajustando la configuración de TRD y los campos de radicación.

<!-- TODO: translate to English -->
