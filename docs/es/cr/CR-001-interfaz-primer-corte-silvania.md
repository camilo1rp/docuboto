# CR-001: Interfaz del primer corte para la Alcaldía de Silvania

> **Solo en español.** Es una solicitud de cambio interna, como el
> [plan de brechas de backend](../plan-brechas-silvania.md), del que es la contraparte de
> frontend.

| Campo | Valor |
|---|---|
| Identificador | CR-001 |
| Fecha | 18 de noviembre de 2026 |
| Solicita | Proboto, para la puesta en marcha en la Alcaldía de Silvania |
| Para | Equipo de desarrollo de Docuboto |
| Estado | Propuesta |
| Decisión que la sustenta | [ADR-057](../adr/ADR-057-interfaz-por-perfil-para-la-primera-alcaldia.md) |
| Base revisada | `main` @ `15be6dc` (17-nov-2026) y rama `docs/rediseno-ui` @ `aa44976` |
| Prioridad | Las fases 0 a 3 bloquean el arranque en Silvania; la 4 va en el primer trimestre |
| Esfuerzo estimado | 9 a 13 semanas de una persona de frontend, por confirmar con desarrollo |

---

## 1. Qué se pide

Cambiar la interfaz para que cada perfil de la alcaldía resuelva su tarea en una pantalla, en el lenguaje de la entidad, con el semáforo y los impresos que exige su procedimiento SG-GD-PRO-02 y con la accesibilidad que exige la Resolución 1519 de 2020. El cambio toma del rediseño de interfaz 3.0 lo que sirve sin capa de inteligencia y pospone el resto.

Las cinco mejoras que más pesan:

1. **Radicar una entrada en papel en una sola pantalla**, con etiqueta y constancia impresas. Hoy son 6 pasos y no se imprime nada.
2. **Responder como un recorrido continuo**, desde la entrada hasta el envío por correo. Hoy son cuatro pantallas sin enlaces y Envíos solo conoce correo postal.
3. **La puerta de entrada de la alcaldía:** portada, inicio de sesión propio y recuperación de contraseña. Hoy muestran Orpyca y redirigen a Keycloak.
4. **Un semáforo único de cuatro colores en días hábiles**, visible en la bandeja. Hoy tiene tres colores y solo aparece en el Tablero.
5. **Accesibilidad AA en la ventanilla pública.** Hoy no marca obligatorios, el borde de los campos casi no se ve y no hay prueba automática.

## 2. Por qué

| Dato de Silvania | Consecuencia en la interfaz |
|---|---|
| 3 personas en ventanilla; el 100 % de la entrada llega en papel; día pico de 62 entradas | Radicar tiene que ser rápido con teclado y terminar en la etiqueta impresa |
| ~70 funcionarios tramitan y ~20 jefes firman | La bandeja tiene que mostrar lo que vence primero; el jefe tiene que ver lo que debe decidir |
| El 60 % de las respuestas (≈3.560 al año) sale por correo | El envío por correo es el final normal de una respuesta, no un módulo aparte |
| SG-GD-PRO-02 exige etiqueta, constancia, semáforo de cuatro colores y contingencia | Son requisitos de aceptación, no mejoras |
| Usuarios que vienen de un libro de Excel; inspecciones rurales con internet variable | Lenguaje llano, usted, sin códigos; páginas livianas |
| Alcaldía, sujeto obligado de la Ley 1712 | WCAG 2.1 AA en lo público (Resolución 1519 de 2020) |

Los hallazgos completos, con archivo y línea, están en el contexto del [ADR-057](../adr/ADR-057-interfaz-por-perfil-para-la-primera-alcaldia.md).

## 3. Alcance

**Incluye:**

- inicio de sesión y portada;
- Radicar (entrada), Bandeja, Tablero, Inicio y Borradores;
- Firmas, en lo que toca al recorrido de respuesta;
- envío por correo;
- ventanilla pública y consulta;
- navegación;
- capa de tokens y componentes de patrón;
- textos;
- accesibilidad de lo público.

**No incluye:**

- barra de comandos y deshacer con cuenta regresiva;
- panel del agente, «Inteligencia» y «Capacidades»;
- tema oscuro;
- pantallas nuevas de Expedientes, Archivo físico, Transferencias y Administración, salvo lo puntual de la fase 1;
- páginas públicas de instrumentos (ADR-055);
- migración a Svelte 5.

## 4. Cambios solicitados

Las rutas son relativas a `frontend/src/`. La columna «Backend» nombra la pieza de [`plan-brechas-silvania.md`](../plan-brechas-silvania.md) o del ADR de la que depende. «—» significa que no depende de nada.

