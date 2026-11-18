# Plan de implementación del CR-001: interfaz del primer corte en Silvania

> **Solo en español.** Plan de ejecución interno. Es la contraparte de frontend del
> [plan de backend](../plan-brechas-silvania.md).

18 de noviembre de 2026

**Qué es este documento.** Cómo se construyen los 48 cambios de la solicitud de cambio
[CR-001](CR-001-interfaz-primer-corte-silvania.md), que aplica la decisión
[ADR-057](../adr/ADR-057-interfaz-por-perfil-para-la-primera-alcaldia.md). Para cada bloque dice:

- qué PR los agrupa;
- qué archivos toca;
- qué contrato de backend necesita;
- cómo se prueba;
- en qué semana va.

El *qué* y el *por qué* están en el CR y en el ADR; aquí no se repiten.

**Base.** `main` @ `15be6dc` (17 de noviembre de 2026). ADR-051 y ADR-054 ya están
implementados; el siguiente paso del rediseño es el 4 (ADR-048, identidad propia).

**Convención.** Igual que en el plan de backend:

- Las rutas, tablas, eventos y permisos nuevos van en inglés.
- No hay instalaciones con datos: lo que se sustituye se borra, sin capa de compatibilidad
  ni banderas de función.
- Las rutas de frontend que se citan son relativas a `frontend/src/`.

---

## 1. Reglas de ejecución

1. **Un PR por bloque.** Cada PR cierra uno o varios ítems del CR completos y los nombra en el
   título, por ejemplo `feat(frontend): radicar en una pantalla (CR-001 UI-21, UI-22)`. Las
   particiones de páginas (PR-20 y PR-31) van solas y sin cambio de comportamiento, para que se
   puedan revisar.
2. **Los guardianes llegan antes que la limpieza.** Un guardián nuevo entra con su lista de
   excepciones, que es lo que hoy lo incumple. Cada PR posterior solo puede quitar entradas de esa
   lista, nunca añadirlas. La lista vacía es el criterio de cierre de la fase.
3. **Sin cambio de direcciones.** Ninguna ruta de `routes/` se renombra en este plan: los
   favoritos, los enlaces de los correos y las pruebas e2e siguen sirviendo.
4. **Cada PR lleva:**
   - `npm run test:unit`, `npm run check` y `npm run lint` en verde (la CI ya corre el primero);
   - capturas a 1366×768 antes y después de cada pantalla que cambia;
   - si toca una pantalla con prueba axe, la suite autenticada corrida en local con la pila
     arriba (`npm run test:e2e:auth`) y su resultado pegado en el PR. La CI no corre e2e hoy
     (`.github/workflows/ci.yml:215` solo ejecuta `test:unit`).
5. **Textos según el glosario.** El glosario del ADR-057 §7 se versiona en
   `lib/textos/glosario.js`: término, variantes prohibidas y uso. Los guardianes de texto lo leen
   de ahí. Un término nuevo entra al glosario en el mismo PR que lo usa por primera vez.
6. **El backend que falta se pide por contrato, no por pantalla.** La sección 4 fija las formas de
   las rutas de DEV-14, DEV-15 y DEV-16, que el plan de backend no tiene. La pantalla se maqueta
   contra ese contrato y se cierra cuando la ruta existe.

---

## 2. Semana 0: preparación

No se escribe código de pantallas hasta cerrar esto. Toma de 3 a 5 días y Proboto hace la mayor
parte.

| Tarea | Responsable | Resultado |
|---|---|---|
| Instalación de pruebas con datos de Silvania | Desarrollo | Un subcomando de `scripts/seed_demo.py` (por ejemplo `--perfil silvania`) siembra lo de abajo |
| Maquetas de Radicar (ventanilla y éxito), Bandeja, etiqueta, constancia e inicio de sesión | Proboto | Revisadas con al menos una persona de la ventanilla antes de la semana 2 |
| Línea base de las métricas del CR-001 §8 | Proboto | Tiempo de radicar con escaneo, filas visibles y pasos, medidos en la instalación de pruebas |
| Decisiones que bloquean la fase 1 y la fase 2 | Proboto con la alcaldía | Ver sección 9 |

La siembra de Silvania incluye:

- las dependencias del catálogo, más Subía y Agua Bonita;
- los tipos documentales con su `default_response_days`;
- una plantilla de metadatos de entrada;
- un usuario por perfil: ventanilla, funcionario, jefe y administrador;
- unos 40 radicados repartidos en los seis estados del semáforo.

---

## 3. Bloques de trabajo

### Resumen

