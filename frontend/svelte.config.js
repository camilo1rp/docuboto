// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

// Configuración SvelteKit — SSR con adapter-node (igual que sgdINTI).
// El servidor Node renderiza HTML; sigue siendo capa de presentación, no un
// microservicio de dominio (ver ADR-011 y ADR-001).
import adapter from "@sveltejs/adapter-node";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

const config = {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter({
      out: "build",
      precompress: false,
      envPrefix: "",
    }),
    prerender: {
      handleMissingId: "warn",
    },

    // ── Content-Security-Policy ───────────────────────────────────────────
    //
    // No había ninguna. La única barrera contra XSS era `sanitizeHtml.js`,
    // aplicado en las tres superficies que hacen `{@html}`: las observaciones
    // del radicado en el drawer de la bandeja, el cuerpo de los borradores y
    // las plantillas, y los turnos del asistente conversacional. Es una lista
    // de permitidos y funciona — pero es una sola capa, escrita a mano, sobre
    // contenido que en dos de esos tres casos viene de otra persona (el
    // remitente de un documento) o de un modelo de lenguaje. Un fallo del
    // saneador es ejecución de guion con la sesión del funcionario, y las
    // cookies son `httpOnly` pero el guion no las necesita: puede pedirle al
    // gateway lo que quiera desde el propio navegador.
    //
    // La CSP es la segunda capa: aunque un guion se cuele en el HTML, el
    // navegador se niega a ejecutarlo por no llevar el `nonce`.
    //
    // Se declara aquí y no en `hooks.server.js` porque SvelteKit inyecta el
    // script de hidratación en línea y necesita añadirle su propio `nonce`;
    // una cabecera escrita a mano obligaría a `unsafe-inline`.
    csp: {
      mode: "auto",
      directives: {
        "default-src": ["self"],
        // Sin `unsafe-inline`: SvelteKit añade el `nonce` a sus scripts.
        "script-src": ["self"],
        // CON `unsafe-inline`, y es deliberado: Svelte emite atributos
        // `style="..."` para los bindings de estilo, y un atributo en línea no
        // se puede firmar con hash ni con nonce. Declararlo es lo que impide
        // que SvelteKit meta un nonce aquí — si lo metiera, el navegador
        // IGNORARÍA `unsafe-inline` y la interfaz se rompería en silencio.
        "style-src": ["self", "unsafe-inline"],
        // `data:` para los iconos embebidos; `blob:` para las vistas previas
        // generadas en el cliente.
        "img-src": ["self", "data:", "blob:"],
        "font-src": ["self", "data:"],
        // El navegador NUNCA llama al gateway directamente: todas las llamadas
        // pasan por los proxies del propio SSR (regla D-05). Por eso `self`
        // basta y no hay que enumerar el dominio del API.
        "connect-src": ["self"],
        "object-src": ["none"],
        "base-uri": ["self"],
        // Base mínima. Los formularios de la aplicación envían a este origen,
        // PERO el de cierre de sesión termina en el `end_session_endpoint` de
        // Keycloak, que es otro. Ese origen se añade en RUNTIME desde
        // `securityHeaders.js`, porque la URL pública de Keycloak es
        // `$env/dynamic/public` y aquí solo se conocería la del build. Este
        // comentario decía «el logout y los formularios envían solo a este
        // origen»: era falso, y su consecuencia fue que el botón de salir no
        // hacía nada — Chrome bloqueaba el envío y nadie veía por qué.
        "form-action": ["self"],
        "frame-ancestors": ["none"],
      },
    },
  },
};

export default config;
