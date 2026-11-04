# design/ — Rediseño de interfaz Docuboto 3.0

Artefactos de diseño. **No están conectados al backend ni al build de `frontend/`**: son
una propuesta para revisar antes de tocar código.

| Archivo | Qué es |
|---|---|
| [`REDISENO.md`](REDISENO.md) | La especificación: arquitectura de información, capa de inteligencia, sistema de capacidades, modelo de reversibilidad, tokens, componentes, plan de implementación y decisiones abiertas. |
| [`prototipo/index.html`](prototipo/index.html) | Prototipo navegable y autocontenido (sin build, sin servidor). |
| [`prototipo/css/tokens.css`](prototipo/css/tokens.css) | Capa de tokens `--db-*` con alias `--op-*`, con la paleta de Proboto. Es la pieza que se puede soltar en `frontend/src/styles/` sin romper ninguna pantalla. |
| [`prototipo/assets/proboto-mark.svg`](prototipo/assets/proboto-mark.svg) | Isotipo de Proboto (extraído de `proboto.co/favicon.svg`, sin el bloque de metadatos C2PA). |
| `prototipo/preview-*.png` | Capturas de referencia (claro, oscuro, estados, tableta y móvil). |
| [`prototipo/shot.js`](prototipo/shot.js) | Regenera las capturas con el Playwright que ya está en `frontend/node_modules`. |

## Abrir el prototipo

```bash
open design/prototipo/index.html      # macOS
# o simplemente arrástralo al navegador: funciona con file://
```

No necesita red: las tipografías y la marca están copiadas en `prototipo/fonts/` y
`prototipo/assets/`.

## Recorrido sugerido (5 minutos)

1. **`⌘K`** abre la barra única: *Preguntar · Buscar · Hacer*.
2. **Escritorio → "Posible duplicado de E-2026-4798"** abre el radicado con la tarjeta de
   propuesta. **Aplicar** deja una barra de deshacer de 10 s.
3. **Bandeja**: marca dos filas → **Etiquetar**. Deshaz con `⌘Z` o con el botón.
4. **Bandeja → Devolver**: el pre-vuelo dice «se revierte con un traslado nuevo» (clase B,
   no irreversible).
5. **Bandeja → "Cerrar es irreversible"** y **Actividad → No reversible**: constancia con la
   ruta legal correcta (clase C).
6. **Capacidades → Regulado**: pre-vuelo con lo que cambia en las reglas; activar; deshacer.
7. **Capacidades**: intenta apagar *Radicación y consecutivo* y lee por qué se niega.
8. **Inteligencia**: cambia de nivel; *Reservada* sigue apagada hasta declararla.
9. **Interruptor de tema** (arriba a la derecha) y **Densidad** (pantalla Sistema).

## Cómo se adopta `tokens.css`

Cópialo a `frontend/src/styles/` junto a `_tokens.scss` y **borra su bloque
`@font-face`**: el frontend ya carga las tipografías con `@fontsource` desde
`app.scss`. El resto del archivo solo declara variables y el bloque final, que
aliasa cada `--op-*` a su equivalente `--db-*`. No cambia ningún nombre de token
existente, así que los guardianes de `frontend/src/styles/*.test.js` siguen pasando.

## Estado

Propuesta con la **identidad de Proboto** aplicada: azul `#095ECE`, cian `#00D4E0`,
noche `#0A1122`, superficie `#F0F5FC`, y el isotipo de proboto.co (en la barra lateral y
como rostro del agente). Los datos son ficticios pero el dominio es real (radicado,
expediente, TRD, anulación, retención legal, WORM). El modelo de reversibilidad del §6 está contrastado
contra los endpoints y modales que existen hoy — ver el apéndice A de la especificación.
