# ADR-057: La interfaz se ordena por la tarea de cada perfil, empezando por la ventanilla

**Estado**: Propuesto
**Fecha**: 2026-11-18
**Autores**: Proboto (implementación en la Alcaldía de Silvania)
**Relacionados**: [ADR-011](ADR-011-frontend-sveltekit-monorepo.md) (SvelteKit SSR; **se conserva**), [ADR-020](ADR-020-sistema-diseno-orpyca-asistente.md) (sistema de diseño; **se extiende**), [ADR-047](ADR-047-un-proceso-y-cuatro-contenedores.md), [ADR-048](ADR-048-identidad-emitida-por-docuboto.md), [ADR-051](ADR-051-ppi-reducido-vive-en-la-app.md), [ADR-054](ADR-054-la-firma-dice-la-verdad.md), [ADR-055](ADR-055-publicacion-publica-de-instrumentos.md), [ADR-056](ADR-056-un-solo-proxy-bff.md)
**Solicitud de cambio**: [CR-001](../cr/CR-001-interfaz-primer-corte-silvania.md)

---

## Contexto

Los ADR-047 a ADR-056 adelgazan la plataforma para una alcaldía pequeña y dejan la interfaz fuera de alcance. El ADR-056 lo dice: *«las pantallas grandes siguen grandes»*. El plan de backend de Silvania también la excluye (`docs/es/plan-brechas-silvania.md:12`). La primera instalación del rediseño es la Alcaldía de Silvania (Cundinamarca), y la interfaz es lo que sus funcionarios van a juzgar.

### Quién la usa

| Perfil | Personas | Tarea diaria |
|---|---:|---|
| Ventanilla única | 3 | Radicar todo lo que entra. El 100 % de la entrada llega en papel; el día pico fue de 62 entradas |
| Funcionario que tramita | ~70 | Atender su bandeja, trasladar y proyectar respuestas |
| Jefe que firma | ~20 | Dar visto bueno, firmar y vigilar vencimientos de su dependencia |
| Ciudadano | — | Radicar una PQRSD por la web y consultar su trámite |

Hoy trabajan con un libro de Excel y 15 hojas por oficina. El 60 % de las respuestas (unas 3.560 al año) sale por correo electrónico. Dos inspecciones rurales (Subía y Agua Bonita) radican con el mismo consecutivo por internet. La norma interna con la que la alcaldía va a medir el sistema es el procedimiento **SG-GD-PRO-02** (Ventanilla Única de Correspondencia), que exige etiqueta de radicado y constancia impresa, un semáforo de cuatro colores y radicación de contingencia.

### Lo que encontramos en la interfaz

Revisión sobre `main` @ `15be6dc` (17 de noviembre de 2026), leyendo el código; no se ejecutó la aplicación.

**Está organizada por objeto técnico, no por tarea:**

- **Radicar una entrada en papel** exige 6 pasos (`routes/(app)/radicar/+page.svelte:84-91`). El primero pregunta *«¿Empiezas con un documento o el formulario?»* aunque la lectura del documento depende de un proveedor de inteligencia que Silvania no tiene. Al terminar, la pantalla solo ofrece «Ir a la bandeja» y «Radicar otro» (`:1065-1069`): **no se imprime etiqueta ni constancia**.
- **Responder una petición** recorre cuatro destinos sin enlaces entre sí: Bandeja, Borradores, Firmas y Envíos. «Responder» en la bandeja radica una salida desde un modal cuyos campos son todos opcionales (`bandeja/+page.svelte:359`). Envíos solo conoce operadores postales y pide teclear el radicado (`envios/+page.svelte:449-494`).
- **El semáforo** tiene cuatro etiquetas y tres colores: «Urgente» y «Vencido» comparten el mismo (`lib/components/workflow/WorkflowCard.svelte:85-91`). Solo aparece en el Tablero, y el panel principal cuenta días calendario (`dashboard/+page.server.js:217-219`). SG-GD-PRO-02 pide cuatro colores en días hábiles.
- **La navegación** es una lista plana de 20 entradas, filtrada por permiso (`lib/utils/navItems.js`).

**Habla del sistema por dentro:**

- Códigos de requisito y jerga en Radicar: «RF-SEG-08» (`:1334`), «RF-RAD-04» (`:1441`), «checksum SHA-256» (`:1530`), «para este tenant» (`:1650`).
- Un campo «Detalles adicionales — JSON» en el modal de trámite (`TransaccionModal.svelte:224`).
- `mensajeError` devuelve el texto o el código del servidor tal cual cuando no lo conoce (`lib/utils/apiError.js:458`, `:465`).
- Tú y usted mezclados: unos 51 marcadores de tuteo frente a unos 220 de usted.

