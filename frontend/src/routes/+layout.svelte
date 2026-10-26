<!--
  Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
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
  // Solo el núcleo y la familia SOLID. `all.min.css` (72 KB) arrastra además
  // `regular` y `brands`, y con ellos sus fuentes: el navegador descarga
  // `fa-brands-400.woff2` (116 KB) y `fa-regular-400.woff2` (25 KB) para
  // iconos que esta aplicación no usa ni una vez.
  //
  // Medido, no supuesto: 771 usos de `fas`/`fa-solid` en el árbol y CERO de
  // `far`/`fab`. El pendiente que pedía esto decía «solo usa solid y regular»,
  // y hasta eso estaba de más.
  //
  // Si algún día entra un icono de marca, el import de `brands.min.css` va
  // aquí al lado — y que haga falta ese gesto es la gracia: hoy nadie decidió
  // traerse 141 KB de fuentes, simplemente venían.
  import "@fortawesome/fontawesome-free/css/fontawesome.min.css";
  import "@fortawesome/fontawesome-free/css/solid.min.css";

  import "../styles/app.scss";
</script>

<slot />