| PR | Bloque | Ítems del CR-001 | Depende de | Esfuerzo | Semana |
|---|---|---|---|---|---|
| PR-01 | Guardianes de texto y limpieza de jerga y tuteo | UI-01, UI-03 | — | 2 d | 1 |
| PR-02 | Errores sin fugas, campo JSON, `RevertirModal` | UI-02, UI-04, UI-05 | — | 1 d | 1 |
| PR-03 | Accesibilidad de la ventanilla pública y contraste de controles | UI-06, UI-07 | — | 2 d | 1 |
| PR-04 | axe en páginas públicas y 320 px | UI-08 | PR-03 | 1 d | 2 |
| PR-05 | Filas del panel como enlaces | UI-09 | — | 0,5 d | 2 |
| PR-10 | Inicio de sesión propio y sesión | UI-10, UI-13, UI-15 | ADR-048 | 3 d | 2–3 |
| PR-11 | Recuperación y cambio obligatorio | UI-11, UI-12 | Plan backend §2 y §3 | 2 d | 2–3 |
| PR-12 | Restablecer, desbloquear y vigencia en `admin/usuarios` | UI-14 | Plan backend §2 | 1 d | 3 |
| PR-13 | Portada e identidad de la entidad | UI-16, UI-17 | Contrato §4.4 | 2 d | 3 |
| PR-14 | Constancia ciudadana de la ventanilla web | UI-18 | — | 1 d | 3 |
| PR-20 | Partir Radicar, sin cambio de comportamiento | UI-20 | — (ADR-056 opcional) | 3 d | 4 |
| PR-21 | Radicar en una pantalla | UI-21, UI-22, UI-23, UI-27, UI-28 | PR-20; ADR-055 §4 | 4 d | 4–5 |
| PR-22 | Éxito, etiqueta, constancia y reimpresión | UI-24, UI-25, UI-26 | PR-21; contrato §4.2 | 3 d | 5–6 |
| PR-23 | Radicación de contingencia | UI-29 | PR-21; contrato §4.3 | 2 d | 6 |
| PR-24 | Perfil PDF/A y número del procedimiento | UI-30, UI-31 | INF-08; DEV-06 | 1 d | 6 |
| PR-30 | Semáforo del servidor y `SemaforoChip` | UI-40, UI-50 | Contrato §4.1 | 3 d | 7 |
| PR-31 | Partir Bandeja, sin cambio de comportamiento | UI-41 | — (ADR-056 opcional) | 4 d | 7–8 |
| PR-32 | Lista compacta, orden por vencimiento y acción principal | UI-42, UI-43 | PR-30, PR-31 | 3 d | 8 |
| PR-33 | Responder con borrador ligado y línea de estado | UI-44, UI-45 | PR-31 | 3 d | 9 |
| PR-34 | Enviar la respuesta por correo | UI-46 | Plan backend §3 y §4 | 2 d | 9–10 |
| PR-35 | Pedir información faltante | UI-47 | Plan backend §5 | 2 d | 10 |
| PR-36 | Firma de reservados e inicio del jefe | UI-48, UI-49 | PR-30 | 2 d | 10 |
| PR-40 | Capa de tokens completa y piso tipográfico | UI-60, UI-65 | — | 4 d | 11 |
| PR-41 | Navegación agrupada, inicio por perfil y administración oculta | UI-61, UI-62, UI-66 | — | 2 d | 11 |
| PR-42 | `ConfirmDialog` y migración de las seis mecánicas | UI-63 | — | 3 d | 12 |
| PR-43 | `Tabs` y `Aviso` | UI-64 | — | 2 d | 12 |

**Total: unos 58 días de trabajo de frontend**, sin contar las revisiones. Una persona a tiempo
completo los hace en 12 semanas (60 días hábiles). La columna «Semana» es indicativa y la semana
13 es de estabilización (sección 7).

### Fase 0 · Limpieza (semana 1 y comienzo de la 2)

**PR-01 · Guardianes de texto.**

- **Guardianes nuevos**, en `lib/textos/` y con el patrón de `styles/tokens-existen.test.js`
  (lee los `.svelte` con `execSync` y `git ls-files`):
  - `sin-codigos-en-pantalla.test.js`: busca `PERM_`, `USUA_`, `RF-`, `ADR-`, `tenant`,
    `checksum`, `ingesta`, `indexad`, `JSON` y `HTTP` en el marcado, sin contar comentarios ni
    `<script>`.
  - `usted-siempre.test.js`: busca marcadores de tuteo (`tu `, `tus `, `puedes`, `sube`, `escribe`,
    `elige`…). La lista exacta sale del glosario.
- **Limpieza en el mismo PR:** `routes/(app)/radicar/+page.svelte:1334`, `:1441`, `:1530` y `:1650`,
  y el texto visible de `bandeja`, `borradores`, `firmas`, `envios` y `dashboard`.
- **Excepciones:** lo de `admin/*` queda en la lista y sale en PR-41.
- **Hecho cuando** esas seis páginas no aparecen en ninguna lista de excepciones.

**PR-02 · Errores, JSON y revertir.**

- `lib/utils/apiError.js:458` y `:465`: cuando el catálogo no conoce el código, devolver
  «No se pudo completar la acción. Si continúa, informe al administrador la referencia {ref}.»
  `ref` es el `request_id` de la respuesta si viene, o la hora. Prueba unitaria: ningún mensaje
  casa con `/\b[a-z]+_[a-z_]+\b|\b[45]\d\d\b/`.
- **Mensajes de servidor: no se tocan.** Las 49 cadenas «Error {estado}» de los `+page.server.js` y
  `+server.js` se resuelven con el mensaje único del proxy del ADR-056. Las 29 de los `.svelte` sí
  entran aquí.
- `lib/components/workflow/TransaccionModal.svelte:224`: el campo JSON solo se pinta con
  `USUA_PERM_ADMIN`.
- `lib/components/workflow/RevertirModal.svelte`: título «Devolver al paso anterior», motivo
  obligatorio, sin casilla y sin «no se puede deshacer». Se actualiza su prueba.

