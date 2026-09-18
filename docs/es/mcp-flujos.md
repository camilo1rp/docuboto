# Clasificación del API por flujos de trabajo (para las herramientas del MCP)

## Por qué esta clasificación, y por qué por FLUJOS

El `mcp-server` traduce el API a herramientas que un modelo puede usar. La
pregunta «¿qué herramientas faltan?» no se responde bien mirando el mapa de
servicios: ese mapa está ordenado por **quién sirve** cada endpoint, y lo que
hay que decidir es **qué necesita alguien que está haciendo un trabajo**. Un
funcionario de ventanilla no piensa en *document-service*; piensa en radicar,
repartir y contestar, y en ese trámite tocan cuatro servicios distintos.

De ahí que el criterio de agrupación sea el flujo de trabajo. Y de ahí también
que el resultado no sea solo una lista de herramientas nuevas, sino **qué
procedimientos** hacen falta: dentro de un flujo, los actos van en un orden que
lo fija el dominio, no la comodidad.

## Método: contar, no leer

El censo se hace cruzando dos fuentes **mecánicamente**, nunca a ojo:

1. el OpenAPI agregado del gateway (`GET /api/v1/openapi.json`), normalizando el
   doble prefijo que introduce el agregador (`/api/v1/archive/api/v1/…`);
2. el catálogo declarativo del MCP (`services/mcp-server/app/tools/catalog.py`),
   con las plantillas de ruta normalizadas (`{id}` → `{x}`).

Un recuento a mano sobre este volumen se equivoca, y peor: se equivoca de forma
tranquilizadora. Ya ocurrió en este proyecto con un catálogo de 1.135 municipios
verificado contra cuatro casos conocidos.

**Límite declarado del censo**: el agregador del gateway compone la documentación
de **siete** de los once servicios. `knowledge-service`, `signature-service` y el
propio `mcp-server` no aparecen, así que **367 es un suelo, no el total**. Se
dice aquí en vez de dejar que alguien lo descubra buscando una herramienta que
el censo nunca vio.

## El reparto

| Flujo | Operaciones | Herramientas | Cobertura |
|---|---:|---:|---:|
| **SISTEMA** (sellado, no expuesto) | 63 | 0 | — *por diseño* |
| **F1** Ventanilla y radicación | 37 | 23 | 62 % |
| **F2** Trámite y reparto | 28 | 15 | 54 % |
| **F3** Respuesta y despacho | 21 | 9 | 43 % |
| **F4** Expediente | 31 | 21 | 68 % |
| **F5** Archivo físico | 14 | 11 | 79 % |
| **F6** Transferencias | 11 | 9 | 82 % |
| **F7** Disposición y eliminación | 11 | 10 | 91 % |
| **F8** Preservación y firma | 28 | 11 | 39 % |
| **F9** Instrumentos archivísticos | 69 | 38 | 55 % |
| **F10** Correo institucional | 14 | 8 | 57 % |
| **F11** Consulta y reportes | 9 | 5 | 56 % |
| **F12** Administración y seguridad | 59 | 26 | 44 % |
| **F13** Notificaciones e integración | 10 | 7 | 70 % |
| **TOTAL** | **405** | **193** | |

> **Esta tabla cambió el 2026-09-17, y varios números EMPEORARON porque se
> volvieron ciertos.** El censo se alimentaba del OpenAPI agregado del gateway,
> que compone la documentación de siete de los once servicios: knowledge,
> signature y el propio mcp-server no estaban. No era que el total saliera corto;
> es que la cobertura se publicaba como porcentaje sobre un universo que no los
> contenía. F8 decía 55 % sin haber contado ni una de las 21 operaciones del
> circuito de firma. Ahora el censo lee el esquema de **cada servicio**
> (`scripts/censo-esquemas.sh`) y el que no responda se dice en voz alta en vez
> de saltarse.

## Qué queda FUERA, y por qué

Un censo que contara los 367 como deuda mentiría: hay operaciones que **no deben
ser herramientas**, y decir cuáles es parte del trabajo.

- **`/internal/*`** — endpoints sellados con `X-Internal-Token` para tráfico
  servicio-a-servicio. Exponerlos como herramienta sería abrir por la puerta del
  asistente lo que se cerró en la del gateway.
