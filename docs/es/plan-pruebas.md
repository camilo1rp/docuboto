# Plan de pruebas funcionales — OrpycaMCP

> **Qué es este documento.** El recorrido completo del sistema _desde el navegador_, bloque por
> bloque, tal y como lo haría una entidad que lo estrena. No es una lista de deseos: es el plan
> que se ejecutó contra `tenant_demo` en agosto de 2026, con los actos reales —incluidos los
> irreversibles— y con los defectos que encontró ya corregidos.
>
> **Por qué existe.** Hasta esta revisión, la garantía del proyecto era **de unidad y de
> contrato, no de uso**. Las 36 pantallas de `(app)/` tenían cero cobertura de navegador: los
> únicos tests de extremo a extremo comprobaban que tres rutas redirigen a `/login`, el contrato
> PKCE y el render de `/403`. Un defecto que solo aparece al pulsar un botón —una pantalla que no
> enlaza, un panel que no reacciona, una tarjeta que lleva a un 403— era invisible.

---

## 1. Cómo se usa

### 1.1 Preparación del entorno

Un solo comando reconstruye el entorno entero desde cero:

```bash
./scripts/reset-entorno.sh                     # tamaño por defecto
./scripts/reset-entorno.sh --radicados 400 --expedientes 30
```

**Destruye los volúmenes** de Postgres, MinIO y Redis, y pide una frase tecleada antes de
hacerlo. Recrear en vez de restaurar un *snapshot* es deliberado: `public.audit_log` es
append-only por Ley 594 —ni el `--purge` del seed lo toca—, así que la única forma de dejarlo
limpio es recrear la base; sembrar por la API hace que cada documento **nazca con su asiento y
su índice**, mientras que un *snapshot* puede contener estados que el sistema ya no sabe
producir; y un *snapshot* caduca contra las migraciones. Se paga en tiempo, a propósito.

Los guiones corren **dentro de la red** (imagen `scripts/Dockerfile`), porque hablan con
Postgres por asyncpg y con Keycloak por su API de administración, y desde el host ninguno de
los dos se alcanza igual. La única pieza que corre en el **host** es
`scripts/gen-user-cert.sh`: la clave de la sub-CA de usuarios no entra en ningún contenedor.

Si el entorno ya está montado y solo se quiere volumen o perfiles, los pasos sueltos siguen
existiendo (`seed_demo.py`, `seed_corpus.py`, `perfiles_prueba.py`) — el orquestador los
encadena, no los sustituye.

| Recurso       | URL                          | Para qué                                                    |
| ------------- | ---------------------------- | ----------------------------------------------------------- |
| Aplicación    | http://localhost:19300       | todo el plan                                                |
| Gateway / API | http://localhost:19080/docs  | verificación fuera de pantalla                              |
| MailHog       | http://localhost:8025        | **el único sitio donde se ve** que las notificaciones salen |
| MinIO         | http://localhost:19901       | anexos, índices firmados, actas                             |
| Keycloak      | http://localhost:19180/admin | identidad                                                   |

El entorno crea **cinco perfiles deliberadamente distintos**. No son cinco copias con nombres
distintos: cada uno ejercita un camino que ninguno de los otros toca, y la lista se diseñó
después de comprobar en septiembre de 2026 que el peor defecto del recorrido —tener credencial
de firma **impedía** firmar cadenas— solo apareció porque un usuario tenía, por accidente, algo
que los demás no.

| Usuario      | Permisos                              | CRUD  | Clearance | TOTP | Credencial   | Camino que solo él ejercita              |
| ------------ | ------------------------------------- | ----- | --------- | ---- | ------------ | ---------------------------------------- |
| `operador`   | los 25 operativos (todos menos ROOT)  | 3     | 3         | sí   | ninguna      | firma **nativa** (HMAC)                  |
| `jefe`       | los 25                                | 3     | 3         | sí   | **activa**   | firma **personal XAdES**, `totp_requerido` |
| `secretaria` | los 25                                | 3     | **2**     | no   | ninguna      | OTP-correo; el peldaño intermedio        |
| `archivista` | los 25                                | 3     | 3         | sí   | **revocada** | credencial revocada ≠ ausente            |
| `consulta`   | `PERM_RADI` + `USUA_PERM_EXPEDIENTE`  | **1** | **1**     | no   | ninguna      | el barrido negativo de B13               |

Dos filas que no son de adorno. `secretaria` en clearance **2** da el peldaño intermedio: con
solo 1 y 3 el no-read-up se comprueba sobre un único salto, y el escalón de en medio es donde
vive el error de comparación clásico (`>` donde va `>=`). `archivista` con la credencial
**revocada** cubre un tercer estado que el código distingue explícitamente de «sin
certificado» y que no ejercitaba ninguna prueba.

`reset-entorno.sh` imprime al final una tarjeta con los secretos TOTP en base32 y el código
vigente, para cargarlos en una app autenticadora. Los OTP por correo de quien no tiene TOTP
llegan a MailHog.

### 1.2 Forma de cada caso

Compacta a propósito: lo que no se pueda ejecutar y auditar con estos seis campos, sobra.

```
### B4-07 · Devolver un radicado con motivo
Pantalla     /bandeja → drawer → Devolver
Precondición B4-03 (radicado asignado a JUR); usuario `operador`
Pasos        1. Abrir la fila del radicado 2026-DEMO-E-000001
             2. Devolver → motivo «falta el anexo de cédula» → Confirmar
Esperado     Toast de éxito; el radicado vuelve a VUR; el drawer refleja el nuevo paso
Evidencia    nº de radicado (alimenta B4-09)
Fuera de pantalla  /admin/auditoria → asiento `workflow.returned` con actor `operador`
```

El campo **«fuera de pantalla» no es decorativo**: es el que distingue _el botón respondió_ de
_el acto ocurrió_. Va en todo caso que escribe. Cuatro actos centrales del sistema
—distribución, visto bueno, reclasificación y disposición— confirman con un aviso efímero y
dejan su rastro en otra pantalla; sin ese campo, todos ellos dan verde por no comprobar nada.

### 1.3 El orden de los bloques no es temático

Es **de dependencia de estado**: cada bloque produce el dato que consume el siguiente. Alterarlo
obliga a inventar datos por SQL, que es exactamente lo que el seed evita —sembrar por SQL rompe
la cadena de hashes de `audit_log`, y esa rotura **no se repara**, porque el registro es
_append-only_ por Ley 594.

---

## 2. Los bloques