### Fase 0 · Limpieza (1 semana, sin dependencias)

| ID | Cambio | Dónde | Se acepta cuando |
|---|---|---|---|
| UI-01 | Quitar códigos y jerga del texto visible: `RF-*`, `PERM_*`, `USUA_*`, ADR, «tenant», «checksum», «ingesta», «indexado» | `routes/(app)/radicar/+page.svelte:1334`, `:1441`, `:1530`, `:1650`; `envios`; `admin` | El guardián de patrones prohibidos pasa sin excepciones en `radicar`, `bandeja`, `borradores`, `firmas`, `envios` y `dashboard` |
| UI-02 | `mensajeError` nunca devuelve el texto ni el código crudo del servidor: mensaje genérico con referencia | `lib/utils/apiError.js:458`, `:465` | Prueba unitaria: ningún mensaje coincide con un código (`snake_case`) ni con un estado HTTP |
| UI-03 | Usted en toda la aplicación | Unos 51 marcadores de tú; primero `radicar`, `firmas`, `+error` y `403` | El guardián de tuteo pasa |
| UI-04 | Ocultar el campo «Detalles adicionales — JSON» a quien no administra | `lib/components/workflow/TransaccionModal.svelte:224` | Un funcionario sin `USUA_PERM_ADMIN` no lo ve |
| UI-05 | `RevertirModal` pasa a «Devolver al paso anterior», con motivo y sin casilla de «no se puede deshacer» | `lib/components/workflow/RevertirModal.svelte:136`, `:156` | El texto ya no afirma irreversibilidad; la prueba del componente lo fija |
| UI-06 | Ventanilla pública: obligatorios marcados con `required` y texto, error por campo con `aria-invalid` y `aria-describedby`, foco al primer error, `autocomplete` en nombre, correo, teléfono y dirección, `<main>` y enlace de salto | `routes/(public)/radicacion/+page.svelte`, `routes/(public)/consulta/+page.svelte` | El formulario vacío se recorre y se corrige solo con teclado |
| UI-07 | Token `--op-control-border` de al menos 3:1 para los controles, y foco con contorno de 2 px en `FormField` | `styles/_tokens.scss`, `lib/components/ui/FormField.svelte` | Guardián que calcula la razón de contraste |
| UI-08 | Prueba axe de las páginas públicas (portada, radicación con error y con acuse, consulta, inicio de sesión, índice de clasificados, eliminación, comprobar firma) y viewport de 320 px | Nuevo `e2e/axe-publicas.spec.js`; `e2e/_adaptabilidad.js` | Sin fallos serios ni críticos |
| UI-09 | Las filas del panel principal abren su radicado | `routes/(app)/dashboard/+page.svelte` | Cada pendiente es un enlace |

### Fase 1 · Puerta de entrada (1 a 2 semanas, con ADR-048)

| ID | Cambio | Backend | Se acepta cuando |
|---|---|---|---|
| UI-10 | Formulario propio de inicio de sesión con `FormField`, título y `h1` de la entidad; se retira `login/callback` | ADR-048 `POST /auth/login` | No queda «Orpyca», «OrpycaMCP» ni «proveedor de identidad» en `routes/(public)` |
| UI-11 | «¿Olvidó su contraseña?» → «Enviar enlace»; pantalla para la contraseña nueva desde el enlace | Plan de brechas §2: `/auth/password/forgot` y `/reset` | La respuesta es la misma exista o no la cuenta; el enlace vencido se explica |
| UI-12 | Cambio obligatorio en el primer ingreso y tras un restablecimiento, con las reglas visibles: 12 caracteres y sin repetir las últimas 5 | §2: `must_change_password`, `/auth/password/change` | No se puede llegar a otra pantalla sin cambiarla |
| UI-13 | Aviso de cuenta bloqueada con la hora en que se desbloquea | ADR-048: `failed_logins`, `locked_until` | La persona sabe cuánto esperar y a quién acudir |
| UI-14 | En `admin/usuarios`: restablecer contraseña, desbloquear y fecha de vencimiento de la cuenta | §2: `/users/{id}/password-reset`, `/unlock`, `expires_at` | El administrador lo hace sin salir del panel; un contratista vencido no entra |
| UI-15 | Sesión vencida: aviso y regreso a la misma pantalla tras entrar de nuevo | ADR-048: refresco de 12 h | Se conserva `?next=` |
| UI-16 | Portada pública de la entidad con dos acciones: «Radicar PQRSD» y «Consultar mi trámite» | — | Un ciudadano llega al formulario en un clic |
| UI-17 | Escudo, nombre oficial y color institucional de la entidad, configurables, en barra superior, inicio de sesión, páginas públicas e impresos | Parámetros de la entidad (`entidad_nombre` y un parámetro nuevo para el escudo) | Se cambian sin recompilar |
| UI-18 | Constancia ciudadana de la ventanilla web: imprimible, con «Copiar código» y envío al correo declarado | Constancia de `acuse_correo.py` | El ciudadano conserva su código sin tomar pantallazo |