- **`/api/v1/tenants/*`** — deliberadamente **no enrutado** en el gateway: el
  registro de entidades vive en el schema `public` y cualquier usuario
  autenticado de una entidad podría leer y escribir el de otra. El alta de
  entidades es una operación de plataforma (`scripts/init_tenant.py`), no de
  aplicación.
- **`/api/v1/public/*`** — la ventanilla ciudadana y la consulta por código de
  verificación. Son endpoints **sin sesión**, pensados para el ciudadano; el MCP
  actúa siempre en nombre de un funcionario autenticado.
- **`/health*`** — sondas del balanceador.
- **Descargas de binarios** — un anexo se descarga por su ruta mediada, no se
  devuelve como resultado de una herramienta.
- **`/api/v1/storage/*` salvo `upload`** — el gateway **no las enruta** («SOLO
  /upload se expone», `proxy.py`). Contarlas como cobertura pendiente inflaba el
  denominador con operaciones que no son alcanzables ni deben serlo; y `upload`
  tampoco es una herramienta, porque subir un fichero por un chat exigiría que el
  modelo aportara los bytes.
- **Los gemelos `…-internal`** — disparos de sistema sellados con
  `X-Internal-Token` (D-02). Dejarlos en su flujo hacía que un servicio bien
  cerrado pareciera un hueco.

## Flujo por flujo

### F1 · Ventanilla y radicación — 70 %

El trámite de entrada: recibir, identificar al ciudadano, radicar, clasificar y,
si procede, anular o publicar.

**Cubierto**: radicar, consultar (por UUID y por número), listar, actualizar,
tipificar, anexos, respuestas, disposición, catálogo de terceros con la cascada
DIVIPOLA, actores congelados del envío, reclasificación motivada, publicación de
oficio y el circuito de anulación completo (solicitar · consultar · cola ·
aprobar · rechazar).

**Pendiente**: firma electrónica del radicado (va con F8), exportación e
importación de paquetes interoperables, y la carga masiva —que es un trabajo con
seguimiento de `job`, no una llamada—.

**Nota de dominio**: sobre una Entrada la anulación **no existe como acto** y el
sistema responde `409`, no `403`. No es una restricción de permisos: no se puede
negar haber recibido algo.

### F2 · Trámite y reparto — 54 %

**Cubierto**: bandeja, pendientes, asignar, trasladar, devolver con causal,
completar paso, ejecutar transacción, revertir la última asignación, cadena de
vistos buenos (crear · consultar · decidir), paso actual, hoja de ruta y eventos.

**Pendiente**: reasignación en cascada (acto de administración), barrido de
vencidos (disparo de sistema) y el motor de reglas (configuración, no trámite).

**Detalle que importa**: `decidir_visto_bueno` corresponde a un endpoint **sin
gate de permiso**, porque la autorización sale de la identidad —ser el revisor
de turno—. El catálogo declara `PERM_VOBO` marcándolo como permiso resuelto en
tiempo de ejecución: una escritura sin permiso declarado sería visible para toda
la entidad.

### F3 · Respuesta y despacho — 60 %

**Cubierto**: plantillas, borradores (crear · editar · consultar · aprobar ·
radicar), respuesta vinculada, bandeja de envíos, despacho postal, actualización
manual del estado y envíos por radicado.

**Pendiente**: consulta de rastreo contra el operador (hoy es un *stub*
declarado, no un defecto), alta y baja de plantillas (administración) y el FUID
(pertenece a F5/F6).

### F4 · Expediente — 68 %

**Cubierto**: crear, listar, buscar, consultar, actualizar, vincular, excluir con
causal, cerrar, foliado, índice (JSON y XML), verificación de integridad,
versiones, firma del índice, publicación de oficio, hoja de ruta, historial de
acceso, grupos autorizados y ubicación física.

**Pendiente**: exportar el ZIP y el AIP de preservación (son descargas de
artefactos, no consultas), la inclusión masiva —que tiene seguimiento de trabajo—
y la transferencia del expediente, que pertenece a F6.

**Un acto que merece su nombre**: excluir un radicado **no lo borra**. El índice
conserva su lugar como hueco trazable con la causal a la vista; un expediente del
que desaparecen documentos sin dejar rastro no sirve como prueba.

### F5 · Archivo físico — 79 % · F6 · Transferencias — 82 %

Eran los dos flujos que **no se podían llevar desde el MCP**, y eran los
siguientes por valor: un archivista no puede trabajar con el asistente si no
puede ubicar una caja ni cerrar una transferencia.