| #       | Bloque                                                                                                                                                                      | Produce / por qué va aquí                                                                     |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **B0**  | Arranque y sesión — login PKCE, `/403`, guarda SSR, `/perfil` (contexto, TOTP, credencial de firma)                                                                         | TOTP y credencial son **prerequisito duro** de B6 y B11                                       |
| **B1**  | Identidad — `/admin/usuarios`, `/admin/grupos` (permisos, nivel CRUD, clearance), `/admin/dependencias`, `/admin/parametros`                                                | el clearance habilita la clasificación de B3; los festivos cambian el plazo que B3 calcula    |
| **B2**  | Instrumentos — `/admin/trd` y `/admin/tvd` (nueva versión → aprobar → convalidar → RUSD), `/admin/metadatos`, `/admin/catalogos`, `/admin/plantillas`                       | **sin serie y tipo documental no se puede radicar**                                           |
| **B3**  | Radicación — `/radicar` (5 pasos, anexos, sugerencia RAG), `/borradores` (crear → aprobar → radicar)                                                                        | produce las Entradas y la Salida que consume todo lo demás                                    |
| **B4**  | Bandeja y trámite — 3 vistas, filtros, drawer, distribuir, asignar, trasladar, devolver, VoBo en cadena, ejecutar transacción, respuesta vinculada                          | produce la Salida vinculada que firma B6                                                      |
| **B5**  | Expedientes — las 8 pestañas: vincular (individual y por lote), excluir, foliar, metadatos, índice electrónico, integridad, versiones, exportar ZIP                         | **foliar antes de cerrar**: el cierre de B11 congela la foliación                             |
| **B6**  | Firma — `/firmas` (individual, lote, rechazo con motivo), firmar índice, `/admin/seguridad/claves`                                                                          | aquí se prueba la **cadena de firmantes en orden**                                            |
| **B7**  | Envíos postales — `/envios`: registrar, cambiar estado, devolución, planilla                                                                                                | `Actualizar rastreo` es **stub declarado**, no defecto                                        |
| **B8**  | Archivo físico — ubicaciones, unidades, vincular, localizar, préstamos, FUID (JSON y XML)                                                                                   | su ubicación alimenta la transferencia de B11                                                 |
| **B9**  | Consulta y asistente — `/busqueda` (exacta, semántica, antecedentes), `/dashboard`, `/reportes`, asistente, marco de alcance, propuesta firmada, dictado                    | la búsqueda semántica valida de paso la **ingesta event-driven** de E21                       |
| **B10** | Interoperabilidad — exportar ZIP, importar (stepper de 4 pasos), lotes con seguimiento de job                                                                               | el _round-trip_ export→import es el caso de valor                                             |
| **B11** | **Actos irreversibles** (orden interno abajo)                                                                                                                               | cada uno **quema** su objeto                                                                  |
| **B12** | Observabilidad — `/admin/auditoria` (**+ reverificar cadena**), `/admin/notificaciones`, `/admin/colas`, `/admin/preservacion`, `/admin/pinar`                              | el último **a propósito**: es el instrumento que verifica todo lo anterior                    |
| **B13** | Barrido negativo con `consulta`                                                                                                                                             | cierra los dos ejes de gate sin duplicar el plan                                              |
| **B14** | Bandeja de correo — `/admin/correo` (cuentas, app OAuth) y `/correo/[cuentaId]`: sincronizar, reservar, soltar, descartar con motivo, radicar con anexo, recorte de cuentas | añadido en 2026-08-28 al cerrar E19; **exige un servidor IMAP de pruebas**, por eso no estaba |

### 2.1 Orden interno de B11, y qué inutiliza cada acto

Estrictamente en esta secuencia, porque cada uno cierra la puerta al siguiente:

1. **Reclasificar** un radicado a Reservada. _Inutiliza_: deja de verse con clearance 1.
2. **Publicar de oficio** otro radicado (Ley 1712). _Inutiliza_: el acto no se repite.
3. **Anular la Salida** — y el contraste que es el caso de verdad: sobre una **Entrada** la
   anulación **no debe ofrecerse**, y el backend debe responder **409 `acto_no_aplica_al_tipo`**,
   no 403. Un acto que sobre cierta clase de documento _no existe_ no es cuestión de permiso.
4. **Derogar** una versión de TRD. _Inutiliza_: esa serie para clasificar.
5. **Cerrar el expediente** → índice electrónico + firma XAdES + sellado. _Inutiliza_: vincular,
   excluir, foliar y editar metadatos.
6. **Transferir** el expediente cerrado → acta, **firma personal con TOTP por cada rol**
   (receptor, remitente, elaborador), verificar firmas y sello.
7. **Recibir / rechazar** la transferencia — dos expedientes, uno por rama.

### 2.2 B13 — los dos ejes de gate

No repite ningún caso. Cada comprobación es **doble por diseño**, porque un botón oculto no
prueba nada:

- **Permiso ausente**: `/envios` y `/admin/colas` degradan a estado vacío (no a error); las
  tarjetas de `/admin` correspondientes no aparecen.
- **Nivel CRUD insuficiente** (`crud = 1`): en `/expedientes/[id]` no está _Cerrar_ ni
  _Transferir_; en `/admin/trd` no está el circuito de versiones.
- **Y el backend también dice que no**: invocar el proxy BFF directamente, sin pasar por el
  botón. **Este es el punto del bloque** — si el gate vivía solo en el navegador, aquí se cae.
- **No-read-up**: el radicado reclasificado en B11-1 no aparece en `/busqueda` ni en `/bandeja`,
  y su acceso directo por URL da 403/404 **indistinguibles**.
- **Las dos rutas que fallan en el `load`** (`/expedientes/[id]`, `/admin/pinar/[id]`) dan `403`,
  no una pantalla en blanco.

### 2.3 B14 — la bandeja de correo, y el servidor que hay que levantar

Es el único bloque que **no se puede ejecutar contra el despliegue de desarrollo tal cual**: hace
falta un buzón IMAP de verdad. Con GreenMail en la red del compose basta, y sembrar tres correos
—uno con anexo— da los siete casos:

```bash
docker run -d --name greenmail --network orpyca-mcp_orpycamcp-net \
  -e GREENMAIL_OPTS='-Dgreenmail.setup.test.imap -Dgreenmail.users=buzon:clave@entidad.gov.co' \
  greenmail/standalone:2.1.0
```

Luego, en `/admin/correo`, dar de alta la cuenta con anfitrión `greenmail`, puerto `3143`, sin
TLS, y la contraseña de la cuenta de prueba.

| #      | Caso                  | Lo que distingue _el botón respondió_ de _el acto ocurrió_                                                         |
| ------ | --------------------- | ------------------------------------------------------------------------------------------------------------------ |
| B14-01 | Pantalla de cuentas   | ninguna petición a `/api/v1/ingest/` — la ruta heredada está retirada, solo `/email/cuentas` y `/email/apps-oauth` |
| B14-02 | Sincronizar           | la bandeja trae los correos **en orden de recepción** y detecta el anexo                                           |
| B14-03 | Cerrar sin radicar    | se llama a `/liberar`: la reserva se suelta y **otro funcionario puede abrir el correo**                           |
| B14-04 | «Soltarlo» en la fila | misma comprobación desde la lista, sin abrir el formulario                                                         |
| B14-05 | Descartar con motivo  | asiento `email.mensaje.descartado` en `/admin/auditoria`, con actor y motivo y **sin asunto ni remitente**         |
| B14-06 | Radicar               | número asignado, `.eml` conservado como anexo, y el mensaje **movido a `Radicados` en el servidor IMAP**           |
| B14-07 | Recorte de cuentas    | un administrador recibe **`404` neutro** sobre el buzón personal de otro funcionario, no `403`                     |

El caso que justifica el bloque es **B14-07**: el gate llegaba donde hacía falta técnicamente
—`sincronizar` y `radicar`, porque necesitan abrir la sesión IMAP— y no donde hacía falta por
seguridad. Leer, reservar, soltar y descartar cruzaban el recorte sin comprobarlo.

