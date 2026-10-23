# ADR-038: Antirrobots ALTCHA en la ventanilla pública y ficha provisional del remitente anónimo

**Estado**: Aceptado
**Fecha**: 2026-10-20
**Autores**: Giampiero (mantenedor principal); triage 2026-10-09, tanda C-3 (C16 y C17)
**Relacionados**: [ADR-031](ADR-031-constancia-autorizacion-ip-firmada-politica-por-entidad.md) (constancia de la autorización y política), [ADR-035](ADR-035-estado-de-la-entidad-cumplimiento-en-el-borde.md) (estado de la entidad en el borde), [ADR-003](ADR-003-asyncpg-sin-orm.md) (SQL crudo), [ADR-010](ADR-010-libreria-compartida.md) (auditoría inmutable)

---

## Contexto

La ventanilla pública (`POST /api/v1/public/{tenant}/ventanilla` y la ruta heredada `/pqrs`) es la única puerta que crea radicados —con número oficial inmutable (Ley 594/2000)— sin autenticar. Tenía tres defensas: honeypot, límite de tasa en el gateway y la política de tratamiento. Dos defectos quedaban abiertos:

- **C16 — sin antirrobots.** Un script que respete el honeypot y la cuota por IP quema numeración oficial y almacenamiento (los anexos se suben antes del consecutivo). reCAPTCHA contradice la soberanía declarada (no se envía el tráfico de una entidad pública a un tercero).
- **C17 — el remitente se asociaba por número de documento.** `crear_o_recuperar` buscaba la ficha por `(tipo, número)` y devolvía la ajena: quien escribía la cédula de otra persona quedaba atado a SU ficha (con la dirección y el teléfono vigentes de otra persona), sin verificar nada. Era a la vez una suplantación gratuita y un oráculo de enumeración (la respuesta, o el tiempo, delata qué cédulas están en el directorio). Además creaba la ficha **fuera** de la transacción del radicado: una falla posterior la dejaba huérfana.

## Decisión

### C16 — ALTCHA autoalojado, protocolo v1, en la biblioteca estándar

1. **Reto**: `GET /api/v1/public/{tenant}/ventanilla/reto` (document-service; el gateway ya enruta `/api/v1/public/` y lo deja anónimo). **Sin base de datos y sin Redis**: emitir es cálculo. `salt = 32 hex ? expires=<epoch>&t=<slug>&v=1`; `challenge = sha256(salt + número)`; `signature = HMAC-SHA256(ALTCHA_HMAC_KEY, challenge)`. `t` ata el reto a la entidad y `expires` a su vigencia: ambos están dentro de `challenge`, que la firma cubre, así que alargar el vencimiento o cambiar de entidad invalida la firma. Cae en el cubo anónimo **general** del gateway, no en el de radicación (si cayera en el estrecho, pedir el reto gastaría la cuota del POST).
2. **Verificación** dentro del POST, campo `altcha` **obligatorio** en `VentanillaRadicacion` y en `PqrsCreate` (una puerta sin el gate sería el atajo). Un único punto: `VentanillaService.radicar`, para que ventanilla y PQRS no diverjan. Orden, todo fail-closed: forma (base64, JSON, tipos), algoritmo `SHA-256` exacto, `salt` con parámetros `expires,t,v` únicos y `t == entidad`, rango, solución, firma, y **solo después de la firma** el vencimiento. Va **después** del camino de reintento por `Idempotency-Key` (la huella excluye `altcha`) y **antes** de política, anexos y consecutivo: el costo caro solo lo paga quien resolvió un reto vigente y no usado.
3. **Antirreplay en Redis**: `SET altcha:usado:{slug}:{challenge} 1 NX EX max(1, expires − ahora + holgura)`. Redis caído o sin clave → `503 antirrobots_no_disponible` (fail-closed, nombra los canales alternos: presencial y correo). No es un «cierre total no neutral»: no corre plazo alguno sin radicado y el canal alterno existe. Un `409` de política o un `422` de anexo **quema** el reto; el cliente pide otro (cuesta milisegundos).
4. **Códigos**: `422 antirrobots_invalido`, `422 antirrobots_vencido`, `409 antirrobots_usado`, `503 antirrobots_no_disponible`. Ninguno revela nada del radicado ni del directorio.
5. **Configuración**: `ALTCHA_HMAC_KEY` (≥ 32 bytes; el servicio no arranca sin ella; obligatoria `${…:?}` en producción; nunca llega al navegador), `ALTCHA_TTL_SEGUNDOS` (300, 60–900), `ALTCHA_MAXNUMBER` (100000, 1000–1000000), `ALTCHA_SKEW_SEGUNDOS` (60). Clave por despliegue, no por entidad (la entidad va en el `salt`).
6. **Widget fijado en `altcha@2.3.0` exacto** (sin `^`): la 3.x usa otro protocolo (PBKDF2/Argon2, `keyPrefix`) y un `^` lo cambiaría en silencio. `tests/test_altcha.py` fija un **vector generado con el algoritmo del widget 2.3.0** (no con este código): un bump del widget sin cambiar el verificador lo rompe.
7. **Frontend** (otro agente): el widget y su worker salen de `'self'` (sin `worker-src blob:`), el proxy SSR del reto reenvía las cabeceras de IP, el payload viaja **dentro de `datos`** (no en cabecera: no hay listas de cabeceras del gateway que tocar).

