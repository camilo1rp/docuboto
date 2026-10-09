# Auditoría de Seguridad

Esta página registra los hallazgos de la auditoría semanal de dependencias (job `dependency-audit` en CI).

## Estado actual

**No hay una ejecución de la auditoría de dependencias registrada en esta página.** La frase «sin
vulnerabilidades altas o críticas» que figuraba aquí no la respaldaba ningún artefacto: el job
`dependency-audit` era fail-open (`pip-audit ... || true`, un informe ausente se contaba como «0
paquetes», y la decisión de fallar la tomaba un modelo de lenguaje). Desde 2026-10-03 la
auditoría es determinista y fail-closed (`scripts/audit_umbral.py`: un informe ausente o corrupto
es un fallo, y toda vulnerabilidad con corrección disponible también), cubre cada
`requirements*.lock`, corre en el programado semanal y en cada merge request, y se suma `npm audit
--omit=dev --audit-level=high` sobre el frontend en cada pipeline. Las imágenes se escanean con
Trivy antes de publicarse. **La fecha y el pipeline de la última ejecución limpia los escribe el
propio job al terminar (`audit.json` queda como artefacto); hasta la primera ejecución con este
cambio, el estado es «sin ejecución registrada».**

> Esta página se actualiza vía Merge Request desde el job programado: redacta el informe a partir de `audit.json`, pero **no decide** si el job falla (lo decide el umbral).

## Auditoría de segunda pasada (septiembre de 2026)

Además de la revisión automática de dependencias, en septiembre de 2026 se hizo una
auditoría manual de segunda pasada sobre catorce ejes que las revisiones anteriores no
cubrían. Las doce auditorías previas se habían concentrado en el control de acceso por
endpoint y en la integridad del registro de auditoría; esta miró el perímetro, la
confianza entre servicios, el aislamiento a nivel de datos, la superficie pública, las
subidas de archivos, la criptografía, las salidas de red, el frontend, los analizadores
de documentos, la mensajería interna, el asistente, la infraestructura, los registros y
la lógica de negocio.

### Método

Cada hallazgo pasó por revisores independientes cuyo cometido era **refutarlo**, y solo
se registra el que sobrevivió a esa revisión. Cada corrección se valida restaurando el
defecto y comprobando que la prueba correspondiente falla: una prueba que no distingue
el código correcto del defectuoso no cubre nada.

### Resultado

Se confirmaron 81 hallazgos: 6 de severidad alta, 30 media y 45 baja. Se refutaron 42.
Todos pasaron por revisión adversarial; ninguno queda sin verificar. **Los 81 están
corregidos**: los seis de severidad alta, los treinta medios y los cuarenta y cinco bajos.

Las áreas con más hallazgos fueron la infraestructura de despliegue, la superficie
pública sin autenticar y la lógica de negocio del circuito de firma. Cuatro hallazgos
afectan solo al entorno de desarrollo y no a la configuración de producción.

### Cambio de comportamiento en la ventanilla ciudadana

La ruta heredada de radicación de PQRS pasa ahora por la ventanilla única, como su
propio contrato ya declaraba. Eso la sujeta a las decisiones de la entidad: el tipo
documental debe estar declarado admisible y debe existir una dependencia que reciba lo
presentado desde fuera.

**Antes de actualizar**, cada entidad debe verificar que tiene declarado al menos un
tipo documental admisible en ventanilla y una dependencia receptora. Sin ellos, la ruta
responderá «ventanilla no habilitada» en lugar de admitir la radicación. Es el
comportamiento correcto —hasta ahora esa puerta admitía documentos en entidades que no
habían habilitado la radicación en línea, dejándolos fuera de la bandeja de toda
dependencia— pero es un cambio visible.

### Cierre de la auditoría del 2 de octubre de 2026 (tanda T1)

