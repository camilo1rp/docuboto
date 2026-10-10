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
- Disposición final configurable por serie: **CT** (conservación total), **E** (eliminación), **MF** (microfilmación), **RD** (reproducción/digitalización), **S** (selección)

---

## Ley 527 de 1999 y Decreto Único 1074 de 2015 — Firma electrónica y digital

Dan **equivalencia funcional** y valor probatorio a los documentos electrónicos y sus firmas. La **Ley 527/1999** define la firma electrónica (métodos: claves, biométricos, contraseñas) y la firma digital (criptografía con certificado de entidad acreditada), y la **equivalencia funcional** de la firma (art. 7) y del escrito. Su reglamento vigente es el **Decreto Único 1074/2015, Cap. 47** (arts. 2.2.2.47.1–47.10, que **compilan y amplían** el Decreto 2364/2012: confiabilidad 47.4, efectos jurídicos 47.5, obligaciones del firmante 47.6, grado de seguridad 47.8) y **Cap. 48** + **Decreto 333/2014** (entidades de certificación / acreditación **ONAC**). El **Decreto 526/2021** habilita la firma electrónica del contrato de trabajo.

> ⚠️ Citar el **DUR 1074/2015**, no el Decreto 2364/2012 de forma autónoma. Marco íntegro extraído en `documentos/normativa-firma/`.

**Qué implementa (o especifica) OrpycaMCP de este marco** (épica E06):
- Firma del **índice electrónico al cierre** del expediente (Acuerdo AGN 001/2024 Art. 4.3.2.4) — hoy firma nativa, XAdES+TSA diferida.
- **Atribución verificada** del firmante (identidad del JWT), **2FA en el acto de firma personal** y **sello electrónico de persona jurídica** para el autofirmado de documentos generados por el sistema.
- Conservación de la prueba (firma + certificado + estampado de tiempo + refrendo) sin alterar el contenido firmado.

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
- Almacenamiento en el almacén de objetos S3 con control de versiones
- Trazabilidad de quién cargó cada archivo y cuándo

---

## NTC-ISO 15489 — Gestión de Documentos

Estándar internacional adoptado en Colombia. Define los principios de:

- **Autenticidad**: el documento es lo que dice ser
- **Fiabilidad**: el contenido es una representación completa del acto
- **Integridad**: está completo y no ha sido alterado
- **Disponibilidad**: puede ser localizado y utilizado cuando se necesite

---

## Ley 1581 de 2012 y DUR 1074 de 2015 — Protección de datos personales

La **ventanilla pública** recoge datos personales de quien radica y, por eso, su **autorización previa, expresa e informada** (Ley 1581/2012, arts. 8 lit. b, 9 y 12): el titular debe conocer el aviso de privacidad (DUR 1074/2015 art. 2.2.2.25.3.2) **antes** de autorizar.

**Qué implementa OrpycaMCP** (ADR-031):
- La **política de tratamiento es de cada entidad** (`politica_tratamiento_version`, `_url`, `_texto` en `sgd_param_admin`) y la sirve el servidor en `GET /api/v1/public/{tenant}/ventanilla/politica`. Sin política publicada la ventanilla **no radica** (409 `politica_no_publicada`); si cambió mientras se diligenciaba el formulario, 409 `politica_desactualizada`.
- La **constancia de autorización** (v2) guarda versión y huella de la política, su URL, el instante y la IP que el gateway **firmó** (nunca la declarada por el cliente); sin firma válida la IP queda nula y `ip_verificada=false`.

### Supresión y retención {#supresion}

El titular puede pedir la **supresión** de sus datos (Ley 1581 art. 8 lit. e y art. 15), pero el derecho **no procede mientras exista un deber legal de conservarlos** (DUR 1074/2015 art. 2.2.2.25.2.8; Ley 594/2000 art. 11 y la TRD de la serie documental). Un radicado es documento de archivo: se conserva durante su retención y **procede la supresión al vencer la retención**, por la disposición final de la TRD (eliminación, o selección y conservación total). La solicitud **no se ignora**: se responde como **reclamo** (art. 15) explicando el deber de conservación y la fecha a partir de la cual procede. La plantilla de política ([politica-tratamiento-plantilla.md](politica-tratamiento-plantilla.md)) incluye este texto con el ancla `#supresion`, a la que enlaza la interfaz bajo la casilla de autorización.

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