### Fase 2 · Ventanilla (2 a 3 semanas)

| ID | Cambio | Backend | Se acepta cuando |
|---|---|---|---|
| UI-20 | Partir `radicar/+page.svelte` en componentes: remitente, documento, metadatos, destino, carga y resumen. Si el proxy único está listo, la carga de datos pasa a él en el mismo cambio | ADR-056, opcional | Ningún archivo de Radicar pasa de 1.500 líneas |
| UI-21 | Radicar una entrada en una sola pantalla (ADR-057 §3): canal «Presencial» por defecto, destino obligatorio, remitente buscado al escribir, `Ctrl+Enter` para radicar | — | Una entrada en papel se radica solo con teclado y sin desplazar a 1366×768 |
| UI-22 | Sin proveedor de inteligencia no se ofrece «empezar por un documento» | Configuración de la entidad (ADR-051) | Con `PPI_PROVIDER_URL` vacío, Radicar abre en el formulario |
| UI-23 | Campos de la plantilla de metadatos del tipo dentro de la misma pantalla; mensaje claro cuando el tipo no tiene plantilla | ADR-055 §4: `422 metadatos_no_declarados` | Dice «Este tipo aún no está listo para radicar; avise al administrador» |
| UI-24 | Pantalla de éxito con el número a 32 px, «Copiar», semáforo y vencimiento, «Imprimir etiqueta», «Imprimir constancia» y «Radicar otro» con el foco | DEV-14 | Se imprime sin salir de la pantalla |
| UI-25 | Hojas de impresión de la etiqueta (con QR a la consulta pública) y de la constancia en media carta, con la variante verbal | DEV-14 | Vista previa correcta en el tamaño de etiqueta acordado y en media carta |
| UI-26 | Reimpresión de etiqueta y constancia desde el detalle del radicado | DEV-14, asiento de reimpresión | Queda en el historial |
| UI-27 | «Radicar otro» conserva tipo y modo | — | El segundo radicado no vuelve al primer paso |
| UI-28 | Autoguardado del formulario en el navegador de quien radica | — | Recargar a mitad de formulario no pierde datos |
| UI-29 | Interruptor «Radicar en contingencia»: fecha y hora reales de recibo, número de planilla y motivo | DEV-16: `recibido_at` y permiso propio | No aparece un destino nuevo en el menú; el radicado lleva la marca «contingencia» |
| UI-30 | Indicación del perfil de escaneo PDF/A junto a la carga | INF-08 (decisión sobre PDF/A) | La ventanilla sabe con qué perfil escanear |
| UI-31 | Número de radicado en el formato del procedimiento | DEV-06 | La interfaz muestra el formato que devuelve el servidor, sin armarlo |

### Fase 3 · Bandeja y respuesta (3 a 4 semanas)

| ID | Cambio | Backend | Se acepta cuando |
|---|---|---|---|
| UI-40 | Componente `SemaforoChip` con los seis estados del ADR-057 §4: color, icono y texto con los días hábiles que quedan | DEV-15: estado `naranja`, umbrales por entidad y días hábiles | El mismo radicado tiene el mismo color en Bandeja, Tablero, Inicio y éxito de Radicar |
| UI-41 | Partir `bandeja/+page.svelte`: lista, detalle, barra de acciones y modales | ADR-056, opcional | Ningún archivo de Bandeja pasa de 1.500 líneas |
| UI-42 | Lista con filas de 56 px a dos líneas, columna «Vence», orden por vencimiento y filtros rápidos: Vencidos, Vencen pronto, Esta semana, Todos | DEV-15 | Al menos 9 filas a 1366×768 |
| UI-43 | Una acción principal según el estado y el resto en «Más»; verbos del glosario | — | El detalle no muestra más de 3 botones del mismo peso |
| UI-44 | «Responder» abre un borrador ligado a la entrada, con asunto y destinatario precargados | Vínculo `responde_a` | Ya no se radica una salida vacía desde la bandeja |
| UI-45 | Línea de estado en el radicado: Borrador, Visto bueno, Firma, Envío; cada paso enlaza al siguiente | — | Se llega del borrador al envío sin usar el menú |
| UI-46 | Al terminar la firma: «Enviar a {correo}», con el estado «Enviado»; «Marcar como devuelto» para un rebote | DEV-01 y plan §3–§4: `POST /radicados/{id}/shipments/email`, estado `sent` | Una petición se cierra sin abrir Envíos |
| UI-47 | «Pedir información faltante» junto a «Responder»: plazo en pausa, fecha del mes para el desistimiento y aviso cuando llega | DEV-03 y plan §5: `/deadline/pause`, `/resume`, `/extend-lapse` | La tarjeta muestra «Plazo suspendido»; el sistema avisa, no finaliza |
| UI-48 | Mensaje cuando un documento reservado exige certificado y la persona no lo tiene | ADR-054 (`firma_personal_require_xades`) | Dice qué falta y dónde inscribir el certificado |
| UI-49 | Inicio del jefe con el bloque «Por decidir»: firmas, vistos buenos y vencidos de su dependencia; titular calculado con datos | Consultas existentes del panel | Con `asistente_habilitado = false` no queda hueco |
| UI-50 | El panel principal usa el semáforo del servidor y deja de contar días calendario | DEV-15 | `dashboard/+page.server.js` no calcula días |

