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
  /admin/correo — Ingesta de correo (E19).

  **Capacidad construida y no ofrecida.** El backend radicaba correos desde F2 y
  no había ninguna pantalla: la ingesta solo se podía disparar con curl y su
  resultado solo se podía ver buscando los radicados a ciegas. Es el mismo hueco
  que tenían la ventanilla pública y la verificación por código, y se cierra
  igual — dando la puerta, no reimplementando lo de dentro.

  Esta pantalla es **configuración**: los buzones de la entidad, sus carpetas y
  la aplicación OAuth con la que se accede a Google o Microsoft. Radicar no se
  hace aquí: se hace en la bandeja de cada buzón, correo por correo, y cada
  ficha enlaza a la suya.

  Hubo una ruta heredada —un único buzón declarado en el entorno con las
  variables `IMAP_*`, y un botón que radicaba de golpe todo lo no leído—. Está
  retirada: radicaba también la publicidad, porque no tenía dónde separar lo que
  se radica de lo que no.

-->
<script>
  import { onMount } from "svelte";
  import { invalidateAll } from "$app/navigation";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  import { codigoError, mensajeError } from "$lib/utils/apiError.js";
  import Button from "$lib/components/ui/Button.svelte";
  import Card from "$lib/components/ui/Card.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  $: cuentas = data.cuentas ?? [];
  $: cuentasError = data.cuentasError ?? null;
  $: apps = data.apps ?? [];
  $: appsError = data.appsError ?? null;

  // ── Cuentas ────────────────────────────────────────────────────────────
  //
  // Los tres tipos que la entidad puede necesitar, explicados por lo que
  // implican y no solo por su nombre: la diferencia entre institucional y
  // personal no es de etiqueta, es que de una se custodia la credencial y de la
  // otra no.
  const TIPOS = [
    {
      valor: "institucional",
      etiqueta: "De la entidad (compartida)",
      ayuda:
        "El buzón de la ventanilla, que trabajan varios funcionarios. Sus credenciales las custodia el sistema, cifradas.",
    },
    {
      valor: "personal",
      etiqueta: "Personal de un funcionario",
      ayuda:
        "El correo institucional de una persona. Su contraseña NO se guarda: se le pide cada vez que entra a trabajar su buzón.",
    },
  ];

  const PROVEEDORES = [
    { valor: "imap", etiqueta: "Servidor IMAP (hosting, Zimbra, cPanel…)", oauth: false },
    { valor: "gmail", etiqueta: "Google Workspace / Gmail", oauth: true },
    { valor: "microsoft", etiqueta: "Microsoft 365 / Exchange Online", oauth: true },
  ];

  // Por qué Google y Microsoft no admiten contraseña. Se dice en la pantalla, no
  // solo se impide: un administrador que no lo sepa buscará el problema en la
  // contraseña, que es donde no está.
  const MOTIVO_OAUTH = {
    gmail:
      "Google Workspace dejó de aceptar usuario y contraseña por IMAP el 1 de mayo de 2025, ni siquiera contraseñas de aplicación.",
    microsoft:
      "Microsoft 365 no acepta usuario y contraseña por IMAP desde que Exchange Online retiró la autenticación básica, en 2022.",
  };

  let formCuenta = null;
  let formApp = null;
  let guardando = false;
  /** @type {Record<string, {ok: boolean, detalle: string, carpetas: string[]}>} */
  let pruebas = {};
  let probando = "";

  function nuevaCuenta() {
    formCuenta = {
      nombre: "",
      tipo: "institucional",
      proveedor: "imap",
      direccion: "",
      usuario_login: "",
      host: "",
      puerto: 993,
      usa_ssl: true,
      password: "",
      carpeta_origen: "INBOX",
      carpeta_radicados: "Radicados",
      dependencia_destino: "",
      modo: "asistida",
    };
  }

  $: proveedorExigeOauth = Boolean(formCuenta && MOTIVO_OAUTH[formCuenta.proveedor]);
  $: appDelProveedor = formCuenta ? apps.find((a) => a.proveedor === formCuenta.proveedor) : null;
  // Una cuenta personal nunca guarda credencial; una de proveedor con OAuth2
  // tampoco pide contraseña. El campo se oculta en ambos casos, y el formulario
  // no la envía — ocultar sin dejar de enviar sería el defecto clásico.
  $: pideContrasena =
    Boolean(formCuenta) && !proveedorExigeOauth && formCuenta.tipo === "institucional";

  async function guardarCuenta() {
    guardando = true;
    try {
      const cuerpo = { ...formCuenta };
      cuerpo.auth_tipo = proveedorExigeOauth ? "oauth2" : "password";
      if (proveedorExigeOauth) {
        if (!appDelProveedor) {
          notifyError("Primero registre la aplicación OAuth de su entidad para este proveedor.");
          return;
        }
        cuerpo.app_oauth_id = appDelProveedor.id;
      }
      if (!pideContrasena) delete cuerpo.password;
      for (const clave of ["usuario_login", "host", "dependencia_destino"]) {
        if (!cuerpo[clave]) delete cuerpo[clave];
      }
      const res = await fetch("/admin/correo/api/cuentas", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cuerpo),
      });
      const datos = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(mensajeError(datos, `No se pudo crear la cuenta (${codigoError(datos)}).`));
        return;
      }
      notifySuccess(`Cuenta «${datos.nombre}» creada.`);
      formCuenta = null;
      await invalidateAll();
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      guardando = false;
    }
  }

  async function probar(cuenta) {
    probando = cuenta.id;
    try {
      const cuerpo = {};
      if (cuenta.tipo === "personal") {
        // La contraseña de una cuenta personal no está guardada; sin ella no hay
        // nada que probar, y se dice en vez de mandar una petición que fallará.
        const escrita = window.prompt(
          `Contraseña de ${cuenta.direccion} (no se guarda, se usa solo para esta prueba)`,
        );
        if (!escrita) return;
        cuerpo.password = escrita;
      }
      const res = await fetch(`/admin/correo/api/cuentas/${cuenta.id}/probar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cuerpo),
      });
      const datos = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(mensajeError(datos, `No se pudo probar (${codigoError(datos)}).`));
        return;
      }
      pruebas = { ...pruebas, [cuenta.id]: datos };
      if (datos.ok) notifySuccess(datos.detalle);
      else notifyError(datos.detalle);
      await invalidateAll();
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      probando = "";
    }
  }

  async function autorizar(cuenta) {
    try {
      const res = await fetch(`/admin/correo/api/cuentas/${cuenta.id}/oauth/iniciar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const datos = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(
          mensajeError(datos, `No se pudo iniciar la autorización (${codigoError(datos)}).`),
        );
        return;
      }
      // Qué cuenta se está autorizando se recuerda aquí porque el proveedor
      // redirige a una URL fija —no admite comodines en la ruta— y al volver hay
      // que saberlo. No es un canal de confianza y no hace falta que lo sea: el
      // backend comprueba que el `state` pertenezca a esa cuenta y a ese
      // usuario, así que manipularlo solo consigue que el canje falle.
      sessionStorage.setItem("orpyca:oauth-correo-cuenta", cuenta.id);
      window.location.href = datos.authorization_url;
    } catch {
      notifyError("No se pudo conectar al servidor.");
    }
  }

  async function guardarApp() {
    guardando = true;
    try {
      const res = await fetch("/admin/correo/api/apps-oauth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formApp),
      });
      const datos = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(
          mensajeError(datos, `No se pudo registrar la aplicación (${codigoError(datos)}).`),
        );
        return;
      }
      notifySuccess("Aplicación registrada.");
      formApp = null;
      await invalidateAll();
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      guardando = false;
    }
  }

  function nuevaApp() {
    formApp = {
      proveedor: "gmail",
      client_id: "",
      client_secret: "",
      azure_directory_id: "",
      redirect_uri: `${window.location.origin}/admin/correo/oauth/callback`,
      notas: "",
    };
  }

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Ingesta de correo" }]);
  });
