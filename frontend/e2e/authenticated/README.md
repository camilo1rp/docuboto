# Suite "authenticated" — requiere la pila completa

Los tests de este directorio ejercitan el login PKCE **real** contra Keycloak y
vistas autenticadas (bandeja, acciones de operador, dock del asistente) contra
el `api-gateway` real.

**No corren en el smoke**: `playwright.config.js` los excluye vía
`testIgnore: ["**/authenticated/**"]`, para que un chequeo de PR siga siendo
determinista y sin infraestructura. Corren con su propio config.

## Qué hay aquí

- `_sesion.js` (helper) y `_auth.setup.js` (proyecto `setup`) — la **única**
  puerta de sesión de la suite.
- `login-flow.spec.js`, `bandeja-operador.spec.js`, `asistente-dock.spec.js` — los
  flujos de operador.
- **`rbac-restringido.spec.js`** — dos perfiles: `consulta` (sin `USUA_PERM_ADMIN`)
  no ve «Administración» y forzar `/admin/usuarios` o `/admin/trd` termina en
  `/403`; `e2e2` sí entra. `consulta` es del grupo «Consulta (solo lectura, nivel 1)»
  de `scripts/seed_demo.py` (y está en `infra/keycloak/realm-dev.json`).
- **`firma-gate-orden.spec.js`** — el gate de orden de la firma (AREA-05-08): sobre el
  borrador con circuito de dos turnos que siembra `seed_demo.py` (texto editado tras
  abrirlo), un usuario fuera de la cadena (`e2e1`) recibe `403 no_es_firmante` sin
  `documento_modificado` ni `version`, y el firmante con turno (`jefe`), `409
documento_modificado` (control positivo).
- **`axe-vistas-clave.spec.js`** — `@axe-core/playwright` en 7 vistas clave, WCAG 2.1
  AA: falla con violaciones `serious`/`critical`.
- **`adaptabilidad-app.spec.js`** — 17 pantallas × 3 anchos (móvil 390, portátil
  1366, escritorio 1920), midiendo desplazamiento horizontal y tablas estrujadas
  con un navegador de verdad. Existe porque ese defecto lo reportó una persona
  usando el producto **con la suite entera en verde**: Vitest corre sobre jsdom,
  que no calcula layout, así que dos mil pruebas no sabían cuántos píxeles ocupa
  nada. Su gemelo para las páginas públicas vive en `e2e/adaptabilidad.spec.js` y
  sí corre en el smoke, porque no necesita sesión. El medidor es el mismo
  (`e2e/_adaptabilidad.js`): copiarlo sería garantizar que dentro de un mes midan
  cosas distintas.

## Cómo correrlos

1. Levantar la pila completa desde la raíz del repo:

   ```bash
   ./scripts/dev-secrets.sh
   docker compose --profile dev up -d --build
   python scripts/seed_demo.py --gateway http://localhost:19080
   ```

   Espera a que Keycloak, el `api-gateway` y Postgres estén _healthy_
   (`docker compose ps`).

2. Ejecutar con el config dedicado, pasando las credenciales del usuario de
   prueba que sembró el seed:

   ```bash
   E2E_USERNAME=operador E2E_PASSWORD='...' \
     npx playwright test --config=playwright.authenticated.config.js
   ```

Las credenciales **no tienen valor por defecto a propósito**: un default
funcional aquí acabaría commiteado y sería una cuenta conocida más.

**La sesión se abre una vez por IDENTIDAD** (2026-09-19, ampliado el 2026-09-24). El
proyecto `setup` ejecuta `_auth.setup.js`, que inicia sesión y guarda el estado en
`playwright/.auth/` (ignorado por git); las demás specs lo heredan con
`test.use({ storageState: estadoDe(...) })` y **ninguna teclea credenciales**. Antes
cada prueba hacía su propio login contra Keycloak: con 60 casos eran 60 inicios de
sesión. Un guardián de Vitest lo mantiene así
(`src/pruebas/una-sola-puerta-de-sesion.test.js`), con las tres excepciones
declaradas: el helper, el setup y `login-flow.spec.js`, que **se aísla del estado
guardado a propósito** porque su objeto es el inicio de sesión y heredar una sesión
abierta la dejaría midiendo nada.

**Y son tres identidades, no una** (`e2e1`, `e2e2`, `e2e3` — ver `IDENTIDADES` en
`_sesion.js`). La cuota del gateway es **por persona** (120 peticiones/min) y cada
pantalla cuesta unas **7**, así que la suite entera concentrada en una sola identidad
se come su propio 429 a mitad de la corrida. En producción esas mismas peticiones las
reparten veinte personas.

El reparto **no esquiva el límite**: deja de pedirle a una identidad que represente a
una oficina. Subir el tope solo para las pruebas taparía el eje que existe para que la
cuota sea de cada quien. Las tres cuentas están en `infra/keycloak/realm-dev.json` y
comparten **el mismo grupo** que `operador` («Demostración», los 25 permisos), para que
repartir la carga no cambie el sujeto de la prueba — con perfiles distintos, un fallo
de permisos se leería como un fallo de la pantalla.