**F5 cubierto**: ubicaciones (listar, crear, consultar), unidades de conservación
(listar, crear, contenido, movimientos), colocación de expedientes con su rango
de folios, y préstamos (registrar y devolver). Falta el `PATCH` de una ubicación
y el listado de préstamos de una unidad concreta.

**F6 cubierto**: el ciclo de custodia entero — crear, enviar, recibir, rechazar,
consultar, el acta congelada y su verificación de firmas. Falta la firma personal
del acta, que exige un segundo factor (TOTP) y no debe pedirse por un chat.

**Los tres actos que cambian la custodia** —enviar, recibir, rechazar— se declaran
irreversibles: cambian quién responde del acervo, y eso no se «deshace».

### F7 · Disposición y eliminación — 91 % (10 de 11)

El único procedimiento del sistema que termina **destruyendo documentos de
archivo**, en cinco fases: proponer → publicar → avalar → acta firmada →
ejecutar. Se ofrecen las cuatro primeras. **La quinta no.**

#### Qué se debe confirmar: una escalera de tres escalones y un límite

El gate tenía un solo escalón —la frase fija `CONFIRMO` para todo lo
irreversible— y con la eliminación en el catálogo deja de bastar. No por
severidad: por **objeto**. Una frase fija se puede teclear de memoria antes de
leer nada, así que confirma «un acto», no «este acto».

| Escalón | Qué se pide | Para qué actos |
|---|---|---|
| **1** | aprobar, sin teclear | escrituras reversibles: proponer un lote, crear una caja, vincular |
| **2** | la frase fija `CONFIRMO` | irreversible pero sin pérdida: radicar, reclasificar, publicar el inventario, enviar o recibir una transferencia |
| **3** | **el identificador del objeto, tomado de la tarjeta** | lo que habilita una destrucción: avalar un lote de eliminación |
| **—** | no se ofrece | ejecutar el borrado |

El tercer escalón es el que importa: reproducir el identificador del lote **no se
puede anticipar**, hay que haber mirado la tarjeta. Y lo que no hace, dicho en
voz alta: no convierte a un modelo en una persona. Encarece el descuido; no lo
impide.

De ahí el límite. `ejecutar` es el único camino del sistema que borra documentos
de archivo, y su garantía humana no puede depender de que un cliente ajeno haya
preguntado a alguien. No se ofrece por este canal, y **la ausencia se declara**
en el marco de alcance con su motivo y su vía: dejar el lote avalado y con su
acta firmada, y ejecutar desde la interfaz. Un catálogo recortado en silencio
hace que el límite se descubra pidiendo algo que nunca va a ocurrir.

#### La fricción va repartida al revés de como suele hacerse

**Retirar un lote del procedimiento —el acto que PARA la destrucción— es el más
barato de todo F7**: se aprueba con un clic. Avalar, que la habilita, es el más
caro. Si parar costara lo mismo que seguir, la fricción empujaría hacia
adelante, que es justo lo contrario de lo que se busca.

#### La tarjeta resume, no vuelca

Un lote lleva los identificadores de los expedientes que va a destruir. Volcar
cuarenta UUID en la tarjeta no informa: **tapa**. Las listas se muestran por su
tamaño —«12 elementos»— y el detalle sigue viajando dentro de la propuesta
firmada. Lo que cambia es qué se le pone delante a quien decide.

**Lo que falta de F7**: solo `ejecutar`, y por decisión.

### F8 · Preservación y firma — 39 % (11 de 28)

> Dos de las herramientas de este flujo —el AIP empaquetado y los eventos PREMIS
> de un expediente— apuntan a las rutas **mediadas** de archive-service, porque
> las de storage van selladas con `X-Internal-Token`. El censo las cuenta por su
> ruta, así que aparecen en F4; conceptualmente pertenecen aquí. Se dice para que
> el 55 % no se lea como un hueco que no existe.

**Cubierto**: el plan de preservación (consultar, versiones y declarar una nueva
—incluida la política de obsolescencia que consume el barrido de migración—), el
AIP ya empaquetado, los eventos PREMIS de un objeto, el registro de un evento, la
migración a PDF/A y la cadena de procedencia de un archivo. Más la observabilidad
del sellado: qué índices quedaron pendientes de firma y qué artefactos están por
vencer su retención.