---

## 3. Dónde este plan puede dar un falso verde

Anotado **por adelantado**, porque son los casos en que «carga bien» no significa «funciona». Un
plan que no declara sus puntos ciegos los convierte en aprobados.

- **~~PINAR siempre saldrá vacío~~ — declarado por error, y comprobarlo destapó cinco defectos.**
  Este documento declaraba PINAR no verificable de extremo a extremo. **Era falso**: la pantalla
  crea planes, recorre los cuatro estados de su ciclo (borrador → aprobado → en ejecución →
  cerrado) y deja asiento de cada acto en `audit_log`. Al ejecutarlo aparecieron cinco
  advertencias de actos irreversibles troceadas en columnas por un `display: flex`. **Lección que
  vale más que el caso**: un punto ciego declarado y no comprobado es, en la práctica, una
  pantalla que nadie mira. Lo que sí sigue siendo cierto es el desnivel de cobertura — el backend
  tiene 36 endpoints y la interfaz consume 6.
- **503 honestos que parecen averías**: asistente sin `ANTHROPIC_API_KEY`, RAG generativo con
  `LLMProvider=disabled`, transcripción sin `whisper-service`. Son la respuesta correcta —
  comprobar la configuración antes de tocar nada.
- **El botón de dictado no aparece en Chrome**: es ADR-020 funcionando. En Chrome el reconocedor
  del navegador manda el audio a Google, así que el camino es Whisper local. Ausencia esperada.
- **WORM y PDF/A**: con `preservation_enabled=False` la pantalla de preservación no muestra
  artefactos protegidos, y PDF/A informa `no_evaluado` sin veraPDF.
- **Empaquetado AIP y protección WORM no tienen disparador en la interfaz**: son actos de
  reconciliación. La pantalla los _observa_, no los ejecuta.
- **Los avisos se desvanecen.** Muestrear una captura unos segundos tarde produce un hallazgo
  falso de «la interfaz no dijo nada». Ocurrió durante la ejecución.

## 4. Lo que este plan NO cubre, y por qué

Se declara como límite, no se disimula:

- Alta de tenants y ciclo de vida de ficheros en storage (retirados del gateway a propósito,
  `X-Internal-Token`), y todos los endpoints `*-internal`.
- Empaquetado AIP, protección WORM, barrido de vencidos y las 6 fases de reconciliación (cron).
- ~~**Ingesta IMAP**~~ — **cubierta desde 2026-08-28**: es el bloque B14. Dejó de estar fuera
  el día que se levantó un servidor IMAP de pruebas, que era el único motivo por el que lo estaba.
- **OAI-PMH y CMIS** (`public_interop_enabled=False`, sin pantalla).
- ~~**PQRS ciudadana y verificación pública por código** — endpoints públicos **sin pantalla**~~ —
  **cerrado**: `(public)/radicacion` consume `/{tenant}/ventanilla` y `(public)/consulta` consume
  `/{tenant}/verify/{code}`. Se deja tachado y no borrado porque este párrafo llegó a contradecir
  al de abajo, que ya daba los tres hallazgos por cerrados: una lista de «lo que no se cubre» que
  no se actualiza acaba afirmando lo contrario que el texto que la sigue.
- Callback postal HMAC, protocolo MCP externo, `indice/rebuild`, `/auth/clearance`.

De esa lista salieron **tres hallazgos de producto**: capacidades que existían en el backend
sin ninguna forma de llegar a ellas. Las tres están cerradas — ventanilla única y consulta
ciudadana en `(public)/`, e ingesta de correo en `/admin/correo` + `/correo/[cuentaId]`. El patrón se repitió lo
bastante como para dejarlo escrito: **un endpoint sin puerta no es una funcionalidad entregada**,
y el inventario de pantallas es el sitio donde esa deuda se ve.

---

## 4.bis Decisión de producto **resuelta** (resultado de B13, cerrada el 2026-08-28)

El barrido negativo dejó una pregunta que no era un defecto de código sino una decisión que
alguien tenía que tomar:

> **Las lecturas de TRD y de PINAR no exigían ningún permiso**, y sus proxies BFF solo
> comprobaban que hubiera sesión. Un usuario sin `USUA_PERM_TRD` ni `USUA_PERM_ADMIN` veía el
> listado y el detalle completos escribiendo la URL — aunque la barra lateral le escondía
> Administración entera y `/admin` gateaba sus tarjetas.

**Lo primero que cambió fue el tamaño del problema.** El recuento original —«TRD 4, PINAR 11»—
se hizo a mano. Un censo por FORMA sobre los routers de los dos servicios encontró **33
lecturas sin gate** alcanzables por el gateway: las 4 de TRD, **4 de TVD que la pregunta no
mencionaba**, 11 de PINAR, y 14 de catálogos (dependencias, parámetros, tipos documentales,
plantillas de metadatos). Es la enésima vez en este proyecto que una lista escrita a mano se
queda corta en silencio.

**Y con la lista completa, la disyuntiva se disolvió.** «Gatear o declarar abierto» era una
pregunta mal planteada, porque esas 33 lecturas no son una sola cosa:

| Clase                            | Qué es                                                                                                                            | Decisión                                                                              |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| **Vocabulario de clasificación** | listado y detalle de series TRD y agrupaciones TVD, cálculo de retención, tipos documentales, dependencias, catálogos, parámetros | **abierto** a cualquier autenticado del tenant, **declarado en el código y con test** |
| **Circuito del instrumento**     | `GET /trd/{code}/versiones` y su gemelo de TVD                                                                                    | **`USUA_PERM_TRD`**                                                                   |
| **PINAR entero**                 | las 11 lecturas del plan                                                                                                          | **`USUA_PERM_ADMIN`**, gate en el propio `APIRouter`                                  |

El **vocabulario** no se gatea porque `/radicar` y `/expedientes` lo consumen para poder
clasificar: `USUA_PERM_TRD` lo tiene quien **administra** el instrumento, no quien lo usa, y
exigirlo dejaría sin radicar a todo operador ordinario —el remedio sería peor que el defecto, y
el síntoma («no puedo radicar») caería lejos de su causa—. Además el instrumento convalidado es
de publicación obligatoria (Ac. AGN 004/2019, compilado por 001/2024; Ley 1712/2014 art. 9). Eso
**también lleva test**: si alguien lo «asegura» de más, se pone en rojo antes que la ventanilla.

El **circuito** sí, porque `GET /{code}/versiones` no devuelve el instrumento publicado sino su
elaboración: los borradores, el `acta_comite`, la `fecha_aprobacion_comite`, la
`instancia_convalidante` y el `created_by` de cada versión. Su gemelo `GET /revision-pendiente`
ya exigía el permiso: era el hueco que quedaba de ese mismo par.

**PINAR se cierra en vez de abrirse** por dos motivos. Un plan en `borrador` es material de
trabajo del Comité. Y el PINAR aprobado sí es de publicación obligatoria (Decreto 1080/2015
art. 2.8.2.5.8), pero **publicar es un acto de la entidad hacia fuera** —una pantalla en
`(public)/`, como la consulta ciudadana— y no un `GET` interno sin gate que se le parece por
accidente; hoy no existía ningún consumidor de esas lecturas fuera de `/admin/pinar`, así que
abrirlas no servía a nadie. Es la misma lección que dejó el `verify` público: un endpoint sin
gate no es publicación, es un descuido con forma de publicación.

