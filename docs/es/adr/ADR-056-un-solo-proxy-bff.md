# ADR-056: Un proxy BFF, no doscientos setenta y seis

**Estado**: Aceptado
**Fecha**: 2026-11-07
**Autores**: Camilo Romero
**Relacionados**: [ADR-011](ADR-011-frontend-sveltekit-monorepo.md) (SvelteKit SSR; **se conserva**), [ADR-020](ADR-020-sistema-diseno-orpyca-asistente.md) (sistema de diseño), [ADR-047](ADR-047-un-proceso-y-cuatro-contenedores.md), [ADR-048](ADR-048-identidad-emitida-por-docuboto.md), [ADR-051](ADR-051-ppi-reducido-vive-en-la-app.md), [ADR-055](ADR-055-publicacion-publica-de-instrumentos.md)

---

## Contexto

El frontend es SvelteKit renderizado en el servidor y actúa como BFF: el navegador nunca llama al API. La forma en que lo hace es el problema.

- **276 archivos `+server.js`**, 22.533 líneas, mediana de 73 líneas cada uno.
- **248 de ellos** repiten el mismo bloque: leer `locals.accessToken`, si no hay sesión devolver 401, `fetch(API_V1 + ruta)`, mapear el error, devolver JSON.
- Existe **un** helper `_proxy` en todo el árbol de rutas; el resto no lo usa.
- Los archivos más grandes pasan de 200 líneas y contienen lógica de negocio (normalización de respuestas, recorte de campos, decisiones de qué mostrar).

La consecuencia no es solo el volumen. Es que **cambiar un endpoint del API no obliga a cambiar nada visible**: hay un archivo por endpoint, y cada uno puede haber divergido en cómo trata los errores. Cuando se retira `/knowledge/search` (ADR-051) hay que tocar tres proxies; cuando se añade un prefijo nuevo hay que crear un archivo.

Las 54 páginas Svelte suman 50.506 líneas y algunas pasan de 900. Eso es un problema distinto y este ADR no lo resuelve: la fusión y el proxy no convierten una pantalla grande en pantallas pequeñas.

## Decisión

### 1. Un único endpoint BFF con lista blanca de rutas

Un solo `+server.js` que atiende `/api/[...ruta]` y reenvía a la app:

```
frontend/src/routes/api/[...ruta]/+server.js
```

Reglas:

1. **Lista blanca explícita**: una tabla `RUTAS` que declara método y patrón. Lo que no está, es 404. Un `[...ruta]` abierto convertiría el frontend en un proxy de todo, incluidos los endpoints que hoy no se exponen.
2. **Sesión obligatoria por defecto**: si la ruta no está marcada `publica: true`, exige `locals.accessToken` y devuelve 401 sin llamar a la app.
3. **Método y cuerpo pasan tal cual.** Sin traducción de esquemas: el contrato ya está en OpenAPI y duplicarlo en el BFF es de donde vienen las divergencias.
4. **Errores traducidos una sola vez**: el formato `{error, detail, status}` que ya usa la app pasa con su código; lo inesperado se convierte en 502 con un mensaje en español, en un solo sitio.
5. **Timeout único** configurable, en lugar de un valor por archivo.

Las páginas dejan de tener proxies por endpoint y llaman `fetch('/api/v1/…')` desde el servidor (o desde el navegador, cuando el dato no es sensible: el proxy es delgado y ya no hay motivo para que todo pase por `+page.server.js`).

### 2. La lista blanca se deriva, no se escribe a mano

La tentación es mantener 276 entradas. En su lugar:

- La tabla se **genera** desde el inventario de rutas que la app publica (su OpenAPI), con una marca por ruta de si es pública.
- Un script (`npm run rutas:bff`) la regenera, y un guardián en CI falla si hay una llamada del frontend a una ruta que no está en la tabla — que es el error real que se comete hoy, y que ahora se detecta en el build en lugar de en producción.

### 3. Lo que se retira con los proxies

| Pieza | Destino |
|---|---|
| 276 `+server.js` (22.533 líneas) | 1 archivo + la tabla generada |
| Guardián «todo proxy exige sesión» (vitest) | Se sustituye por el guardián de la lista blanca, que es la misma afirmación pero sobre una tabla |
| Proxies huérfanos de `/knowledge/*` (12 llamadas) | Apuntan a `document` (ADR-055 §7) o desaparecen con la UI que los usaba |
| Cualquier normalización de respuesta duplicada | Una sola, o ninguna |

### 4. La UI que se retira con esto

- **«Preguntar»** en la bandeja (llamaba a `/knowledge/rag`, que responde 503 por diseño).
- **«Sugerir con antecedentes»** en radicar y borradores (mismo endpoint).
- El panel de **disponibilidad de inteligencia** por entidad.

Se **ocultan**, no se dejan apuntando a un 404. «Buscar» y «antecedentes» por texto completo se conservan con su ruta nueva.

## Alternativas consideradas

- **Mantener los proxies por endpoint y consolidar con un helper.** Descartado: reduce líneas pero no cambia la propiedad dañina, que es que cada endpoint tiene un archivo que puede divergir, y que añadir un endpoint obliga a crear un archivo.
- **Que el navegador llame a la app directamente con CORS y un token en memoria.** Descartado: el proyecto eligió no exponer el API al navegador y almacenar tokens en cookies `httpOnly` (ADR-011); revertir eso reabre XSS como robo de token.
- **Un `[...ruta]` abierto sin lista blanca.** Descartado: expondría rutas internas y de administración que hoy no están proxeadas, y convertiría un error de configuración en una superficie de ataque.
- **Servir el API y el frontend desde el mismo origen sin BFF, con la sesión en una cookie que la app lee.** Descartado: hoy funciona precisamente porque el frontend es el que renueva el token; mover eso a la app significa llevar la lógica de cookies y refresh a Python, que es más trabajo que un proxy de 200 líneas.

## Consecuencias

- **~22.500 líneas menos** y un solo sitio donde un error se traduce mal.
- **Añadir un endpoint deja de requerir un archivo de frontend.** La lista blanca se regenera.
- **El BFF pierde la posibilidad de recortar respuestas por endpoint.** Donde hoy un proxy devuelve 5 campos de 30, devolverá 30 y la página elegirá. Es aceptable: el payload ya viaja dentro de la red local, y el recorte en el BFF no era un control de seguridad sino una comodidad.
- **La lista blanca es una lista de seguridad y hay que tratarla así.** Una ruta marcada `publica: true` por error expone datos sin sesión; el guardián tiene que probar que las públicas son exactamente las esperadas, no solo que las privadas exigen sesión.
- **El guardián de la lista blanca sustituye a un guardián existente.** Hay que conservar la afirmación («toda ruta no pública exige sesión») y no solo cambiar la implementación.
- **Las pantallas grandes siguen grandes.** Este ADR no toca 50.506 líneas de Svelte; si el dolor fuese la UI y no la plomería, el rediseño sería otro.
