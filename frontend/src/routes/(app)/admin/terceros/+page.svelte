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
  /admin/terceros — el directorio y su administración (E01, RF-RAD-05).

  **Por qué existe.** Hasta ahora un tercero solo podía NACER, como efecto
  colateral de elegir un destinatario al radicar. No había dónde listarlos,
  revisarlos ni consolidar los que sobraban: `/admin` tenía veinte secciones y
  ninguna era esta. El Orfeo del que deriva el sistema acabó con el mismo señor
  treinta veces en el catálogo.

  **Los duplicados van arriba, y separados por eje.** No valen lo mismo:
  coincidir en documento de identidad es certeza —son la misma persona— y
  coincidir en nombre es SOSPECHA, porque puede haber dos Juan Pérez. Por eso
  la pantalla los muestra y nunca fusiona sola.

  **Fichas por conciliar (C17).** Quien radica por la ventanilla pública no está
  autenticado: el documento de identidad que declara es una afirmación, no un
  hecho. Por eso su ficha nace PROVISIONAL —sin ocupar el documento en el
  directorio— y nunca se asocia sola a una ficha existente con el mismo número:
  asociarlas por el número dejaría a cualquiera anexar su correspondencia a la
  ficha de otra persona. Una persona con permiso decide aquí, y el acto queda
  asentado en la auditoría. El número de radicado no cambia en ningún caso.

  **Fusionar no borra ni reescribe.** Los radicados de la ficha absorbida siguen
  nombrándola: el día del envío existían dos fichas y se usó una, y decir lo
  contrario falsificaría un acto ya ocurrido (lo impone un trigger de la base).
  Lo que la fusión añade es lo que se supo después, y el superviviente hereda
  el historial al leerlo. Se dice en pantalla antes de confirmar, porque quien
  fusiona tiene derecho a saber qué NO va a pasar.
