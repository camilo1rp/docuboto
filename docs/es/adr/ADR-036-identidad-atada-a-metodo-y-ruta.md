# ADR-036: La identidad firmada se ata al método y a la ruta de la petición

**Estado**: Aceptado
**Fecha**: 2026-10-17
**Autores**: Giampiero (mantenedor principal); triage B10, ítem 5
**Relacionados**: [ADR-035](ADR-035-estado-de-la-entidad-cumplimiento-en-el-borde.md) (la identidad caduca a los 120 s), [ADR-010](ADR-010-libreria-compartida.md) (librería compartida), [ADR-031](ADR-031-constancia-autorizacion-ip-firmada-politica-por-entidad.md) (la IP pública ya va atada a la ruta)

---

## Contexto

El gateway firma con HMAC las cabeceras de identidad (`CAMPOS_FIRMADOS`: usuario, entidad, roles, canal, IP, instante) y cada servicio la verifica en `IdentidadFirmadaMiddleware`. Desde B9 la identidad caduca a los 120 s, pero **dentro de esa ventana** un juego de cabeceras capturado en la red interna valía para *cualquier* operación del servicio destino: una identidad emitida para `POST /preservacion/proteger-indice` se podía reproducir contra `POST /preservacion/legal-hold-internal` o contra un `DELETE`.

## Decisión

1. **El método y la ruta canónica entran en la base del HMAC y no viajan.** `base = VERSION_FIRMA ("v2") · METODO · ruta_canonica · valores de CAMPOS_FIRMADOS`, unidos con `\x1e`. No hay cabeceras `X-Identity-Method`/`X-Identity-Path`: el emisor conoce la petición que va a hacer y el verificador toma la suya de su `scope`; con una cabecera, alguien acabaría confiando en ella.
2. **API sin valor por defecto.** `firmar(cabeceras, *, metodo, ruta)` y `verificar(cabeceras, *, metodo, ruta)`: quien olvide atarlos falla al escribirlo. `IdentidadFirmadaMiddleware` verifica con `scope["method"]` y `scope["path"]`; un desajuste responde `401 identidad_no_autentica` (el mismo mensaje que una firma mala).
3. **`ruta_canonica`** es idéntica en los dos extremos: quita la query y el fragmento (no deciden qué operación es) y **decodifica hasta estabilizar** (máx. 8 pasadas; si no estabiliza o no empieza por `/` no es una ruta y se rechaza). Motivo: Starlette decodifica una vez, httpx reenvía la cadena tal cual y uvicorn decodifica otra vez en el destino; con una sola decodificación por lado `/x/%2541` se firmaría como `/x/%41` y se verificaría como `/x/A`.
4. **El gateway** firma `request.method` y `request.scope["path"]`. El proxy reenvía `upstream_base + "/" + path` con ese mismo `path` (no reescribe prefijos), así que la ruta firmada es la que ve el destino. **Si un día el gateway reescribiera prefijos, la firma tendría que moverse a `_handle_proxy` y hacerse con la ruta del upstream.**
5. **Los ocho clientes internos** que propagan la identidad de una persona (archive → signature ×3, archive → storage ×3, document → storage, signature → storage) usan una constante `_RUTA_*` a la vez en `identidad.firmar(..., metodo="POST", ruta=_RUTA_*)` y en `client.post(_RUTA_*, ...)`.
6. **Guardián AST** (`shared/tests/test_la_identidad_propagada_va_firmada.py`): para cada emisor censado exige `metodo=` y `ruta=` en `firmar`, que la ruta firmada sea el mismo nombre/literal que el primer argumento de la llamada HTTP de la función y que el método coincida con el verbo. El del gateway debe firmar `request.method` y `request.scope['path']`.

## Residuos declarados (auditoría B10)

- **La firma no cubre la query ni el cuerpo.** Una identidad capturada para `POST /sign-xml` vale, dentro de su ventana de 120 s, para
  cualquier cuerpo en esa ruta; una de `GET /documents`, para cualquier filtro. Siempre con los permisos de esa misma persona y
  entidad y solo para quien ya está dentro de la red. La ventana corta es la mitigación; atar el cuerpo exigiría hashearlo en el
  gateway en cada petición y se deja fuera a propósito.
- **`ruta_canonica` es con pérdida.** Decodificar hasta estabilizar hace que dos rutas distintas del destino (`/x/a/b` y `/x/a%2Fb`,
  que llega como `%252F`) compartan firma pese a enrutar a operaciones distintas. El alcance es mínimo (exige una identidad viva
  capturada y solo permite la operación «hermana» con un parámetro que lleva `%`, que no es un UUID válido) y rechazar `%2F`
  codificado rompería rutas legítimas con identificadores textuales; se acepta y se declara. Si algún día el gateway firma la ruta
  que ya canonizó `_normalize_path`, el destino podría exigir que no quede `%` residual.
- **Una ruta que no estabiliza no es un 500.** El gateway firma antes de que el proxy normalice la ruta; si `ruta_canonica` la rechaza
  responde el mismo `404 route_not_found` que el proxy (antes: `IdentidadNoAutentica` sin capturar, un traceback por petición).

## Consecuencias

- Una identidad válida reutilizada en otro método u otra ruta da `401 identidad_no_autentica`. La ventana de 120 s sigue existiendo para *la misma* operación (un reintento idéntico): no es un nonce de un solo uso.
- **Corte atómico, sin rama de compatibilidad**: la base del HMAC cambió, así que el api-gateway y los nueve servicios con `IdentidadFirmadaMiddleware` (archive, knowledge, auth, document, signature, notification, workflow, tenant, storage) deben reconstruirse y arrancar **a la vez**; si conviven dos versiones, toda petición autenticada responde 401.
- `firmar_origen`, `firmar_ip_real` y `firmar_ip_publica` son otros mecanismos y no cambian. El mcp-server no verifica identidad: habla con el gateway y este vuelve a firmar.
- Los clientes que llaman a una ruta con la identidad en un `Mount` o con reescritura de prefijo deberán firmar la ruta tal como la verá el destino.