### Fase 4 · Armazón y patrones (2 semanas, sin dependencias)

| ID | Cambio | Se acepta cuando |
|---|---|---|
| UI-60 | Adoptar `tokens.css` del 3.0 completo: los 35 alias que faltan, los cuatro nombres `--db-*` reconciliados con `main` y los colores de Bulma sincronizados | Prueba que exige alias para todo `--op-*` y contraste de 4,5:1 en los pares de texto |
| UI-61 | Navegación agrupada (Trabajo, Archivo, Administración), con contadores y sin cambio de direcciones | La ventanilla ve 5 destinos o menos; las pruebas e2e de navegación siguen verdes |
| UI-62 | Pantalla de inicio por perfil (ADR-057 §1), derivada de los permisos | La ventanilla llega a Radicar sin clics tras entrar |
| UI-63 | `ConfirmDialog` con cuatro niveles; migrar las seis mecánicas actuales | Ninguna casilla «Entiendo…» fuera del componente |
| UI-64 | Componentes `Tabs` y `Aviso`; migrar las 11 pestañas y las 35 clases de aviso | `role="tablist"` solo dentro de `ui/Tabs.svelte` |
| UI-65 | Piso tipográfico de 12 px | Ningún token de tamaño por debajo de 12 px |
| UI-66 | Grupo de administración oculto a quien no administra | Un funcionario no ve esos enlaces y la dirección directa responde 403 |

## 5. Dependencias de backend

| Pieza de backend | Sección del plan de brechas | Ítems de este CR |
|---|---|---|
| Inicio de sesión propio, recuperación, cambio obligatorio, bloqueo, vigencia | §2 y ADR-048 | UI-10 a UI-15 |
| Correo saliente autenticado | §3 | UI-11, UI-18, UI-46 |
| Envío de la respuesta por correo | §4 (DEV-01) | UI-46 |
| Suspensión del término y desistimiento | §5 (DEV-03) | UI-47 |
| Semáforo de cuatro colores en días hábiles, umbrales por entidad | DEV-15 (no está en el plan de brechas: hay que añadirlo) | UI-40, UI-42, UI-50 |
| Etiqueta y constancia (datos, asiento de reimpresión) | DEV-14 (no está en el plan de brechas: hay que añadirlo) | UI-24 a UI-26 |
| Radicación de contingencia | DEV-16 (no está en el plan de brechas: hay que añadirlo) | UI-29 |
| Numeración del procedimiento | DEV-06 | UI-31 |
| Metadatos obligatorios por tipo | ADR-055 §4 | UI-23 |
| Proxy único | ADR-056 | UI-20, UI-41 y la traducción de errores de UI-02 |

DEV-14, DEV-15 y DEV-16 vienen de la revisión de brechas de Silvania y todavía no tienen sección en `plan-brechas-silvania.md`. Sin ellas, UI-24 a UI-29 y UI-40 se pueden maquetar pero no cerrar.

## 6. Impacto

