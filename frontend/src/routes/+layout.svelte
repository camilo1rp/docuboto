<!--
  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
  Contacto: aurigadl@gmail.com

  Software libre: este archivo se distribuye bajo la Licencia Pública General
  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
-->

<!--
  Layout raíz de la aplicación.
  Importa los estilos globales (tokens Orpyca, Bulma, layout, utilities).
  El <slot> renderiza el subtree de la ruta actual.
-->
<script>
  // Tipografías self-hosted (@fontsource, sin CDN externo) — solo los pesos usados.
  import "@fontsource/public-sans/400.css";
  import "@fontsource/public-sans/500.css";
  import "@fontsource/public-sans/600.css";
  import "@fontsource/public-sans/700.css";
  import "@fontsource/space-grotesk/400.css";
  import "@fontsource/space-grotesk/500.css";
  import "@fontsource/space-grotesk/600.css";
  import "@fontsource/space-grotesk/700.css";

  // Font Awesome (iconografía del sistema de diseño — sidebar, botones).
  import "@fortawesome/fontawesome-free/css/all.min.css";

  import "../styles/app.scss";
</script>

<slot />