Si el entorno se recrea desde cero, las tres cuentas nacen del realm, pero **estar en
Keycloak no da permisos**: el alta en el tenant es perezosa (al primer login) y llega
sin grupo. Hay que copiarles la membresía de `operador`:

```sql
INSERT INTO tenant_demo.auth_memberships (user_id, group_id)
SELECT u.id, (SELECT m.group_id FROM tenant_demo.auth_memberships m
              JOIN tenant_demo.auth_users o ON o.id = m.user_id
              WHERE o.username = 'OPERADOR' LIMIT 1)
FROM tenant_demo.auth_users u WHERE u.username IN ('E2E1','E2E2','E2E3')
ON CONFLICT (user_id, group_id) DO NOTHING;
```

Sin credenciales, el proyecto `setup` **falla y se lleva la suite entera**, en
vez de saltarse en silencio. Es deliberado: una suite que se salta sola informa
igual que una en verde, y este directorio existe porque un «todo pasa» tapó un
defecto que el usuario sí veía.

> **Histórico, y la mitad que faltaba.** Estos tests estuvieron apagados con
> **triple freno**: `testIgnore`, `test.describe.skip` en cada archivo y
> credenciales ausentes. El propio README pedía «crea un segundo config sin esa
> exclusión», y nadie lo creó. Un test que exige editar su propia configuración
> para ejecutarse **no se ejecuta nunca**: los dos frenos redundantes se
> retiraron y quedó el que sí significa algo.
>
> Retirar los frenos **no bastó**. Al ejecutarlos por primera vez, **4 de 6
> fallaron**: eran esqueletos escritos contra una interfaz imaginada. El
> localizador de la contraseña casaba con dos elementos (el `<input>` y el
> botón «Show password» de Keycloak, cuyo `aria-label` contiene la palabra), y
> los de bandeja daban por supuesta una tabla cuando `/bandeja` abre en
> **Tablero**. Reactivar una suite sin correrla es media corrección: hoy pasan
> las seis, y el inicio de sesión vive en **un solo sitio** (`_sesion.js`) en
> vez de copiado en tres.
>
> Aquel «en vez de copiado en tres» era **la mitad de la historia**: había una
> cuarta copia, en `asistente-dock.spec.js`, y se quedó fuera precisamente
> porque era la que funcionaba, así que nada la delató. Retirada en 2026-09-19,
> ya con guardián: un recuento que se cree completo no informa de menos, afirma
> de más.

## Estado verificado (2026-09-19)

```
E2E_USERNAME=operador E2E_PASSWORD='…' npx playwright test --config=playwright.authenticated.config.js
  60 passed (1.3 min)
```

Verde entero, y es la primera vez. El recorrido dice más que el número: **35/17** al
principio (la barra superior desbordaba en todas las pantallas de móvil), **47/5**
(el `sr-only` que desplazaba el documento), **55/3** y **57/1** (la cuota del gateway,
que iba por IP) y **60/0** con la cuota repartida por identidades.

Los cinco rojos que esta suite mantuvo a propósito entre el 19 y el 24 de septiembre
**están cerrados**: los provocaba un `span.sr-only` (1 px, `position: absolute`) dentro
de las cabeceras de las tablas anchas, que se resolvía contra el documento en vez de
contra el envoltorio que recorta. Detalle en [`roadmap.md`](../../../docs/es/roadmap.md).

La primera corrida completa dio **35 verdes y 17 rojos**, y los 17 eran todos los
casos de móvil: un **único** defecto global —la barra superior desbordaba por el
`min-width: auto` por defecto de un hijo flex— que se leía como diecisiete
defectos de pantalla.

> **El instrumento también se equivocaba.** La lista de «culpables» que imprimía el
> test salía de `getBoundingClientRect()`, que **ignora el recorte de los ancestros**:
> acusaba a una tabla perfectamente contenida en cada corrida, y mandó el diagnóstico
> días por el camino equivocado. Se comprobó pasándolo sobre una página mínima **sana**:
> acusaba exactamente igual. Un culpable que aparece también cuando no hay delito no es
> una pista.

## Por qué no corre en CI/smoke

- Requiere Docker Compose completo (Postgres + Keycloak + 11 microservicios)
  arriba y con datos semilla — infraestructura pesada para un chequeo de PR.
- Depende de credenciales de un usuario de prueba (secreto), no solo de código.
- Es inherentemente más frágil que el smoke sin backend (arranque de
  contenedores, _timing_ de Keycloak).
- **Escribe**: los actos que ejerce dejan asiento en `audit_log`, que es
  _append-only_ por Ley 594. No se corre contra un tenant que importe.

El smoke sin backend (`e2e/route-guard.spec.js`, `e2e/public-pages.spec.js`)
cubre de forma determinista el contrato de la guarda de sesión SSR y las
páginas públicas, que es donde vivió el bug histórico D-16 (500 SSR sin
pruebas de navegador).

## Relación con el plan de pruebas

Esta suite es el sedimento automatizable de `docs/es/plan-pruebas.md`: los
recorridos que resultaron **estables** durante la revisión por navegador. El
plan cubre mucho más de lo que aquí está automatizado —y lo dice—, pero un plan
que solo puede ejecutar una persona se degrada en cuanto deja de ejecutarlo.