El gate de PINAR va en el `APIRouter`, **por admisión**: lo hereda el endpoint que alguien añada
mañana. Su guardián recorre las rutas que el router declara —no una lista— y exige el 403 en
todas, con aserción anti-vacuidad para que no pase en verde si el censo devolviera cero.

---

## 5. Criterio de parada

Se para y se pregunta si aparece algo que cambie el alcance: un defecto que exija decisión de
producto, una migración pendiente en el tenant, o **tres fallos seguidos en el mismo bloque** —
señal de que el entorno está mal, no el código.

Los defectos **se corrigen en el momento**, con su test y su **verificación por mutación**:
revertir la corrección debe poner su test en rojo. Un test que sigue verde bajo mutación se tira.
En este proyecto ya ha pasado varias veces: un test con mocks no prueba una restricción de
integridad ni la atomicidad de una transacción.

---

## 6. Resultado de la ejecución de agosto de 2026

La revisión encontró y corrigió defectos en **todos** los bloques que ejecutaron actos de
escritura. El detalle de cada corrección —qué fallaba, por qué y con qué prueba se cerró— está en
el [changelog](changelog.md). El patrón dominante merece quedar escrito aquí, porque es el que
esta clase de revisión existe para cazar:

> **El sistema ya tenía el dato bueno y la pantalla mostraba otro.**

No eran fallos de cálculo ni de diseño: eran cadenas de alternativas con el operando equivocado
delante (`t.expediente_id ?? t.expediente?.code` — el valor bueno no puede aparecer nunca), dos
mitades cada una correcta y el **par** roto (el formulario enviaba `observacion`, el backend
esperaba `motivo`), o un literal que llevaba desde el primer día fuera del `CHECK` que debía
admitirlo. **Ningún test de un solo lado los detecta**, y por eso ninguno los detectó.

La prueba más fuerte del plan es la última: **reverificar la cadena de hashes de `audit_log`**
sobre todo lo que la revisión generó. Sobre el tenant de demostración, **968 de 972 asientos
recomputan su hash correctamente y ninguno está genuinamente roto** tras una revisión completa encadenada —radicación, flujos,
expedientes, firmas, transferencias—. La garantía de integridad de Ley 594 queda probada **con
datos, no con mocks**.

### El bloque que más se temía, y lo que contestó

B13 —el barrido negativo— existía por una sospecha concreta: que algún gate viviera **solo en el
navegador**, defecto que en este proyecto ya había aparecido dos veces. La respuesta es que
**no**. Cerrar y transferir un expediente, crear una serie TRD o un plan PINAR y listar envíos
responden **403 desde el backend** cuando se invocan por el proxy BFF, sin pasar por ningún
botón; el eje de nivel CRUD es real (`min_crud=3`) y no un adorno de la interfaz; y el
no-read-up oculta el radicado reclasificado devolviendo **404, no 403** —un 403 confirmaría que
existe—, comprobado con control positivo para descartar que la búsqueda estuviera simplemente
rota.

Lo que sí falló fueron **dos mensajes**, y los dos por el mismo motivo de fondo que el resto de
la revisión: el sistema tenía el dato bueno y la pantalla decía otra cosa. `/envios` presentaba
un 403 como «no hay registros», afirmando que la entidad no tiene envíos postales; y el 403 de
nivel insuficiente acusaba al usuario de carecer de un permiso **que sí tiene**, mandando a su
administrador a buscar una concesión que ya existía.

Las cuatro filas restantes no son manipulación: son datos de prueba insertados por SQL crudo en
una sesión anterior, y su daño **no es reparable** porque el registro es append-only. Eso destapó
el hallazgo de fondo de B12: las tres defensas del registro cubrían _modificar_ y _borrar_ un
asiento, y **ninguna cubría añadir uno** que no viniera de `audit.append`.

### B14, añadido después: el gate que llegó donde hacía falta técnicamente

La bandeja de correo se recorrió el 2026-08-28, ya cerrado el resto del plan, y su hallazgo no
encaja en ninguno de los dos patrones anteriores. No era la pantalla mostrando otro dato ni un
gate que viviera solo en el navegador: era un gate **puesto donde el código lo necesitaba, no
donde la seguridad lo necesitaba**. `sincronizar` y `radicar` comprobaban el recorte de cuentas
porque sin él no podían ni abrir la sesión IMAP; leer, reservar, soltar y descartar no lo
comprobaban, y por ahí un funcionario alcanzaba el buzón **personal** de otro. La corrección
responde **`404`, no `403`**, por el mismo motivo que el no-read-up de B13: distinguirlos
convierte el endpoint en un oráculo de qué buzones tiene la entidad.

Y una segunda aparición del patrón que este plan lleva cazando desde el principio: **`liberar()`
existía en el repositorio, con su test, desde el incremento anterior — y ninguna pantalla lo
llamaba**. Cerrar el formulario sin radicar dejaba el correo reservado para siempre. Se destapó
cruzando `docs/*/api.md` contra el OpenAPI del servicio en marcha, no navegando.

> **Y una trampa del propio método.** La primera medición dio «946 de 951, 1 roto» porque
> reimplementé el cálculo del hash a mano para verificarlo. La buena usa
> `orpycamcp_common.audit.compute_hash`, la misma función que escribe los asientos: **no se
> reimplementa la cosa que se está verificando** — se comprueba con ella, o se comprueba otra cosa.

---

## 7. Segunda ejecución (septiembre de 2026) — E23, producción documental

Un recorrido acotado a la superficie más nueva: `/borradores`, `/firmas` y, de rebote,
`/anulaciones`. Cuatro defectos, y los cuatro comparten una forma que el bloque de agosto no
había producido:

> **Cada pieza hacía bien lo suyo, y entre ellas no había nada.**

No hay valor equivocado ni gate ausente dentro de una función. Lo que falta es la relación entre
dos partes correctas — y un test por pieza no puede echar de menos una garantía que no vive en
ninguna. La pregunta que los caza no es *«¿qué hace esta función?»* sino **«¿qué impide el acto
indebido?»**.

### 7.1 Los cuatro, y cómo se destapó cada uno

| Defecto | Cómo apareció |
|---|---|
| El circuito de aprobación **no ataba**: se abría, se ignoraba y se radicaba igual | Leyendo `aprobar()` y viendo que no consulta nada |
| Y el acto que había que atar era **radicar**, no aprobar | **Mirando la base**: dos borradores aprobados con circuito abierto |
| Un turno de visto bueno **se podía firmar**, y la bandeja invitaba en lote | Recorriendo `/firmas` como `jefe` |
| Se aprobaba y se anulaba **sin ver el documento** | Censo `grep` de señales de acceso al objeto por pantalla |

Los dos del medio no salen leyendo código. El segundo apareció **consultando el estado real de
`tenant_demo`**, y es la lección de método de esta tanda: el código dice lo que puede pasar, la
base dice lo que **ya pasó**. El tercero solo aparece pulsando.

### 7.2 Casos ejecutables