#### Sellar en WORM: el escalón es «no se ofrece», por una razón distinta

En F7 el motivo para no ofrecer `ejecutar` era la **garantía humana**. Aquí es
anterior y más simple: **el acto exige un identificador que el llamante no puede
conocer legítimamente**. `proteger-indice` y `proteger-artefacto` piden el
`file_id` del artefacto firmado, que solo compone archive-service en el contexto
del expediente — por eso **la propia interfaz tampoco los ofrece**.

Un modelo tendría que inventárselo. Y sellar el fichero equivocado bajo un
candado de diez años **no se deshace: la retención no se acorta**. La tarjeta de
confirmación no protege de eso: le enseñaría a la persona un UUID que no tiene
forma de contrastar, y una tarjeta que muestra un dato incontrastable no es un
control, es un trámite.

Lo mismo vale para el empaquetado manual del AIP, que además es un acto de
reconciliación sin decisión humana detrás.

**Lo que sí se ofrece es enterarse**: el sellado ocurre solo al cerrar el
expediente, y lo que un operador necesita es ver qué quedó a medias y reintentar
la firma del índice. No ofrecer el acto no puede significar dejar a ciegas.

#### Dos distinciones que evitan gastar fricción donde no toca

- **Migrar formato NO es irreversible**: crea un derivado y **el original se
  conserva intacto** (Decreto 2609/2012 art. 32). Se aprueba sin teclear nada.
- **Anotar en el registro PREMIS sí lo es**, aunque no destruya: el registro es
  append-only, así que lo irreversible es **la afirmación**. Una intervención mal
  descrita queda dicha para siempre → frase fija.
- **Declarar el plan de preservación tampoco**: es versionado append-only, la
  corrección es declarar otra versión y la anterior sigue ahí.

#### El circuito de firma: se lee y se prepara, no se ejerce

Hasta el 2026-09-17 este flujo no contaba **ninguna** de las 21 operaciones del
servicio de firma, porque el censo no las veía. Al verlas, resultó que el
circuito entero —trece operaciones de cadena— no tenía una sola herramienta: el
asistente no podía contestar «¿qué tengo pendiente de firmar?», que es la
pregunta más frecuente que hay sobre él.

**Lo que ahora se ofrece** son las cinco de leer y preparar: los pendientes del
propio turno, el estado de una cadena con sus turnos en orden, si un documento ya
está en circuito, el comprobante de una firma, y **abrir** un circuito — que no
firma nada ni compromete a nadie, porque cada turno lo decide su firmante y puede
rechazarlo.

**Lo que no se ofrece, y por qué son dos razones distintas**:

- **Firmar un turno** consume un segundo factor de un solo uso —código por correo
  o de la app autenticadora—. Pedirlo por un chat lo traslada a un canal que no lo
  custodia. Es el mismo motivo, ya escrito, por el que no se ofrece firmar el acta
  de una transferencia.
- **Dar el visto bueno no consume ningún factor**, y aun así tampoco se ofrece.
  Lo que afirma es «he leído este texto y lo apruebo», queda anclado a la huella
  del documento, y este canal no puede poner el documento delante de nadie.
  Aprobar sin verlo es exactamente el defecto que el circuito existe para
  impedir. Rechazar decide sobre el documento igual que aprobar, en el otro
  sentido.
- **Comprobar la integridad de una firma** exige el XML firmado completo como
  entrada: por aquí habría que pegarlo o inventarlo, y una verificación sobre un
  documento inventado devuelve algo que parece una garantía sin serlo. Mismo
  criterio que el sellado WORM.

**Lo que falta de F8**: los tres actos de preservación declarados arriba, y del
circuito de firma, nada que se deba ofrecer — lo que queda fuera está declarado
en el marco de alcance, no omitido.

### F9 · Instrumentos archivísticos — 55 %

El bloque más grande (69 operaciones): TRD, TVD, tipos documentales, elementos de
metadatos y PINAR. La TRD es lo que fija cuánto se conserva cada cosa y qué se
hace con ella al final — el instrumento del que cuelga todo lo demás.

**Cubierto**: el circuito de versiones completo de TRD y TVD (crear · nueva
versión · aprobar · devolver · convalidar · derogar · registrar RUSD), el
historial de versiones, la disposición vencida y su decisión, los expedientes
pendientes de revisión tras un cambio de tabla, tipos documentales, elementos y
plantillas de metadatos, y la lectura del PINAR con el registro de avance.

