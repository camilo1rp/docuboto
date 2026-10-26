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
  / — Landing pública de Docuboto.

  Un usuario con sesión activa nunca ve este marcado: +page.server.js#load
  redirige a /dashboard antes de renderizar. Esta página es la puerta de
  entrada para visitantes anónimos: presenta el producto e invita a iniciar
  sesión. Un único <h1> (el titular del hero); el resto de la jerarquía usa
  h2/h3 en orden.
-->
<script>
  import Button from "$lib/components/ui/Button.svelte";
  import Card from "$lib/components/ui/Card.svelte";

  const features = [
    {
      icon: "fa-solid fa-inbox",
      title: "Radicación E/S/I",
      body: "Registra comunicaciones de entrada, salida e internas con número de radicado único e inmutable.",
    },
    {
      icon: "fa-solid fa-folder-tree",
      title: "Expedientes & TRD",
      body: "Organiza cada asunto en expedientes y aplica la Tabla de Retención Documental de tu entidad.",
    },
    {
      icon: "fa-solid fa-diagram-project",
      title: "Flujos de distribución",
      body: "Enruta documentos entre dependencias con trazabilidad completa de cada traslado.",
    },
    {
      icon: "fa-solid fa-signature",
      title: "Firma electrónica",
      body: "Firma personal y sellado del índice electrónico con estándar XAdES.",
    },
    {
      icon: "fa-solid fa-shield-halved",
      title: "Auditoría inmutable",
      body: "Cada acción queda registrada en un historial que no se puede alterar ni borrar.",
    },
    {
      icon: "fa-solid fa-wand-magic-sparkles",
      title: "IA / capa MCP",
      body: "Un asistente conversacional que traduce lenguaje natural en acciones auditadas, con tu autorización.",
    },
  ];

  const trustItems = [
    "Ley 594/2000",
    "Acuerdo AGN 060/2001",
    "Decreto 1080/2015",
    "Ley 1712/2014",
    "MIPG",
    "AGPL v3",
  ];

  const evolution = [
    { before: "Monolito PHP", after: "Microservicios FastAPI" },
    { before: "Sin multi-tenant", after: "Multi-tenant nativo por esquema" },
    { before: "Pestañas múltiples por tarea", after: "Vistas consolidadas y guiadas" },
    { before: "Búsqueda limitada", after: "Búsqueda de texto completo + IA" },
    { before: "Sin trazabilidad de IA", after: "Asistente con auditoría y confirmación" },
    { before: "Firma externa manual", after: "Firma electrónica integrada (XAdES)" },
  ];
</script>

<svelte:head>
  <title>Docuboto — Gestión documental libre, moderna e inteligente</title>
  <meta
    name="description"
    content="OrpycaMCP: radica, traza y conserva cada documento oficial con validez legal. Software libre, multi-entidad y listo para la IA."
  />
</svelte:head>

<a class="skip-link" href="#contenido">Saltar al contenido principal</a>