```
### E23-01 · El circuito abierto impide radicar
Pantalla     /borradores → Abrir un borrador aprobado con circuito abierto
Precondición Circuito de ≥2 turnos creado y sin resolver
Pasos        1. Abrir el borrador  2. Bajar hasta las acciones de flujo
Esperado     NO se ofrece «Radicar»; aviso «quedan N de M turnos por resolver»
Fuera de pantalla  POST /borradores/{id}/radicar → 409 circuito_pendiente, y
             el número NO se consume (el gate va antes de pedir el consecutivo)
```

```
### E23-02 · Fail-closed cuando no se puede preguntar
Pantalla     ninguna (servicio)
Precondición `docker compose stop signature-service`
Pasos        1. Aprobar o radicar un borrador
Esperado     503 circuito_no_verificable; el estado en la base NO cambia
Por qué      Suponer «no hay circuito» reabre el agujero justo cuando el
             sistema está degradado
```

```
### E23-03 · Un visto bueno no se puede firmar, ni suelto ni en lote
Pantalla     /firmas con un turno de rol `visto_bueno`
Pasos        1. Mirar la fila  2. Abrir el detalle
Esperado     Sin casilla de selección y sin barra «seleccionar todas»;
             la acción dice «Ver / Dar visto bueno»; el detalle ofrece
             «Dar visto bueno» y «Rechazar», nunca «Firmar»
Fuera de pantalla  POST /signature/requests/{id}/sign → 409
             turno_es_de_visto_bueno, ANTES de consultar la credencial
             (ningún factor 2FA se consume en un acto que se va a rechazar)
```

```
### E23-04 · Se puede ver aquello sobre lo que se decide
Pantalla     /firmas → detalle · /anulaciones → detalle
Esperado     «Ver documento» abre el PDF del borrador;
             «Ver el radicado {n.º}» lleva a /bandeja?radicado={id}
Límite       La previsualización exige PERM_RADI: un revisor con solo
             PERM_FIRMA recibe 403 y lo ve DICHO, no un botón mudo
```

### 7.3 Dos trampas del propio método, otra vez

**Di por caído un servicio que estaba sano.** `wget` dentro del contenedor devolvía «Connection
refused» sobre `/login`, y estuve podando disco y reconstruyendo imágenes. El rechazo era del
**segundo salto**: `/login` responde 302 hacia Keycloak, wget sigue el redirect, y ese host no
existe dentro del contenedor. La primera petición funcionaba. Tenía tres evidencias de que el
servicio estaba vivo —socket en LISTEN, proceso en pie, healthcheck en verde— y las traté como una
paradoja en vez de como una refutación. *Cuando el sistema insiste por tres vías en que está sano,
la sospechosa es la herramienta de diagnóstico.*

**Y un guardián que no caía bajo su propio defecto.** Al extender el detector de mensajes sin
tildes a las pantallas, la primera versión descartaba todo fragmento con `{variable}` dentro —para
no marcar nombres de variable como prosa—. Pero los avisos que importan casi siempre llevan una
(«quedan {n} de {total} turnos»), así que se saltaba exactamente la familia de mensajes que
existía para vigilar. Lo destapó devolverle el error original: **pasó en verde**. La mutación de un
guardián se hace con el defecto **real** que motivó escribirlo; uno inventado tiende a caer en la
parte que ya funciona.

### 7.4 El cierre del recorrido, y los tres defectos que salieron de él

El turno final de firma y la radicación se ejecutaron el 2026-09-03 como
`operador`. **Cada paso destapó un defecto distinto, y ninguno era visible desde
el código.**

| Paso | Lo que apareció |
|---|---|
| Pedir el segundo factor | El correo falló tres veces y la pantalla dijo «Enviamos un código, vence en 4:48». El `503 canal_2fa_no_disponible` estaba escrito y **no podía dispararse nunca**: el servicio de correo respondía `202 Accepted` aunque el envío hubiera fallado |
| Firmar | `NotNullViolationError` en `firma.tipo_firma` → **500 en la cara del firmante**. Firmar estaba roto para todo el que no tuviera credencial PKI personal, que es el caso común |
| Radicar | `415 Formato no admitido: application/pdf` — storage rechazaba el PDF/A que el propio sistema produce |

Los tres tienen la misma raíz de método: **son cruces entre servicios, y todos
los tests de esos cruces usan dobles**. Un `AsyncMock` acepta cualquier fila, y
un `202` simulado no distingue «lo he recibido» de «lo he entregado». La
confirmación más dura: en la base de demostración **no había ni una firma
`nativa`** — ese camino nunca había llegado a Postgres en toda la vida del
servicio.

De ahí salió el cambio de infraestructura de esta tanda: **`signature-service`
estrena tier de integración** contra Postgres real, con el mismo gate
`RUN_INTEGRATION=1` y schemas desechables que ya usaban document, archive y
storage.

**Y una garantía que sí funcionó, escrita para otra cosa.** El 415 ocurrió
*después* de renderizar y *antes* de pedir el consecutivo, porque el orden de
`radicar` es combinar → renderizar → subir → **número**. Resultado: **no se
gastó ningún número**; el borrador se quedó en `aprobado` sin `tracking_number`.
La regla «un fallo de render no consume un radicado» cubrió un fallo que nadie
había previsto. Poner lo irreversible al final protege de lo que aún no se ha
inventado.

Resultado final: **`2026-DEMO-S-000066`**, con su PDF/A anexado como
`documento_principal` (`2026-DEMO-S-000066.pdf`) — lo que prueba de paso la
segunda pasada de combinación, que re-renderiza el documento con el número ya
asignado.

### 7.5 El quinto defecto: un acto válido que nadie podía ejecutar

Quedaba una cosa por ver con los ojos: el `503 canal_2fa_no_disponible`. Estaba
probado por test y por mutación, nunca en pantalla. Para provocarlo hace falta el
correo caído, así que se apagó MailHog y se pidió firmar un turno pendiente.

No llegó al `503`. Murió antes, en el paso 1 del asistente, con esto:

> *firma_solicitud.payload_sha256 debe fijarse antes de emitir un desafío de
> doble factor (RF-FIR-13)*

El nombre de una columna de la base, en la cara de un funcionario que no tiene
nada que hacer con esa frase. Debajo había un defecto entero.

**Dos reglas escritas aparte que nunca se encontraron.**
`CADENA_PAYLOAD_OBLIGATORIO` deja `radicado` y `expediente` fuera *a propósito, y
con la reserva escrita en el comentario*: se firman como conjunto, no tienen un
artefacto canónico propio. El contrato, por tanto, permite abrir la cadena sin
huella. Y `emitir_desafio`, escrito en otro momento, la exigía para **todos** los
tipos. Entre las dos, esas cadenas se podían abrir y no se podían cerrar.

Es el **gemelo invertido** del defecto de 7.4: allí un `fail-closed` no podía
dispararse nunca; aquí un gate se dispara *siempre* sobre dos tipos válidos. Los
dos se leen como una defensa y ninguno lo es. Un gate que nadie puede satisfacer
no protege un acto: lo suprime.

**Por qué no lo vio nada.** Cada regla, por separado, es correcta y está probada.
Lo que falla es que discrepan, y eso no vive en ninguna pieza. Además: *ninguna
pantalla abre hoy cadenas de esos dos tipos* — solo `/borradores`, que sí resuelve
la huella. Un tipo que el contrato admite y la interfaz nunca produce es un punto
ciego estructural. Las dos cadenas que lo destaparon las había creado el `seed`,
que es el único cliente real que ejercita ese camino.