### C17 — Ficha provisional siempre que un anónimo aporta documento

1. Con `identificacion`, `VentanillaService` **no consulta el directorio**: pasa `remitente_provisional` a `DocumentService.create` (argumento interno, **no** campo de `DocumentCreate`: un cliente autenticado no debe poder pedir fichas provisionales), que crea la ficha **dentro de la transacción del radicado** y la ata como remitente principal. Si el radicado falla no queda ficha huérfana. Un solo camino de código, mismo tiempo y misma respuesta exista o no el documento.
2. **Modelo** (migración tenant `049`): `tercero.identidad` (`confirmada`|`provisional`), `documento_declarado_tipo/numero` (**inmutables** por trigger: lo declarado es evidencia), `conciliacion` (`pendiente`|`vinculada`|`confirmada`|`descartada`), `conciliada_at`, `conciliada_por` (**`auth_users.id`**, nunca el `sub` de Keycloak), `conciliacion_motivo` (≥ 10 caracteres). Una provisional **no** lleva `tipo/numero_documento` (CHECK): no ocupa el documento ni choca con `ux_tercero_documento`. Los estados terminales no se rehacen (trigger). Las filas existentes son `confirmada` por `DEFAULT`: sin backfill ni rama «legado».
3. **Se excluyen** del directorio: `buscar`, `buscar_por_email` (ingesta), `grupos_duplicados`; `get_por_documento` ya no las ve (número `NULL`). `vincular_actores` desde el radicador interno → `422 tercero_provisional`; `fusionar` → `409 tercero_provisional` (la fusión no deja el motivo ni el asiento de la conciliación y el hueco `doc_sup and doc_abs` la dejaría pasar siempre).
4. **Cola y acto**: `GET /terceros/por-conciliar` y `POST /terceros/{id}/conciliar` (`vincular` | `confirmar` | `descartar`; `medio_verificacion` obligatorio en las dos primeras), `PERM_RADI` con `min_crud=3` también en la lectura (datos personales no verificados). **Clearance no-read-up en el `WHERE`** de las filas, del conteo y de cada `UPDATE` (una provisional atada a un radicado por encima del clearance del llamante no aparece ni cuenta); `404` indistinguible para inexistente/invisible. `vincular` reutiliza `fusionado_en` (el radicado y su `radicado_actor` no se tocan —el trigger de la 026 lo impide—; la ficha existente hereda el historial al leer); `motivo` obligatorio si el documento del destino no coincide; `confirmar` copia el documento declarado a la identidad (`409 documento_ya_registrado` si ya es de otra: vincular); `descartar` exige motivo. Asiento `document.tercero_conciliado` atómico, solo con identificadores y sin el número de documento (el `audit_log` es inmutable y el dato personal ya vive en la ficha).
5. La identidad del acto sale de `X-User-Id` **firmado por el gateway**: el gateway reconstruye esa cabecera desde la validación de auth-service (`auth_users.id`) y no reenvía la del cliente (lista de admisión `FORWARDED_REQUEST_HEADERS`), así que lo que el proxy del frontend mande (`locals.user.sub`) no llega al servicio.

## Correcciones tras las auditorías de seguridad y archivística de C-3

1. **La cadena de fusiones se lee entera.** `radicados_de` resolvía `fusionado_en` en un solo nivel: provisional → D (vinculada) y luego D → X dejaba los radicados de la provisional fuera de X. Ahora usa `WITH RECURSIVE … CYCLE` sobre `fusionado_en`, con el gate de nivel en el mismo `WHERE`; lo mismo hace el conteo de vínculos de `fusionar` y el `DOC_R` de la combinación de correspondencia (que además sigue la cadena hasta la superviviente: tras `vincular`, el documento verificado es el de la ficha destino; una provisional sin vincular deja `DOC_R` vacío).
2. **Conciliar y fusionar se bloquean en el mismo orden.** `conciliar(vincular)` bloquea la provisional y el destino (`FOR UPDATE`, en orden de id) antes de validar, y el `UPDATE` repite el `EXISTS` del destino (confirmado, vivo, sin fusionar); `fusionar` bloquea sus dos fichas en el mismo orden antes de validar. Una fusión concurrente espera; no hay deadlock por orden único. Si el `UPDATE` no afecta filas se relee con el clearance: ya no visible es `404`, no `409`.
3. **Medio de verificación (evidencia archivística).** `vincular` y `confirmar` exigen `medio_verificacion` ∈ {`presencial_documento`, `respuesta_canal_registrado`, `llamada_telefono_registrado`, `otro`} (con `otro`, `motivo` obligatorio), guardado en `tercero.conciliacion_medio` (CHECK en la 049) y en el asiento. Sin él el acto decía quién decidió, no en qué se basó.
4. **El asiento solo lleva identificadores**: `{accion, destino_id, documento_coincide, radicados, motivo, medio_verificacion}`; ni el documento ni nombres (el `audit_log` es inmutable; el dato personal vive, y se rectifica, en la ficha).
5. **El canal alterno tiene datos.** `GET /ventanilla/estado` expone `contacto` (`entidad_nombre|direccion|telefono|email|web` de `sgd_param_admin`, el membrete de los oficios) y `antirrobots_disponible` (clave ≥ 32 bytes y `PING` a Redis); el `503 antirrobots_no_disponible` repite el contacto en su `detail`. **Hueco declarado: no existe un parámetro de horario de atención**; no se inventa y queda para una tanda de configuración de la entidad. `scripts/verificar-ventanilla.sh` falla si `antirrobots_disponible` es `false`.
6. **`ALTCHA_HMAC_KEY` mínima** es una constante del módulo (`HMAC_MIN_BYTES`), no un ajuste por entorno.
7. La migración `049` se **enmendó en su sitio** (no estaba commiteada, todo es dato de prueba): es idempotente (`ADD COLUMN IF NOT EXISTS`, `DROP CONSTRAINT IF EXISTS`), así que en dev se borró su fila del libro (`public.schema_migrations`, `tenant_demo`) y se reaplicó con el runner.