<div class="landing">
  <!-- ============================== NAV ============================== -->
  <header class="landing-nav">
    <div class="landing-nav__inner">
      <a href="/" class="landing-nav__brand">
        <!--
          A la altura de una barra de navegación (32-36px) la marca denominativa
          del logotipo completo queda ilegible. Aquí entra solo el isotipo, con el
          nombre en texto: se lee a cualquier tamaño y no obliga a servir un mapa
          de bits enorme para una franja de 36px.
        -->
        <img src="/brand/orpyca-mark.svg" alt="" class="landing-nav__logo" />
        <span class="landing-nav__wordmark">Orpyca</span>
      </a>

      <nav class="landing-nav__links" aria-label="Navegación principal">
        <!-- Los dos trámites CIUDADANOS van primero y antes que «Iniciar
             sesión»: quien llega aquí sin cuenta viene a radicar o a consultar,
             no a entrar al sistema. Estuvieron construidos en el backend y sin
             ninguna forma de llegar a ellos. -->
        <a href="/radicacion">Radicar un documento</a>
        <a href="/consulta">Consultar mi trámite</a>
        <!-- Publicidad previa de la eliminación documental (RF-TRD-07, fase 2):
             tiene que estar donde alguien la encuentre sin buscarla, o el plazo
             para oponerse se agota sin que nadie sepa que existía. -->
        <a href="/eliminacion">Eliminación documental</a>
        <a href="/indice-clasificados">Información clasificada y reservada</a>
        <a href="#caracteristicas">Características</a>
        <a href="#normativa">Normativa</a>
        <a href="#evolucion">Evolución</a>
      </nav>

      <Button href="/login" variant="primary" size="md">Iniciar sesión</Button>
    </div>
  </header>

  <main id="contenido">
    <!-- ============================== HERO ============================== -->
    <section class="hero">
      <div class="hero__inner">
        <div class="hero__copy">
          <span class="hero__badge">
            <i class="fa-solid fa-heart" aria-hidden="true"></i>
            Software libre · AGPL v3
          </span>

          <h1 class="hero__title">
            El sistema de gestión documental que tu entidad ya conoce, ahora moderno, libre e
            inteligente.
          </h1>

          <p class="hero__subtitle">
            Radica, traza y conserva cada documento oficial con validez legal. Software libre,
            multi-entidad y listo para la IA.
          </p>

          <div class="hero__actions">
            <Button href="/login" variant="primary" size="lg">Iniciar sesión</Button>
            <Button href="#caracteristicas" variant="secondary" size="lg">Conocer ORPYCA</Button>
          </div>
        </div>

        <div class="hero__visual" aria-hidden="true">
          <div class="radicado-mock">
            <div class="radicado-mock__header">
              <span class="radicado-mock__label">Radicado</span>
              <span class="radicado-mock__number">2026-ICETEX-E-004821</span>
            </div>
            <div class="radicado-mock__body">
              <div class="radicado-mock__row">
                <span class="radicado-mock__key">Tipo</span>
                <span class="radicado-mock__value">Entrada</span>
              </div>
              <div class="radicado-mock__row">
                <span class="radicado-mock__key">Dependencia</span>
                <span class="radicado-mock__value">Atención al ciudadano</span>
              </div>
            </div>
            <ol class="radicado-mock__trace">
              <li class="radicado-mock__trace-item radicado-mock__trace-item--done">
                <span class="radicado-mock__dot"></span>
                Radicado
              </li>
              <li class="radicado-mock__trace-item radicado-mock__trace-item--done">
                <span class="radicado-mock__dot"></span>
                Distribuido
              </li>
              <li class="radicado-mock__trace-item radicado-mock__trace-item--active">
                <span class="radicado-mock__dot"></span>
                En trámite
              </li>
              <li class="radicado-mock__trace-item">
                <span class="radicado-mock__dot"></span>
                Archivado
              </li>
            </ol>
          </div>
        </div>
      </div>
    </section>

    <!-- ========================== TRUST STRIP ========================== -->
    <section class="trust" id="normativa" aria-labelledby="trust-heading">
      <h2 id="trust-heading" class="visually-hidden">Cumplimiento normativo</h2>
      <ul class="trust__list">
        {#each trustItems as item (item)}
          <li class="trust__chip">{item}</li>
        {/each}
      </ul>
    </section>

    <!-- ========================== CARACTERÍSTICAS ========================== -->
    <section class="features" id="caracteristicas" aria-labelledby="features-heading">
      <div class="section-inner">
        <h2 id="features-heading" class="section-title">Todo lo que tu entidad necesita</h2>
        <p class="section-subtitle">Un sistema, un flujo de trabajo, sin pestañas dispersas.</p>

        <div class="features__grid">
          {#each features as feature (feature.title)}
            <Card>
              <div class="feature-card">
                <i class="{feature.icon} feature-card__icon" aria-hidden="true"></i>
                <h3 class="feature-card__title">{feature.title}</h3>
                <p class="feature-card__body">{feature.body}</p>
              </div>
            </Card>
          {/each}
        </div>
      </div>
    </section>

    <!-- ========================== EVOLUCIÓN ========================== -->
    <section class="evolution" id="evolucion" aria-labelledby="evolution-heading">
      <div class="section-inner">
        <h2 id="evolution-heading" class="section-title">De Orfeo a ORPYCA</h2>
        <p class="section-subtitle">
          Conservamos las reglas de negocio que tu entidad ya conoce; modernizamos la tecnología que
          las soporta.
        </p>

        <div class="evolution__table-wrap">
          <table class="evolution__table">
            <caption class="visually-hidden">Comparación tecnológica entre Orfeo y ORPYCA</caption>
            <thead>
              <tr>
                <th scope="col">Orfeo (antes)</th>
                <th scope="col">ORPYCA (ahora)</th>
              </tr>
            </thead>
            <tbody>
              {#each evolution as row (row.before)}
                <tr>
                  <td>{row.before}</td>
                  <td>
                    <i class="fa-solid fa-arrow-right evolution__arrow" aria-hidden="true"></i>
                    {row.after}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- ========================== CTA FINAL ========================== -->
    <section class="cta-final" aria-labelledby="cta-final-heading">
      <h2 id="cta-final-heading" class="cta-final__title">
        Empieza a radicar, trazar y conservar hoy mismo.
      </h2>
      <p class="cta-final__subtitle">
        Ingresa con tu cuenta institucional y encuentra tus radicados en segundos.
      </p>
      <Button href="/login" variant="primary" size="lg">Iniciar sesión</Button>
    </section>
  </main>

  <!-- ============================== FOOTER ============================== -->
  <footer class="landing-footer">
    <div class="landing-footer__inner">
      <span class="landing-footer__brand">
        <img src="/brand/orpyca-mark.svg" alt="" class="landing-footer__mark" />
        ORPYCA
      </span>
      <p class="landing-footer__note">
        Software libre bajo licencia AGPL v3. Hereda las reglas de negocio de
        <a href="https://github.com/orfeosgd/orfeo" target="_blank" rel="noopener noreferrer"
          >Orfeo GPL</a
        >.
      </p>
    </div>
  </footer>
</div>

<style lang="scss">
  .skip-link {
    position: absolute;
    left: -9999px;
    top: 0;
    z-index: 100;
    background: var(--op-primary-dark);
    color: var(--op-on-primary);
    padding: var(--op-space-2) var(--op-space-4);
    border-radius: var(--op-radius-sm);

    &:focus {
      left: var(--op-space-4);
      top: var(--op-space-4);
    }
  }

  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }

  .landing {
    background: var(--op-bg);
    color: var(--op-text);
    font-family: var(--op-font-family);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }

  // ---------------------------------------------------------------- Nav ---
  .landing-nav {
    position: sticky;
    top: 0;
    z-index: 20;
    background: var(--op-surface);
    border-bottom: 1px solid var(--op-border);

    &__inner {
      max-width: 1200px;
      margin: 0 auto;
      padding: var(--op-space-3) var(--op-space-5);
      display: flex;
      align-items: center;
      gap: var(--op-space-5);
    }

    &__brand {
      display: inline-flex;
      align-items: center;
      gap: var(--op-space-2);
      flex-shrink: 0;
      text-decoration: none;
    }

    &__logo {
      height: 36px;
      width: 36px;
      object-fit: contain;
    }

    &__wordmark {
      font-family: var(--op-font-display);
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-bold);
      color: var(--op-primary-dark);
      letter-spacing: 0.01em;
    }

    &__links {
      display: none;
      flex: 1;
      gap: var(--op-space-5);

      @media (min-width: 768px) {
        display: flex;
      }

      a {
        color: var(--op-text-secondary);
        text-decoration: none;
        font-size: var(--op-font-sm);
        font-weight: var(--op-weight-medium);
        padding: var(--op-space-2) 0;
        min-height: 44px;
        display: inline-flex;
        align-items: center;
        transition: color var(--op-transition);

        &:hover {
          color: var(--op-primary-dark);
        }

        &:focus-visible {
          outline: 2px solid var(--op-primary);
          outline-offset: 2px;
        }
      }
    }
  }

  // --------------------------------------------------------------- Hero ---
  .hero {
    background: linear-gradient(135deg, var(--op-brand-700), var(--op-primary-dark));
    color: var(--op-on-primary);
    padding: var(--op-space-8) var(--op-space-5);

    &__inner {
      max-width: 1200px;
      margin: 0 auto;
      display: grid;
      gap: var(--op-space-7);
      align-items: center;

      @media (min-width: 960px) {
        grid-template-columns: 1.1fr 0.9fr;
      }
    }

    &__badge {
      display: inline-flex;
      align-items: center;
      gap: var(--op-space-2);
      background: var(--op-on-brand-overlay);
      border: 1px solid var(--op-on-brand-overlay-border);
      border-radius: var(--op-radius-full);
      padding: var(--op-space-1) var(--op-space-4);
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-medium);
      margin-bottom: var(--op-space-5);
    }

    &__title {
      font-family: var(--op-font-display);
      font-weight: var(--op-weight-display);
      font-size: clamp(28px, 4vw, var(--op-font-size-display));
      line-height: 1.15;
      margin: 0 0 var(--op-space-4);
    }

    &__subtitle {
      font-size: var(--op-font-lg);
      line-height: 1.6;
      color: var(--op-on-primary);
      margin: 0 0 var(--op-space-6);
      max-width: 60ch;
    }

    &__actions {
      display: flex;
      flex-wrap: wrap;
      gap: var(--op-space-3);
    }
  }

  // ------------------------------------------------------ Radicado mock ---
  .radicado-mock {
    background: var(--op-surface);
    color: var(--op-text);
    border-radius: var(--op-radius-lg);
    box-shadow: var(--op-shadow);
    padding: var(--op-space-5);
    max-width: 420px;
    margin: 0 auto;

    &__header {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
      padding-bottom: var(--op-space-4);
      border-bottom: 1px solid var(--op-border);
      margin-bottom: var(--op-space-4);
    }

    &__label {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    &__number {
      font-family: var(--op-font-family-mono);
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-bold);
      color: var(--op-primary-dark);
    }

    &__body {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-2);
      margin-bottom: var(--op-space-5);
    }

    &__row {
      display: flex;
      justify-content: space-between;
      font-size: var(--op-font-sm);
    }

    &__key {
      color: var(--op-text-secondary);
    }

    &__value {
      font-weight: var(--op-weight-medium);
    }

    &__trace {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: var(--op-space-3);
    }

    &__trace-item {
      display: flex;
      align-items: center;
      gap: var(--op-space-3);
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);

      &--done {
        color: var(--op-text);
      }

      &--active {
        color: var(--op-primary-dark);
        font-weight: var(--op-weight-bold);
      }
    }

    &__dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--op-border);
      flex-shrink: 0;
    }

    &__trace-item--done &__dot,
    &__trace-item--active &__dot {
      background: var(--op-primary);
    }
  }

  // -------------------------------------------------------- Trust strip ---
  .trust {
    background: var(--op-surface);
    border-bottom: 1px solid var(--op-border);
    padding: var(--op-space-5);

    &__list {
      max-width: 1200px;
      margin: 0 auto;
      list-style: none;
      display: flex;
      flex-wrap: wrap;
      justify-content: center;
      gap: var(--op-space-3);
      padding: 0;
    }

    &__chip {
      background: var(--op-primary-light);
      color: var(--op-primary-dark);
      border-radius: var(--op-radius-full);
      padding: var(--op-space-1) var(--op-space-4);
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-medium);
    }
  }

  // ------------------------------------------------------- Section base ---
  .section-inner {
    max-width: 1200px;
    margin: 0 auto;
    padding: var(--op-space-8) var(--op-space-5);
  }

  .section-title {
    font-family: var(--op-font-display);
    font-size: var(--op-font-2xl);
    font-weight: var(--op-weight-bold);
    text-align: center;
    margin: 0 0 var(--op-space-2);
  }

  .section-subtitle {
    text-align: center;
    color: var(--op-text-secondary);
    font-size: var(--op-font-base);
    max-width: 60ch;
    margin: 0 auto var(--op-space-7);
  }

  // ------------------------------------------------------- Feature grid ---
  .features {
    background: var(--op-bg);

    &__grid {
      display: grid;
      gap: var(--op-space-5);
      grid-template-columns: 1fr;

      @media (min-width: 640px) {
        grid-template-columns: repeat(2, 1fr);
      }

      @media (min-width: 960px) {
        grid-template-columns: repeat(3, 1fr);
      }
    }
  }

  .feature-card {
    &__icon {
      font-size: 24px;
      color: var(--op-primary-dark);
      margin-bottom: var(--op-space-3);
    }

    &__title {
      font-size: var(--op-font-md);
      font-weight: var(--op-weight-bold);
      margin: 0 0 var(--op-space-2);
    }

    &__body {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      line-height: 1.6;
      margin: 0;
    }
  }

  // --------------------------------------------------------- Evolution ---
  .evolution {
    background: var(--op-surface);
    border-top: 1px solid var(--op-border);
    border-bottom: 1px solid var(--op-border);

    &__table-wrap {
      overflow-x: auto;
      // `position: relative` NO es cosmético: sin bloque contenedor propio, un
      // `position: absolute` de dentro (el `sr-only` de las cabeceras) se resuelve
      // contra el DOCUMENTO y este `overflow` no lo alcanza — desplazaba la página
      // entera hacia una franja vacía. Ver el hallazgo de los 390 px.
      position: relative;
    }

    &__table {
      width: 100%;
      border-collapse: collapse;
      font-size: var(--op-font-sm);

      th,
      td {
        text-align: left;
        padding: var(--op-space-3) var(--op-space-4);
        border-bottom: 1px solid var(--op-border);
      }

      th {
        color: var(--op-text-secondary);
        font-weight: var(--op-weight-medium);
        font-size: var(--op-font-xs);
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }

      td:first-child {
        color: var(--op-text-secondary);
      }

      td:last-child {
        font-weight: var(--op-weight-medium);
      }
    }

    &__arrow {
      color: var(--op-primary-dark);
      margin-right: var(--op-space-2);
    }
  }

  // ---------------------------------------------------------- CTA final ---
  .cta-final {
    background: linear-gradient(135deg, var(--op-brand-700), var(--op-primary-dark));
    color: var(--op-on-primary);
    text-align: center;
    padding: var(--op-space-8) var(--op-space-5);

    &__title {
      font-family: var(--op-font-display);
      font-size: var(--op-font-2xl);
      font-weight: var(--op-weight-bold);
      margin: 0 0 var(--op-space-3);
    }

    &__subtitle {
      font-size: var(--op-font-base);
      color: var(--op-on-primary);
      max-width: 60ch;
      margin: 0 auto var(--op-space-6);
    }
  }

  // --------------------------------------------------------------- Footer ---
  .landing-footer {
    background: var(--op-text);
    color: var(--op-on-brand-text-muted);
    padding: var(--op-space-6) var(--op-space-5);

    &__inner {
      max-width: 1200px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: var(--op-space-2);
      align-items: center;
      text-align: center;

      @media (min-width: 640px) {
        flex-direction: row;
        justify-content: space-between;
        text-align: left;
      }
    }

    &__brand {
      display: inline-flex;
      align-items: center;
      gap: var(--op-space-2);
      font-family: var(--op-font-display);
      font-weight: var(--op-weight-bold);
      color: var(--op-surface);
    }

    &__mark {
      width: 26px;
      height: 26px;
      object-fit: contain;
    }

    &__note {
      margin: 0;
      font-size: var(--op-font-xs);

      a {
        color: var(--op-surface);
        text-decoration: underline;

        &:focus-visible {
          outline: 2px solid var(--op-surface);
          outline-offset: 2px;
        }
      }
    }
  }
</style>