**La corrección.** `huella_para_desafio(sol)` devuelve `payload_sha256` donde
existe y, donde no, el SHA-256 de `"{objeto_tipo}:{objeto_id}"` — que son
exactamente los bytes que `firmar` sella en ese caso. El desafío no promete más de
lo que la firma cumple, ni menos. El criterio ya estaba en el mismo archivo:
`_gate_documento_vigente` retorna sin decir nada para estos tipos en vez de
denegar. *Lo que no se puede comprobar no se afirma, pero tampoco se convierte en
un veto.*

Una sola función, dos llamadas —emisión y verificación—. Si al corregir solo una
hubieran divergido, todo desafío de estos tipos se habría rechazado como «de otro
documento», y ese fallo solo aparece al teclear el OTP, con el código ya gastado.

**El test que había afirmaba el `422`.** Codificaba el defecto, igual que el `202`
de 7.4. Tercera vez en esta ejecución que un test verde era la descripción fiel de
un error. El nuevo aserta sobre el `hash_documento` *persistido*: comprobar solo el
`201` pasaría también si la huella se guardara en blanco.

**Lo que se comprobó en el sistema, no en un doble.** Con MailHog caído, la
pantalla dijo «No se pudo enviar el correo con el código (notification-service)» —
el `503` que faltaba por ver, alcanzado justo porque el `422` ya no lo tapaba. Los
dos arreglos verificados de una pasada. Repuesto el correo, el turno se firmó, y la
base lo confirma:

| dato | valor |
|---|---|
| solicitud / turno | `firmado` / `firmado` |
| `firma.tipo_firma` | `nativa` (la corrección de 7.4, otra vez en la base) |
| `firma.payload_sha256` | `10988359…` |
| `firma_challenge.hash_documento` | `10988359…` — **el mismo** |
| digest de `radicado:f6377c02-…` | `10988359…` |

El desafío quedó atado a los mismos bytes que la firma selló. Es la afirmación
central de la corrección, confirmada por Postgres y no por un mock.

Y una garantía ajena que se confirmó de paso: hay **dos** filas de desafío
—`expirado` y `consumido`— y **ninguna** del intento con el correo caído. El `503`
hizo el *rollback* completo que promete su docstring, sin dejar un desafío huérfano
cuyo código nadie recibió.

### 7.5.bis El sexto: un nombre que prometía otra cosa

Salió del **campo «fuera de pantalla»** del propio plan — el que distingue *el
botón respondió* de *el acto ocurrió*. Al comprobar los asientos de la firma
recién hecha, dos consecutivos del mismo acto decían cosas distintas:

| asiento | valor |
|---|---|
| `firma.challenge.emitido` | `49fff365…` |
| `firma.turno_firmado` → `hash_documento` | `cd51626b…` |

`cd51626b…` es el SHA-256 del **texto hexadecimal** de `49fff365…`. El hash del
hash. El asiento publicaba bajo la clave `hash_documento` el `payload_sha256` que
devuelve el firmante, que es el SHA-256 de *los bytes que se firmaron* — y para un
`borrador` esos bytes son la huella, no el documento.

**No rompe nada criptográfico**: la verificación recompone los mismos bytes y es
coherente consigo misma. Por eso ninguna suite lo ve. Rompe la verificación *desde
fuera*: quien tenga el PDF compara con `firma.payload_sha256`, no coincide, y nada
en el nombre se lo advierte. Y es justo lo que la Ley 594 pide poder demostrar —
**qué** se firmó.

**Lo más incómodo del hallazgo**: el test que cubre ese asiento ya lo sabía. Su
comentario decía, con precisión, *«the audited hash is the SHA-256 of that hex
string, not the hex string itself»*. Alguien miró el comportamiento, lo describió
fielmente y dejó la clave llamándose `hash_documento`. No era desconocido: era
conocido y normalizado. Y el docstring del firmante prometía lo contrario — «el
SHA-256 del contenido»—, séptima aparición en este proyecto del comentario que
declara lo que el código no cumple.

**Se corrigió el nombre, no el valor.** El HMAC de las firmas ya emitidas cubre
esos bytes y reescribirlo las invalidaría; `audit_log` es *append-only* por ley y
no se toca hacia atrás. El asiento pasa a `payload_firmado_sha256`, el docstring
deja de prometer una correspondencia inexistente, y `FirmaResponse.payload_sha256`
documenta que **no sirve para comprobar contra un PDF en la mano**. Lo que no se
puede arreglar se declara.

De paso: el registro del factor consumido por TOTP leía la huella directamente de
la solicitud y salía **vacío** para `radicado`/`expediente`; ahora usa el mismo
resolutor compartido.

Cómo se encontró importa tanto como el qué: **mirando los datos que el sistema
produce, no el código que los produce**. Leyendo el código no se ve — cada línea,
por separado, es correcta.

### 7.7 B6 ejecutado con los cuatro perfiles: el séptimo defecto

El bloque B6 —«la cadena de 4 firmantes en orden»— nunca se había ejecutado. Al hacerlo, con un
usuario distinto por turno, salió el defecto más grave del recorrido, y salió porque **un perfil
tenía algo que ningún otro tiene**: `jefe` es el único con credencial de firma personal activa.

| Perfil | 2FA | Credencial | Camino que solo él ejercita |
|---|---|---|---|
| `operador` | TOTP activo | `pendiente_emision` | firma `nativa` |
| `jefe` | TOTP activo | **activa** | firma `personal` / `xades_personal` |
| `secretaria` | ninguno | ninguna | OTP-correo, firma `nativa` |
| `archivista` | ninguno | ninguna | cierre de la cadena |
| `consulta` | — | — | barrido negativo (B13) |

**El defecto: tener credencial de firma quitaba la capacidad de firmar cadenas.** El backend
decide —y lo dice en un comentario— que un actor con certificado activo firma por la vía personal
y solo por ella, y esa vía exige `totp_code`. `SignAssistant.svelte`, el único componente que
firma cadenas, manda siempre `challenge_id` + `otp` y nunca `totp_code`. Cada regla, por
separado, es correcta y está probada; el defecto vive entre las dos.

Y era peor que un error: la rama personal devolvía `409 2fa_requerido`, **el mismo código** que
significa lo contrario —«arranca el reto por correo»—. El asistente lo leía así, emitía otro OTP,
llegaba el correo, el firmante lo pegaba, el backend volvía a pedir `totp_code`. Bucle sin salida
y sin un mensaje veraz, gastando un desafío y un correo por vuelta. En la base quedaron cuatro
desafíos de `jefe` con `intentos = 0`: el OTP nunca llegó siquiera a comprobarse.

*Corrección*: código de error propio (`totp_requerido`) y un modo TOTP en el asistente, que al
recibirlo **no emite ningún reto** y pide el código de la app autenticadora. Verificado por
mutación y en el sistema corriendo.