</script>

<svelte:head>
  <title>Ingesta de correo — OrpycaMCP</title>
</svelte:head>

<div class="correo">
  <header class="correo__header">
    <h1 class="correo__title">Ingesta de correo</h1>
    <p class="correo__subtitle">
      Configure los buzones desde los que la entidad recibe comunicaciones. Cada uno se trabaja en
      su propia bandeja.
    </p>
  </header>

  <!-- ── Cuentas de correo ────────────────────────────────────────────── -->
  <Card title="Buzones configurados">
    {#if cuentasError}
      <EmptyState
        icon="fas fa-triangle-exclamation"
        title={cuentasError.status === 403
          ? "Sin permiso para ver las cuentas de correo"
          : "No se pudieron cargar las cuentas"}
        description="{cuentasError.detail} Esto no significa que no haya ninguna configurada: no se pudo preguntar."
      />
    {:else if cuentas.length === 0}
      <EmptyState
        icon="fas fa-envelope"
        title="Todavía no hay ningún buzón configurado"
        description="Un buzón es la cuenta de correo desde la que la entidad recibe comunicaciones para radicar. Puede tener varios: uno para PQRS, otro para notificaciones judiciales, otro para facturación."
      />
    {:else}
      <ul class="cuentas" role="list">
        {#each cuentas as cuenta (cuenta.id)}
          <li class="cuenta">
            <div class="cuenta__cabecera">
              <span class="cuenta__nombre">{cuenta.nombre}</span>
              <span class="cuenta__chip cuenta__chip--{cuenta.tipo}">
                {cuenta.tipo === "personal" ? "Personal" : "De la entidad"}
              </span>
              {#if cuenta.modo === "automatica"}
                <span class="cuenta__chip">Radica automáticamente</span>
              {/if}
              {#if !cuenta.activa}
                <span class="cuenta__chip cuenta__chip--inactiva">Inactiva</span>
              {/if}
            </div>

            <dl class="cuenta__datos">
              <dt>Dirección</dt>
              <dd><code class="op-dato">{cuenta.direccion}</code></dd>
              {#if cuenta.usuario_login && cuenta.usuario_login !== cuenta.direccion}
                <!--
                  Se muestra SOLO cuando difiere de la dirección, que es cuando
                  importa. El dato viajaba en la respuesta y la ficha no lo leía:
                  al diagnosticar un «credenciales rechazadas», saber con qué
                  usuario se está autenticando es lo primero que hace falta.
                -->
                <dt>Usuario</dt>
                <dd><code class="op-dato">{cuenta.usuario_login}</code></dd>
              {/if}
              <dt>Servidor</dt>
              <dd>{cuenta.host}:{cuenta.puerto}{cuenta.usa_ssl ? " (SSL)" : ""}</dd>
              <dt>Carpetas</dt>
              <dd>
                Lee de <strong>{cuenta.carpeta_origen}</strong>; al radicar mueve a
                <strong>{cuenta.carpeta_radicados}</strong>
              </dd>
              {#if cuenta.dependencia_destino}
                <dt>Reparte a</dt>
                <dd>{cuenta.dependencia_destino}</dd>
              {/if}
            </dl>

            {#if cuenta.ultimo_error}
              <p class="cuenta__error">
                <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
                Último intento fallido: {cuenta.ultimo_error}
              </p>
            {/if}

            {#if cuenta.auth_tipo === "oauth2" && !cuenta.tiene_credencial}
              <!--
                Este es el estado que un administrador tiene que poder ver de un
                vistazo: la cuenta existe pero nadie ha autorizado todavía el
                acceso al buzón, así que no traerá ni un correo. Sin decirlo, el
                síntoma sería «no llega nada», indistinguible de un buzón vacío.
              -->
              <p class="cuenta__aviso">
                Falta autorizar el acceso al buzón ante {cuenta.proveedor === "gmail"
                  ? "Google"
                  : "Microsoft"}. Mientras tanto esta cuenta no puede traer correo.
              </p>
            {/if}

            {#if pruebas[cuenta.id]?.carpetas?.length}
              <p class="cuenta__carpetas">
                Carpetas del buzón: {pruebas[cuenta.id].carpetas.join(" · ")}
              </p>
            {/if}

            <div class="cuenta__acciones">
              <!--
                La bandeja es a donde se va a trabajar; el resto de esta pantalla
                es configuración. Sin este enlace la bandeja existiría y solo se
                alcanzaría escribiendo la URL — que es exactamente el hueco que
                esta revisión ha encontrado media docena de veces.
              -->
              <Button size="sm" icon="fas fa-inbox" href="/correo/{cuenta.id}">
                Abrir la bandeja
              </Button>
              <Button
                variant="ghost"
                size="sm"
                icon="fas fa-plug"
                loading={probando === cuenta.id}
                on:click={() => probar(cuenta)}
              >
                Probar conexión
              </Button>
              {#if cuenta.auth_tipo === "oauth2"}
                <Button size="sm" icon="fas fa-key" on:click={() => autorizar(cuenta)}>
                  {cuenta.tiene_credencial ? "Volver a autorizar" : "Autorizar acceso"}
                </Button>
              {/if}
            </div>
          </li>
        {/each}
      </ul>
    {/if}

    <div class="correo__acciones">
      <Button icon="fas fa-plus" on:click={nuevaCuenta}>Añadir un buzón</Button>
    </div>
  </Card>

  <!-- ── Aplicación OAuth de la entidad ──────────────────────────────── -->
  <Card title="Aplicación para Google y Microsoft">
    <p class="correo__nota">
      Para leer un buzón de Google Workspace o de Microsoft 365 hace falta que
      <strong>su entidad</strong> registre una aplicación en su propio Google Cloud o Azure AD. No la
      registra OrpycaMCP: una aplicación interna de su organización está exenta de la verificación y la
      auditoría anual que Google exige a las demás, y así es su entidad quien controla qué accede a su
      correo y puede revocarlo.
    </p>

    {#if appsError}
      <EmptyState
        icon="fas fa-triangle-exclamation"
        title="No se pudieron cargar las aplicaciones"
        description={appsError.detail}
      />
    {:else if apps.length === 0}
      <p class="correo__nota">
        Todavía no hay ninguna registrada. No hace falta si sus buzones están en un servidor IMAP
        propio o de hosting.
      </p>
    {:else}
      <dl class="correo__datos">
        {#each apps as app (app.id)}
          <dt>{app.proveedor === "gmail" ? "Google" : "Microsoft"}</dt>
          <dd>
            <code class="op-dato">{app.client_id}</code>
            {#if !app.tiene_secreto}<em> — sin secreto</em>{/if}
          </dd>
        {/each}
      </dl>
    {/if}

    <div class="correo__acciones">
      <Button variant="ghost" icon="fas fa-plus" on:click={nuevaApp}>
        Registrar una aplicación
      </Button>
    </div>
  </Card>
</div>

<!-- Alta de un buzón -->
<Modal
  open={Boolean(formCuenta)}
  title="Añadir un buzón"
  size="lg"
  on:close={() => (formCuenta = null)}
>
  {#if formCuenta}
    <div class="form">
      <label class="form__campo">
        <span>Nombre</span>
        <input class="input" bind:value={formCuenta.nombre} placeholder="Ventanilla PQRS" />
        <small>Cómo lo llaman en la entidad, para distinguirlo de los demás.</small>
      </label>

      <fieldset class="form__campo">
        <legend>Tipo de buzón</legend>
        {#each TIPOS as t (t.valor)}
          <label class="form__radio">
            <input type="radio" bind:group={formCuenta.tipo} value={t.valor} />
            <span>
              <strong>{t.etiqueta}</strong>
              <small>{t.ayuda}</small>
            </span>
          </label>
        {/each}
      </fieldset>

      <label class="form__campo">
        <span>Proveedor</span>
        <select class="input" bind:value={formCuenta.proveedor}>
          {#each PROVEEDORES as p (p.valor)}
            <option value={p.valor}>{p.etiqueta}</option>
          {/each}
        </select>
        {#if proveedorExigeOauth}
          <small class="form__aviso">
            {MOTIVO_OAUTH[formCuenta.proveedor]} Se conectará mediante autorización (OAuth2), que se concede
            después de crear el buzón.
          </small>
          {#if !appDelProveedor}
            <small class="form__aviso">
              Antes hay que registrar la aplicación de su entidad para este proveedor.
            </small>
          {/if}
        {/if}
      </label>

      <label class="form__campo">
        <span>Dirección de correo</span>
        <input class="input" bind:value={formCuenta.direccion} placeholder="pqrs@entidad.gov.co" />
      </label>

      <label class="form__campo">
        <span>Usuario para autenticarse <em>(si no es la dirección)</em></span>
        <input class="input" bind:value={formCuenta.usuario_login} placeholder="(opcional)" />
        <small>
          Algunos servidores piden solo la parte anterior a la arroba, o
          <code class="op-dato">DOMINIO\usuario</code>. Déjelo vacío si acepta la dirección
          completa.
        </small>
      </label>

      {#if formCuenta.proveedor === "imap"}
        <div class="form__fila">
          <label class="form__campo">
            <span>Servidor</span>
            <input class="input" bind:value={formCuenta.host} placeholder="imap.entidad.gov.co" />
          </label>
          <label class="form__campo form__campo--corto">
            <span>Puerto</span>
            <input class="input" type="number" bind:value={formCuenta.puerto} />
          </label>
          <label class="form__casilla">
            <input type="checkbox" bind:checked={formCuenta.usa_ssl} />
            <span>Usar SSL</span>
          </label>
        </div>
      {/if}

      {#if pideContrasena}
        <label class="form__campo">
          <span>Contraseña del buzón</span>
          <input class="input" type="password" bind:value={formCuenta.password} />
          <small>Se guarda cifrada y no vuelve a mostrarse.</small>
        </label>
      {:else if formCuenta.tipo === "personal"}
        <p class="form__aviso">
          La contraseña de un buzón personal <strong>no se guarda</strong>. Se le pedirá al
          funcionario cada vez que entre a trabajar su correo.
        </p>
      {/if}

      <div class="form__fila">
        <label class="form__campo">
          <span>Carpeta de la que se lee</span>
          <input class="input" bind:value={formCuenta.carpeta_origen} />
          <small>
            A esta carpeta se arrastra lo que sí se va a radicar. Lo que quede fuera —publicidad,
            boletines— no lo verá el sistema.
          </small>
        </label>
        <label class="form__campo">
          <span>Carpeta a la que se mueve al radicar</span>
          <input class="input" bind:value={formCuenta.carpeta_radicados} />
          <small>Así sale de la vista de todos a la vez y no se radica dos veces.</small>
        </label>
      </div>

      <label class="form__campo">
        <span>Dependencia a la que se reparte <em>(opcional)</em></span>
        <input class="input" bind:value={formCuenta.dependencia_destino} />
      </label>

      <fieldset class="form__campo">
        <legend>Cómo se radica</legend>
        <label class="form__radio">
          <input type="radio" bind:group={formCuenta.modo} value="asistida" />
          <span>
            <strong>Con revisión</strong>
            <small>
              Un funcionario ve el correo, completa lo que falte y radica. Queda registrado quién lo
              hizo.
            </small>
          </span>
        </label>
        <label class="form__radio">
          <input type="radio" bind:group={formCuenta.modo} value="automatica" />
          <span>
            <strong>Automáticamente</strong>
            <small>
              Todo lo que entre en la carpeta se radica sin revisión previa. Consume números de
              radicado, que no se pueden liberar.
            </small>
          </span>
        </label>
      </fieldset>
    </div>
  {/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (formCuenta = null)} disabled={guardando}>
      Cancelar
    </Button>
    <Button loading={guardando} on:click={guardarCuenta}>Crear buzón</Button>
  </svelte:fragment>
</Modal>

<!-- Alta de la aplicación OAuth de la entidad -->
<Modal
  open={Boolean(formApp)}
  title="Registrar la aplicación de su entidad"
  size="lg"
  on:close={() => (formApp = null)}
>
  {#if formApp}
    <div class="form">
      <p class="form__aviso">
        Estos datos salen de la aplicación que su entidad registre en
        {formApp.proveedor === "gmail" ? "Google Cloud" : "Azure AD"}. Regístrela como
        <strong>interna de su organización</strong>: así queda exenta de la verificación que Google
        exige a las aplicaciones públicas.
      </p>

      <label class="form__campo">
        <span>Proveedor</span>
        <select class="input" bind:value={formApp.proveedor}>
          <option value="gmail">Google Workspace</option>
          <option value="microsoft">Microsoft 365</option>
        </select>
      </label>

      <label class="form__campo">
        <span>Identificador de la aplicación (client ID)</span>
        <input class="input" bind:value={formApp.client_id} />
      </label>

      <label class="form__campo">
        <span>Secreto de la aplicación (client secret)</span>
        <input class="input" type="password" bind:value={formApp.client_secret} />
        <small>Se guarda cifrado y no vuelve a mostrarse.</small>
      </label>

      {#if formApp.proveedor === "microsoft"}
        <label class="form__campo">
          <span>Identificador del directorio de Azure AD</span>
          <input class="input" bind:value={formApp.azure_directory_id} />
          <small>El «Directory (tenant) ID» de la aplicación registrada. Google no lo usa.</small>
        </label>
      {/if}

      <label class="form__campo">
        <span>Dirección de retorno (redirect URI)</span>
        <input class="input" bind:value={formApp.redirect_uri} />
        <small>
          Tiene que estar registrada <strong>idéntica</strong> en la consola del proveedor: una barra
          de más y la autorización falla con un error que no explica nada.
        </small>
      </label>

      <label class="form__campo">
        <span>Notas <em>(opcional)</em></span>
        <input class="input" bind:value={formApp.notas} />
      </label>
    </div>
  {/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (formApp = null)} disabled={guardando}>Cancelar</Button>
    <Button loading={guardando} on:click={guardarApp}>Registrar</Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .correo {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-5);
  }

  .correo__title {
    font-weight: 700;
    color: var(--op-text);
  }

  .correo__subtitle {
    color: var(--op-text-secondary);
  }

  .correo__datos {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: var(--op-space-2) var(--op-space-4);
    align-items: center;
    margin-bottom: var(--op-space-4);

    dt {
      color: var(--op-text-secondary);
    }
  }

  .correo__nota {
    margin-top: var(--op-space-3);
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
  }

  .cuentas {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
    margin-bottom: var(--op-space-4);
  }

  .cuenta {
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-4);
  }

  .cuenta__cabecera {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--op-space-2);
    margin-bottom: var(--op-space-3);
  }

  .cuenta__nombre {
    font-weight: 700;
  }

  .cuenta__chip {
    font-size: var(--op-font-xs);
    padding: 0.1rem 0.5rem;
    border-radius: var(--op-radius-full);
    background: var(--op-bg);
    color: var(--op-text-secondary);
    border: 1px solid var(--op-border);
  }

  .cuenta__chip--personal {
    background: var(--op-info-bg);
    color: var(--op-info-fg);
    border-color: transparent;
  }

  .cuenta__chip--inactiva {
    background: var(--op-warning-bg);
    color: var(--op-warning-fg);
    border-color: transparent;
  }

  .cuenta__datos {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: var(--op-space-1) var(--op-space-4);

    dt {
      color: var(--op-text-secondary);
    }
  }

  .cuenta__error,
  .cuenta__aviso,
  .cuenta__carpetas {
    margin-top: var(--op-space-3);
    font-size: var(--op-font-sm);
  }

  .cuenta__error {
    color: var(--op-error-fg);
  }

  .cuenta__aviso {
    color: var(--op-warning-fg);
  }

  .cuenta__carpetas {
    color: var(--op-text-secondary);
  }

  .cuenta__acciones {
    display: flex;
    flex-wrap: wrap;
    gap: var(--op-space-2);
    margin-top: var(--op-space-3);
  }

  /*
    El formulario es una COLUMNA de campos. Cada campo es un hijo directo con su
    etiqueta, su control y su ayuda dentro — la prosa nunca queda suelta bajo un
    contenedor flex, que fue un defecto real de este repositorio.
  */
  .form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
  }

  .form__campo {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-1);
    flex: 1;

    > span,
    legend {
      font-weight: 600;
    }

    small {
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
    }
  }

  .form__campo--corto {
    max-width: 8rem;
  }

  .form__fila {
    display: flex;
    flex-wrap: wrap;
    gap: var(--op-space-4);
    align-items: flex-start;
  }

  .form__radio,
  .form__casilla {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    padding: var(--op-space-2) 0;

    /* El texto del radio va en un hijo propio, en columna: si `strong` y `small`
       colgaran del `label` (que es flex en fila) quedarían como dos columnas. */
    > span {
      display: flex;
      flex-direction: column;
      gap: 0.15rem;
    }
  }

  .form__aviso {
    color: var(--op-warning-fg);
    font-size: var(--op-font-sm);
  }

  /*
    Cada párrafo es un hijo directo; el contenedor NO es flex en fila por eso
    mismo — la advertencia de un acto irreversible troceada en columnas fue un
    defecto real de este repo (ver `prosa-en-flex.test.js`).
  */
  .correo__confirmacion {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
  }
</style>