El sello institucional de la entidad y el bloqueo de inmutabilidad (WORM) dejaron de ser
alcanzables con la sesión de un usuario: ahora solo los aplica el servicio de archivo, con
credencial interna. Antes, cualquier usuario con permiso de firma podía estampar el sello de
la entidad sobre un documento arbitrario, y uno con permiso de expedientes podía ocupar el
bloqueo irreversible de un expediente ajeno o fijar una retención por litigio sin acto que
la levantara. La retención por litigio ahora se declara y se levanta por un acto propio, con
motivo obligatorio y asiento de auditoría.

### Cierre de la auditoría del 2 de octubre de 2026 (tanda T2)

Administrar usuarios, grupos y permisos ya no se puede hacer con el permiso de solo lectura:
quien lo recibía únicamente para consultar la auditoría podía crear usuarios y concederse
permisos. Tampoco se puede entrar en un grupo con más nivel de acceso a documentos reservados
que el propio. El token de acceso se valida con más rigor (tipo y cliente emisor), la caché de
validación del gateway quedó sellada e indica cuánto le queda al token, la verificación de la
cadena de auditoría deja de revelar a un perfil sin habilitación cuántos asientos clasificados
existen, y quitar el segundo factor a otra persona queda siempre asentado en la misma operación.

### Cierre de la auditoría del 2 de octubre de 2026 (tanda T3)

Un usuario con permiso de radicación en modo consulta ya no puede ver el asunto de un radicado
reservado por el listado de radicados de un tercero ni por el de respuestas, y esos asuntos
dejan de viajar al proveedor externo del asistente. Los borradores de respuesta heredan la
clasificación del radicado que responden: no se listan, leen ni editan sin el nivel
suficiente, y editarlos deja asiento. La firma declarativa de un anexo exige nivel de edición,
toma la huella del servidor (no del cliente) y deja asiento en la misma operación.

### Cierre de la auditoría del 2 de octubre de 2026 (tanda T4)

Un radicado que fallaba a mitad de la radicación ya no gasta un número de la serie oficial.
Un paquete de importación ya no puede mover el consecutivo con números de otra entidad ni
desbordarlo (que dejaba sin radicar todo un año). Despachar un envío o cambiar su estado a mano
exige nivel de edición completo, deja asiento con la persona responsable y no pisa un estado
final si el operador postal se adelantó. Importar y exportar paquetes exigen el mismo nivel,
no congelan el servicio y se ejecutan de uno en uno.

### Cierre de la auditoría del 2 de octubre de 2026 (tanda T5)

Un webhook ya no se lleva la causal ni el fundamento jurídico de una reserva, ni el número y la
ruta de dependencias de lo reservado: lo que no declara su clasificación sale mínimo. Abrir o
cerrar un canal de salida exige nivel de edición completo y deja asiento con la persona
responsable; la entrega se fija a la dirección que se validó. La cadena de vistos buenos y la
hoja de ruta de un radicado ya eliminado por retención respetan el nivel de acceso de quien
consulta.

### Cierre de la auditoría del 2 de octubre de 2026 (tanda T6)

Una sola persona ya no puede proponer, avalar y ejecutar la destrucción de un lote: avalar y
ejecutar piden nivel 4 y exigen personas distintas (quien propone no avala, quien avala no
ejecuta), comprobado en la propia sentencia y antes del primer borrado. Las transiciones del
lote son atómicas con su asiento, no se ejecuta sin el candado de retención del acta, y la
puerta pública de un lote ya no revela qué entidades aloja la instalación. El índice
electrónico que se firma deja de sellar lo que escribe quien vincula: número de radicado,
tipología, dependencia productora, formato, tamaño y huella los resuelve el servidor y se
rechaza lo declarado que no coincida. Quién prestó o devolvió una unidad física queda por
identificador, no por login. **Pendiente**: el préstamo físico sin control de clasificación
(AREA-01-13) y propagar al índice un retipificado posterior.

### Cierre de la auditoría del 2 de octubre de 2026 (tanda T8)