**PR-03 · Ventanilla pública y contraste.**

- `routes/(public)/radicacion/+page.svelte` y `routes/(public)/consulta/+page.svelte`:
  - `required` y «(obligatorio)» en tipo, asunto, descripción y nombre;
  - un mapa `errores[campo]`, con `aria-invalid` y `aria-describedby`, y foco al primer campo
    inválido;
  - `autocomplete` (`name`, `email`, `tel`, `street-address`);
  - `<main id="contenido">` y enlace de salto.
- `styles/_tokens.scss`: token `--op-control-border` (propuesta `#8a8f8c`, 3,4:1 sobre blanco).
- `lib/components/ui/FormField.svelte`: lo usa en el borde y añade
  `:focus-visible { outline: 2px solid var(--op-primary) }`.
- **Guardián nuevo** `styles/el-control-se-ve.test.js`: calcula la razón WCAG de
  `--op-control-border` contra `--op-surface` y exige al menos 3.

**PR-04 · axe en lo público.**

- **Ubicación:** `e2e/authenticated/axe-publicas.spec.js`. Va en la suite que corre contra la pila,
  porque radicación y consulta piden datos al backend; corre sin `storageState`.
- **Recorrido:**
  - `/`;
  - `/radicacion`, vacía, con el formulario enviado vacío y con un acuse;
  - `/consulta`, vacía y con un resultado;
  - `/login`, `/indice-clasificados`, `/eliminacion` y `/firma`.
- **Mismo umbral que `axe-vistas-clave.spec.js`:** falla con `serious` o `critical`.
- **`e2e/_adaptabilidad.js`:** se añade el viewport de 320 px a las páginas públicas.

**PR-05 · Panel.** `routes/(app)/dashboard/+page.svelte`: cada pendiente es un `<a>` al radicado.
El cálculo de días calendario de `+page.server.js:217-219` se queda hasta PR-30.

**Puerta de la fase 0:** las listas de excepciones de PR-01 sin las seis páginas de trabajo, axe
público en verde y capturas del formulario público con errores.

### Fase 1 · Puerta de entrada (semanas 2 y 3)

Va con el paso 4 del rediseño (ADR-048). Si el backend de identidad no está listo en la semana 2,
la fase 4 sube a las semanas 2 y 3 y esta baja a las 11 y 12. Las dos son independientes entre sí.

**PR-10 · Inicio de sesión propio.**

- **Se borra el flujo de Keycloak:** `lib/services/auth.server.js` (`exchangeCode`, PKCE y
  `revokeSession`), `routes/(public)/login/callback/` y sus pruebas.
- **El inicio de sesión pasa a ser un formulario:** `routes/(public)/login/+page.svelte` con una
  acción en `+page.server.js` que llama `POST /auth/login` y guarda las cookies con
  `setSessionCookies`.
- **El formulario** usa `FormField`, `autocomplete="username"` y `"current-password"`, y el título y
  el `h1` de la entidad (PR-13).
- **Errores:** credenciales inválidas → «Usuario o contraseña incorrectos»; cuenta bloqueada →
  «Cuenta bloqueada hasta las {hora}. Si es urgente, pida al administrador que la desbloquee.»
- **Sesión vencida:** `hooks.server.js` refresca contra `/auth/refresh` (12 horas). Al vencer,
  redirige a `/login?next={ruta}` con el aviso «Su sesión terminó. Ingrese de nuevo para continuar
  donde estaba.»
- **Entidad no operativa:** el `403` de `entity_not_operational` se muestra en el formulario, con el
  contacto de la entidad.
- **Pruebas:** se reescriben `e2e/public-pages.spec.js` (ya no hay 302 a Keycloak),
  `e2e/authenticated/_auth.setup.js` y `login-flow.spec.js`.

**PR-11 · Recuperación y cambio obligatorio.**

- **`routes/(public)/recuperar`:** correo institucional y «Enviar enlace». La respuesta es siempre
  la misma.
- **`routes/(public)/restablecer/[token]`:** contraseña nueva y su confirmación, con las reglas a la
  vista (12 caracteres, sin repetir las últimas 5). Si el token venció, «Pida uno nuevo».
- **`routes/(app)/cambiar-contrasena`.**
- **Bloqueo hasta cambiar:** `routes/(app)/+layout.server.js` redirige a esa pantalla mientras
  `must_change_password` sea verdadero, salvo la propia ruta y `/logout`. Prueba unitaria del
  layout.

**PR-12 · Usuarios.** `routes/(app)/admin/usuarios`:

- acciones «Restablecer contraseña» y «Desbloquear», con `ConfirmDialog` de nivel 2 o, mientras
  llega PR-42, con `Modal`;
- columna y campo «Vence el» para `expires_at`;
- chip «Bloqueada» o «Vencida».

**PR-13 · Portada e identidad.**

- **`routes/(public)/+page.svelte` se reescribe:** nombre de la entidad, dos botones grandes
  («Radicar PQRSD» y «Consultar mi trámite»), horario de radicación y contacto.
- **Sale el contenido comercial.** Se borran `landing-page.test.js` y `landing-server.test.js` y se
  escriben las pruebas de la portada nueva.