**El octavo, que es el sexto incompleto.** Con la vía personal por fin ejecutada, dos turnos de la
misma cadena, sobre el mismo objeto y con `huella_de_contenido: false` en ambos, publicaron
huellas distintas: `3727b474…` (nativa, digest de `expediente:<id>`) y `c4321d48…` (personal,
digest del envoltorio `<acto xmlns="urn:orpyca:firma-personal" …>` que XAdES necesita).
Comprobado reconstruyendo el envoltorio. La documentación que escribí la semana pasada para
`payload_sha256` cubría solo la rama nativa —la escribí sin haber ejecutado nunca la de PKI—.
Corregida: el campo queda declarado comparable **solo entre firmas del mismo `provider`**.

*Resultado de B6*: cadena de 4 turnos cerrada en orden, `firma_solicitud` en `firmado`, por tres
caminos distintos (nativa+correo, personal+TOTP, nativa+correo).

### 7.7.bis Lo que el barrido de B6 confirmó de paso

- **El orden se respeta en el backend, no solo en la lista.** `POST .../challenge` sobre un turno
  que no es el tuyo → `403 no_es_su_turno` (RF-FIR-04), invocado desde la consola con la sesión
  real, sin pasar por ningún botón. Y **sin gastar nada**: cero filas en `firma_challenge`, cero
  correos.
- **Inconsistencia menor, anotada y no corregida**: el mismo turno ajeno devuelve `403
  no_es_su_turno` en `/challenge` y `409 documento_modificado` en `/sign`. Los dos extremos del
  mismo acto comprueban las guardas en orden distinto.
- **Observación sin conclusión**: un desafío emitido cuyo paso 2 no llegué a ver en pantalla. No
  pude atribuirlo a ningún clic, y la explicación inocente —los desafíos vencen en 5 minutos y
  buscar el código en MailHog tarda más— cubre igual de bien el patrón. Se anota como no
  demostrado, no como defecto.
- **El TOTP de `/perfil` no lo usa la bandeja de firmas.** `operador` tiene TOTP activo y su firma
  salió igualmente por correo: la rama TOTP del backend solo se entra si el cliente manda el
  código, y solo `/transferencias` lo hace. No es el defecto de arriba —aquí el correo sí funciona—
  pero deja el segundo factor más fuerte sin usar, y ata toda firma de cadena a que el correo
  salga.

### 7.8 B13 con `consulta`: el control negativo hizo su trabajo

El usuario `consulta` existe para una sola cosa: con un solo usuario que lo puede todo, un botón que
debería desaparecer y un backend que debería negarse son indistinguibles de que no haya control.

**Lo que salió bien, y no es poco.** El menú se recorta solo a lo concedido. `/envios` y
`/admin/colas` no degradan a un EmptyState mentiroso: nombran el permiso que falta, y el de envíos
además **desmiente la inferencia equivocada** — «Esto no significa que no haya envíos registrados».
El `403` de cerrar y transferir dice *«Tiene el permiso `USUA_PERM_EXPEDIENTE` con nivel 1, y esta
acción requiere nivel 3 o superior»*: distingue «te falta el permiso» de «te falta nivel», que es
justo lo que el barrido anterior encontró roto. Y el no-read-up es **indistinguible de verdad**: el
radicado reservado `2026-DEMO-E-000001` no aparece en búsqueda (`total: 0`) y su acceso directo
devuelve un `404` **byte a byte idéntico** al de un UUID que no existe.

**Lo que salió mal.** Invocando el proxy BFF desde la consola, sin pasar por ningún botón:
`consulta` **editó los metadatos de un expediente**. `require_permission` tiene `min_crud=1` por
defecto y cuatro escrituras de expedientes lo omitían. Censo derivado: **90 de 135** escrituras con
gate no exigen nivel. Corregidas las cuatro que la caminata demostró; las 86 restantes quedan
declaradas en `shared/tests/test_escritura_exige_nivel.py`, que impide que la lista crezca.

**Dos hallazgos que resultaron NO serlo, comprobados antes de reportarlos** —y que son la mitad del
valor de este bloque—:

- `consulta` exporta el expediente entero en ZIP con `200`. Parece egreso masivo por un usuario de
  lectura, pero la migración `018` ya **declara** que descargar un expediente exige
  `USUA_PERM_EXPEDIENTE` y que `PERM_DESCARGAEXP` no restringe nada, con un test que vigila que esa
  lista no caduque. Es la conducta escrita.
- Editar un expediente `transferred` parece violar el cierre archivístico. Pero la guarda de estado
  existe y es **por campo**: `trd_serie_id` —que decide la retención— devuelve
  `409 expediente_not_open`; el nombre y la descripción se permiten a propósito, como corrección de
  metadatos. Verificado ejecutándolo, no leyéndolo.

### 7.9 La verificación final del plan: la cadena de `audit_log`

El plan la llama «la prueba más fuerte»: si la cadena de hashes valida tras cientos de actos reales
encadenados, la integridad de Ley 594 queda probada con datos y no con mocks. Ejecutada al cierre de
esta sesión, sobre `tenant_demo`.

**`/admin/auditoria` declara «CADENA DE AUDITORÍA COMPROMETIDA» a partir del asiento `41004`.** No es
una manipulación: los asientos `41004`–`41007` tienen `hash` con valor literal `h1`, `h2`, `h3`, `h4`
y `prev_hash` vacío. Son filas que una suite de integración insertó por SQL en el `audit_log` de la
base de desarrollo. La causa raíz **ya está cerrada** —la migración global 006 añadió
`chk_audit_log_hash_forma`, que impide escribir un hash de juguete— y el propio test lo documenta.
Pero las filas **no se pueden borrar, porque el registro es append-only por ley**.

**Recuento propio, con las dos condiciones del verificador real** (hash recomputado *y* `prev_hash`
encadenado) y sin su salida temprana:

| | |
|---|---|
| Asientos del tenant | **2056** |
| Discrepancias | **4** — `41004`, `41005`, `41006`, `41007` |
| Asientos reales que verifican | **2052**, incluido todo lo que generó esta sesión |

El asiento siguiente encadena correctamente sobre `h4`: la contaminación está *dentro* de la cadena,
no pegada al final. **La garantía de integridad se sostiene sobre 2052 asientos reales encadenados.**

**Y aquí está el hallazgo.** `verify_chain` (`shared/orpycamcp_common/audit.py`) **retorna en la
primera rotura**: `(False, id)`. Nunca mira más allá. Combinado con un registro que por ley no se
puede reparar, eso significa que **una sola rotura inutiliza la verificación para el resto de la vida
del sistema** — la herramienta deja de servir exactamente en la situación para la que existe: después
de un incidente. El aviso dice «no continúe asumiendo que el resto del registro es confiable sin
investigar», y no ofrece ninguna forma de investigar: el recuento de esta tabla hubo que hacerlo a
mano. Además subestima la posición probatoria de la entidad, que puede demostrar 2052 asientos
íntegros y la pantalla presenta como un todo comprometido.

Es la misma forma que el gate que se dispara siempre, invertida una vez más: **una alarma que no se
puede apagar deja de ser una alarma**, y entonces una rotura genuina se lee igual que el ruido.

**Corregido.** `verify_chain` recorre ahora la cadena entera y devuelve un `ResultadoCadena` con
todas las roturas, el total y cuántos asientos verifican. La decisión de fondo: **tras una rotura se
sigue encadenando desde el hash ALMACENADO**, no desde el recomputado — con el recomputado una sola
fila mala pintaría de rotas todas las siguientes y el resultado volvería a ser «desde aquí no sé
nada» con más pasos. Tope de 100 roturas enumeradas, pero **contadas todas**: un registro masivamente
corrupto no puede parecer que tiene exactamente 100 problemas.