El segundo factor y la credencial ya no se comprueban contra otra persona que la firmante: el OTP salía
al correo del dueño del bearer, el TOTP se verificaba contra su dispositivo y la clave personal era la
suya, mientras la firma se atribuía a `X-User-Id`. El comentario lo llamaba «fail-safe» y era al revés
(alcanzable desde un servicio interno comprometido, no desde fuera). Ahora auth-service dice **de quién**
es cada factor y signature-service exige que coincida (`403 identidad_incoherente`, con asiento y antes de
pedir el factor). Quien ya tiene TOTP no baja al correo en actos de nivel ≥ 2. La huella de radicado y
expediente no la elige el cliente, y el firmante tampoco elige los bytes al firmar donde la cadena no fija
huella. El HMAC de la firma nativa y el del OTP usan claves derivadas por entidad y propósito (HKDF) con un
mensaje que ata entidad y objeto, y `key_version` conserva la verificación de lo anterior. `/verify` y
`GET /firmas/{id}` aplican el no-read-up. **Pendiente (decisión de la entidad o externo)**: activar
`FIRMA_PERSONAL_REQUIRE_XADES` (lo clasificado aún puede firmarse con HMAC nativo si el gate sigue en `off`;
ahora hay aviso al arrancar y asiento si el canal de credenciales cae), el vocabulario de motivos de
revocación y el HSM/KMS.

### Cierre de la auditoría del 2 de octubre de 2026 (tanda T9)

La cola de eventos fallidos (`evento_dead_letter`) es **una sola tabla física** que escriben workflow,
signature, notification y knowledge en el mismo esquema de la entidad, y su clave primaria era solo
`event_id`. Cuando un mismo evento agotaba los reintentos en dos consumidores (caída de la base, entidad
sin tablas, sobre malformado), el segundo `INSERT … ON CONFLICT DO NOTHING` no hacía nada: ese grupo no dejaba
fila ni asiento `evento.dead_letter`, su administrador no lo veía (el listado filtra por grupo) y el
mensaje ya estaba confirmado en Redis. Se perdía la trazabilidad (Ley 594/2000, arts. 4 y 19) sin error
alguno; no es explotable con un token y no rompe el aislamiento, por eso se calificó media. Ahora la clave es
`(origin_group, event_id)` (migración idempotente en los cuatro servicios, aplicable en cualquier orden) y un
guardián de `shared/tests` rechaza cualquier `ON CONFLICT` sobre esa tabla que no incluya el grupo.
knowledge-service no tenía forma de inspeccionar ni reprocesar su cola: un `purged`/`annulled` aparcado
dejaba el fragmento buscable con su texto hasta siempre. Ahora tiene la misma superficie que los demás
(`PERM_DLQ_ADMIN`, escritura a nivel 3, no-read-up sobre el nivel vivo, asiento atómico). También: el nombre
del consumidor es por réplica (antes fijo), el dead-letter huérfano guarda la causa del último fallo (antes
vacía), `X-Truncated` ya no salta con exactamente 1000 filas y `/health/ready` muestra la DLQ y el último
barrido. **Pendiente (decisión de operación)**: Prometheus/alertas; llevar el clearance de la lista al `WHERE`
y paginación por cursor en el listado (con más de 1000 pendientes los más antiguos siguen fuera de la ventana).

### Cierre de la auditoría del 2 de octubre de 2026 (tanda T10)