#### La fricción va en el NÚMERO DE VERSIÓN

Convalidar y derogar cambian **qué tabla gobierna** la clasificación de aquí en
adelante. El error irreparable no es convalidar —eso es lo que toca— sino
convalidar **la versión equivocada**: no se corrige con otro acto, hay que abrir
una versión nueva y recorrer el circuito entero, con la tabla equivocada rigiendo
mientras tanto. Por eso ambos piden teclear el número de versión.

**Y los pasos que tienen vuelta atrás no piden nada.** El circuito ofrece
`devolver` para regresar de aprobada a borrador: poner ahí la fricción del acto
irreversible sería gastarla donde hay remedio. Es la misma regla que en F7 —parar
barato, consumar caro— aplicada a un circuito con marcha atrás propia.

#### PINAR: se lee y se anota el avance; no se formula

Se ofrecen el tablero, el mapa de ruta, los objetivos, los proyectos, la
priorización y el registro de avance. **Armar la estructura del plan no**: formular
un PINAR es un ejercicio de planeación con el Comité delante —qué aspectos
críticos pesan más, qué se hace primero—, no una secuencia de altas. Declarado en
el marco con su vía.

#### Dos herramientas que apuntaban a un endpoint sellado

`consultar_aip` y `eventos_preservacion` (de F8) apuntaban a las rutas de
storage, que exigen `X-Internal-Token` y solo alcanza el propio servicio: habrían
dado `401` a todo el mundo. Las buenas son las **mediadas** de archive-service,
donde el mediador resuelve el clearance del llamante contra el nivel vigente del
expediente. Lo cazó el guardián compartido al cruzar el permiso declarado con el
real — el mismo que antes estaba medio ciego.

### F10 · Correo institucional — 57 % (8 de 14)

Este flujo se parte en dos mitades que no se parecen en nada, y **el reparto es la
decisión**:

- **La bandeja es trabajo de ventanilla.** Un correo que entra por el buzón oficial es
  una comunicación oficial recibida (Ac. AGN 060/2001 art. 3): decidir si se radica,
  quién lo trabaja y a qué dependencia se reparte es exactamente lo que hace una unidad
  de correspondencia. Se ofrece entera — listar buzones, ver la bandeja y su resumen,
  sincronizar, reservar, liberar, radicar y descartar con motivo.
- **La configuración de los buzones no.** Registrar la aplicación OAuth de la entidad,
  dar de alta una cuenta o canjear una autorización es mover credenciales: el
  `client_secret` del proveedor, la contraseña del buzón, el código de autorización.
  Nada de eso puede viajar por un chat.

Tampoco se ofrecen los **buzones personales**: su contraseña no se guarda a propósito
—viaja en cada petición y muere con ella—, así que trabajarlos exigiría teclearla aquí.
Por eso `sincronizar_buzon` y `radicar_correo` **no declaran el parámetro `password`**
aunque la ruta lo admita; un buzón personal responde `password_requerida`, que es la
respuesta correcta y no un fallo.

La fricción está donde se consume algo: `radicar_correo` y `descartar_correo` piden
`CONFIRMO` —radicar consume un número inmutable y **sobre una Entrada la anulación no
existe**—, mientras que reservar, liberar y sincronizar no piden nada. Liberar es el
acto de arrepentirse: parar tiene que ser barato.

**No se pide teclear el identificador del correo.** Sería un UUID que la persona no
puede contrastar con nada — el mismo motivo por el que no se ofrece sellar un artefacto
en WORM. La tarjeta enseña el asunto y el remitente, que sí se contrastan.

