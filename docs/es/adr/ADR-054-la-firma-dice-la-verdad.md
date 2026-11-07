# ADR-054: La firma dice la verdad o no se llama firma

**Estado**: Aceptado
**Fecha**: 2026-11-07
**Autores**: Camilo Romero
**Relacionados**: [ADR-016](ADR-016-firma-electronica.md) (firma electrónica), [ADR-034](ADR-034-xmldsig-conforme-firma-verificable-por-terceros.md) (XMLDSig conforme), [ADR-030](ADR-030-constancia-firma-inobtenible.md) (constancia de firma inobtenible), [ADR-052](ADR-052-librerias-en-vez-de-implementaciones-propias.md), [ADR-055](ADR-055-publicacion-publica-de-instrumentos.md)

---

## Contexto

La firma es la parte del sistema de la que un tercero —un juez, la Procuraduría, el Archivo General— depende para creer algo. Se verificaron cuatro cosas antes de escribir este ADR, y las cuatro son defectos, no decisiones discutibles.

### 1. Hay dos caminos de firma y el que se usa no se verifica

| Camino | Dónde firma | Dónde queda el registro | ¿Se puede verificar? |
|---|---|---|---|
| **Declarativa (E06)**, la que usan los funcionarios | `document/routers/signatures.py` | `signatures` (migración `011`) | Sí, por hash contra las firmas del radicado |
| **Probatoria**, la que sella | `signature-service` | `firma`, `firma_solicitud`, `firma_cadena` | Sí, por XAdES con `POST /verify` |

El registro de deuda del proyecto lo declara así: *«Signature verification gap: `/verify` reads the empty `signatures` table, not `firma`»* (`docs/en/technical-reference.md:689`). Es decir: el endpoint que un tercero usaría para comprobar una firma con valor probatorio consulta **la tabla del camino simple**, donde no hay filas de firma XAdES.

La comprobación de que ese diagnóstico sigue siendo el estado real: `POST /api/v1/signature/verify` resuelve por `FirmaRepository.get(...)` sobre `firma` y devuelve 404 si no hay fila. `GET /api/v1/documents/{id}/signatures/verify` resuelve por `SignatureRepository` sobre `signatures`, y responde `{valid: bool}` según haya o no coincidencia de hash. Son dos preguntas distintas («¿esta firma existe y es válida?» y «¿este hash está entre las firmas de este radicado?») y la primera no tiene pantalla.

### 2. Un acto clasificado puede firmarse con HMAC

La política es por entidad y se lee de `sgd_param_admin` con respaldo en el entorno:

```378:378:services/signature-service/signature/core/config.py
    firma_personal_require_xades: Literal["off", "clasificados", "todos"] = "off"
```

Con `off` —el valor por defecto, y el que siembra la migración— una firma personal sobre un documento de nivel 2 o 3 puede resolverse con la firma **simplemente electrónica** (HMAC sobre el hash, con `firmante_id` y `sello_tiempo` de la base). Ese camino es íntegro, pero **no es verificable por un tercero**: la clave la tiene el sistema, no el firmante, así que no hay prueba de autoría que sobreviva a la entidad.

`params_entidad.py` ya razona bien el fail-closed para valores corruptos (`todos`), pero el valor por defecto es el permisivo. La Ley 527 de 1999 (art. 7) y el DUR 1074 de 2015 (arts. 2.2.2.47.x) admiten la firma electrónica simple, pero la carga de la prueba cambia: es quien quiere hacerla valer quien debe demostrar fiabilidad. Para material clasificado la entidad necesita esa fiabilidad, no la puede presumir.

### 3. OCSP está apagado y el sello se declara no acreditado

```304:304:services/signature-service/signature/core/config.py
    signer_ocsp_enabled: bool = False
```

Con OCSP en `False`, una firma LT/LTA no lleva evidencia de revocación del lado del firmante, y la única comprobación es la de revocación personal contra `auth-service`, en línea. `acreditado` es un literal `False` en `keys.py::load_seal`. Eso es **honesto** —ADR-016 dice que nunca se reportará como cualificada— pero significa que, de las dos partes de una firma avanzada (identidad verificada y dispositivo seguro), el sistema solo sostiene la primera.

### 4. Faltan las dos cosas que hacen útil un camino probatorio

- **No hay pantalla que responda «¿este PDF es el que se firmó?»** (deuda declarada, `technical-reference.md:689`).
- **`POST /verify` es autenticado y no público.** Su docstring lo explica y es correcto para no filtrar contenido, pero deja sin respuesta al caso real: un ciudadano o un ente de control que recibe un documento firmado y quiere comprobarlo.

## Decisión

### 1. Un solo camino verificable, y la firma simple se declara como tal

Se mantienen los dos caminos, porque tienen funciones distintas, pero se nombran por lo que son y no por dónde viven:

| Camino | Nombre en el producto | Lo que prueba |
|---|---|---|
| Declarativa (E06) | **Constancia de firma simple** | Que este usuario, con este permiso, declaró este hash en este instante. Íntegro y atribuible dentro del sistema. **No** verificable por un tercero. |
| Probatoria (XAdES) | **Firma electrónica** | Que este certificado firmó este XML con este sello de tiempo. Verificable por cualquiera con el certificado. |

Reglas:

- **La UI no llama «firma» a la constancia simple sin calificarla.** Cualquier lugar que hoy muestre «firmado» distingue «firma electrónica» de «constancia de firma simple». Es un cambio de etiqueta, no de comportamiento, y evita la lectura equivocada que el propio sistema induce hoy.
- **`firma_personal_require_xades` pasa a `clasificados` por defecto** en el código y en la migración que siembra la fila. `off` deja de ser el valor con el que nace una entidad. La entidad puede bajarlo a `off` de forma explícita y queda en auditoría.
- **El camino simple deja de ser el que responde a «¿es válida esta firma?»**. La pregunta probatoria la responde solo el camino XAdES.

### 2. Verificación pública por código, sin filtrar contenido

Se añade una superficie pública, hermana de la consulta de radicado (`GET /api/v1/public/{tenant}/verify/{code}`), que responde a la pregunta probatoria sin exponer el documento:

`GET /api/v1/public/{tenant}/firma/{firma_id}` →

```json
{
  "firma_id": "...",
  "valida": true,
  "firma_criptograficamente_valida": true,
  "revocacion_status": "no_revocado",
  "nivel": "B-T-LT",
  "firmante": "nombre que consta",
  "firmado_en": "2026-11-07T12:00:00-05:00",
  "sello_tiempo": "2026-11-07T12:00:03-05:00",
  "huella_documento": "sha256:…",
  "acreditado": false,
  "constancia": "url al XML firmado, si el solicitante tiene permiso"
}
```

- **Sin contenido**: no devuelve el XML ni el PDF, solo el veredicto y los metadatos que la propia firma declara.
- **No es oráculo**: el `firma_id` es un UUID de 128 bits, igual que el código de consulta actual, y un identificador inexistente da el mismo 404.
- **Reutiliza el mismo motor**: llama a `verify_xades` con las mismas comprobaciones (`signed_xml_sha256`, huella del sello, huella de la TSA cuando hay `SignatureTimeStamp`), no una versión recortada.
- **`acreditado` se publica tal cual.** Es el campo que impide que alguien lea «válida» como «cualificada».

### 3. La verificación personal es en línea y sigue siéndolo

`GET /internal/signing-key/revocation-status` contra `auth-service`, sin caché, se conserva: una firma de una clave revocada debe dejar de reportarse válida en el mismo minuto. El costo (una llamada interna por verificación) es aceptable al volumen del cliente y es una decisión ya tomada en ADR-034.

### 4. Dos defectos concretos que se corrigen

| Defecto | Corrección |
|---|---|
| El registro de deuda dice que `/verify` lee `signatures` y no `firma` | Se verifica contra el código antes de tocar nada; si el endpoint probatorio ya lee `firma`, lo único que falta es la superficie pública (§2) y la etiqueta (§1). Si además hay una ruta que responde con la tabla equivocada, se corrige y se añade la prueba que lo fija. |
| `verify` es el único camino y no hay pantalla | Una pantalla `/(public)/firma/[id]` y un enlace desde «firmas» a «comprobar una firma». |

### 5. Lo que este ADR **no** hace

- **No sustituye la capa XAdES** por otra implementación (ADR-052 lo difiere, con la batería de EU DSS como puerta).
- **No acredita el sello.** `acreditado=False` se queda hasta que haya un prestador acreditado en ONAC detrás; el ADR solo asegura que no se mienta sobre ello.
- **No enciende OCSP por defecto.** Se deja como opción por entidad documentada, porque un OCSP mal configurado produce firmas que fallan al verificar. Se marca como requisito declarado para cualquier entidad que firme hacia fuera.

## Alternativas consideradas

- **Unificar todo en el camino XAdES y retirar la firma declarativa.** Descartado: la constancia simple es lo que permite a un funcionario con permiso dejar rastro de que revisó y avaló sin tener certificado personal enrolado, y hoy es el camino que usan las pantallas. Retirarlo obligaría a cada funcionario a un certificado antes de poder trabajar.
- **Exigir XAdES siempre (`todos`).** Descartado como valor por defecto: una entidad sin certificados personales enrolados no podría firmar nada, y el sistema quedaría inutilizable en el día uno. `clasificados` protege lo que lo necesita y deja operar.
- **Devolver el XML firmado en la verificación pública.** Descartado: convertiría la verificación en una descarga de documentos sin control de acceso ni clearance. Quien necesite el XML lo pide autenticado, con permiso.
- **Verificación pública por hash del documento.** Descartado: permitiría confirmar por fuerza bruta el contenido de un documento reservado. El identificador de firma no revela nada del contenido.

## Consecuencias

- **Se puede responder a un tercero.** «Esta firma es válida, la hizo esta persona en esta fecha, con este sello de tiempo, y no está acreditada» es una respuesta completa y verificable.
- **Cambia una etiqueta en varias pantallas.** Donde hoy dice «firmado» habrá que distinguir. Es trabajo de UI y de traducción de mensajes, con la prueba de mensajes en español que ya existe.
- **Una entidad puede nacer en `clasificados` y no poder firmar clasificados hasta enrolar certificados.** Ese es el punto: el fallo se ve el día uno, no en la auditoría.
- **La verificación pública añade una superficie anónima.** Se acota con el límite de tasa de `edge` y con el identificador de 128 bits; no hay contenido que filtrar.
- **Sigue sin haber firma cualificada.** Se declara en cada respuesta y en la documentación; el producto no la promete.