Lo que el asistente confirma en nombre de una persona entraba al gateway por la ruta de un dominio y se
asentaba como canal `api`, igual que una llamada directa: no se podía distinguir lo que propuso un modelo
ni atar el asiento a su turno. Ahora cada llamada del mcp-server lleva una marca firmada (ligada a la entidad
y al token, 120 s de vida) que el gateway verifica antes de asentar `mcp`; sin firma válida el canal es `api`,
y el asiento de canal `mcp` guarda el `request_id` y el `jti` de la propuesta que la persona aprobó. El
asistente además marca como «no verificado» todo número de radicado de su respuesta que ningún resultado de
consulta devolvió (antes «no inventes números» era solo una línea del prompt), reenvía la IP real firmada (sus
llamadas ya no comparten el cubo por IP de un único contenedor entre todas las entidades) y mide cada turno.
En la capa de conocimiento, `ollama_local` ya no se acepta como local por su nombre: con una `OLLAMA_URL`
pública el RAG dejaba salir chunks reservados y clasificados y solo avisaba en el log; ahora los excluye. Las
sugerencias del RAG tienen aceptación humana (solo quien la generó, atómica con su asiento) y se retira la
promesa falsa de «opt-in por entidad». **Verificado que no aplica**: la reclasificación de un expediente no
existe (su nivel no se puede cambiar tras crearlo), así que no hay nada que propagar al índice; un guardián lo
deja falsable. **Pendiente (decisión de producto)**: habilitar la capa de conocimiento por entidad, cuyo
default `false` apagaría la ingesta actual. **Residual**: el canal `mcp` solo aparece en los servicios ya
reconstruidos con la librería compartida nueva.

### Cierre de la auditoría del 2 de octubre de 2026 (tanda T11)

La guarda de sesión de los proxies del frontend se probaba en una muestra elegida a mano (21 de 219) y los
flags de la cookie del token (`httpOnly`, `sameSite`, `secure`) no los afirmaba ningún test: bastaba cambiar
uno para que el token pasara a ser legible por JavaScript o viajara entre sitios sin que nada lo notara.
Ahora un guardián recorre todos los `+server.js` de la zona privada (290 verbos), los invoca sin sesión con
el body, la red y las cookies bloqueados y exige 401; y un test fija los flags en producción y en desarrollo.
Forzar la URL de un área de `/admin` sin su permiso termina en `/403` (el permiso exigido es el real de cada
área, y un área sin declarar queda cerrada); es **espejo de interfaz**, el backend sigue re-validando cada
llamada. **Sin escalada previa**: un proxy sin guarda reenviaba sin token y el gateway respondía 401.
**Residual**: los e2e nuevos (perfil restringido y axe) están escritos y no corren aún en CI.

### Cierre de la auditoría del 2 de octubre de 2026 (tanda T12, archive y storage)

- **La deduplicación de anexos era un oráculo de existencia (AREA-08-09, media).** Subir un contenido que ya
  existía como anexo de otro usuario devolvía `200` con la fila ajena —su `id` y su `filename` original, que en un
  anexo reservado suele revelar el asunto—; quien adivinara una plantilla rellenada confirmaba que existía. Ahora la
  deduplicación es solo del **mismo autor** (en la consulta y repetida en el servicio): con otro autor se crea una
  fila propia (`201`), y sin autor (ventanilla pública, sistema) no se deduplica. La purga de huérfanos conserva su
  protección (`dedup_hit_at`) para el caso del mismo autor.
- **La réplica del AIP atestiguaba `ok` sobre una copia que nadie miró y filtraba el texto del almacén
  (AREA-08-04, media).** Ahora se relee la retención y el SHA-256 de lo copiado, un secundario igual al primario
  se etiqueta `local`, y el motivo que llega al evento PREMIS y al `audit_log` es solo el tipo de la excepción
  (+ código S3), no `str(exc)` (host, bucket, request-id).
- **Inmutabilidad del acta sin capa de privilegios (AREA-07-08, baja).** Desde que el rol de la app no es dueño
  no puede desactivar el trigger, pero conservaba `UPDATE`/`DELETE` sobre el acta y las firmas personales: la
  única barrera era el trigger. La migración `054` los retira (misma guarda de existencia de rol que la `008`);
  probado con el rol real `orpycamcp_app`: falla por privilegios, no por el trigger.
