# Suite "authenticated" — requiere la pila completa

Los tests de este directorio ejercitan el login PKCE **real** contra Keycloak y
vistas autenticadas (bandeja, acciones de operador, dock del asistente) contra
el `api-gateway` real.

**No corren en el smoke**: `playwright.config.js` los excluye vía
`testIgnore: ["**/authenticated/**"]`, para que un chequeo de PR siga siendo
determinista y sin infraestructura. Corren con su propio config.

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
funcional aquí acabaría commiteado y sería una cuenta conocida más. Sin ellas,
los casos que necesitan identidad se saltan solos —esa es la única barrera que
queda—, y los que no la necesitan (credenciales inválidas vuelven a `/login`)
siguen corriendo.

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

## Estado verificado (agosto 2026)

```
E2E_USERNAME=jefe E2E_PASSWORD='…' npx playwright test --config=playwright.authenticated.config.js
  6 passed (14s)
```

Sin credenciales: **5 saltan y 1 corre** — la de credenciales inválidas, que no
necesita identidad. Ese es el comportamiento correcto por defecto.

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