-->
<script>
  import { onMount } from "svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import Card from "$lib/components/ui/Card.svelte";
  import DataTable from "$lib/components/ui/DataTable.svelte";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import { permissionLevels } from "$lib/stores/auth.js";
  import { codigoError, mensajeError } from "$lib/utils/apiError.js";

  /** @type {import('./$types').PageData} */
  export let data;

  let terceros = data.terceros ?? [];
  let duplicados = data.duplicados ?? [];
  let consulta = "";
  let buscando = false;

  const COLUMNAS = [
    { key: "nombre", label: "Nombre" },
    { key: "entidad", label: "Entidad" },
    { key: "numero_documento", label: "Documento" },
    { key: "email", label: "Correo" },
    { key: "municipio", label: "Municipio" },
  ];

  async function buscar() {
    buscando = true;
    try {
      const res = await fetch(
        `/admin/terceros/api?accion=buscar&q=${encodeURIComponent(consulta)}`,
      );
      const cuerpo = await res.json();
      if (!res.ok) throw cuerpo;
      terceros = cuerpo;
    } catch (e) {
      notifyError(mensajeError(e, "No se pudo consultar el directorio."));
    } finally {
      buscando = false;
    }
  }

  async function recargarDuplicados() {
    try {
      const res = await fetch("/admin/terceros/api?accion=duplicados");
      const cuerpo = await res.json();
      if (res.ok) duplicados = cuerpo;
    } catch {
      // El listado principal no depende de esto: un fallo aquí no lo tumba.
    }
  }

  // --- Fichas por conciliar (C17) ------------------------------------------
  // Espejo de `PERM_RADI` con `min_crud=3` (lectura Y escritura: la cola lista datos
  // personales de ciudadanos). El backend re-valida; aquí solo no se ofrece lo que
  // rechazaría.
  const TAM_COLA = 20;
  const MIN_MOTIVO = 10;
  $: puedeConciliar = ($permissionLevels?.PERM_RADI ?? 0) >= 3;

  /** @type {any[]} */
  let cola = [];
  let colaTotal = 0;
  let colaPagina = 1;
  let colaCargando = false;
  let colaError = "";
  let colaIniciada = false;

  async function cargarCola(/** @type {number} */ pagina = colaPagina) {
    colaCargando = true;
    colaError = "";
    try {
      const res = await fetch(
        `/admin/terceros/api?accion=por_conciliar&page=${pagina}&size=${TAM_COLA}`,
      );
      const cuerpo = await res.json().catch(() => null);
      if (!res.ok) throw cuerpo;
      const total = Number(res.headers.get("X-Total-Count"));
      cola = Array.isArray(cuerpo) ? cuerpo : [];
      colaTotal = Number.isFinite(total) && total > 0 ? total : cola.length;
      colaPagina = pagina;
      colaIniciada = true;
    } catch (e) {
      colaError = mensajeError(e, "No se pudo cargar la lista de fichas por conciliar.");
    } finally {
      colaCargando = false;
    }
  }

  $: colaDesde = colaTotal === 0 ? 0 : (colaPagina - 1) * TAM_COLA + 1;
  $: colaHasta = (colaPagina - 1) * TAM_COLA + cola.length;
  $: colaHayMas = colaHasta < colaTotal;

  onMount(() => {
    if (puedeConciliar) cargarCola(1);
  });

  /** @param {any} e */
  function mensajeConciliacion(e) {
    // `motivo_requerido` existe también en la firma con otro sentido: aquí manda este.
    if (codigoError(e) === "motivo_requerido") return "Escriba el motivo: esta decisión lo exige.";
    return mensajeError(e, "No se pudo conciliar la ficha.");
  }

  const TITULOS_CONCILIAR = {
    vincular: "Vincular a la ficha existente",
    confirmar: "Confirmar como ficha nueva",
    descartar: "Descartar la identidad declarada",
  };
  const FRASE_VINCULAR = "vincular";

  let modalConciliar = false;
  /** @type {'vincular'|'confirmar'|'descartar'} */ let accionConciliar = "vincular";
  /** @type {any} */ let provisional = null;
  /** @type {any} */ let destino = null;
  let motivo = "";
  let fraseVincular = "";
  let medio = "";
  let conciliando = false;
  let busquedaDestino = "";
  /** @type {any[]} */ let candidatosDestino = [];
  let buscandoDestino = false;
  let errorConciliar = "";

  function abrirConciliar(
    /** @type {any} */ ficha,
    /** @type {'vincular'|'confirmar'|'descartar'} */ accion,
  ) {
    provisional = ficha;
    accionConciliar = accion;
    // Sin preselección: elegir la ficha de destino es un acto deliberado de quien concilia.
    destino = null;
    medio = "";
    motivo = "";
    fraseVincular = "";
    busquedaDestino = "";
    candidatosDestino = [];
    errorConciliar = "";
    modalConciliar = true;
  }

  async function buscarDestino() {
    buscandoDestino = true;
    try {
      const res = await fetch(
        `/admin/terceros/api?accion=buscar&q=${encodeURIComponent(busquedaDestino)}`,
      );
      const cuerpo = await res.json().catch(() => null);
      if (!res.ok) throw cuerpo;
      candidatosDestino = Array.isArray(cuerpo) ? cuerpo : [];
    } catch (e) {
      errorConciliar = mensajeError(e, "No se pudo buscar en el directorio.");
    } finally {
      buscandoDestino = false;
    }
  }

  function elegirDestino(/** @type {any} */ t) {
    const declarado = String(provisional?.documento_declarado_numero ?? "").trim();
    destino = {
      id: t.id,
      nombre: t.nombre,
      email: t.email,
      municipio: t.municipio,
      coincide: Boolean(declarado) && String(t.numero_documento ?? "").trim() === declarado,
    };
  }

  // El motivo se exige cuando el backend lo exige: al descartar y al vincular a una
  // ficha cuyo documento NO coincide con el declarado (decisión humana que debe quedar
  // explicada en el asiento).
  // Cómo se comprobó que quien radicó es quien dice ser: lo exigen vincular y confirmar
  // (descartar no lo lleva). Con «otro» el motivo es obligatorio.
  const MEDIOS = [
    { value: "presencial_documento", label: "Presentó el documento en persona" },
    { value: "respuesta_canal_registrado", label: "Respondió por el canal ya registrado" },
    { value: "llamada_telefono_registrado", label: "Llamada al teléfono ya registrado" },
    { value: "otro", label: "Otro (explique)" },
  ];
  $: pideMedio = accionConciliar === "vincular" || accionConciliar === "confirmar";
  $: motivoExigido =
    accionConciliar === "descartar" ||
    (accionConciliar === "vincular" && destino && !destino.coincide) ||
    (pideMedio && medio === "otro");
  $: motivoValido = !motivoExigido || motivo.trim().length >= MIN_MOTIVO;
  $: puedeEnviarConciliar =
    !conciliando &&
    motivoValido &&
    (!pideMedio || medio !== "") &&
    (accionConciliar !== "vincular" ||
      (Boolean(destino) && fraseVincular.trim().toLowerCase() === FRASE_VINCULAR));

  async function confirmarConciliar() {
    if (!puedeEnviarConciliar) return;
    conciliando = true;
    errorConciliar = "";
    try {
      const res = await fetch(`/admin/terceros/api?accion=conciliar&id=${provisional.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          accion: accionConciliar,
          destino_id: accionConciliar === "vincular" ? destino.id : null,
          // En «descartar» NO se envía: el backend lo rechaza (medio_verificacion_no_aplica).
          ...(pideMedio ? { medio_verificacion: medio } : {}),
          motivo: motivo.trim() || null,
        }),
      });
      const cuerpo = await res.json().catch(() => null);
      if (!res.ok) throw cuerpo;
      notifySuccess(
        accionConciliar === "vincular"
          ? `«${provisional.nombre}» quedó vinculada a «${destino.nombre}». Los números de radicado no cambian.`
          : accionConciliar === "confirmar"
            ? `«${provisional.nombre}» es ahora una ficha confirmada del directorio.`
            : `«${provisional.nombre}» se descartó como identidad: sigue siendo copia del remitente en sus radicados.`,
      );
      modalConciliar = false;
      // Si era la última de la página, retrocede una para no quedar en una vacía.
      const pagina = cola.length === 1 && colaPagina > 1 ? colaPagina - 1 : colaPagina;
      await Promise.all([cargarCola(pagina), buscar()]);
    } catch (e) {
      // Se queda DENTRO del modal: quien concilia no pierde el motivo que escribió.
      errorConciliar = mensajeConciliacion(e);
      // El destino ya no sirve (p. ej. se fusionó): hay que elegir otro, no reintentar el mismo.
      if (codigoError(e) === "destino_invalido") destino = null;
      // 404/409: la ficha ya no está pendiente o cambió; refrescar la cola al cerrar.
      if (
        codigoError(e) === "tercero_ya_conciliado" ||
        codigoError(e) === "tercero_no_encontrado"
      ) {
        await cargarCola(colaPagina);
      }
    } finally {
      conciliando = false;
    }
  }

  // --- Ficha: dónde aparece --------------------------------------------------
  let drawerAbierto = false;
  /** @type {any} */
  let fichaAbierta = null;
  let radicados = [];
  let cargandoRadicados = false;

  async function verFicha(/** @type {any} */ t) {
    fichaAbierta = t;
    drawerAbierto = true;
    radicados = [];
    cargandoRadicados = true;
    try {
      const res = await fetch(`/admin/terceros/api?accion=radicados&id=${t.id}`);
      const cuerpo = await res.json();
      if (res.ok) radicados = cuerpo;
    } catch {
      notifyError("No se pudo leer en qué radicados aparece esta ficha.");
    } finally {
      cargandoRadicados = false;
    }
  }

  // --- Fusión: acto irreversible --------------------------------------------
  // La frase tecleada nombra el acto Y el objeto. Un «sí» genérico confirma
  // «una acción», no ESTA: quien lo teclea de memoria no se entera de a quién
  // está consolidando.
  const FRASE = "fusionar";
  let modalFusion = false;
  /** @type {any} */ let superviviente = null;
  /** @type {any} */ let absorbido = null;
  let fraseTecleada = "";
  let fusionando = false;

  function abrirFusion(/** @type {any} */ vive, /** @type {any} */ muere) {
    superviviente = vive;
    absorbido = muere;
    fraseTecleada = "";
    modalFusion = true;
  }

  async function confirmarFusion() {
    if (fraseTecleada.trim().toLowerCase() !== FRASE) return;
    fusionando = true;
    try {
      const res = await fetch(`/admin/terceros/api?accion=fusionar&id=${superviviente.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ absorbe_id: absorbido.id }),
      });
      const cuerpo = await res.json();
      if (!res.ok) throw cuerpo;
      notifySuccess(
        `«${absorbido.nombre}» se consolidó en «${superviviente.nombre}». ` +
          `${cuerpo.vinculos_heredados} radicado(s) pasan a verse desde la ficha superviviente.`,
      );
      modalFusion = false;
      await Promise.all([recargarDuplicados(), buscar()]);
    } catch (e) {
      notifyError(mensajeError(e, "No se pudo completar la fusión."));
    } finally {
      fusionando = false;
    }
  }
