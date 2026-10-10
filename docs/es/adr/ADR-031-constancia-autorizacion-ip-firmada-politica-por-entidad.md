# ADR-031: Constancia de autorización: IP firmada en rutas públicas y política por entidad

**Estado**: Aceptado
**Fecha**: 2026-10-09
**Autores**: Giampiero (mantenedor principal); triage de la ventanilla pública (tanda B2)
**Relacionados**: [ADR-010](ADR-010-libreria-compartida.md) (librería compartida), [ADR-013](ADR-013-autorizacion-resuelta-en-bd.md), [ADR-023](ADR-023-worm-object-lock-preservacion.md) (WORM)

---

## Contexto

La ventanilla pública (`POST /api/v1/public/{tenant}/ventanilla` y la PQRS heredada) recoge la
autorización de tratamiento de datos personales (Ley 1581/2012, arts. 8 lit. b y 12) y guarda una
**constancia** en `documents.metadata.consentimiento`. Esa constancia es prueba, y tenía cuatro defectos:

1. **La IP la escribía el cliente.** `_ip_declarada` leía `X-Real-Ip` y, si faltaba, `X-Forwarded-For`,
   que el gateway reenvía y escribe quien llama. El campo se llamaba `ip_declarada` por honestidad, pero
   un dato probatorio que se elige con una cabecera no prueba nada.
2. **La política era una variable del frontend**, única para todas las entidades y sin relación con lo
   que la entidad publicó: el servidor aceptaba **cualquier** cadena como `politica_version`.
3. **Sin política publicada se radicaba igual** (`sin-politica-publicada`): una autorización «informada»
   sin aviso de privacidad (DUR 1074/2015 art. 2.2.2.25.3.2) no lo es.
4. **El acuse de entrega de un envío postal** (`POST /envios/{id}/estado`) solo cotejaba que el
   `file_id` existiera y la huella coincidiera: servía el fichero que subió **otra persona** o un
   **anexo** ya radicado como «constancia de entrega».

## Decisión

### 1. IP verificada: par de cabeceras propio, firmado por el gateway

En una ruta pública no hay persona ni token, así que no hay identidad que firmar; pero sí hay una IP. El
gateway la deriva por saltos de confianza (`resolve_client_ip` con `TRUSTED_PROXIES`, nunca el
`X-Forwarded-For` crudo) y la inyecta con un **par propio**: `X-Real-Ip`, `X-Real-Ip-Ts` y
`X-Public-Ip-Signature`, HMAC-SHA256 con `IDENTITY_SIGNING_SECRET` sobre `("ip-publica", ip, ruta, ts)`.
La firma va ligada a la **ruta** y caduca a los **120 s**. `X-Real-Ip` no está en la lista de cabeceras
reenviables, así que lo que el cliente mande se sobrescribe.

Alternativa descartada (A): reusar `X-Real-Ip` + `X-Identity-Signature` con los demás campos vacíos. Choca
con `IdentidadFirmadaMiddleware` (se dispara por `X-User-Id`) y mezcla la identidad de una persona con la
de un anónimo bajo el mismo secreto de firma.

`orpycamcp_common.identidad` ofrece `firmar_ip_publica(ip, ruta, ts)` e
`ip_publica_verificada(cabeceras, ruta)`. **Sin secreto configurado no se emite ni se verifica** (la IP
queda nula): el fallo seguro es no afirmar. document-service **nunca** lee `X-Forwarded-For`.

**Constancia v2**: `{norma, version_constancia: 2, politica_version, politica_huella, politica_url,
instante, ip, ip_verificada}`. Sin firma válida: `ip = null`, `ip_verificada = false`. **No bloquea la
radicación**: la IP es evidencia complementaria, no requisito de la autorización. Las constancias v1
(`ip_declarada`) se conservan tal cual; no se reescriben.

### 2. La política es de la entidad y la sirve el servidor