- **Firmas personales del acta sin Object-Lock (AREA-07-10, media; solo la parte técnica).** El XML de las firmas
  de quien elaboró, entregó y recibió vivía en el bucket de documentos, sin retención. Entra al almacén WORM
  (post-commit, best-effort, con reconciliación). **Cerrado el 2026-10-04**: el acto `firma_inobtenible` (`POST /acta/firma-inobtenible`, `USUA_PERM_ADMIN` ≥ 3, constancia inmutable `059` + asiento atómico, estado `firmada_con_excepcion`) y la renovación CT de estas firmas (`060`). **Remediación 2026-10-09 (ADR-030, `061`)**: la constancia no estaba firmada ni protegida y su soporte era texto libre (alto); ahora es XML canónico con sello XAdES-T y firma personal del declarante según la política de la entidad, Object-Lock con renovación CT y soporte resuelto por el servidor; el declarante no puede ser ninguna de las tres partes (servicio y motor); la excepción es revocable (fila append-only); el `fundamento` solo sale a `USUA_PERM_ADMIN` ≥ 3; y `sellada_con_excepcion` evita llamar «firmada» a un acta sin firmas personales.
- **Fondos acumulados sin WORM (AREA-06-04, media).** Su índice, acta y AIP quedaban sin Object-Lock de forma
  indefinida (fail-closed correcto, pero permanente). Ahora se cablean con la fecha extrema final y un clamp
  (`hoy + 400 días` como mínimo). **Riesgo a confirmar**: un COMPLIANCE irreversible de ~400 días sobre documentación
  cuyo calendario ya venció (la D4.4 original decía «no fijar retención»); lo decide el usuario.
- **Lo que no es seguridad pero cierra evidencia**: el índice no se sella si no valida contra su XSD; una
  transferencia no se prepara con el índice sin firmar y su acta señala la firma que ampara el índice entregado.
- **Residual**: el `CHECK` de `objeto` (055) exime a las filas anteriores a su fecha (un `NOT VALID` a secas
  habría dejado sin poder avanzar a las transferencias en curso). El aviso por correo del índice agotado no tiene
  consumidor propio en notification-service.

### Cierre de la auditoría del 2 de octubre de 2026 (tanda T13, document-service)

- **Un clasificado podía quedar sin motivo y la promesa de «nunca NULL» no tenía respaldo (AREA-09-04, media).**
  Los caminos de escritura del API ya exigían el fundamento, pero la importación admitía un nivel reservado con
  `fundamento` nulo y la base no lo impedía. Ahora la importación rechaza todo el paquete y una restricción de la
  base (validada, con relleno explícito de lo heredado) cierra cualquier otra entrada; el índice del art. 20
  muestra además el área responsable y quién radicó.
- **El cambio de nivel de un radicado se publicaba después del commit y se perdía en silencio (AREA-09-05, media).**
  Si Redis caía justo ahí, el nivel quedaba subido y el índice semántico seguía sirviendo el fragmento al nivel
  anterior. Ahora el evento se escribe en la **misma transacción** que el cambio y su asiento (outbox) y un relé lo
  reenvía; lo mismo para el alta y la anulación. **Pendiente (decisión de producto)**: el doble control para
  desclasificar. **Residual**: aún no hay un barrido de reconciliación nivel-del-fragmento en knowledge.
- **Publicar de oficio difundía el primer anexo y no distinguía lo recibido de fuera (AREA-07-12, media, rebajada de
  alta: exigía competencia de clasificación).** CMIS solo sirve ahora anexos marcados por el acto de publicación, y
  no se publica de oficio lo que entró por la ventanilla pública ni lo tipificado como admitido en ella (datos de
  terceros, Ley 1581).
- **Radicación pública sin freno de automatización ni reintento seguro (AREA-02-07, media).** Un campo trampa rechaza
  a los programas que rellenan todo el formulario antes de consumir número, y una clave de idempotencia hace que un
  reintento (o un doble clic) devuelva el mismo radicado con un solo consecutivo, también bajo concurrencia.
  **Pendiente (decisión de usuario)**: el proveedor de CAPTCHA (reCAPTCHA contradice la soberanía declarada).