- **Componente `lib/components/layout/IdentidadEntidad.svelte`:** escudo y nombre. Lo usan la barra
  superior (`AppLayout.svelte`), el inicio de sesión, las páginas públicas y las hojas de impresión
  de PR-22.
- **Datos:** de `lib/utils/contactoEntidad.js`, extendido con el escudo (contrato §4.4).

**PR-14 · Constancia ciudadana.** En `routes/(public)/radicacion/+page.svelte` (acuse, `:345-367`):

- «Copiar código», «Imprimir constancia» (hoja de impresión con `@media print`) y enlace directo a
  `/consulta?codigo=…`;
- se cambia «no podemos volver a mostrárselo» por «Guárdelo: lo necesita para consultar su
  trámite.»;
- si el ciudadano dio correo, se dice que la constancia también le llegó allí. Ese correo ya existe
  en `acuse_correo.py`.

**Puerta de la fase 1:** el recorrido completo (entrar, olvidar, restablecer, primer ingreso,
bloqueo y desbloqueo) hecho a mano por una persona que no lo construyó, axe en verde en las
pantallas nuevas y `grep -ri orpyca routes/\(public\)` sin resultados en texto visible.

### Fase 2 · Ventanilla (semanas 4 a 6)

**PR-20 · Partir Radicar.**

- **Componentes nuevos** en `lib/components/features/radicar/`:
  - `RemitenteBloque.svelte`, que envuelve el `ActorPicker`;
  - `DocumentoBloque.svelte`;
  - `MetadatosBloque.svelte`, que hoy es el render de `supportedTemplateProperties`;
  - `DestinoBloque.svelte`;
  - `CargaAnexos.svelte` (`addFiles`, `handleFileDrop`, `uploadPendingAnexos`);
  - `ResumenRadicado.svelte`;
  - `ExitoRadicado.svelte`.
- **Estado del formulario:** pasa a un store `lib/stores/radicarForm.js`, para que lo compartan los
  bloques y el autoguardado de PR-21.
- **Sin cambio visible:** `radicar-page.test.js` y `axe-vistas-clave.spec.js` siguen en verde sin
  tocarse.
- **Guardián de tamaño:** `+page.svelte` de Radicar queda por debajo de 1.500 líneas, y entra en
  `styles/la-pagina-no-crece.test.js` con su tope.
- **Proxy único:** si el ADR-056 ya está, la carga de datos pasa al proxy en este mismo PR, y se
  borran `radicar/api/*` salvo lo que el proxy no cubra.

**PR-21 · Radicar en una pantalla.**

- **Modo ventanilla:** con `docType === "E"` la página muestra los cuatro bloques a la vez, con
  `CargaAnexos` y `ResumenRadicado` en una columna derecha de unos 520 px. Salida e interna
  conservan los pasos, reducidos a los que aplican.
- **Sin inteligencia, sin paso 0:** el paso «¿Empiezas con un documento o el formulario?»
  (`radicar/+page.svelte:1092-1103`) solo se muestra si `+page.server.js` recibe
  `intelligence_enabled` verdadero en la configuración de la entidad. Con `PPI_PROVIDER_URL` vacío
  no aparece y no se pide `/knowledge/ingesta`.
- **Valores y validación:**
  - canal «Presencial» por defecto;
  - `destDeptCode` obligatorio para entradas, en `validateStep` (`:909`);
  - el `ActorPicker` busca al escribir, con 300 ms de espera, y deja de exigir el clic en «Buscar».
- **Metadatos:** se piden con el tipo, como hoy (`cargarPlantilla`, `:369`). Si el radicado
  responde `422 metadatos_no_declarados`, el bloque muestra «Este tipo aún no está listo para
  radicar. Avise al administrador para que configure sus metadatos.» y deshabilita «Radicar».
- **Teclado:**
  - `Ctrl+Enter` radica;
  - `Enter` en un campo de una línea pasa al siguiente;
  - el foco empieza en el buscador de remitente.
- **«Radicar otro»** (`resetForm`, `:968`) conserva `docType`, canal y destino, y pone el foco en el
  remitente.
- **Autoguardado:** el store escribe en `sessionStorage` con la clave
  `radicar:{usuario}:{entidad}` cada 2 segundos de inactividad. Al cargar, se ofrece «Recuperar
  lo que estaba escribiendo». Al radicar se borra. No se guardan los anexos, solo sus nombres, y se
  avisa.
- **Pruebas:**
  - unitarias de `validateStep`, del store y de la regla del paso 0;
  - e2e nueva `e2e/authenticated/radicar-ventanilla.spec.js`: radicar una entrada con anexo solo
    con teclado, a 1366×768, y comprobar que no hubo desplazamiento vertical.

**PR-22 · Éxito e impresión.**

- **`ExitoRadicado.svelte`:**
  - número a 2 rem (32 px) con «Copiar»;
  - `SemaforoChip`, provisional con `due_date` hasta PR-30;
  - fecha de vencimiento formateada con `lib/utils/fecha.js`;
  - botones «Imprimir etiqueta», «Imprimir constancia» y «Radicar otro», con el foco en el primero.
