# Plan de backend — brechas de operación de Silvania

> **Spanish only.** This page has no English translation: MkDocs falls back to the
> Spanish version inside the English site (`fallback_to_default: true`). It is an internal
> planning document, not reference documentation.

10 de noviembre de 2026

**Qué es este documento.** El plan de los cambios de backend que pide la revisión
«Escenarios de operación en la Alcaldía de Silvania» (68 escenarios, 10 de noviembre de
2026) y el lugar de cada uno en el orden de ejecución del rediseño (ADR-047 a ADR-056).
El frontend queda fuera: se revisa aparte.

**Base.** Código de la rama `feat/radicar-ingest-entry` @ `cda9a956`: el proceso único, el
esquema de base generado y el outbox `trabajo_fuera` ya están. El siguiente paso del
rediseño es el 4 (ADR-048, identidad propia).

**Convención.** Las tablas, rutas, eventos y permisos nuevos van en inglés. No hay
instalaciones con datos: lo que se sustituye se borra, sin capa de compatibilidad.

---

## 1. Dónde encaja cada cambio

| Orden | Paso del rediseño | Cambio de backend | Escenarios |
|---|---|---|---|
| 1 | Paso 4, ADR-048 (identidad) | Recuperación de contraseña, restablecimiento por el administrador, cambio obligatorio, vigencia de la cuenta | E1, E6, E7 |
| 2 | Mismo paso, requisito previo | Correo saliente autenticado | E1, C1 |
| 3 | Nuevo, justo después del paso 4 | Envío de la respuesta por correo | C1 |
| 4 | Nuevo, independiente de los pasos 5 a 8 | Suspensión del término y desistimiento tácito | A9 |
| 5 | Junto con A9 | Fecha de inicio según el horario de atención | A6 |
| 6 | Paso 9, ADR-055 (conformidad) | Informe de oportunidad | B13 |
| 7 | Después del paso 9 | Plazos en horas, respuesta a varias entradas, delegación de firma | A11, B7, B5 |

C1 y A9 no dependen de los pasos 5 a 8 (eventos en PostgreSQL, bytes en disco, proxy BFF,
librerías). Si la fecha de Silvania aprieta, los dos pueden ir antes.

---

## 2. Identidad (dentro de ADR-048)

`auth_users` ya tiene `failed_logins` y `locked_until`, que hoy no escribe nadie.

**Esquema**

- Columnas en `auth_users`: `password_hash`, `must_change_password`, `expires_at`.
- Tabla `password_reset_tokens`: `user_id`, `token_hash`, `expires_at`, `used_at`. Un solo
  uso, 15 minutos.

**Rutas**

| Ruta | Qué hace |
|---|---|
| `POST /auth/password/forgot` | Responde siempre 202, para no revelar qué cuentas existen. |
| `POST /auth/password/reset` | Consume el token. |
| `POST /auth/password/change` | Cambio por el propio usuario con sesión. |
| `POST /users/{id}/password-reset` | Contraseña temporal y `must_change_password`. |
| `POST /users/{id}/unlock` | Limpia el bloqueo. |

**Cumplimiento.** `UsuarioActualMiddleware` ya resuelve el usuario en cada request. Se
añaden `expires_at` y `must_change_password` a esa única consulta: un contratista vencido
deja de entrar en el siguiente request, sin trabajo nocturno.

**Auditoría.** `auth.password_reset_requested`, `auth.password_reset_by_admin`,
`auth.user_unlocked`, `auth.user_expired`.

---

## 3. Correo saliente

`notification/core/email_client.py` envía texto plano sin autenticación, sin TLS y sin
adjuntos. La constancia de `document/services/acuse_correo.py` pasa por ahí, así que sale
de `noreply` y no del buzón institucional.

- Se sustituye por un único `send_mail()` con STARTTLS, autenticación por contraseña o
  XOAUTH2, y adjuntos.