- **Sin constancia de la autorización de tratamiento de datos (AREA-09-08, media).** La ventanilla y la PQRS exigen
  ahora la autorización y guardan versión de la política, instante e IP **declarada** (no verificada) en el radicado
  y en su asiento. **Residual**: la IP verificada por saltos de confianza requiere que el gateway la inyecte en las
  rutas públicas; el texto de la política lo aporta la entidad.
- **Secreto del webhook postal en claro, único y sin rotación (AREA-09-10, media).** Ahora hay dos ranuras cifradas,
  con rotación sin corte (la anterior caduca) y un endpoint administrativo que no devuelve ni asienta el secreto;
  entregar a mano un envío exige el acuse de recibo. **Residual**: la alerta por devolución en notification no se
  construyó.
- **Datos mínimos del radicado (AREA-09-02, media) y ventanilla sin verificación previa (AREA-09-07, baja):** ver el
  registro de cambios (`T13`).

### Cierre de la auditoría del 2 de octubre de 2026 (tanda T16, documentación)

Sin cambio de comportamiento de seguridad, y por eso importa: lo que se corrigió es lo que **la
documentación afirmaba** y el código ya contradecía. Dos recetas inseguras dejaron de estar
escritas (un `SET search_path` con el slug interpolado sin validar ni entrecomillar, en
`multi-tenancy.md` y ADR-002: quien copiara el ejemplo abría una inyección en el `search_path`), el
alta de entidades se documenta por su única vía real (el gateway no expone `/api/v1/tenants`), y
los hallazgos de escrituras sin gate y de limitador por IP se cierran con su evidencia.
**Se dice la verdad sobre los webhooks (AREA-14-10)**: el endurecimiento SSRF está implementado
(validación al crear y en cada entrega, TOCTOU de DNS cerrado), pero **las entregas no se registran
de forma durable** —solo `logger.warning`—; un administrador que suscriba una URL externa sin
`event_types` hace salir todo el contenido no clasificado y hoy no queda constancia más que en
logs rotables. Queda pendiente la tabla `webhook_entregas` (append-only). Residuales aceptados del
limitador: los tokens inválidos no se cachean (una IP puede forzar hasta 1200 validaciones por
minuto contra auth-service) y el limitador falla abierto sin Redis. Un guardián
(`shared/tests/test_la_documentacion_dice_lo_que_hay.py`) ata cada una de estas frases al hecho
del código que la haría falsa.

### Secreto de los webhooks cifrado en reposo

La clave con la que se firma cada entrega de webhook ya no se guarda en claro: quien lea la base de
datos o un respaldo no puede forjar entregas que el receptor aceptaría. Va cifrada con una clave de
custodia propia del servicio (fuera de la base, versionada y rotable), atada al tenant y al
webhook; el servicio la descifra solo en memoria para firmar y deja de entregar cualquier fila que
aún esté en claro o que no pueda descifrar. Nunca se devuelve en una respuesta ni se escribe en
registros o asientos de auditoría.

### Qué no cubre este resumen

Por su naturaleza, esta página no detalla los vectores concretos. El informe completo,
con la ubicación y el ataque de cada hallazgo, es material interno y no se publica.

<!-- TODO: translate to English -->

### tenant-service y el rol propietario (octubre de 2026, T7)

La migración `008` de auth-service eximía a tenant-service del rol de aplicación porque «no estaba enrutado por el gateway»; sí lo está (`/dependencias`, `/catalogos`, `/config`, `/business-days`, `/pinar`). Con el rol propietario, el solo-anexar de `audit_log` no lo frenaba. Corregido: el servicio opera con `DB_APP_USER`, `POST /tenants` no ejecuta DDL y el alta y el cambio de estado dejan asiento atómico. Un guardián (`shared/tests/test_servicio_enrutado_no_conecta_como_propietario.py`) exige el rol de aplicación en ambos compose para todo servicio que el gateway enrute.