**La puerta de entrada no es de la alcaldía:**

- La portada pública es la página comercial de Orpyca, con 10 menciones de la marca (`routes/(public)/+page.svelte`).
- El inicio de sesión dice «OrpycaMCP» y «Redirigiendo al proveedor de identidad» (`routes/(public)/login/+page.svelte:59`, `:74`). El ADR-048 retira Keycloak, y con él ese flujo.

**No cumple lo mínimo de accesibilidad en lo público.** La Resolución 1519 de 2020 de MinTIC exige WCAG 2.1 AA a las alcaldías desde 2022.

- La ventanilla web no marca ningún campo obligatorio (cero `required` en `routes/(public)/radicacion/+page.svelte`) y da un solo error al final.
- El borde de los campos mide 1,22:1 sobre blanco (`--op-border: #e7eae2`, `styles/_tokens.scss:49`); el mínimo es 3:1.
- El código de consulta del ciudadano tiene 32 caracteres y no se puede imprimir ni copiar (`:352-364`).
- Ninguna página pública tiene prueba automática de accesibilidad.

**Confirmar un acto delicado tiene seis gramáticas:** casilla, teclear el código del lote, teclear el número de radicado, frase fija, motivo con mínimo de 5, 10 o 20 caracteres, y solo botón. Una de ellas miente: `RevertirModal.svelte:136` dice «no se puede deshacer» sobre un acto que se compensa con otro traslado.

**Las páginas del trabajo diario son las más grandes:** Bandeja 3.470 líneas, Radicar 2.556 y Borradores 2.032, sobre un total de 56 páginas y 50.619 líneas.

### Lo que propone el rediseño de interfaz 3.0

La rama `docs/rediseno-ui` (`aa44976`, 3 de noviembre de 2026, sin fusionar) trae una especificación y un prototipo navegable:

- navegación de 4 destinos de trabajo y 3 de gobierno;
- barra ⌘K para preguntar, buscar y hacer;
- panel fijo del agente con tarjetas de sugerencia;
- «Capacidades» con modos Esencial, Escala y Regulado;
- un modelo de reversibilidad en tres clases;
- una capa de tokens `--db-*`.

Acierta en la estructura, pero es anterior a los ADR-047 a 056 y gira alrededor del agente, que Silvania no usa en el primer corte. Lo medimos en Chrome a 1366×768:

- Con el panel del agente abierto quedan 738 px para trabajar y la Bandeja muestra 4 filas.
- La capa `tokens.css` deja 35 de los 84 tokens `--op-*` sin alias.
- axe-core reporta fallos críticos en las 12 pantallas revisadas.
- No diseña nada de lo que pide SG-GD-PRO-02.

## Decisión

### 1. La interfaz se ordena por perfil y por tarea

Cada perfil entra a la pantalla de su tarea, y esa pantalla resuelve la tarea completa sin obligar a saber a qué otra ir:

| Perfil | Pantalla de inicio | La tarea termina en la misma pantalla |
|---|---|---|
| Ventanilla | Radicar | Radicado, etiqueta y constancia impresas |
| Funcionario | Bandeja, ordenada por vencimiento | Trasladar, responder o finalizar |
| Jefe | Inicio, con el bloque «Por decidir» | Visto bueno, firma y vencidos de su dependencia |
| Ciudadano | Portada de la entidad | Radicar o consultar en un clic |

La pantalla de inicio se deriva de los permisos que ya existen; no hay rol nuevo. La navegación se agrupa (Trabajo, Archivo, Administración) **sin cambiar las direcciones**: los favoritos, los enlaces de los correos y las pruebas e2e siguen sirviendo.

### 2. Del 3.0 se adopta la estructura, no el agente

| Pieza del 3.0 | Decisión |
|---|---|
| Modelo de reversibilidad y diálogo único de confirmación | Se adopta (§6) |
| Alertas agrupadas por tiempo | Se adopta como base del semáforo (§4) |
| Capa de tokens | Se adopta **completa**: los 35 alias que faltan, nombres reconciliados con los `--db-*` de `main` y Bulma sincronizado |
| Navegación agrupada | Se adapta: sin cambio de direcciones y con «Radicar» como destino propio |
| Bandeja maestro–detalle | Se adapta: filas de 56 px, sin panel lateral fijo |
| Inicio («Escritorio») | Se adapta: el titular y los indicadores salen de datos, no de un agente |
| Barra de comandos | Se pospone; cuando llegue, solo Buscar y Hacer, con `Ctrl+K` |
| Panel del agente, sugerencias, «Inteligencia» | Se pospone mientras la entidad no tenga proveedor (ADR-051) |
| «Capacidades» y modos | Se pospone; es configuración del operador, no de la alcaldía |
| Tema oscuro | Se pospone |