- **Hojas de impresión**, rutas nuevas sin `AppLayout`:
  - `routes/(app)/radicados/[id]/etiqueta`: tamaño de la etiqueta por parámetro de la entidad,
    10 × 5 cm por defecto, con `@page { size: … }`;
  - `routes/(app)/radicados/[id]/constancia`: media carta, con la variante verbal si el canal es
    «Verbal».
  - Las dos llevan `IdentidadEntidad` y QR a `{origen}/consulta?codigo=…`, hecho con la librería
    `qrcode` (MIT, sin dependencias de red). Se cargan con los datos del contrato §4.2, llaman a
    `window.print()` al montar y registran la impresión.
- **Reimpresión:** «Reimprimir etiqueta» y «Reimprimir constancia» en el detalle del radicado de la
  bandeja.
- **Pruebas:** unitarias de las dos hojas (los datos obligatorios de SG-GD-PRO-02, p. 18, están en
  el marcado) y una captura de la vista previa de impresión en el PR.

**PR-23 · Contingencia.**

- **Interruptor** «Radicar en contingencia» en la cabecera del modo ventanilla, visible solo con el
  permiso `intake.register_contingency`.
- **Al encenderlo** aparecen tres campos obligatorios: fecha y hora real de recibo (no futura, no
  más antigua que el parámetro), número de planilla y motivo.
- **Durante la contingencia:** banda amarilla en la cabecera y chip «Contingencia» en el éxito y en
  la bandeja.
- **Contrato:** §4.3.

**PR-24 · PDF/A y numeración.**

- Texto fijo junto a `CargaAnexos` con el perfil de escaneo acordado (INF-08).
- El número de radicado se muestra tal como lo devuelve el servidor, sin armarlo ni partirlo en
  ninguna pantalla: lo confirma una búsqueda de `split("-")` sobre `tracking_number`.

**Puerta de la fase 2:**

- una persona de la ventanilla radica 10 entradas seguidas en la instalación de pruebas, con
  escaneo e impresión;
- se mide el tiempo medio;
- la etiqueta impresa en la impresora real de la alcaldía, o en una del mismo tamaño, es legible y
  el QR abre la consulta.

### Fase 3 · Bandeja y respuesta (semanas 7 a 10)

**PR-30 · Semáforo.**

- **Backend:** contrato §4.1.
- **Frontend:**
  - `lib/components/ui/SemaforoChip.svelte`, con los seis estados, color, icono y texto
    («Por vencer · quedan 4 días hábiles»);
  - `lib/utils/semaforo.js`, con el mapa único de estado a etiqueta, color e icono.
- **Se reemplaza:**
  - el mapa de `lib/components/workflow/WorkflowCard.svelte:85-91`;
  - las clases sin uso `.semaforo-*` de `styles/utilities.scss`;
  - el cálculo de días de `routes/(app)/dashboard/+page.server.js:217-219`.
- **Guardián nuevo** `lib/components/ui/semaforo-unico.test.js`: ningún archivo fuera de
  `semaforo.js` declara `verde`, `amarillo`, `naranja` o `vencido` como claves de un mapa de
  colores.

**PR-31 · Partir Bandeja.**

- **Componentes nuevos** en `lib/components/features/bandeja/`:
  - `BandejaLista.svelte`;
  - `BandejaDetalle.svelte`;
  - `BarraAcciones.svelte`;
  - un archivo por modal que hoy vive inline en `routes/(app)/bandeja/+page.svelte`: anulación
    (`:2283`), devolver (`:2351`), responder (`:2428`), visto bueno (`:2488`), reclasificar y crear
    cadena.
- **Sin cambio visible:** `bandeja-operador.spec.js` y la prueba axe siguen en verde sin tocarse.
- **Tope de tamaño:** el mismo de PR-20.
- **Proxy único:** si el ADR-056 está listo, pasa en este PR.

**PR-32 · Lista y acciones.**

- **`BandejaLista`:**
  - filas de 56 px a dos líneas, con barra de color de 6 px y `SemaforoChip`;
  - columna «Vence»;
  - orden por defecto `deadline.business_days_left` ascendente, con los vencidos primero;
  - filtros rápidos con conteo: Vencidos, Vencen pronto (amarillo y naranja), Esta semana y Todos.
- **El Tablero** (`WorkflowBoard.svelte`) ordena igual y deja de cortar en 100 sin avisar
  (`PAGE_SIZE`, `:105`): pagina, o dice «Se muestran 100 de N».
- **`BarraAcciones`:**
  - una acción principal según el estado: Recibir, Responder o Finalizar;
  - el resto en un menú «Más», con los verbos del glosario: Trasladar, Devolver, Informar,
    Reclasificar y Solicitar anulación.
- **Prueba e2e:** a 1366×768 se ven al menos 9 filas.

**PR-33 · Responder y línea de estado.**

- **«Responder»** en `BandejaDetalle` navega a `/borradores?nuevo=1&padre={id}`.
- **`routes/(app)/borradores/+page.svelte`:**
  - lee `padre` y abre el formulario de borrador con `radicado_padre_id` (el campo ya existe en
    `BorradorCreate`, `services/document-service/document/schemas/borrador.py:78`);
  - precarga el asunto («Respuesta a {asunto}») y el destinatario, que es el remitente de la
    entrada.
- **El modal de respuesta rápida** (`bandeja/+page.svelte:2428`) se borra, y con él la radicación
  de una salida vacía desde la bandeja.