- **Código:** sobre todo `routes/(app)/radicar`, `bandeja`, `dashboard`, `borradores`, `firmas`, `routes/(public)/*`, `lib/components/ui`, `lib/components/workflow`, `lib/utils/apiError.js`, `lib/utils/navItems.js` y `styles/`.
- **Pruebas nuevas:** los diez guardianes del ADR-057 §10 y `e2e/axe-publicas.spec.js`. Algunos fallarán el primer día sobre código existente: se activan con lista de excepciones que se vacía por fase.
- **Pruebas existentes:** las que buscan textos exactos cambian con los textos. `SidebarNav.test.js` y las pruebas de `navItems` pasan a afirmar los grupos.
- **Documentación:** `docs/es/pantallas.md` y `docs/en/pantallas.md`, `getting-started.md` y los changelogs, en cada fase.
- **Operación:** el manual de la ventanilla y la capacitación se escriben sobre estas pantallas, no sobre las actuales.

## 7. Riesgos

| Riesgo | Mitigación |
|---|---|
| Partir Radicar y Bandeja y migrarlas al proxy en momentos distintos obliga a tocarlas dos veces | Una sola intervención por página (UI-20, UI-41); si el proxy no está listo, se parte sin él |
| La migración a Svelte 5 se cruza con la partición de páginas | Se decide antes de la fase 2 si va antes o después; no en medio |
| Los textos nuevos rompen pruebas que buscan textos exactos | Cada fase actualiza sus pruebas; la prueba de mensajes en español sigue siendo la red |
| La plantilla de metadatos de entrada tiene más de cuatro campos y Radicar no cabe | Confirmar con la alcaldía antes de la fase 2; si no cabe, el bloque de metadatos se pliega |
| Los umbrales del semáforo cambian después del arranque | Son parámetros de la entidad, no código |
| La exigencia de certificado para reservados bloquea firmas el primer día | Decisión explícita de la entidad antes del arranque (UI-48 muestra qué falta) |

## 8. Cómo se verifica

**Medidas, antes de la fase 2 y antes de salir en vivo:**

| Medida | Hoy | Meta |
|---|---|---|
| Pasos para radicar una entrada en papel | 6 pasos y 1 modal | 1 pantalla |
| Tiempo para radicar una entrada con escaneo e imprimir la etiqueta | Sin medir | Menos de 2 minutos |
| Pantallas para responder y enviar por correo | 4 | 1, más la firma |
| Filas visibles en la Bandeja a 1366×768 | Sin medir en la actual; 4 en el prototipo 3.0 | 9 o más |
| Destinos del menú para la ventanilla | 7 u 8 | 5 o menos |
| Formas de confirmar un acto delicado | 6 | 1 componente con 4 niveles |
| Páginas públicas con prueba automática de accesibilidad | 0 | Todas |
| Textos con códigos internos en pantalla | Unos 22 | 0 |

**Prueba con usuarios.** Dos sesiones de una hora en una instalación de pruebas: las 3 personas de ventanilla y 5 funcionarios. Cinco tareas por persona:

1. radicar una entrada en papel con anexos e imprimir la etiqueta;
2. encontrar lo que vence esta semana;
3. trasladar a otra dependencia;
4. responder y enviar por correo;
5. pedir información faltante al peticionario.

Meta: 5 de 5 tareas sin ayuda.

## 9. Decisiones pendientes

**De desarrollo:**

- [ ] ¿Quién hace el frontend de las brechas? El plan de backend lo deja fuera.
- [ ] ¿Radicar y Bandeja pasan al proxy único cuando se partan, o después del arranque?
- [ ] ¿La migración a Svelte 5 va antes o después de la fase 2?
- [ ] ¿El semáforo lo devuelve el servidor en cada radicado y paso, como pide el ADR-057 §4?
- [ ] ¿La consulta pública puede aceptar el número de radicado más un dato, en lugar del código de 32 caracteres?
- [ ] ¿Con qué se valida el PDF/A sin veraPDF?
- [ ] ¿Se conservan Space Grotesk y Public Sans, o se cambia a Geist como en proboto.co?

**De la alcaldía:**

- [ ] Umbrales y nombres de los cuatro colores del semáforo (SG-GD-PRO-02 dice 5 y 3 días).
- [ ] Tamaño de la etiqueta, modelo de impresora y si se pega antes o después de escanear.
- [ ] Campos de la plantilla de metadatos de entrada.
- [ ] Escudo, nombre oficial y color institucional.
- [ ] Texto de la constancia de radicación y de la autorización de datos, revisado por Jurídica.
- [ ] Si arranca con la exigencia de certificado para reservados o sin ella.
- [ ] Personas para las dos sesiones de prueba.

## 10. Aprobación

| Rol | Nombre | Decisión | Fecha |
|---|---|---|---|
| Desarrollo | | | |
| Proboto | | | |
| Alcaldía de Silvania (Secretaría de Gobierno) | | | |