Los campos de la respuesta son **aditivos** (`broken_id` se conserva), y la pantalla pasó de un
veredicto binario a decir lo que la entidad puede demostrar. Verificado en el sistema: produce sola
los mismos números que el recuento a mano — 4 roturas, 2052 asientos íntegros, los cuatro id
enumerados con su motivo. Verificación por mutación con el defecto real (devolver en la primera
rotura pone el test en rojo).

### 7.6 Lo que esta ejecución NO cerró

- ~~El turno final de firma y la radicación con el PDF/A adjunto~~ — **cerrado**, ver 7.4.
- ~~El `503 canal_2fa_no_disponible` en pantalla~~ — **cerrado**, ver 7.5.
- **Que tener un turno en un circuito dé por sí solo derecho a leer lo que se te pide aprobar.**
  Hoy la lectura se apoya en `PERM_RADI`, que es un permiso de producción documental. Se declara
  como límite, con su mensaje explícito en pantalla, en vez de disimularlo.
- **Capacidad construida sin puerta**, anotada y no perseguida:
  `/firmas/api/documento/[documentId]` y su `/verify` existen, están probados y **no los llama
  ninguna pantalla** (comprobado de nuevo el 2026-09-06). El proxy dice en su propio docstring
  para quién se hizo —«pantallas de detalle de documento que ya calculan el hash del
  contenido»— y esa pantalla nunca se escribió.

  **Y no basta con abrirle una puerta.** Se intentó el 2026-09-06 —una sección en el detalle del
  radicado que hashea el archivo en el navegador con WebCrypto y pregunta al `/verify`— y se
  revirtió antes de entregarla, al comprobar dos cosas:

  1. La tabla `signatures` (firmas a nivel documento) está **vacía**, y **ninguna pantalla las
     crea**: el `POST` hermano tampoco tiene puerta. No es una capacidad sin puerta, es una
     funcionalidad entera sin usar.
  2. Ese `/verify` consulta `signatures`, **no** la tabla `firma` de las cadenas. Un radicado
     firmado por el circuito de `/firmas` no aparece ahí.

  Juntas, la pantalla habría contestado **«No coincide» a todos los archivos, siempre**, incluidos
  los de radicados que SÍ están firmados: le habría dicho a un funcionario que un documento firmado
  no lo está. Una puerta a una habitación vacía que además miente es peor que ninguna puerta.

  Así que la pregunta del sexto defecto —«¿este PDF que tengo es el que se firmó?»— **sigue sin
  respuesta en todo el sistema**: `payload_sha256` no sirve para eso (y ya lo declara), y la vía
  que existía para responderla no está conectada por ningún extremo. Cerrarlo de verdad es una
  decisión de producto: qué significa que un radicado esté firmado, y cuál de los dos mecanismos
  responde.

---

## 8. Tercera ejecución (2026-09-07) — B2, los instrumentos

Primer bloque recorrido sobre el entorno reconstruido con `reset-entorno.sh`. **B2 cerró en
verde, sin un solo defecto**, y eso merece explicarse tanto como un bloque en rojo: es el
primero que lo hace en las tres ejecuciones.

### 8.1 Alcance declarado antes de empezar

`operador` tiene `USUA_PERM_TRD` y `USUA_PERM_EXPEDIENTE` con **nivel 3**, que es el que exigen
crear y derogar versiones. Se comprobó **contra la base antes de pulsar nada**, siguiendo la
regla de declarar los permisos por adelantado en vez de descubrir el límite chocando con él.

### 8.2 Lo ejecutado

| Caso | Qué se comprobó | Resultado |
|---|---|---|
| B2-02a | Aprobar v1 de `DEM-100` | `aprobada`; acta y fecha persistidas; asiento con actor y payload |
| **B2-02a′** | **`PATCH` directo al proxy, sin pasar por el botón** | **409 `trd_version_inmutable`**, citando Ac. AGN 001/2024 y **ofreciendo la vía correcta**; la fila quedó intacta (5/10) |
| B2-02b | Convalidar v1 | acto administrativo, fecha e instancia guardados; RUSD queda pendiente, como declara el diálogo |
| B2-01 | Crear v2 | nace en `borrador`; la v1 **sigue rigiendo**; el vocabulario se queda con la v1 |
| **B2-01′** | **Convalidar la v2: el relevo** | v1 → `derogada` con `valid_to`; **`DEM-PET` se re-apunta solo** a la v2 |
| B2-03 | Vocabulario tras el relevo | los 6 tipos siguen sirviendo en `/radicar` |
| B2-04 | `/admin/tvd`, `/admin/metadatos`, `/admin/catalogos`, `/admin/plantillas` | las cuatro sanas; **aislamiento TRD↔TVD intacto en ambas direcciones** |

### 8.3 Las tres cosas que hacen que este bloque aguante

1. **El gate no vive en el navegador.** Retirar «Editar» de la fila aprobada no prueba nada; el
   `PATCH` invocado a mano contra el proxy responde 409. Y el mensaje **no se limita a negar**:
   nombra la norma y dice qué hacer en su lugar.
2. **El relevo es un solo acto.** `archive.trd_version_convalidada` y
   `archive.trd_version_derogada` comparten segundo en `audit_log`: convalidar la N+1 y derogar
   la N ocurren en la misma transacción. Si fueran dos pasos, un fallo entre medias dejaría al
   código de serie con dos versiones vigentes o con ninguna.
3. **El re-apuntado del vocabulario estaba previsto y comentado.** Los tipos documentales
   cuelgan de la FILA, no del código, así que sin `repoint_tipos_documentales` la convalidación
   de la N+1 los dejaría huérfanos de una versión recién derogada. Ocurre **al convalidar y no
   al crear**, que es lo que permite a la v1 seguir rigiendo con su vocabulario mientras la v2
   se prepara.

### 8.4 Declarado y no perseguido

- **Cinco de los seis tipos documentales cuelgan de series en `borrador`** y `/radicar` los
  ofrece sin distinguirlos. El riesgo está contenido: `trd_disposition.py` exige
  `estado == "convalidada"`, así que se puede clasificar contra un borrador pero **no destruir
  nada** por él. Bloquearlo dejaría una instalación nueva sin poder radicar hasta recorrer el
  circuito entero, que es peor. Lo que falta es que la pantalla lo diga.
- **La v1 derogada sigue listándose** en `/admin/trd`. Es correcto —muestra su estado y el
  historial del instrumento es parte del instrumento— pero conviene no confundirlo con vigencia.

### 8.5 Falso positivo propio, anotado a propósito

Di por perdida el acta del Comité al ver `acto_administrativo` vacío en la fila. Estaba guardada
en `acta_comite`, una columna distinta que **no vi porque leí un `\d` truncado a 25 líneas**.
`acto_administrativo` es el de la convalidación, otro acto y otro campo.

Queda escrito porque el error es del mismo tipo que los que este plan busca —creerse una lectura
parcial— y esta vez lo cometió quien revisaba. La corrección fue mirar el `UPDATE` real antes de
reportar nada.