- **`lib/components/features/radicados/LineaEstado.svelte`:**
  - Borrador → Visto bueno → Firma → Envío;
  - cada paso con su estado y un enlace al siguiente;
  - va en el detalle de la bandeja y en el de borradores.

**PR-34 · Enviar por correo.**

- **Al terminar `SignAssistant`** (paso de comprobante), y en el detalle de una salida firmada, el
  botón principal es «Enviar a {correo}», con el correo del destinatario.
- **Diálogo de nivel 3:** destinatario, cuenta institucional de salida y anexos que van.
- **Llama** a `POST /radicados/{id}/shipments/email` (plan de backend §4).
- **Estados:** «Enviado el {fecha y hora}» en la línea de estado, y «Marcar como devuelto» con el
  flujo de devolución existente.
- **Envíos:** `routes/(app)/envios` muestra el canal en la lista y deja de exigir que se teclee el
  radicado cuando se llega desde un radicado (`envios/+page.svelte:449-494`).

**PR-35 · Pedir información faltante.**

- **Acción** junto a «Responder»: abre un borrador de requerimiento ligado (igual que PR-33) y, al
  radicarlo, llama `POST /radicados/{id}/deadline/pause` (plan de backend §5).
- **Mientras dura:**
  - la tarjeta y la fila muestran «Plazo suspendido · el peticionario tiene hasta el
    {lapse_date}»;
  - el semáforo pasa a `paused`.
- **Acciones del estado:** «Reanudar plazo» (`/resume`) y, cumplido el mes, aviso «Puede declarar
  el desistimiento». El sistema no finaliza solo.

**PR-36 · Reservados e Inicio del jefe.**

- **Reservados:** si firmar un reservado responde que falta certificado
  (`firma_personal_require_xades`, ADR-054), `SignAssistant` muestra «Este documento es reservado
  y exige firma con certificado. Inscriba su certificado en Perfil → Firma, o pida al
  administrador que revise la exigencia.» `apiError.js` recibe el código y su mensaje.
- **Inicio del jefe** (`routes/(app)/dashboard`): primer bloque «Por decidir», con las solicitudes de
  firma, los vistos buenos pendientes y los vencidos de su dependencia, cada fila con enlace.
- **Titular por plantilla,** con datos y no con un agente: «Tiene {n} vencidos y {m} por vencer».
- **Prueba unitaria:** con `asistente_habilitado = false` no queda ningún bloque vacío.

**Puerta de la fase 3:** el mismo radicado sembrado tiene el mismo color en Bandeja, Tablero,
Inicio y éxito de Radicar, y un funcionario responde y envía por correo una petición sin abrir el
menú.

### Fase 4 · Armazón y patrones (semanas 11 y 12)

**PR-40 · Tokens.**

- **Se adopta** `design/prototipo/css/tokens.css` de la rama `docs/rediseno-ui`.
- **Se añaden** los 35 alias que le faltan, se reconcilian los cuatro nombres `--db-*` que ya
  existen en `main` (`-dark`/`-light` frente a `-ink`/`-soft`) y se generan los colores de
  `styles/custom-bulma.scss:30-44` desde los mismos valores.
- **Guardianes nuevos:**
  - todo `--op-*` tiene alias;
  - los pares de texto declarados en `_tokens.scss` miden al menos 4,5:1;
  - ningún token de tamaño de letra baja de 12 px.
- **No entran** el tema oscuro ni los cambios de geometría: el radio, el ancho de la barra lateral
  y el alto de la barra superior se quedan como están.

**PR-41 · Navegación.**

- **`lib/utils/navItems.js`:** cada ítem lleva un `group` (`trabajo`, `archivo` o `administracion`)
  y, opcionalmente, un `badge` (pendientes de bandeja y de firma). `SidebarNav.svelte` pinta los
  grupos.
- **Inicio por perfil:** una función `lib/utils/inicioPorPerfil.js` resuelve la pantalla de
  inicio desde los permisos. La usan los dos sitios que hoy redirigen fijo a `/dashboard`
  (`routes/(public)/+page.server.js:24` y `routes/(public)/login/+page.server.js:75`):
  - radica entradas y no tramita: `/radicar`;
  - tiene `PERM_FIRMA`: `/dashboard`;
  - en otro caso: `/bandeja`.
- **Administración:** el grupo solo se pinta con `USUA_PERM_ADMIN`; `routes/(app)/admin/+layout.server.js`
  ya devuelve 403.
- **Excepciones:** salen las de `admin/*` de los guardianes de PR-01.

**PR-42 · `ConfirmDialog`.**

- **Componente** `lib/components/ui/ConfirmDialog.svelte`, construido sobre `Modal`, con los
  niveles 1 a 4 del ADR-057 §6.
- **Migración de las seis mecánicas:**
  - las casillas «Entiendo…» de `FinalizarTramiteModal`, `DisposicionRadicadoDrawer`,
    `ExcepcionesFirma` y `admin/interoperabilidad`;
  - el código de lote de `admin/eliminacion`;
  - el número de radicado de `desclasificaciones`;
  - las frases fijas de `admin/terceros`, `admin/seguridad/claves`,
    `admin/flujos/reasignacion` y `perfil`.