## Deuda declarada adicional

- **Disposición final del radicado**: el único `DELETE FROM radicados` del servicio arrastra `radicado_actor` por `ON DELETE CASCADE`, pero **nadie purga `tercero`**: la provisional atada solo a ese radicado sobrevive con su nombre, correo, teléfono, dirección y documento declarado (y, si seguía `pendiente`, reaparece en la cola con `radicados: []`). La disposición final NO la alcanza. Corrección propuesta (no implementada): al eliminar un radicado, borrar —con asiento— las provisionales `pendiente`/`descartada` que quedaron sin vínculos, y anonimizar las `vinculada`.
- **E19 (`resolver_por_email`)**: corrección propuesta para la ingesta de correo, mismo criterio que C17: asociar por la dirección **solo cuando el mensaje trae DKIM o SPF en `pass` alineado con el dominio del `From`**; si no hay autenticación verificable, el remitente entra como ficha **provisional** y pasa por la misma cola de conciliación. Hoy asocia a la ficha existente con solo el `From`, que es trivialmente falsificable.

## Alternativas descartadas

- **Protocolo v2 (PBKDF2) / widget 3.x** y **librería Python `altcha`**: el primero cambia widget y verificador a la vez; la segunda añade una dependencia (lockfile, mantenimiento) para ~100 líneas de `hashlib`/`hmac`. El verificador queda aislado en `app/core/altcha.py` para sustituirlo.
- **Antirreplay en una tabla del tenant** consumida dentro de la transacción del radicado: atómica y sin quemar retos en fallos, pero consumir **después** de subir anexos deja que un mismo reto dispare N subidas en paralelo antes del commit. Se queda Redis, que se consulta antes.
- **Ficha provisional solo si el número ya existe**: abre un oráculo de tiempo (rama distinta) y permite a un anónimo «reservar» la cédula de otro creando primero la ficha definitiva con datos falsos.

## Residuos declarados

- **Carga interna**: toda petición identificada deja una provisional (1:1 con radicados identificados), no una por persona. La cola se ordena por antigüedad y trae la ficha `candidata`; **agrupar por `documento_declarado_numero` y conciliar por lote** queda como mejora de la pantalla.
- **`resolver_por_email` (E19)**: la ingesta de correo asocia por dirección, mismo vector que C17 por otro canal. **No entra en C-3** y queda declarado como deuda.
- **Protocolo v2 (PBKDF2)** cuando se suba el widget a 3.x (roadmap).
- Una provisional con `documento_declarado_tipo` nulo no se puede **confirmar** (`422 documento_sin_tipo`) ni tiene `candidata`: se vincula o se descarta. El formulario no exige el tipo cuando hay número; endurecerlo es decisión de producto.
- La plantilla de respuesta imprime `DOC_R` desde `tercero.numero_documento`: de una provisional sale vacío (lo declarado no se imprime como si estuviera verificado).
- Un reto vale ~5 minutos y un solo uso, pero un robot que resuelva retos sigue pudiendo radicar: ALTCHA sube el costo, no lo hace imposible; el límite de tasa y el honeypot siguen siendo parte de la defensa.

## Consecuencias

- El ciudadano no resuelve nada visible (WCAG 2.2 SC 3.3.8): su navegador hace una comprobación de unos segundos.
- Producción exige `ALTCHA_HMAC_KEY`; sin Redis la ventanilla pública responde 503 (la radicación presencial y por correo siguen).
- Todo script que radique por la puerta pública resuelve el reto con `scripts/lib/altcha_solver.py` (stdlib). Ningún script del repositorio lo hacía al momento de este ADR: `seed_demo.py` y `seed_corpus.py` usan la API autenticada; `scripts/verificar-ventanilla.sh` comprueba que el reto se emita.