- `email_cuenta` gana `smtp_host` y `smtp_port`. El alcance OAuth de Google que ya se pide
  cubre SMTP (`oauth_correo.py:49`).
- El correo del sistema (recuperación, avisos a funcionarios) usa el SMTP del entorno,
  ahora con autenticación. El correo al ciudadano usa una `email_cuenta`.

**Protocolo.** Envío SMTP por el puerto 587 con STARTTLS y XOAUTH2, con la identidad del
buzón institucional. La autenticación por contraseña queda solo para servidores de correo
que no son de Google ni de Microsoft, como el correo de un proveedor de alojamiento.

- **La misma credencial de la ingesta.** El lado IMAP ya se autentica con XOAUTH2 y el
  envío reutiliza ese token de refresco. Con Google, el alcance que ya se pide cubre SMTP.
  Con Microsoft hay que añadir el alcance `SMTP.Send` junto al de IMAP. Falta comprobar si
  el flujo OAuth de Microsoft está implementado hoy.
- **Lo que se rompe es la autenticación básica.** Google ya rechaza contraseñas planas en
  Workspace y Microsoft está retirando la autenticación básica de SMTP en Exchange Online.
  La fecha de corte de Microsoft está sin verificar. OAuth no se ve afectado.
- **Entregabilidad.** El correo que sale por el proveedor va firmado con el SPF, DKIM y
  DMARC del dominio. Enviar desde el servidor propio o un relé chocaría con las reglas de
  remitentes de Google y Yahoo.
- **Microsoft puede tener SMTP AUTH apagado.** Muchos inquilinos lo traen así por defecto.
  El administrador de la alcaldía debe activarlo para el buzón que se use; va en la lista
  de instalación.
- **Las API HTTP del proveedor quedan fuera del primer corte.** La API de Gmail y
  `sendMail` de Microsoft Graph son dos implementaciones más para el mismo resultado.
  Graph es la alternativa si un inquilino de Microsoft no activa SMTP AUTH.
- **Límites diarios de envío.** No afectan el volumen de Silvania (unas 3.560 respuestas al
  año). Pueden afectar las notificaciones masivas de Tránsito (C5).

**Restricción por comprobar.** El worker corre sin claves de cifrado y no puede descifrar
la credencial del buzón. El envío se ejecuta en el proceso de la aplicación, encolado en
`trabajo_fuera`.

---

## 4. Envío de la respuesta por correo (C1)

Se reutiliza `postal_shipment` con un canal. Un módulo paralelo duplicaría el acuse y la
devolución.

**Esquema**

- `channel` (`postal` | `email`), `email_cuenta_id`, `message_id`.
- `guia` admite nulo cuando el canal es `email`.
- Estado nuevo: `sent`.

**Ruta.** `POST /radicados/{id}/shipments/email` con destinatario, cuenta y anexos. El
destinatario toma por defecto el correo del tercero.

**Flujo**

1. Se arma el mensaje MIME con el PDF firmado.
2. Se guarda el `.eml` como archivo; su huella va en `acuse_file_id` y `acuse_sha256`.
3. Se encola el envío.
4. Cuando el servidor SMTP acepta, el envío pasa a `sent` y se emite
   `document.shipment_sent`.

**Rebotes.** En el primer corte, el funcionario marca `devuelto` a mano con el flujo de
devolución que ya existe. Después, la ingesta IMAP puede reconocer los avisos de rebote
por `message_id`.

**Permiso.** `shipment.send_email`, comprobado contra el acceso a la cuenta, como ya hace
la constancia.

**Envío masivo de Tránsito (C5).** Sale de la misma ruta más el trabajo por lotes. No
entra en el primer corte.

---

## 5. Suspensión del término y desistimiento tácito (A9)

**Esquema.** Tabla `radicado_deadline_pauses`:

| Columna | Contenido |
|---|---|
| `radicado_id` | La petición. |
| `requirement_radicado_id` | La salida con el requerimiento. |
| `paused_at` | Cuándo se suspendió. |
| `remaining_business_days` | Días hábiles que quedaban. |
| `lapse_date` | Un mes calendario desde el requerimiento. |
| `resumed_at` | Cuándo se reanudó. |
| `outcome` | `resumed` o `withdrawn`. |

**Rutas.** `POST /radicados/{id}/deadline/pause`, `/resume` y `/extend-lapse` (la prórroga
única que permite el art. 17).

**Comportamiento**

- Suspender deja `due_date` en nulo y conserva `termino_inicial`.
- Reanudar recalcula `due_date` desde el día hábil siguiente con los días que quedaban.

**Revisión de vencidos.** Lee `due_date < CURRENT_DATE` (`workflow_repository.py:460`), así
que una fecha nula ya excluye lo suspendido. Se añade una segunda comprobación sobre
`lapse_date` que avisa a la dependencia.

**Eventos.** `document.deadline_paused`, `document.deadline_resumed`,
`document.withdrawal_due`.

**Auditoría.** El mismo patrón que `fijar_vencimiento`.

---

## 6. Horario de atención (A6)

- `sgd_param_admin` gana la hora de cierre.
- En `document_service.py:398`, la fecha de inicio pasa a ser el siguiente día hábil cuando
  se radica después del cierre o en día no hábil.

**Comprobar antes.** El cálculo usa `from_date=now.date()`. Si `now` está en UTC, todo lo
que se radica después de las 7 p. m. de Bogotá ya cuenta desde el día equivocado.

---

## 7. Informe de oportunidad (B13)

- Rutas `GET /reports/timeliness` y `.csv`, junto a las que ya hay en `reports.py`.
- Por dependencia y tipo documental: respondidas a tiempo, respondidas tarde, abiertas en
  término, abiertas vencidas, suspendidas y días promedio de respuesta.
- Fecha de respuesta: la primera salida ligada por `responde_a`, o la finalización.
- Va después de A9: sin la suspensión, las peticiones en requerimiento contarían como
  vencidas.

---

## 8. Prioridad 3

| Escenario | Cambio |
|---|---|
| A11, plazos en horas | `due_at TIMESTAMPTZ` en `radicados` y `default_response_hours` en `tipos_documentales`. Mientras tanto, un tipo con plazo de 2 días cubre las tutelas. |
| B7, respuesta a varias entradas | La columna `responde_a` se sustituye por la tabla `radicado_response_links`. Unos seis puntos de uso, incluidos exportación e importación. |
| B5, delegación de firma | Tabla `signing_delegations` con delegante, delegado, vigencia y el archivo del acto. El delegado firma como él mismo y la firma registra la delegación. Va después de ADR-054, para que los dos cambios de firma no choquen. |

---

## 9. Sin trabajo de backend

- A4, A5, A7, A10 y C4 se resuelven con filas de semilla (tipos documentales, medio de
  recepción `verbal`, un metadato para la guía) y con procedimiento.
- F5 es infraestructura: el respaldo horario se envía al mismo almacén externo que usa
  ADR-050, con retención bloqueada y credenciales fuera del servidor.

---

## 10. Decisiones abiertas

1. **Estado del envío por correo.** Se propone `sent` y no `entregado`: que el servidor
   SMTP acepte el mensaje no prueba que el ciudadano lo recibió, y ADR-054 fija la regla
   de no afirmar más de lo que se puede probar.
2. **Reanudación del término (art. 17).** La ley dice que el término «se reactivará». El
   plan continúa con los días que quedaban, que es la lectura más estricta para la
   alcaldía. Jurídica debe confirmar si se reanuda o se reinicia.
3. **Desistimiento tácito.** Se propone que el sistema solo avise. El desistimiento es un
   acto motivado que alguien firma; no debe finalizar el radicado de forma automática.