- **El motivo mínimo** queda en 10 caracteres en todas.
- **Guardián:** ninguna casilla `bind:checked` con nombre `confirm*` o `entiendo*` fuera del
  componente.

**PR-43 · `Tabs` y `Aviso`.**

- **Componentes:** `lib/components/ui/Tabs.svelte` (`role="tab"`, `aria-controls`, flechas) y
  `lib/components/ui/Aviso.svelte` (info, éxito, advertencia y error).
- **Migración:** las 11 listas de pestañas y las 35 clases de aviso.
- **Guardián:** `role="tablist"` solo dentro de `Tabs.svelte`.

**Puerta de la fase 4:** todas las listas de excepciones vacías y la suite axe autenticada en
verde.

---

## 4. Contratos de backend que este plan necesita

El plan de backend cubre identidad (§2), correo saliente (§3), envío por correo (§4) y
suspensión del término (§5). Estos cuatro no están, y bloquean PR-13, PR-22, PR-23 y PR-30.

### 4.1 Semáforo en días hábiles (DEV-15)

**Hoy.** `_semaforo()` (`services/workflow-service/workflow/services/workflow_service.py:127-138`)
usa días calendario, umbrales fijos (3 y 1) y cuatro valores sin naranja. Solo sale en la respuesta
del paso de flujo (`:1475`).

**Cambio.**

- **Parámetro de entidad `deadline_thresholds`** en `PARAMS_ADMITIDOS`
  (`services/tenant-service/tenant/routers/config.py:212`), con valor por defecto
  `{"yellow": 5, "orange": 3}` (SG-GD-PRO-02).
- **`business_days_between(start, end, non_business)`** en `tenant/core/calendar.py`, junto a
  `add_business_days`, con el mismo calendario con el que `calculate_due_date` fija hoy
  `due_date`.
- **`_semaforo()` se sustituye por `deadline_status()`**, que devuelve
  `{"status": "green|yellow|orange|red|paused|none", "business_days_left": int | null}`:
  - `paused` cuando hay suspensión abierta (plan de backend §5);
  - `none` cuando no hay `due_date`.
- **El objeto `deadline`** va en la respuesta del paso de flujo y en las de radicado: detalle,
  bandeja, búsqueda y lista del panel.
- **Prueba:** tabla de casos con festivos, fin de semana, cada umbral y suspensión.

### 4.2 Etiqueta y constancia (DEV-14)

- **`GET /radicados/{id}/receipt`** devuelve:
  - número, `registered_at`, folios y anexos (de la plantilla de metadatos);
  - dependencia destino y nombre de quien radicó;
  - `due_date` y `verification_code`;
  - canal y, para «Verbal», identificación y dirección de notificación.
  Se protege con el mismo permiso de lectura del radicado. El `verification_code` solo sale para
  entradas.
- **`POST /radicados/{id}/receipt/prints`** con `{"kind": "label" | "receipt"}`. Registra
  `document.receipt_printed` en la auditoría.
- **Correo de acuse:** `acuse_correo.py` agrega folios y anexos, que SG-GD-PRO-02 (p. 19) exige.
- **Fuera del primer corte:** el PDF de la constancia. La hoja se imprime desde el navegador.

### 4.3 Radicación de contingencia (DEV-16)

- **Columnas** `received_at`, `contingency_sheet` y `contingency_reason` en `radicados`. Si
  `received_at` es nulo, vale `registered_at`.
- **Campos opcionales** del mismo `POST` de radicación, aceptados solo con el permiso
  `intake.register_contingency`.
- **Validación:**
  - `received_at` no puede ser futura;
  - no puede ser más antigua que el parámetro de entidad `contingency_max_age_days`, por defecto 5.
- **El plazo** cuenta desde `received_at`.
- **Reporte** `GET /reports/contingency?from&to` para cotejar con la planilla.
- **Auditoría:** `document.registered_in_contingency`.

### 4.4 Identidad de la entidad

- **Parámetro `entidad_escudo`**, una imagen PNG o SVG de hasta 200 KB, que se sube desde
  `admin/parametros` y se guarda con el almacenamiento existente.
- **`GET /public/entity`** devuelve lo que hoy junta `contactoEntidad.js` más la URL pública del
  escudo y un color institucional (`entidad_color`). Sin sesión, para portada, inicio de sesión y
  ventanilla pública.

---

## 5. Cronograma

Semana 1 es la semana en que se aprueba el CR-001. El cuadro supone una persona de frontend a tiempo
completo y el backend del plan de brechas avanzando en paralelo.

| Semana | Frontend | Backend que debe estar listo al empezar la semana |
|---|---|---|
| 0 | Preparación (sección 2) | — |
| 1 | Fase 0: PR-01 a PR-03 | — |
| 2–3 | PR-04 y PR-05; fase 1: PR-10 a PR-14 | ADR-048 con plan de backend §2 y §3; contrato §4.4 |
| 4 | PR-20 y comienzo de PR-21 | ADR-055 §4 (`422 metadatos_no_declarados`) |
| 5–6 | PR-21 a PR-24 | Contratos §4.2 y §4.3; decisiones de INF-08 y DEV-06 |
| 7–8 | PR-30 a PR-32 | Contrato §4.1 |
| 9–10 | PR-33 a PR-36 | Plan de backend §4 y §5 |
| 11–12 | Fase 4: PR-40 a PR-43 | — |
| 13 | Estabilización, prueba con usuarios y capacitación | — |