> **Dos defectos que aparecieron al construir esto**, ambos del patrón «capacidad
> construida y no ofrecida»:
>
> 1. **El funcionario de ventanilla no podía llegar a su buzón.** La bandeja se gatea
>    con `PERM_RADI` a propósito, pero `GET /email/cuentas` —la única forma de saber qué
>    buzones existen y con qué identificador— exigía `USUA_PERM_ADMIN`, y la única
>    entrada en la interfaz era un botón dentro de `/admin/correo`. Aflojar ese gate no
>    afloja nada: **qué cuentas devuelve el listado no lo decide el permiso, lo decide
>    el `WHERE` del repositorio**, que reparte la personal a su dueño y la institucional
>    a los asignados — y que ya recibía `es_admin` como parámetro, es decir, estaba
>    escrito para servir a quien no lo es. Corregido, con entrada de menú y pantalla
>    índice `/correo`.
> 2. **Se radicaba con la dependencia y el tipo documental escritos a mano.** La
>    pantalla enviaba `dest_dept` (un nombre suelto) en vez de `dest_dept_code`, así que
>    un radicado nacido de un correo quedaba sin vínculo con el organigrama y no
>    aparecía al repartir por código; y un tipo documental tecleado no casa con ninguna
>    serie de la TRD, que es la que gobierna cuánto se conserva. `/radicar` siempre
>    ofreció selectores para los dos. Ahora esta pantalla también.

### F13 · Notificaciones e integración — 70 % (7 de 10)

Se ofrece **observar** y **desatascar**; no se ofrece **configurar**.

**Cubierto**: el historial de notificaciones y una notificación concreta —«¿le llegó el
aviso al ciudadano?» es la pregunta que aparece cuando alguien reclama—, los canales de
salida configurados, y la cola de eventos fallidos entera: listar, consultar, reintentar
y descartar con motivo.

**No se ofrece** dar de alta ni retirar un webhook: manda los eventos documentales de la
entidad a una URL de fuera, y abrir un canal de salida permanente hacia un tercero no se
decide en una conversación.

Los dos actos sobre la cola piden `CONFIRMO`, y en direcciones opuestas: reintentar
tiene efecto **externo y no transaccional** —se manda correo—, así que puede duplicar
avisos ya recibidos; descartar significa que lo que ese evento iba a producir no va a
ocurrir nunca. Se prefiere el riesgo de duplicar al de perder un aviso en silencio, y
por eso se dice en la descripción de la herramienta.

`POST /notifications/send` no cuenta como pendiente: va **sellado con token interno**
—lo llama signature-service para los códigos de segundo factor— y sin ese sello
cualquier token válido podría enviar correo con la identidad de la institución.

### F11 · Consulta y reportes — 40 %

**Cubierto**: búsqueda, reporte de radicación y las tres herramientas de
conocimiento (búsqueda semántica, antecedentes, redacción con citas).

**Pendiente**: los reportes de tiempos de respuesta e inventario.

### F12 · Administración y seguridad — 44 % (26 de 59)

Este flujo se resuelve casi entero **declarando lo que no se ofrece**, y las 26
herramientas son todas de lectura. No hay ni una escritura, y no es una omisión
por hacer.

**Cubierto** — lo que se pregunta mientras se tramita:

- **Plazos**: el vencimiento en días hábiles según el calendario de la entidad, y
  los festivos que lo explican. Contar días corridos da una fecha equivocada, y
  los términos de ley se cuentan así.
- **Estructura orgánica**: dependencias en lista, en árbol y por código —el
  código es el que piden las herramientas de reparto—.
- **Vocabulario**: los catálogos y sus valores admitidos.
- **Parámetros** de la entidad y su valor actual.
- **Identidad, en lectura**: usuarios, grupos, permisos con su nivel, miembros,
  clearance de un grupo, permisos efectivos de una persona y sus unidades de
  reparto. Responde «¿quién puede radicar?» sin poder cambiar la respuesta.
- **Estado de mis credenciales**: si el segundo factor está activo y si la llave
  de firma está vigente. Nunca el secreto: sirve para saber, *antes* de intentar
  una firma, si va a poder completarla.
- **Auditoría**: la traza de un objeto y la verificación de la cadena de hash.

**No se ofrece, por decisión** (declarado en `NO_OFRECIDAS`, con su vía
alternativa, para que nadie descubra el límite fallando):

| Acto | Por qué |
|---|---|
| Conceder o retirar permisos, cambiar clearance, crear personas y grupos | Es el acto que decide qué puede hacer todo el mundo, este asistente incluido. Si se pudiera ejecutar desde aquí, el marco de alcance dejaría de ser un límite: **una frontera alcanzable desde dentro no es una frontera** |
| Dar de alta o revocar credenciales personales (TOTP, códigos de recuperación, llave de firma) | El secreto se revela **exactamente una vez**, por diseño; entregarlo aquí lo dejaría escrito en el historial de una conversación. Y revocar el segundo factor de otra persona es el paso previo a suplantarla |
| Cambiar festivos o parámetros | Son la regla con la que se cuentan los términos de ley. Un festivo añadido hoy corre el vencimiento de todos los plazos que ya están andando, sin avisar y sin aparecer en la traza de ningún expediente |
| Añadir valores de catálogo o cambiar la estructura de dependencias | El valor de un catálogo cerrado es que sea cerrado: en cuanto se le añaden entradas al vuelo aparecen «Bogotá», «BOGOTA» y «Bogotá D.C.» como tres cosas distintas, y ninguna búsqueda posterior las reúne |