Ninguna pantalla depende de la capa de inteligencia para estar completa. Con `PPI_PROVIDER_URL` vacío no queda hueco, ni color cian, ni una opción que no se pueda usar. En particular, Radicar no ofrece «empezar por un documento».

### 3. Radicar es una pantalla, no un asistente

Para la ventanilla, una entrada se radica en una sola pantalla con cuatro bloques: Remitente, Documento, Metadatos del tipo y Destino. A la derecha van la carga del escaneo y el resumen.

- El canal por defecto es «Presencial», la dependencia destino es obligatoria y el remitente se busca mientras se escribe.
- Los campos de la plantilla de metadatos del tipo se pintan en la misma pantalla. Si el tipo no tiene plantilla y el servidor responde `422 metadatos_no_declarados` (ADR-055 §4), la pantalla lo dice en lenguaje claro y nombra a quién avisar.
- `Ctrl+Enter` radica. Al radicar, la misma pantalla muestra el número a 32 px y ofrece «Imprimir etiqueta», «Imprimir constancia» y «Radicar otro», con el foco en este último. «Radicar otro» conserva el tipo y el modo.
- La etiqueta y la constancia tienen hoja de impresión propia, con el escudo y el nombre de la entidad. La etiqueta lleva QR hacia la consulta pública. Se reimprimen desde el detalle del radicado.
- «Radicar en contingencia» es un interruptor del mismo formulario, con permiso propio. No es un destino nuevo del menú.
- El formulario se guarda solo en el navegador de quien radica, para que recargar o perder la sesión no borre lo escrito.

Para salidas e internas se conserva el recorrido por pasos actual, reducido a los pasos que aplican.

### 4. Hay un solo semáforo y lo calcula el servidor

El color de un plazo no se calcula en el navegador. El servidor devuelve el estado del semáforo con cada radicado y paso, en **días hábiles** con el calendario de la entidad. Los umbrales los fija la entidad:

| Estado | Color | Regla por defecto (SG-GD-PRO-02) |
|---|---|---|
| A tiempo | Verde | Más de 5 días hábiles |
| Por vencer | Amarillo | 5 días hábiles o menos |
| Próximo a vencer | Naranja | 3 días hábiles o menos |
| Vencido | Rojo | Pasó la fecha |
| Plazo suspendido | Sin color | Requerimiento abierto (Ley 1755, art. 17) |
| Sin plazo | Sin color | Tipo documental sin término |

Un solo componente (`SemaforoChip`) lo pinta igual en Bandeja, Tablero, Inicio y la pantalla de éxito de Radicar, siempre con color, icono y texto, por ejemplo «Por vencer · quedan 4 días hábiles».

### 5. Responder es un recorrido, no cuatro pantallas

«Responder» desde una entrada abre un **borrador ligado a ella**, con el asunto y el destinatario precargados. Ya no radica una salida vacía. El detalle del radicado muestra una línea de estado (Borrador, Visto bueno, Firma, Envío) donde cada paso enlaza al siguiente.

Al terminar la firma se ofrece el envío. Si el destinatario tiene correo, lo primero es «Enviar a {correo}». El envío queda **«Enviado» y no «Entregado»**, porque que el servidor de correo lo acepte no prueba que el ciudadano lo recibió (misma regla del ADR-054). Envíos sigue existiendo como registro y para lo postal, pero no es una pantalla que haya que visitar para cerrar una petición.

«Pedir información faltante» vive junto a «Responder». Pone el plazo en pausa y muestra la fecha en que vence el mes para el desistimiento. El sistema avisa, pero no finaliza solo.

### 6. Los actos delicados tienen una sola gramática

Cada acción pertenece a una de tres clases, y la interfaz solo usa el nombre de su clase:

| Clase | Nombre en pantalla | Ejemplos |
|---|---|---|
| Se deshace | «Deshacer» | Asignar, marcar leído, corregir una etiqueta |
| Se corrige con otro registro | El verbo del acto compensatorio: «Devolver al paso anterior», «Levantar retención» | Trasladar, devolver, rechazar un visto bueno |
| Definitivo | Candado y «Definitivo», siempre con la salida legal: «si hay un error, solicite la anulación» | Radicar, firmar, finalizar, cerrar expediente, ejecutar eliminación |

Un componente `ConfirmDialog` con cuatro niveles reemplaza las seis mecánicas actuales:

1. Sin diálogo.
2. Motivo.
3. Motivo y consecuencia a la vista.
4. Teclear el identificador, solo para lo catastrófico: eliminación y reasignación masiva.

`RevertirModal` pasa al nivel 2 y deja de decir «no se puede deshacer». La barra de deshacer con cuenta regresiva del 3.0 se pospone. Las segundas personas que ya exige el servidor (anulación, desclasificación, eliminación) no cambian.

### 7. La interfaz habla como la alcaldía

- **Usted siempre**, para funcionarios y ciudadanos.
- **Un concepto, una palabra**, según un glosario versionado en el repositorio:
  - radicado (el registro con número);
  - documento (el archivo o el papel);
  - dependencia;
  - trasladar, devolver y reasignar (cada uno con su uso);
  - finalizar el trámite, frente a cerrar un expediente;
  - remitente, destinatario y peticionario;
  - visto bueno;
  - «vence el…» (la fecha) y plazo (los días hábiles);
  - folios;
  - nivel de acceso (la persona) y nivel de reserva (el documento).
- **Ningún código interno en pantalla**: ni permisos (`PERM_*`, `USUA_*`), ni requisitos (`RF-*`), ni ADR, ni «tenant», «JSON», «HTTP» o «servidor de…».
- **Los errores dicen qué pasó, si algo se guardó y qué hacer.** Un error que el catálogo no conoce muestra un mensaje genérico con una referencia, nunca el texto ni el código del servidor. Con el ADR-056 esa traducción vive en un solo sitio.
- **Los estados vacíos dicen el siguiente paso.**

### 8. La cara es la de la entidad

- La portada pública es la de la entidad, con dos acciones: «Radicar PQRSD» y «Consultar mi trámite».
- El inicio de sesión propio del ADR-048, las páginas públicas, la barra superior y todo lo impreso llevan el escudo y el nombre de la entidad. Se configuran sin recompilar.
- Docuboto queda como marca secundaria.
- La marca Orpyca sale de toda superficie visible. Los avisos de licencia y de autoría en el código y en `NOTICE` no se tocan.

### 9. El piso de accesibilidad y de formato es una regla, no una intención

- **WCAG 2.1 AA obligatorio en toda página pública** (Resolución 1519 de 2020), comprobado con axe en la CI junto con el formulario en error y el acuse.
- En la aplicación se mantiene la prueba axe de las vistas clave y se amplía a los diálogos abiertos.
- **Reflujo a 320 px** en lo público, en el inicio de sesión y en la consulta.
- **Borde de los controles de formulario de al menos 3:1** y foco con contorno de 2 px.
- **Ningún texto por debajo de 12 px.**
- **Resolución de referencia de la aplicación: 1366×768**, la de un computador de oficina. A esa resolución, la Bandeja muestra al menos 9 filas y Radicar se completa sin desplazar con una plantilla de metadatos de hasta cuatro campos.

### 10. Guardianes

Cada regla queda con una prueba que falla si se rompe:

| Regla | Guardián |
|---|---|
| Sin códigos internos ni jerga en texto visible | Lista de patrones prohibidos sobre los `.svelte` |
| Sin tuteo | Lista de marcadores de tú sobre los `.svelte` |
| `mensajeError` nunca devuelve un código crudo | Prueba unitaria de `apiError.js` |
| El semáforo tiene una sola fuente | Ningún mapa de colores de semáforo fuera de `SemaforoChip` |
| Confirmación única | Ninguna casilla de «Entiendo…» fuera de `ConfirmDialog` |
| Pestañas y avisos únicos | `role="tablist"` solo dentro de `ui/Tabs.svelte` |
| Contraste de controles | Razón de `--op-control-border` frente al fondo ≥ 3:1 |
| Piso tipográfico | Ningún token de tamaño de letra por debajo de 12 px |
| Páginas públicas accesibles | `e2e/axe-publicas.spec.js` sin fallos serios ni críticos |
| Ninguna página vuelve a crecer sin control | Tope de 1.500 líneas por `+page.svelte` para las páginas que se partan |

### 11. Orden respecto al resto del rediseño

1. **Lo que no depende de nadie va primero:** textos, accesibilidad pública, la gramática de confirmación y la capa de tokens completa.
2. **El inicio de sesión propio va con el ADR-048.** Se diseña con él y no después.
3. **Radicar y Bandeja se parten en componentes una sola vez.** Cuando se partan, pasan también al proxy único del ADR-056, para no reescribir su carga de datos dos veces. Si el proxy no está listo, se parten sin él y el proxy los toma después.
4. **La migración a Svelte 5**, que exige el arreglo de `vite` según `CHANGELOG.md`, no se mezcla con este trabajo. Se decide si va antes o después, no en medio.

