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

```bash
./scripts/dev-secrets.sh                       # idempotente; sin él signature-service no arranca
docker compose --profile dev up -d --build     # + --profile assistant si se prueba el dictado
python scripts/seed_demo.py --gateway http://localhost:19080
```

| Recurso       | URL                          | Para qué                                                    |
| ------------- | ---------------------------- | ----------------------------------------------------------- |
| Aplicación    | http://localhost:19300       | todo el plan                                                |
| Gateway / API | http://localhost:19080/docs  | verificación fuera de pantalla                              |
| MailHog       | http://localhost:8025        | **el único sitio donde se ve** que las notificaciones salen |
| MinIO         | http://localhost:19901       | anexos, índices firmados, actas                             |
| Keycloak      | http://localhost:19180/admin | identidad                                                   |

El seed crea dos usuarios **a propósito distintos**, porque un solo perfil no permite observar
ninguno de los dos ejes de autorización:

| Usuario    | Permisos                                         | Nivel CRUD | Clearance       | Para qué                   |
| ---------- | ------------------------------------------------ | ---------- | --------------- | -------------------------- |
| `operador` | los 25 operativos (todos menos `USUA_PERM_ROOT`) | 3          | 3 (Reservada)   | ejecutar el plan           |
| `consulta` | `PERM_RADI` + `USUA_PERM_EXPEDIENTE`             | **1**      | **1** (Pública) | el barrido negativo de B13 |

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

| #       | Bloque                                                                                                                                                   | Produce / por qué va aquí                                                                  |
| ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| **B0**  | Arranque y sesión — login PKCE, `/403`, guarda SSR, `/perfil` (contexto, TOTP, credencial de firma)                                                      | TOTP y credencial son **prerequisito duro** de B6 y B11                                    |
| **B1**  | Identidad — `/admin/usuarios`, `/admin/grupos` (permisos, nivel CRUD, clearance), `/admin/dependencias`, `/admin/parametros`                             | el clearance habilita la clasificación de B3; los festivos cambian el plazo que B3 calcula |
| **B2**  | Instrumentos — `/admin/trd` y `/admin/tvd` (nueva versión → aprobar → convalidar → RUSD), `/admin/metadatos`, `/admin/catalogos`, `/admin/plantillas`    | **sin serie y tipo documental no se puede radicar**                                        |
| **B3**  | Radicación — `/radicar` (5 pasos, anexos, sugerencia RAG), `/borradores` (crear → aprobar → radicar)                                                     | produce las Entradas y la Salida que consume todo lo demás                                 |
| **B4**  | Bandeja y trámite — 3 vistas, filtros, drawer, distribuir, asignar, trasladar, devolver, VoBo en cadena, ejecutar transacción, respuesta vinculada       | produce la Salida vinculada que firma B6                                                   |
| **B5**  | Expedientes — las 8 pestañas: vincular (individual y por lote), excluir, foliar, metadatos, índice electrónico, integridad, versiones, exportar ZIP      | **foliar antes de cerrar**: el cierre de B11 congela la foliación                          |
| **B6**  | Firma — `/firmas` (individual, lote, rechazo con motivo), firmar índice, `/admin/seguridad/claves`                                                       | aquí se prueba la **cadena de firmantes en orden**                                         |
| **B7**  | Envíos postales — `/envios`: registrar, cambiar estado, devolución, planilla                                                                             | `Actualizar rastreo` es **stub declarado**, no defecto                                     |
| **B8**  | Archivo físico — ubicaciones, unidades, vincular, localizar, préstamos, FUID (JSON y XML)                                                                | su ubicación alimenta la transferencia de B11                                              |
| **B9**  | Consulta y asistente — `/busqueda` (exacta, semántica, antecedentes), `/dashboard`, `/reportes`, asistente, marco de alcance, propuesta firmada, dictado | la búsqueda semántica valida de paso la **ingesta event-driven** de E21                    |
| **B10** | Interoperabilidad — exportar ZIP, importar (stepper de 4 pasos), lotes con seguimiento de job                                                            | el _round-trip_ export→import es el caso de valor                                          |
| **B11** | **Actos irreversibles** (orden interno abajo)                                                                                                            | cada uno **quema** su objeto                                                               |
| **B12** | Observabilidad — `/admin/auditoria` (**+ reverificar cadena**), `/admin/notificaciones`, `/admin/colas`, `/admin/preservacion`, `/admin/pinar`           | el último **a propósito**: es el instrumento que verifica todo lo anterior                 |
| **B13** | Barrido negativo con `consulta`                                                                                                                          | cierra los dos ejes de gate sin duplicar el plan                                           |

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
- **Ingesta IMAP** — gateada y enrutada, pero **sin ninguna pantalla**.
- **OAI-PMH y CMIS** (`public_interop_enabled=False`, sin pantalla).
- **PQRS ciudadana y verificación pública por código** — endpoints públicos **sin pantalla** en
  `(public)/`.
- Callback postal HMAC, protocolo MCP externo, `indice/rebuild`, `/auth/clearance`.

De esa lista, **tres merecen reportarse como hallazgo de producto** aunque no sean del plan:
PQRS, verificación pública e ingesta IMAP **existen en el backend y no hay forma de llegar a
ellas**. Son capacidades construidas y no ofrecidas.

---

## 4.bis Decisión de producto abierta (resultado de B13)

El barrido negativo dejó una pregunta que **no es un defecto de código sino una decisión que
alguien tiene que tomar**, y por eso se anota aquí en vez de resolverse por cuenta propia:

> **Las lecturas de TRD (4 endpoints) y de PINAR (11) no exigen ningún permiso**, y sus proxies
> BFF solo comprueban que haya sesión. Un usuario sin `USUA_PERM_TRD` ni `USUA_PERM_ADMIN` ve el
> listado y el detalle completos escribiendo la URL — aunque la barra lateral le esconde
> Administración entera y `/admin` gatea sus tarjetas.

Los dos son instrumentos de **publicación obligatoria** (Acuerdo AGN 004/2019 para la TRD,
Acuerdo AGN 003/2015 para el PINAR), así que la apertura puede ser deliberada. Pero hoy **no está
declarada en ninguna parte**, y la interfaz dice lo contrario. Las dos salidas son legítimas y
excluyentes: gatear las lecturas —coherente con lo que la interfaz ya comunica— o declararlas
abiertas y dejar de esconder las pantallas.

Lo que no es defendible es lo que hay: que el sistema esconda algo y lo sirva a la vez.

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

> **Y una trampa del propio método.** La primera medición dio «946 de 951, 1 roto» porque
> reimplementé el cálculo del hash a mano para verificarlo. La buena usa
> `orpycamcp_common.audit.compute_hash`, la misma función que escribe los asientos: **no se
> reimplementa la cosa que se está verificando** — se comprueba con ella, o se comprueba otra cosa.