Las 33 operaciones restantes son escrituras de esos cuatro grupos, más la
mecánica de sesión (`token`, `refresh`, `logout`, `validate`), que no es un acto
de negocio: el servidor ya viaja con el token de quien pregunta.

La decisión no vive solo en esta página. `test_el_flujo_de_administracion_no_ofrece_ninguna_escritura`
recorre el catálogo por **prefijo de ruta**, no por lista de nombres, así que una
herramienta nueva sobre `/auth/`, `/config/`, `/catalogos` o `/dependencias` entra
en el guardián el día que se escribe.

## Los procedimientos que pide cada flujo

Un procedimiento encadena herramientas en el orden del dominio y se confirma una
sola vez. Los ocho que existen y los que faltan:

| Procedimiento | Flujos | Estado |
|---|---|---|
| `tramitar_peticion_ciudadana` (radicar → repartir → expedientar) | F1 + F2 + F4 | ✅ |
| `proyectar_y_radicar_respuesta` (redactar → aprobar → radicar) | F3 | ✅ |
| `conformar_expediente` (abrir → vincular) | F4 | ✅ |
| `estado_del_tramite` (5 consultas en una) | F1 + F2 | ✅ |
| `estado_del_expediente` (4 consultas en una) | F4 + F5 | ✅ |
| `ubicar_expediente` (crear unidad → guardar expediente) | F5 | ✅ |
| `preparar_transferencia` (crear → enviar) | F6 | ✅ |
| `atender_correo_del_buzon` (reservar → radicar) | F10 + F1 | ✅ |
| `despachar_respuesta` (responder → despachar por operador) | F3 | pendiente |
| `archivar_expediente` (foliar → cerrar → índice → firma) | F4 + F8 | pendiente |

**`atender_correo_del_buzon` son dos pasos y no tres**: si la radicación falla, la
reserva queda puesta y caduca sola. Soltarla automáticamente sería decidir por la
persona que ya no quiere ese correo, cuando lo normal tras un fallo es reintentar.

**`preparar_transferencia` no recibe**, y es deliberado: recibir es del archivo
receptor, y que una sola llamada hiciera las dos puntas convertiría una entrega
entre dos responsables en un trámite de uno solo.

Tres invariantes vigilan que un procedimiento no prometa lo que no hace: que cada
paso mapee **solo** argumentos que su tool declara —el error real fue escribir
`dest_dept`, que es el nombre del *endpoint*, donde la tool declara
`dest_dept_code`—, que aporte **todos** los obligatorios, y que **ningún
parámetro que se le pide a la persona quede sin usarse**. El tercero destapó que
`responder_radicado` pedía el radicado de origen y no lo vinculaba: su
descripción prometía un vínculo que el circuito de borradores no hace.

## Cómo se repite este censo

```bash
./scripts/censo-esquemas.sh                      # los ONCE servicios → /tmp/oa/
python3 scripts/censo_mcp.py --openapi /tmp/oa   # imprime la tabla de arriba
```

Con el agregado del gateway (`curl … /api/v1/openapi.json -o /tmp/oa.json`) sigue
funcionando, pero **ciego a tres servicios**: es como se produjo la tabla anterior
y por eso el circuito de firma no figuraba ni como hueco. Si el script avisa de
operaciones «SIN CLASIFICAR», eso no es ruido: es que apareció superficie nueva y
está pidiendo que alguien decida a qué flujo pertenece.

El guardián que impide que el catálogo mienta sobre los permisos vive en
`shared/tests/test_permisos_declarados.py`: cruza cada herramienta con el
`require_permission` real de su endpoint —del decorador **y del router**— y falla
en los dos sentidos, porque declarar de menos ofrece un acto que devolverá `403`
y declarar de más lo esconde a quien sí puede ejecutarlo.