</script>

<svelte:head><title>Directorio de terceros — OrpycaMCP</title></svelte:head>

<div class="terceros">
  <h1 class="terceros__titulo">Directorio de terceros</h1>
  <p class="terceros__sub">
    Remitentes y destinatarios del catálogo de la entidad. Buscar antes de crear es lo que evita que
    la misma persona acabe repetida: la búsqueda ignora tildes y mayúsculas.
  </p>

  {#if puedeConciliar}
    <Card>
      <section aria-labelledby="conciliar-titulo" class="terceros__conciliar">
        <h2 class="terceros__h2" id="conciliar-titulo">
          Fichas por conciliar{#if colaTotal > 0}
            ({colaTotal}){/if}
        </h2>
        <p class="terceros__sub">
          Quien radica por la ventanilla pública no está autenticado: el documento que declara es
          una
          <strong>afirmación sin verificar</strong>, así que su ficha es
          <strong>provisional</strong> y no se asocia sola a ninguna ficha existente, aunque el número
          coincida. Aquí una persona decide, y queda constancia. El número de radicado no cambia.
        </p>

        {#if colaError}
          <p class="terceros__aviso" role="alert">
            {colaError}
            <Button variant="ghost" size="sm" on:click={() => cargarCola(colaPagina)}>
              Reintentar
            </Button>
          </p>
        {:else if colaCargando && !colaIniciada}
          <p class="terceros__tenue" role="status">Consultando…</p>
        {:else if cola.length === 0}
          <EmptyState
            icon="fas fa-clipboard-check"
            title="No hay fichas por conciliar"
            description="Cuando alguien radique por la ventanilla pública declarando un documento, su ficha aparecerá aquí."
          />
        {:else}
          <ul class="terceros__fichas terceros__cola" aria-busy={colaCargando}>
            {#each cola as f (f.id)}
              <li class="terceros__pendiente">
                <div class="terceros__pendiente-datos">
                  <p class="terceros__pendiente-nombre">
                    <strong>{f.nombre}</strong>
                    {#if f.entidad}<span class="terceros__tenue"> · {f.entidad}</span>{/if}
                    <StatusChip status="pending" label="Identidad provisional" />
                  </p>
                  <p class="terceros__tenue terceros__linea">
                    Declaró
                    <code
                      >{f.documento_declarado_tipo ?? "documento"}
                      {f.documento_declarado_numero}</code
                    >
                    (sin verificar)
                    {#if f.email}
                      · {f.email}{/if}
                    {#if f.telefono}
                      · {f.telefono}{/if}
                  </p>
                  {#if f.radicados?.length}
                    <p class="terceros__tenue terceros__linea">
                      Radicados: {#each f.radicados as r, i (r.tracking_number)}{#if i > 0},
                        {/if}<code>{r.tracking_number}</code>{/each}
                    </p>
                  {/if}
                  {#if f.candidata}
                    <p class="terceros__linea">
                      Ya existe una ficha confirmada con ese documento:
                      <strong>{f.candidata.nombre}</strong>
                      {#if f.candidata.municipio}
                        <span class="terceros__tenue"> · {f.candidata.municipio}</span>{/if}
                      {#if f.candidata.email}
                        <span class="terceros__tenue"> · {f.candidata.email}</span>{/if}
                    </p>
                  {:else}
                    <p class="terceros__tenue terceros__linea">
                      No hay ficha confirmada con ese documento.
                    </p>
                  {/if}
                </div>
                <span class="terceros__acciones terceros__acciones--envuelve">
                  <Button
                    variant="secondary"
                    size="sm"
                    on:click={() => abrirConciliar(f, "vincular")}
                  >
                    Vincular a la ficha existente
                  </Button>
                  {#if !f.candidata}
                    <Button
                      variant="secondary"
                      size="sm"
                      on:click={() => abrirConciliar(f, "confirmar")}
                    >
                      Confirmar como ficha nueva
                    </Button>
                  {/if}
                  <Button variant="ghost" size="sm" on:click={() => abrirConciliar(f, "descartar")}>
                    Descartar
                  </Button>
                </span>
              </li>
            {/each}
          </ul>
          <nav class="terceros__paginas" aria-label="Paginación de fichas por conciliar">
            <span class="terceros__tenue" role="status">
              {colaDesde}–{colaHasta} de {colaTotal}
            </span>
            <Button
              variant="ghost"
              size="sm"
              disabled={colaPagina <= 1 || colaCargando}
              on:click={() => cargarCola(colaPagina - 1)}
            >
              Anterior
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={!colaHayMas || colaCargando}
              on:click={() => cargarCola(colaPagina + 1)}
            >
              Siguiente
            </Button>
          </nav>
        {/if}
      </section>
    </Card>
  {/if}

  <!-- Duplicados primero: es lo accionable -->
  {#if data.duplicadoError}
    <Card>
      <p class="terceros__aviso" role="alert">
        No se pudo revisar si hay fichas duplicadas ({data.duplicadoError}). Esto
        <strong>no</strong> significa que no las haya.
      </p>
    </Card>
  {:else if duplicados.length > 0}
    <Card>
      <h2 class="terceros__h2">Posibles duplicados ({duplicados.length})</h2>
      <p class="terceros__sub">
        Coincidir en <strong>documento</strong> es certeza: son la misma persona. Coincidir solo en
        <strong>nombre</strong> es una sospecha — puede haber dos personas homónimas. Por eso nada se
        fusiona solo.
      </p>
      {#each duplicados as g (g.eje + g.clave)}
        <div class="terceros__grupo">
          <p class="terceros__grupo-eje">
            {g.eje === "documento" ? "Mismo documento" : "Mismo nombre"}
            <code class="terceros__clave">{g.clave}</code>
          </p>
          <ul class="terceros__fichas">
            {#each g.fichas as f, i (f.id)}
              <li class="terceros__ficha">
                <span>
                  <strong>{f.nombre}</strong>
                  {#if f.entidad}<span class="terceros__tenue"> · {f.entidad}</span>{/if}
                  {#if f.numero_documento}
                    <span class="terceros__tenue"> · {f.numero_documento}</span>
                  {/if}
                </span>
                <span class="terceros__acciones">
                  <Button variant="ghost" size="sm" on:click={() => verFicha(f)}>Ver</Button>
                  {#if i > 0}
                    <Button variant="ghost" size="sm" on:click={() => abrirFusion(g.fichas[0], f)}>
                      Consolidar en la primera
                    </Button>
                  {/if}
                </span>
              </li>
            {/each}
          </ul>
        </div>
      {/each}
    </Card>
  {/if}

  <Card>
    <div class="terceros__buscador">
      <FormField
        id="dir-buscar"
        label="Buscar en el directorio"
        bind:value={consulta}
        hint="Por nombre o entidad. No distingue tildes ni mayúsculas."
      />
      <Button variant="secondary" loading={buscando} on:click={buscar}>Buscar</Button>
    </div>

    {#if data.terceroError}
      <p class="terceros__aviso" role="alert">
        No se pudo cargar el directorio ({data.terceroError}).
      </p>
    {:else if terceros.length === 0}
      <EmptyState
        icon="fas fa-address-book"
        title="No hay fichas que coincidan"
        description="Prueba con menos palabras: buscar «Pérez» encuentra más que «José Pérez Gómez»."
      />
    {:else}
      <DataTable columns={COLUMNAS} rows={terceros} on:rowclick={(e) => verFicha(e.detail)} />
    {/if}
  </Card>
</div>

<Drawer open={drawerAbierto} title="Ficha del directorio" on:close={() => (drawerAbierto = false)}>
  {#if fichaAbierta}
    <h3 class="terceros__h2">{fichaAbierta.nombre}</h3>
    {#if fichaAbierta.entidad}<p class="terceros__tenue">{fichaAbierta.entidad}</p>{/if}
    <p>
      {#if fichaAbierta.identidad === "provisional"}
        <StatusChip status="pending" label="Identidad provisional" />
      {:else}
        <StatusChip status="success" label="Identidad confirmada" />
      {/if}
    </p>
    <p class="terceros__sub">
      Estos son los datos <strong>vigentes</strong> de la ficha. Lo que se usó en cada envío quedó congelado
      en su radicado y no cambia aunque la ficha se corrija.
    </p>

    <h4 class="terceros__h2">Dónde aparece</h4>
    {#if cargandoRadicados}
      <p class="terceros__tenue">Consultando…</p>
    {:else if radicados.length === 0}
      <p class="terceros__tenue">Todavía no aparece en ningún radicado.</p>
    {:else}
      <ul class="terceros__fichas">
        {#each radicados as r (r.tracking_number + r.rol)}
          <li class="terceros__ficha">
            <span><code>{r.tracking_number}</code> · {r.subject}</span>
            <span class="terceros__tenue">{r.rol}</span>
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
</Drawer>

<Modal
  open={modalFusion}
  title="Consolidar fichas duplicadas"
  on:close={() => (modalFusion = false)}
>
  {#if superviviente && absorbido}
    <p class="terceros__aviso">
      <strong>«{absorbido.nombre}»</strong> quedará marcada como la misma persona que
      <strong>«{superviviente.nombre}»</strong>, y dejará de aparecer al radicar.
    </p>
    <p class="terceros__sub">
      Lo que <strong>no</strong> ocurre: los radicados de la ficha absorbida seguirán nombrándola, porque
      el día de cada envío existían dos fichas y se usó una — reescribirlo daría información errónea sobre
      un acto ya ocurrido. El historial pasa a verse desde la ficha superviviente.
    </p>
    <p class="terceros__sub"><strong>No hay «deshacer fusión».</strong></p>
    <FormField
      id="fusion-frase"
      label={`Escribe «${FRASE}» para confirmar`}
      bind:value={fraseTecleada}
    />
  {/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalFusion = false)}>Cancelar</Button>
    <Button
      variant="primary"
      loading={fusionando}
      disabled={fraseTecleada.trim().toLowerCase() !== FRASE}
      on:click={confirmarFusion}
    >
      Consolidar
    </Button>
  </svelte:fragment>
</Modal>

<Modal
  open={modalConciliar}
  title={TITULOS_CONCILIAR[accionConciliar]}
  on:close={() => (modalConciliar = false)}
>
  {#if provisional}
    <p class="terceros__aviso">
      Ficha provisional de <strong>«{provisional.nombre}»</strong>, que declaró
      <code
        >{provisional.documento_declarado_tipo ?? "documento"}
        {provisional.documento_declarado_numero}</code
      >
      sin verificar.
    </p>

    {#if accionConciliar === "vincular"}
      <p class="terceros__sub">
        Sus radicados pasarán a verse desde la ficha elegida. Los números de radicado y lo que cada
        envío declaró <strong>no cambian</strong>. <strong>No hay «deshacer».</strong>
      </p>
      {#if destino}
        <p class="terceros__aviso" data-testid="destino-elegido">
          Se vinculará a <strong>«{destino.nombre}»</strong>
          {#if destino.municipio}<span class="terceros__tenue"> · {destino.municipio}</span>{/if}
          {#if destino.coincide}
            <StatusChip status="success" label="Documento coincide" />
          {:else}
            <StatusChip status="warning" label="Documento distinto" />
          {/if}
        </p>
      {/if}
      {#if provisional.candidata && destino?.id !== provisional.candidata.id}
        <p class="terceros__aviso" data-testid="candidata-modal">
          Ficha confirmada con el documento declarado: <strong
            >{provisional.candidata.nombre}</strong
          >
          {#if provisional.candidata.municipio}
            <span class="terceros__tenue"> · {provisional.candidata.municipio}</span>{/if}
          <Button
            variant="ghost"
            size="sm"
            on:click={() => (destino = { ...provisional.candidata, coincide: true })}
          >
            Elegir esta ficha
          </Button>
        </p>
      {/if}
      <div class="terceros__buscador">
        <FormField
          id="conciliar-buscar"
          label={destino ? "Elegir otra ficha" : "Buscar la ficha existente"}
          bind:value={busquedaDestino}
          hint="Por nombre o entidad."
        />
        <Button variant="secondary" loading={buscandoDestino} on:click={buscarDestino}>
          Buscar
        </Button>
      </div>
      {#if candidatosDestino.length > 0}
        <ul class="terceros__fichas" aria-label="Fichas encontradas">
          {#each candidatosDestino as t (t.id)}
            <li class="terceros__ficha">
              <span>
                <strong>{t.nombre}</strong>
                {#if t.numero_documento}
                  <span class="terceros__tenue"> · {t.numero_documento}</span>{/if}
              </span>
              <Button variant="ghost" size="sm" on:click={() => elegirDestino(t)}>Elegir</Button>
            </li>
          {/each}
        </ul>
      {/if}
    {:else if accionConciliar === "confirmar"}
      <p class="terceros__sub">
        El documento declarado pasa a ser la identidad de la ficha y queda en el directorio. Úselo
        solo si es una persona distinta de cualquier ficha existente.
      </p>
    {:else}
      <p class="terceros__sub">
        La ficha se queda como copia de lo que declaró el remitente en sus radicados, sin identidad
        propia en el directorio. Úselo si hay sospecha de suplantación o los datos no sirven.
      </p>
    {/if}

    {#if pideMedio}
      <FormField
        id="conciliar-medio"
        type="select"
        label="¿Cómo se comprobó que es la misma persona? (obligatorio)"
        placeholder="Elija el medio de verificación"
        options={MEDIOS}
        required
        bind:value={medio}
      />
    {/if}

    {#if motivoExigido}
      <p class="terceros__aviso" data-testid="aviso-motivo">
        No escriba números de documento ni datos personales: el motivo queda en el registro de
        auditoría, que no se puede borrar.
      </p>
      <FormField
        id="conciliar-motivo"
        type="textarea"
        label="Motivo (obligatorio)"
        bind:value={motivo}
        hint={`Al menos ${MIN_MOTIVO} caracteres. Queda en la auditoría.`}
        error={motivo.trim().length > 0 && !motivoValido
          ? `Faltan caracteres: mínimo ${MIN_MOTIVO}.`
          : ""}
      />
    {/if}

    {#if accionConciliar === "vincular"}
      <FormField
        id="conciliar-frase"
        label={`Escribe «${FRASE_VINCULAR}» para confirmar`}
        bind:value={fraseVincular}
      />
    {/if}

    {#if errorConciliar}
      <p class="terceros__aviso" role="alert">{errorConciliar}</p>
    {/if}
  {/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalConciliar = false)}>Cancelar</Button>
    <Button
      variant="primary"
      loading={conciliando}
      disabled={!puedeEnviarConciliar}
      on:click={confirmarConciliar}
    >
      {TITULOS_CONCILIAR[accionConciliar]}
    </Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .terceros {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__titulo {
      font-weight: var(--op-weight-bold);
      margin: 0;
    }

    &__h2 {
      font-size: var(--op-font-md);
      font-weight: var(--op-weight-bold);
      margin: var(--op-space-3) 0 var(--op-space-1);
    }

    &__sub {
      color: var(--op-text-secondary);
      margin: 0 0 var(--op-space-2);
    }

    &__tenue {
      color: var(--op-text-secondary);
    }

    &__aviso {
      margin: 0 0 var(--op-space-2);
    }

    &__buscador {
      display: flex;
      gap: var(--op-space-2);
      align-items: flex-end;
      margin-bottom: var(--op-space-3);
    }

    &__grupo {
      border-top: 1px solid var(--op-border);
      padding-top: var(--op-space-2);
      margin-top: var(--op-space-2);
    }

    &__grupo-eje {
      font-weight: var(--op-weight-medium);
      margin: 0 0 var(--op-space-1);
    }

    &__clave {
      color: var(--op-text-secondary);
      font-family: var(--op-font-family-mono);
    }

    &__fichas {
      list-style: none;
      margin: 0;
      padding: 0;
    }

    &__ficha {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-1) 0;
      flex-wrap: wrap;
    }

    &__acciones {
      display: flex;
      gap: var(--op-space-1);
      flex-shrink: 0;

      &--envuelve {
        flex-wrap: wrap;
        flex-shrink: 1;
      }
    }

    &__pendiente {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: var(--op-space-3);
      flex-wrap: wrap;
      padding: var(--op-space-2) 0;
      border-top: 1px solid var(--op-border);
    }

    &__pendiente-datos {
      flex: 1 1 18rem;
      min-width: 0;
    }

    &__pendiente-nombre,
    &__linea {
      margin: 0 0 var(--op-space-1);
      overflow-wrap: anywhere;
    }

    &__paginas {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: var(--op-space-2);
      margin-top: var(--op-space-2);
    }
  }
</style>
