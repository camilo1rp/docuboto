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
  admin/eliminacion — Los lotes de eliminación documental (E04, RF-TRD-07).

  La pantalla que faltaba. Hasta ahora se podía proponer un lote desde la cola
  de vencimientos y no había dónde verlo después: el procedimiento existía en
  el backend y se abandonaba a medias en la interfaz — el mismo patrón que ya
  se pagó varias veces en este proyecto, una capacidad construida y no
  ofrecida.

  Las cinco fases están SIEMPRE a la vista, con la que va y las que quedan,
  porque quien publica un inventario tiene que saber que faltan tres pasos
  antes de que se destruya nada — y que el último lo ejecuta una persona distinta de quien avaló.
-->
<script>
  import { invalidateAll } from "$app/navigation";
  import Button from "$lib/components/ui/Button.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import { permissions, permissionLevels, userId } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { mensajeError } from "$lib/utils/apiError.js";
  import { formatearFecha } from "$lib/utils/fecha.js";

  /** @type {import('./$types').PageData} */
  export let data;

  setBreadcrumbs([
    { label: "Administración", href: "/admin" },
    { label: "Eliminación documental" },
  ]);

  $: lotes = data.lotes ?? [];
  $: detalle = data.detalle;
  $: acta = data.acta;
  $: lapidas = data.lapidas ?? [];

  // Dos fuentes para lo mismo y se hacen caso las dos: el nivel que la sesión
  // dice tener y lo que el backend contestó de verdad. Si discrepan manda el
  // backend — es quien decide.
  $: puedeActuar =
    $permissions.includes("USUA_PERM_TRD") &&
    ($permissionLevels["USUA_PERM_TRD"] ?? 0) >= 3 &&
    data.sinPermiso !== true;

  // Avalar y ejecutar piden `USUA_PERM_TRD` con crud>=4 (los actos que habilitan
  // y ejecutan lo irreversible), no el 3 de las fases reversibles. Y la
  // separación de funciones: quien propuso el lote no lo avala y quien lo avaló
  // no lo ejecuta. El backend las impone las dos (403 / 409); esto solo evita
  // ofrecer un botón que siempre respondería que no. Se compara contra el id
  // INTERNO (`userId`), nunca contra `sub`.
  $: nivelIrreversible = puedeActuar && ($permissionLevels["USUA_PERM_TRD"] ?? 0) >= 4;
  $: puedeAvalar = nivelIrreversible && (!$userId || detalle?.creado_por !== $userId);
  $: puedeEjecutar = nivelIrreversible && (!$userId || detalle?.avalado_por !== $userId);

  const ESTADO_LABEL = {
    borrador: "Borrador",
    publicado: "En publicidad",
    avalado: "Avalado por el Comité",
    ejecutado: "Ejecutado",
    anulado: "Retirado",
  };
  const ESTADO_CHIP = {
    borrador: "neutral",
    publicado: "info",
    avalado: "active",
    ejecutado: "neutral",
    anulado: "neutral",
  };

  const HOY = new Date().toISOString().slice(0, 10);

  /** El plazo de publicidad sigue abierto: el aval no se registra todavía. */
  function publicidadAbierta(lote) {
    return lote?.estado === "publicado" && (lote?.publicado_hasta ?? "") >= HOY;
  }

  function fecha(iso) {
    return formatearFecha(iso, { estilo: "medio" }) || "—";
  }

  // ── Acciones ───────────────────────────────────────────────────────────────
  let accion = null; // "publicar" | "avalar" | "anular"
  let enviando = false;
  let errorAccion = "";
  let fPublicadoHasta = "";
  let fActa = "";
  let fFechaAval = HOY;
  let fActo = "";
  let fMotivo = "";
  let fMetodo = "";
  let fObsActa = "";
  let fConfirmacion = "";
  let resultadoEjecucion = null;

  function abrir(cual) {
    accion = cual;
    errorAccion = "";
    fPublicadoHasta = "";
    fActa = "";
    fFechaAval = HOY;
    fActo = "";
    fMotivo = "";
    fMetodo = "";
    fObsActa = "";
    fConfirmacion = "";
    resultadoEjecucion = null;
  }

  function cerrar() {
    accion = null;
    enviando = false;
    errorAccion = "";
  }

  async function ejecutar() {
    enviando = true;
    errorAccion = "";
    try {
      const res = await fetch(`/admin/eliminacion/api/${detalle.id}/ejecutar`, {
        method: "POST",
      });
      const body = await res.json().catch(() => ({}));
      enviando = false;
      if (!res.ok) {
        errorAccion = mensajeError(body, `Error ${res.status}`);
        return;
      }
      // El resultado se queda EN EL MODAL en vez de cerrarlo con un toast: hay
      // que poder leer qué se destruyó y, sobre todo, qué no. Un toast de
      // cuatro segundos sobre un acto irreversible es exactamente donde se
      // pierde la lista de fallidos.
      resultadoEjecucion = body;
      await invalidateAll();
    } catch {
      enviando = false;
      errorAccion = "No se pudo conectar con el servidor.";
    }
  }

  async function firmarActa() {
    enviando = true;
    try {
      const res = await fetch(`/admin/eliminacion/api/${detalle.id}/acta/firmar`, {
        method: "POST",
      });
      const body = await res.json().catch(() => ({}));
      enviando = false;
      if (!res.ok) {
        notifyError(mensajeError(body, `Error ${res.status}`));
        return;
      }
      // Se dice cuál de los dos desenlaces fue: «reintentado» sin decir si
      // funcionó dejaría a quien mira sin saber si volver a intentarlo.
      if (body.firma_estado === "firmado") notifySuccess("Acta firmada.");
      else notifyError(`El sellado sigue pendiente: ${body.last_error ?? "sin detalle"}`);
      await invalidateAll();
    } catch {
      enviando = false;
      notifyError("No se pudo conectar con el servidor.");
    }
  }

  async function enviar(ruta, cuerpo, exito) {
    enviando = true;
    errorAccion = "";
    try {
      const res = await fetch(`/admin/eliminacion/api/${detalle.id}/${ruta}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cuerpo),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        // El detalle del backend se muestra TAL CUAL: distingue «el plazo
        // sigue abierto hasta el X» de «el lote no está publicado», y esa
        // diferencia es la que dice si hay que esperar o corregir.
        errorAccion = mensajeError(body, `Error ${res.status}`);
        enviando = false;
        return;
      }
      cerrar();
      notifySuccess(exito);
      await invalidateAll();
    } catch {
      errorAccion = "No se pudo conectar con el servidor.";
      enviando = false;
    }
  }
</script>

<svelte:head><title>Eliminación documental · OrpycaMCP</title></svelte:head>

<div class="elim">
  <header class="elim__cab">
    <div>
      <h1 class="elim__titulo">Eliminación documental</h1>
      <p class="elim__sub">
        Lotes de eliminación por disposición final de la TRD (Ley 594/2000 art. 24; Acuerdo AGN
        001/2024 art. 4.3.2.5). Se elimina por lotes porque el acta y el aval del Comité son del
        lote.
      </p>
    </div>
  </header>

  <ol class="elim__fases">
    <li><strong>1.</strong> Proponer <span>(desde la cola de vencimientos, en TRD)</span></li>
    <li>
      <strong>2.</strong> Publicar el inventario <span>(para que un tercero pueda oponerse)</span>
    </li>
    <li><strong>3.</strong> Aval del Comité <span>(acta y acto administrativo)</span></li>
    <li><strong>4.</strong> Acta de eliminación firmada</li>
    <li class="elim__fases--pendiente">
      <strong>5.</strong> Borrado seguro
      <span>(lo ejecuta otra persona, con el acta firmada y protegida)</span>
    </li>
  </ol>

  {#if data.sinPermiso}
    <EmptyState
      icon="fas fa-lock"
      title="No tienes acceso a los lotes de eliminación"
      description="Se requiere el permiso USUA_PERM_TRD. Pídeselo a un administrador."
    />
  {:else if data.lotesError}
    <p class="elim__error" role="alert">{data.lotesError}</p>
  {:else if lotes.length === 0}
    <EmptyState
      icon="fas fa-box-open"
      title="No hay lotes de eliminación"
      description="Los lotes se proponen desde la cola de vencimientos, en la pestaña «Disposición vencida» de TRD."
      action="Ir a TRD"
      actionHref="/admin/trd"
    />
  {:else}
    <!-- Técnica WCAG SCR29: región desplazable alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div class="op-table-scroll" role="region" aria-label="Lotes de eliminación" tabindex="0">
      <table class="elim__tabla">
        <caption class="is-sr-only">Lotes de eliminación</caption>
        <thead>
          <tr>
            <th scope="col">Código</th>
            <th scope="col">Estado</th>
            <th scope="col">Expedientes</th>
            <th scope="col">Publicidad hasta</th>
            <th scope="col">Acto administrativo</th>
            <th scope="col"><span class="is-sr-only">Acciones</span></th>
          </tr>
        </thead>
        <tbody>
          {#each lotes as lote (lote.id)}
            <tr class:elim__fila--activa={lote.id === data.seleccionado}>
              <td><strong>{lote.codigo}</strong></td>
              <td>
                <StatusChip
                  status={ESTADO_CHIP[lote.estado] ?? "neutral"}
                  label={ESTADO_LABEL[lote.estado] ?? lote.estado}
                />
              </td>
              <td>{lote.total_items}</td>
              <td>{lote.publicado_hasta ? fecha(lote.publicado_hasta) : "—"}</td>
              <td>{lote.acto_administrativo ?? "—"}</td>
              <td>
                <a class="elim__abrir" href="/admin/eliminacion?lote={lote.id}">Abrir</a>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  {#if data.detalleError}
    <p class="elim__error" role="alert">{data.detalleError}</p>
  {/if}

  {#if detalle}
    <section class="elim__detalle" aria-labelledby="elim-detalle-titulo">
      <header class="elim__detalle-cab">
        <h2 id="elim-detalle-titulo">Lote {detalle.codigo}</h2>
        <StatusChip
          status={ESTADO_CHIP[detalle.estado] ?? "neutral"}
          label={ESTADO_LABEL[detalle.estado] ?? detalle.estado}
        />
      </header>

      {#if detalle.observaciones}
        <p class="elim__obs">{detalle.observaciones}</p>
      {/if}

      <dl class="elim__datos">
        <div>
          <dt>Expedientes</dt>
          <dd>{detalle.total_items}</dd>
        </div>
        <div>
          <dt>Publicado hasta</dt>
          <dd>{detalle.publicado_hasta ? fecha(detalle.publicado_hasta) : "—"}</dd>
        </div>
        <div>
          <dt>Acta del Comité</dt>
          <dd>{detalle.acta_comite ?? "—"}</dd>
        </div>
        <div>
          <dt>Fecha del aval</dt>
          <dd>{detalle.fecha_aval ? fecha(detalle.fecha_aval) : "—"}</dd>
        </div>
        <div>
          <dt>Acto administrativo</dt>
          <dd>{detalle.acto_administrativo ?? "—"}</dd>
        </div>
      </dl>

      {#if detalle.estado === "anulado" && detalle.anulado_motivo}
        <p class="elim__aviso">
          <strong>Lote retirado.</strong>
          {detalle.anulado_motivo}
        </p>
      {/if}

      {#if detalle.estado === "publicado"}
        <p class="elim__aviso">
          El inventario está publicado y
          <a href="/eliminacion/{detalle.id}">se ve sin autenticar</a>. El aval del Comité no se
          registra hasta que venza el plazo para oponerse
          {#if publicidadAbierta(detalle)}
            — todavía abierto hasta el {fecha(detalle.publicado_hasta)}.
          {:else}
            , y ese plazo ya venció.
          {/if}
        </p>
      {/if}

      {#if puedeActuar}
        <div class="elim__acciones">
          {#if detalle.estado === "borrador"}
            <Button variant="primary" on:click={() => abrir("publicar")}>
              Publicar el inventario
            </Button>
          {/if}
          {#if detalle.estado === "publicado" && !publicidadAbierta(detalle) && puedeAvalar}
            <Button variant="primary" on:click={() => abrir("avalar")}>
              Registrar el aval del Comité
            </Button>
          {/if}
          {#if detalle.estado === "avalado" && acta?.firma_estado === "firmado" && puedeEjecutar}
            <Button variant="danger" on:click={() => abrir("ejecutar")}>
              Ejecutar la eliminación
            </Button>
          {/if}
          {#if detalle.estado !== "ejecutado" && detalle.estado !== "anulado"}
            <Button variant="danger" on:click={() => abrir("anular")}>Retirar el lote</Button>
          {/if}
        </div>
      {/if}

      {#if detalle.estado === "avalado" || detalle.estado === "ejecutado"}
        <h3 class="elim__inv-titulo">Acta de eliminación</h3>
        {#if data.actaSobreClearance}
          <p class="elim__aviso">
            Este lote tiene acta, pero incluye expedientes con un nivel de clasificación superior a
            tu nivel de acceso. <strong>No se puede mostrar recortada</strong>: es un documento
            firmado, y quitarle filas rompería su firma.
          </p>
        {:else if data.actaError}
          <p class="elim__error" role="alert">{data.actaError}</p>
        {:else if acta}
          <p class="elim__aviso">
            El acta es de <strong>conservación permanente</strong>: sobrevive a lo eliminado y no se
            elimina nunca. No se regenera ni se edita, y una vez firmada queda bajo
            <strong>Object-Lock</strong> — el almacén deja de admitir su borrado, ni siquiera a un administrador.
          </p>
          <dl class="elim__datos">
            <div>
              <dt>Generada</dt>
              <dd>{fecha(acta.generado_at)}</dd>
            </div>
            <div>
              <dt>Sellado</dt>
              <dd>
                {#if acta.firma_estado === "firmado"}
                  Firmada{acta.xades_level ? ` (${acta.xades_level})` : ""}
                {:else}
                  Pendiente de firma{acta.last_error ? ` — ${acta.last_error}` : ""}
                {/if}
              </dd>
            </div>
            <div>
              <dt>Método de borrado</dt>
              <dd>{acta.metodo_borrado}</dd>
            </div>
            <div>
              <dt>Protección WORM</dt>
              <dd>
                {#if acta.worm_protegido_at}
                  Protegida hasta {fecha(acta.worm_retain_until)}
                {:else if acta.firma_estado === "firmado"}
                  Pendiente
                {:else}
                  —
                {/if}
              </dd>
            </div>
          </dl>
          <p class="elim__sha">
            SHA-256: <code>{acta.acta_sha256}</code>
          </p>
          {#if acta.firma_estado === "firmado" && !acta.acreditado}
            <p class="elim__sub">
              Firma electrónica del art. 7 de la Ley 527 de 1999. <strong
                >No es una firma digital acreditada</strong
              > ante la ONAC (art. 28).
            </p>
          {/if}
          {#if puedeActuar && acta.firma_estado !== "firmado"}
            <div class="elim__acciones">
              <Button variant="secondary" loading={enviando} on:click={() => firmarActa()}>
                Reintentar el sellado
              </Button>
            </div>
          {/if}
        {:else if puedeActuar}
          <p class="elim__aviso">
            Todavía no hay acta. <strong>Se genera antes del borrado, no después</strong>: describe
            un inventario que sale de expedientes que van a dejar de existir.
          </p>
          <div class="elim__acciones">
            <Button variant="primary" on:click={() => abrir("acta")}>
              Generar el acta de eliminación
            </Button>
          </div>
        {:else}
          <p class="elim__sub">Este lote todavía no tiene acta de eliminación.</p>
        {/if}
      {/if}

      {#if detalle.estado === "ejecutado"}
        <h3 class="elim__inv-titulo">Lo que queda</h3>
        <p class="elim__aviso">
          Estos expedientes <strong>ya no existen</strong>. De cada uno queda su lápida —código,
          serie, fechas extremas, volumen y el acto que lo autorizó—, el acta de eliminación y los
          asientos de auditoría. <strong>La denominación no se conserva</strong>: era contenido.
        </p>
        {#if lapidas.length === 0}
          <p class="elim__sub">No se pudieron cargar las lápidas de este lote.</p>
        {:else}
          <!-- Técnica WCAG SCR29: región desplazable alcanzable con teclado (WCAG 2.1.1). -->
          <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
          <div class="op-table-scroll" role="region" aria-label="Expedientes del lote" tabindex="0">
            <table class="elim__tabla">
              <caption class="is-sr-only">Lápidas del lote {detalle.codigo}</caption>
              <thead>
                <tr>
                  <th scope="col">Expediente</th>
                  <th scope="col">Serie</th>
                  <th scope="col">Fechas extremas</th>
                  <th scope="col">Radicados</th>
                  <th scope="col">Anexos</th>
                  <th scope="col">Eliminado</th>
                </tr>
              </thead>
              <tbody>
                {#each lapidas as l (l.expediente_id)}
                  <tr>
                    <td><strong>{l.expediente_code}</strong></td>
                    <td>{l.trd_serie_code ?? "—"}</td>
                    <td>{fecha(l.fecha_extrema_inicial)} – {fecha(l.fecha_extrema_final)}</td>
                    <td>{l.radicados_eliminados}</td>
                    <td>{l.anexos_eliminados}</td>
                    <td>{fecha(l.eliminado_at)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      {/if}

      <h3 class="elim__inv-titulo">Inventario congelado</h3>
      {#if detalle.items.length === 0}
        <p class="elim__sub">
          No hay ningún expediente visible en este inventario con tu nivel de acceso. El lote
          declara {detalle.total_items}.
        </p>
      {:else}
        <!-- Técnica WCAG SCR29: región desplazable alcanzable con teclado (WCAG 2.1.1). -->
        <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
        <div class="op-table-scroll" role="region" aria-label="Radicados del lote" tabindex="0">
          <table class="elim__tabla">
            <caption class="is-sr-only">Expedientes del lote {detalle.codigo}</caption>
            <thead>
              <tr>
                <th scope="col">Expediente</th>
                <th scope="col">Serie</th>
                <th scope="col">Disposición</th>
                <th scope="col">Fechas extremas</th>
                <th scope="col">Radicados</th>
              </tr>
            </thead>
            <tbody>
              {#each detalle.items as item (item.expediente_id)}
                <tr>
                  <td>
                    <strong>{item.expediente_code}</strong>
                    {#if item.expediente_nombre}<br /><span class="elim__sub"
                        >{item.expediente_nombre}</span
                      >{/if}
                  </td>
                  <td>{item.trd_serie_code ?? "—"}</td>
                  <td>{item.disposition_code}</td>
                  <td>{fecha(item.fecha_extrema_inicial)} – {fecha(item.fecha_extrema_final)}</td>
                  <td>{item.radicados_incluidos ?? "—"}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        {#if detalle.items.length < detalle.total_items}
          <p class="elim__sub">
            Se muestran {detalle.items.length} de {detalle.total_items}: el resto tiene un nivel de
            clasificación superior a tu nivel de acceso.
          </p>
        {/if}
      {/if}
    </section>
  {/if}
</div>

<Modal open={accion === "publicar"} title="Publicar el inventario" size="sm" on:close={cerrar}>
  <p>
    El inventario de este lote quedará <strong>a la vista de cualquiera, sin autenticar</strong>,
    para que quien tenga algo que oponer pueda hacerlo antes. La oposición se recibe por la
    ventanilla única, citando el código del lote.
  </p>
  <p class="elim__aviso">
    Publicar <strong>congela el inventario</strong>: después ya no se puede quitar ningún
    expediente. Y sigue sin eliminarse nada.
  </p>
  <FormField
    id="pub-hasta"
    name="publicado_hasta"
    type="date"
    label="Plazo para oponerse (hasta)"
    required
    bind:value={fPublicadoHasta}
    hint="No hay valor por defecto: el plazo lo declara la entidad."
  />
  {#if errorAccion}<p class="elim__error" role="alert">{errorAccion}</p>{/if}
  <svelte:fragment slot="footer">
    <Button variant="secondary" on:click={cerrar}>Cancelar</Button>
    <Button
      variant="primary"
      loading={enviando}
      disabled={!fPublicadoHasta || fPublicadoHasta <= HOY}
      on:click={() =>
        enviar("publicar", { publicado_hasta: fPublicadoHasta }, "Inventario publicado.")}
    >
      Publicar
    </Button>
  </svelte:fragment>
</Modal>

<Modal open={accion === "avalar"} title="Registrar el aval del Comité" size="sm" on:close={cerrar}>
  <p>
    Se registra la decisión del <strong>Comité Institucional de Gestión y Desempeño</strong> que autoriza
    la eliminación. El sistema no decide: deja constancia de que un Comité decidió, y con qué acto.
  </p>
  <p class="elim__aviso">
    Avalar <strong>autoriza</strong> la destrucción; no la ejecuta. Faltan el acta firmada y el
    borrado seguro, que ejecuta <strong>otra persona</strong>: quien propone no avala y quien avala
    no ejecuta.
  </p>
  <FormField
    id="aval-acta"
    name="acta_comite"
    label="Acta del Comité"
    required
    bind:value={fActa}
    hint="Por ejemplo, «Acta 07 del 20 de agosto de 2026»."
  />
  <FormField
    id="aval-fecha"
    name="fecha_aval"
    type="date"
    label="Fecha del aval"
    required
    bind:value={fFechaAval}
  />
  <FormField
    id="aval-acto"
    name="acto_administrativo"
    label="Acto administrativo"
    required
    bind:value={fActo}
    hint="La resolución o acuerdo que autoriza la eliminación."
  />
  {#if errorAccion}<p class="elim__error" role="alert">{errorAccion}</p>{/if}
  <svelte:fragment slot="footer">
    <Button variant="secondary" on:click={cerrar}>Cancelar</Button>
    <Button
      variant="primary"
      loading={enviando}
      disabled={fActa.trim().length < 3 || fActo.trim().length < 3 || !fFechaAval}
      on:click={() =>
        enviar(
          "avalar",
          { acta_comite: fActa, fecha_aval: fFechaAval, acto_administrativo: fActo },
          "Aval del Comité registrado.",
        )}
    >
      Registrar el aval
    </Button>
  </svelte:fragment>
</Modal>

<Modal open={accion === "acta"} title="Generar el acta de eliminación" size="sm" on:close={cerrar}>
  <p>
    El acta deja constancia de <strong>qué</strong> se elimina, <strong>bajo qué acto</strong> y
    <strong>cómo</strong> se destruye. Es de <strong>conservación permanente</strong>: sobrevive a
    lo eliminado y no se elimina nunca, y una vez firmada queda bajo <strong>Object-Lock</strong>:
    el almacén deja de admitir su borrado, ni siquiera por un administrador.
  </p>
  <p class="elim__aviso">
    Se genera <strong>una sola vez</strong> y no se puede editar ni volver a generar. Revisa el inventario
    antes de continuar.
  </p>
  <FormField
    id="acta-metodo"
    name="metodo_borrado"
    type="textarea"
    label="Método de borrado"
    required
    bind:value={fMetodo}
    hint="Cómo se destruyen los documentos. El sistema no lo inventa: lo declara la entidad."
  />
  <FormField
    id="acta-obs"
    name="observaciones"
    type="textarea"
    label="Observaciones (opcional)"
    bind:value={fObsActa}
  />
  {#if errorAccion}<p class="elim__error" role="alert">{errorAccion}</p>{/if}
  <svelte:fragment slot="footer">
    <Button variant="secondary" on:click={cerrar}>Cancelar</Button>
    <Button
      variant="primary"
      loading={enviando}
      disabled={fMetodo.trim().length < 10}
      on:click={() =>
        enviar(
          "acta",
          { metodo_borrado: fMetodo, observaciones: fObsActa || undefined },
          "Acta de eliminación generada.",
        )}
    >
      Generar el acta
    </Button>
  </svelte:fragment>
</Modal>

<Modal open={accion === "ejecutar"} title="Ejecutar la eliminación" size="md" on:close={cerrar}>
  {#if resultadoEjecucion === null}
    <p class="elim__peligro" role="alert">
      <strong>Esto destruye documentos de archivo y no se puede deshacer.</strong>
      Se borrarán los archivos y los radicados de
      <strong>{detalle?.total_items ?? 0}</strong> expedientes.
    </p>
    <p>
      Lo que quedará de cada expediente: su <strong>lápida</strong> —código, serie, fechas extremas,
      volumen y el acto que lo autorizó—, el <strong>acta de eliminación</strong> firmada y los
      asientos de <strong>auditoría</strong>. No quedará el contenido ni la denominación.
    </p>
    <p class="elim__sub">
      Autorizado por {detalle?.acta_comite} · {detalle?.acto_administrativo}
    </p>
    <FormField
      id="ejecutar-confirmacion"
      name="confirmacion"
      label="Escribe el código del lote para confirmar"
      required
      bind:value={fConfirmacion}
      hint="Un acto irreversible no debe estar a un clic de distancia."
    />
    {#if errorAccion}<p class="elim__error" role="alert">{errorAccion}</p>{/if}
  {:else}
    <p>
      Eliminados <strong>{resultadoEjecucion.expedientes_eliminados}</strong> expedientes,
      <strong>{resultadoEjecucion.radicados_eliminados}</strong> radicados y
      <strong>{resultadoEjecucion.anexos_eliminados}</strong> anexos.
    </p>
    {#if resultadoEjecucion.fallidos?.length}
      <p class="elim__peligro" role="alert">
        <strong>No se eliminaron {resultadoEjecucion.fallidos.length}</strong>, y siguen
        <strong>intactos y sin lápida</strong>. El lote sigue sin marcarse como ejecutado; se puede
        volver a ejecutar cuando se resuelva el motivo.
      </p>
      <ul class="elim__rechazos">
        {#each resultadoEjecucion.fallidos as f (f.expediente_id)}
          <li><strong>{f.expediente_code}</strong>: {f.detalle}</li>
        {/each}
      </ul>
    {/if}
  {/if}
  <svelte:fragment slot="footer">
    <Button variant="secondary" on:click={cerrar}>
      {resultadoEjecucion === null ? "Cancelar" : "Cerrar"}
    </Button>
    {#if resultadoEjecucion === null}
      <Button
        variant="danger"
        loading={enviando}
        disabled={fConfirmacion.trim() !== detalle?.codigo}
        on:click={ejecutar}
      >
        Eliminar definitivamente
      </Button>
    {/if}
  </svelte:fragment>
</Modal>

<Modal open={accion === "anular"} title="Retirar el lote" size="sm" on:close={cerrar}>
  <p>
    El lote sale del procedimiento y sus expedientes vuelven a estar disponibles para otro lote.
  </p>
  <p class="elim__aviso">
    Si el inventario llegó a publicarse, <strong>seguirá siendo público</strong> con este motivo: quien
    leyó que esos expedientes iban a eliminarse tiene que poder ver en qué quedó.
  </p>
  <FormField
    id="anular-motivo"
    name="motivo"
    type="textarea"
    label="Motivo"
    required
    bind:value={fMotivo}
    hint="Al menos 10 caracteres. Es la constancia de por qué no se eliminó."
  />
  {#if errorAccion}<p class="elim__error" role="alert">{errorAccion}</p>{/if}
  <svelte:fragment slot="footer">
    <Button variant="secondary" on:click={cerrar}>Cancelar</Button>
    <Button
      variant="danger"
      loading={enviando}
      disabled={fMotivo.trim().length < 10}
      on:click={() => enviar("anular", { motivo: fMotivo }, "Lote retirado.")}
    >
      Retirar el lote
    </Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .elim {
    padding: var(--op-space-4);
  }
  .elim__titulo {
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
  }
  .elim__sub {
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
  }
  .elim__cab {
    margin-bottom: var(--op-space-3);
  }
  .elim__fases {
    display: flex;
    flex-wrap: wrap;
    gap: var(--op-space-3);
    margin-bottom: var(--op-space-4);
    padding: var(--op-space-3);
    background: var(--op-bg);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    font-size: var(--op-font-sm);
    color: var(--op-text);
    list-style: none;

    span {
      color: var(--op-text-secondary);
    }
  }
  .elim__fases--pendiente {
    color: var(--op-warning);
  }
  .elim__tabla {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--op-font-sm);

    th,
    td {
      padding: var(--op-space-2);
      border-bottom: 1px solid var(--op-border);
      text-align: left;
      vertical-align: top;
    }
    th {
      color: var(--op-text-secondary);
      font-weight: var(--op-weight-medium);
    }
  }
  .elim__fila--activa {
    background: var(--op-bg);
  }
  .elim__abrir {
    color: var(--op-primary);
  }
  .elim__detalle {
    margin-top: var(--op-space-5);
    padding: var(--op-space-4);
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
  }
  .elim__detalle-cab {
    display: flex;
    align-items: center;
    gap: var(--op-space-3);
    margin-bottom: var(--op-space-3);
  }
  .elim__obs {
    color: var(--op-text-secondary);
    margin-bottom: var(--op-space-3);
  }
  .elim__datos {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: var(--op-space-3);
    margin-bottom: var(--op-space-3);

    dt {
      color: var(--op-text-secondary);
      font-size: var(--op-font-xs);
    }
    dd {
      color: var(--op-text);
      font-size: var(--op-font-sm);
    }
  }
  .elim__aviso {
    margin: var(--op-space-2) 0;
    padding: var(--op-space-2);
    border-left: 3px solid var(--op-warning);
    background: var(--op-bg);
    color: var(--op-text);
    font-size: var(--op-font-sm);
  }
  .elim__acciones {
    display: flex;
    flex-wrap: wrap;
    gap: var(--op-space-2);
    margin: var(--op-space-3) 0;
  }
  .elim__inv-titulo {
    margin-top: var(--op-space-4);
    margin-bottom: var(--op-space-2);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
  }
  .elim__sha {
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
    word-break: break-all;
    margin-bottom: var(--op-space-2);
  }
  .elim__peligro {
    margin: var(--op-space-2) 0;
    padding: var(--op-space-3);
    border-left: 3px solid var(--op-error);
    background: var(--op-bg);
    color: var(--op-text);
    font-size: var(--op-font-sm);
  }
  .elim__rechazos {
    margin: var(--op-space-2) 0 0 var(--op-space-4);
    font-size: var(--op-font-sm);
    color: var(--op-text);
    list-style: disc;
  }
  .elim__error {
    color: var(--op-error);
    font-size: var(--op-font-sm);
    margin: var(--op-space-2) 0;
  }
</style>