**Si el ADR-048 se atrasa:** la fase 4 pasa a las semanas 2 y 3, y la fase 1 a las 11 y 12. La
fase 2 no depende de la identidad.

**Festivos y fin de año.** Si el CR se aprueba en la semana del 23 de noviembre de 2026, el
cronograma cruza el 8 de diciembre, el 25 de diciembre, el 1 de enero y el 11 de enero de 2027, y
la semana de fin de año suele perderse. Contado así, la semana 13 cae a finales de febrero de 2027.
La fecha de salida en vivo se fija con la alcaldía cuando se cierre la fase 2, no antes.

**Con dos personas de frontend** el cronograma baja a unas 8 semanas: una toma las fases 0, 1 y 4,
y la otra las fases 2 y 3. Las particiones (PR-20 y PR-31) no se reparten.

---

## 6. Cómo se prueba

| Nivel | Qué cubre | Dónde corre |
|---|---|---|
| Unitarias (Vitest) | Componentes, stores, `apiError.js`, `semaforo.js`, layouts | CI, en cada PR |
| Guardianes | Los diez del ADR-057 §10, con lista de excepciones | CI, en cada PR |
| e2e con la pila (Playwright) | Inicio de sesión, radicar en ventanilla, bandeja, responder y enviar, axe público y autenticado | En local antes de cada PR que toque esas pantallas, y completa en cada puerta de fase |
| Revisión visual | Capturas a 1366×768 y a 320 px las públicas | En el PR |
| Prueba con usuarios | Las cinco tareas del CR-001 §8 | Semana 13, y una sesión corta con la ventanilla al cerrar la fase 2 |

**Recomendación aparte.** Una tarea de CI que levante la pila y corra
`e2e/authenticated/axe-*.spec.js`, aunque sea una vez al día sobre `main`. Sin ella, la
accesibilidad pública depende de que alguien corra la suite a mano. No es parte de este plan, pero
conviene decidirlo antes de la fase 1.

---

## 7. Salida en vivo

Semana 13:

1. **Estabilización.** Se corrige lo que salga de la prueba con usuarios y se corre la suite
   completa: unitarias, guardianes, e2e y axe.
2. **Prueba con usuarios.** Dos sesiones de una hora en la instalación de pruebas: las 3 personas de
   ventanilla y 5 funcionarios, con las cinco tareas del CR-001 §8. Se miden tiempo y ayuda pedida
   contra la línea base de la semana 0.
3. **Manual y capacitación.** El manual de la ventanilla y el del funcionario se escriben sobre las
   pantallas nuevas, con capturas de la instalación de pruebas, y la capacitación usa la misma
   instalación.
4. **Criterio para salir:**
   - 5 de 5 tareas sin ayuda en al menos 7 de las 8 personas;
   - radicar con escaneo e impresión en menos de 2 minutos;
   - axe en verde;
   - todas las listas de excepciones vacías.
   Si no se cumple, se sale solo con lo que pasó y el resto se corrige en las dos semanas
   siguientes, sin bloquear la operación.

---

## 8. Quién hace qué

| Rol | Responsabilidades |
|---|---|
| Desarrollo frontend | PR-01 a PR-43, guardianes y pruebas |
| Desarrollo backend | ADR-048, plan de backend §2 a §5, contratos §4.1 a §4.4 de este plan |
| Proboto | Preparación (sección 2), maquetas, decisiones con la alcaldía, revisión de textos contra el glosario, prueba con usuarios, manual y capacitación |
| Alcaldía de Silvania | Decisiones de la sección 9, personas para las pruebas, impresora y etiquetas |

---

## 9. Decisiones que bloquean, y para cuándo

| Decisión | Quién | Hace falta antes de |
|---|---|---|
| Quién hace el frontend y si son una o dos personas | Desarrollo y Proboto | Semana 0 |
| Si Radicar y Bandeja pasan al proxy único al partirse (PR-20, PR-31) | Desarrollo | Semana 4 |
| Si la migración a Svelte 5 va antes o después de la fase 2 | Desarrollo | Semana 4 |
| Contratos §4.1 a §4.4: aceptarlos o proponer otra forma | Desarrollo backend | Semana 1 |
| Escudo, nombre oficial y color institucional | Alcaldía | Semana 2 |
| Textos de la constancia y de la autorización de datos, revisados por Jurídica | Alcaldía | Semana 3 |
| Tamaño de la etiqueta, impresora y si se pega antes o después de escanear | Alcaldía | Semana 4 |
| Campos de la plantilla de metadatos de entrada (cuatro o menos) | Alcaldía | Semana 4 |
| Perfil de escaneo PDF/A y cómo se valida (INF-08) | Desarrollo y alcaldía | Semana 5 |
| Numeración del procedimiento (DEV-06) | Desarrollo y alcaldía | Semana 6 |
| Umbrales y nombres del semáforo (SG-GD-PRO-02: 5 y 3 días) | Alcaldía | Semana 7 |
| Si arranca con la exigencia de certificado para reservados | Alcaldía | Semana 10 |
| Fecha de salida en vivo | Alcaldía y Proboto | Al cerrar la fase 2 |