Tres parámetros de tenant (migración `012` de tenant-service, sembrados **vacíos**):
`politica_tratamiento_version`, `politica_tratamiento_url` y `politica_tratamiento_texto`. Se editan por
`PUT /config/params/{key}` (administrador nivel 3, con asiento). document-service los sirve en
`GET /api/v1/public/{tenant}/ventanilla/politica` → `{publicada, version, url, texto, huella}`, con
`huella = sha256(version ␞ texto ␞ url)` calculada por el servidor. Alternativa descartada: una ruta
pública nueva en tenant-service (superficie anónima nueva y prefijo nuevo en el gateway) cuando la
familia `/{tenant}/ventanilla/*` ya es pública y ya consulta a tenant.

- **Lectura estricta.** `tenant_client.get_params_estricto` lanza ante cualquier fallo; `get_params`
  devuelve `{}` y eso convertiría un error en «la entidad no tiene política». Fallo ⇒ **503
  `politica_no_verificable`**.
- **Al radicar** (ventanilla y PQRS) el servidor relee la política: sin versión, o con versión pero sin
  URL ni texto ⇒ **409 `politica_no_publicada`** (fail-closed); `payload.politica_version` distinta de la
  vigente ⇒ **409 `politica_desactualizada`** (con `politica_vigente`). La huella y la URL de la constancia
  las pone el servidor, nunca el cliente.
- **Antes de lo irreversible.** La política se valida antes de subir anexos, de reclamar la
  `Idempotency-Key` y de consumir el consecutivo: un 409 no deja rastro. Va **después** del camino de
  reintento idempotente, que devuelve lo ya radicado (la autorización se dio bajo la política de entonces).
- `GET /ventanilla/estado` añade `politica_publicada` (no forma parte de `habilitada`) y
  `scripts/verificar-ventanilla.sh` lo comprueba.

### 3. Supresión frente a retención

La supresión (Ley 1581 art. 8 lit. e y art. 15) **no procede mientras haya deber legal de conservar**
(DUR 1074/2015 art. 2.2.2.25.2.8; Ley 594/2000 art. 11 y la TRD de la serie); procede al vencer la
retención, por la disposición final. Se responde como reclamo, no se ignora. Queda en
`docs/es/normativa.md` («Supresión y retención») y en la plantilla `politica-tratamiento-plantilla.md`
(ancla `#supresion`); la UI lo anuncia bajo la casilla con un enlace a `politica_url#supresion`.

### 4. Acuse de envío: autor y no reutilización

storage-service ofrece `POST /api/v1/storage/internal/files/procedencia` (`X-Internal-Token`)
`{file_id}` → `{sha256, uploaded_by, referencias}`, con `FileRepository.referencias()`. En
`postal_service._verificar_acuse`: fichero inexistente o huella distinta ⇒ **422 `acuse_invalido`**;
`uploaded_by` distinto del actor (o nulo) ⇒ **403 `acuse_ajeno`** (se comprueba **antes** que la huella,
para que el 422 no sea un oráculo sobre ficheros ajenos); referencias no vacías ⇒ **409
`acuse_ya_referenciado`**, salvo el reintento del mismo envío con su propio acuse (idempotente); storage
caído ⇒ 503. La migración `045` de document-service crea un índice único parcial sobre
`postal_shipment(acuse_file_id)` como guarda de motor para la carrera (una `UniqueViolation` ⇒ 409). Como
storage deduplica por autor, volver a subir el mismo PDF da el mismo `file_id` y cae en 409: es la misma
prueba.

## Consecuencias

**Positivas**: la IP de la constancia es verificable (o está declarada como ausente); la entidad responde
de su propio aviso de privacidad y la constancia lo huella; el acuse es de quien lo presenta y de un solo
envío.

**Negativas / límites**:
- Una entidad **sin política publicada no recibe radicación en línea** (409) hasta publicarla. La demo la
  siembra (`scripts/seed_demo.py`).
- La IP solo es verificada si el despliegue declara sus proxies: la red de nginx y la del frontend SSR
  tienen que estar en `TRUSTED_PROXIES`, y `IDENTITY_SIGNING_SECRET` configurado. Si no, la constancia
  dice `ip_verificada=false`.
- Tenant-service caído ⇒ la ventanilla no radica (503): es deliberado, no se asienta una autorización sin
  saber qué aviso regía.
- La migración `045` aborta si ya hay un acuse repetido en dos envíos; se corrigen a mano.