El detalle de cada cambio, su criterio de aceptación y su dependencia de backend están en [CR-001](../cr/CR-001-interfaz-primer-corte-silvania.md).

## Alternativas consideradas

- **Implementar el 3.0 completo.**
  - Descartada para el primer corte. Su centro es el agente, que Silvania no tiene y que el ADR-051 reduce.
  - A 1366×768 deja 4 filas en la Bandeja.
  - No diseña la etiqueta, la contingencia, el envío por correo ni el inicio de sesión propio.
  - Sus pantallas fallan axe en nivel crítico.
  - Lo que sí sirve se adopta (§2).
- **Agregar las brechas de Silvania sobre la interfaz actual sin reordenarla.** Descartada. Añadir la etiqueta, el semáforo y la contingencia a una página de 2.556 líneas con 6 pasos deja a la ventanilla igual de lenta, y suma tres funciones más a la página que nadie puede revisar.
- **Radicar en tres pasos, como el prototipo 3.0.** Descartada para la entrada en papel. Con 62 radicados en un día, cada «Continuar» son 124 clics. Los pasos se conservan para salidas e internas, que no tienen ese volumen.
- **Calcular el semáforo en el navegador.** Descartada. El color tiene que coincidir con el informe de oportunidad y con el resumen semanal por dependencia, que se calculan en el servidor. Dos cálculos divergen; hoy ya divergen entre el Tablero y el panel principal.
- **Reescribir el frontend en Svelte 5 antes de tocar las pantallas.** Descartada como precondición. Retrasa todo lo que la alcaldía ve, para un cambio que la alcaldía no ve. Se decide aparte (§11).
- **Mantener la marca Orpyca en la portada como crédito.** Descartada. El crédito y la licencia viven en `NOTICE` y en los encabezados del código; la portada de una alcaldía no es un lugar de crédito.

## Consecuencias

- **Lo que la alcaldía nota:**
  - Una entrada en papel se radica en una pantalla y sale con su etiqueta y su constancia.
  - Una petición se responde y se envía sin saber de memoria a qué pantalla ir.
  - El color del plazo es el mismo en todas partes y coincide con su procedimiento.
- **Es trabajo de frontend con dependencias de backend.** La etiqueta, la contingencia, el semáforo en días hábiles, el envío por correo, la suspensión del término y el inicio de sesión propio necesitan las piezas del plan de brechas. Sin ellas, la pantalla se puede construir pero no cerrar.
- **El 3.0 no se descarta; se secuencia.** Su capa de tokens, su gramática de actos y su navegación entran ahora. El agente, la barra de comandos y «Capacidades» esperan a una entidad que los necesite.
- **Aparecen diez guardianes nuevos.** Algunos van a fallar el primer día sobre código existente y se activan con lista de excepciones que se vacía por fases.
- **Cambian textos en muchas pantallas.** Las pruebas que buscan textos exactos se actualizan con ellas, y la prueba de mensajes en español sigue siendo la red.
- **Primera vez que el proyecto fija una resolución y un piso tipográfico.** Una pantalla que no cabe a 1366×768 deja de ser un detalle visual y pasa a ser un defecto.
- **Quedan fuera del primer corte** la barra de comandos, el deshacer con cuenta regresiva, las pantallas nuevas de expedientes y archivo físico, y las páginas públicas de instrumentos del ADR-055. Siguen su propio orden.

## Preguntas abiertas

1. **Umbrales y nombres del semáforo.** Los de la tabla del §4 salen de SG-GD-PRO-02. La alcaldía debe confirmarlos, y confirmar si «Próximo a vencer» es el nombre que usan para el naranja.
2. **Etiqueta.** Tamaño, impresora y si se pega antes o después de escanear. Cambia el orden de los bloques de Radicar.
3. **Plantillas de metadatos de entrada.** ¿Caben en cuatro campos (medio de recepción, folios, anexos, guía)? Si no, Radicar necesita desplazar a 1366×768.
4. **Exigencia de certificado para reservados.** Desde el ADR-054 nace en `clasificados`. La entidad decide si Silvania arranca así, o en `off` con constancia, mientras llegan los certificados. La interfaz debe mostrar qué falta cuando bloquea.
5. **Orden con el ADR-056 y con Svelte 5** (§11).
6. **Validación de PDF/A** sin veraPDF (ADR-050). La pantalla de carga debe decir algo; qué dice depende de esa decisión.
