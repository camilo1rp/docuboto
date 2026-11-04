/* ============================================================================
   Docuboto 3.0 — shell, estado, router, acciones y sistema de deshacer
   ========================================================================== */
(function () {
  "use strict";

  window.esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };

  /* ==========================================================================
     ESTADO
     ========================================================================== */
  const state = {
    route: "escritorio",
    docTab: "bandeja",
    distTab: "envios",
    alertTab: "vencimientos",
    recordTab: "resumen",
    activeRecord: "r1",
    selected: new Set(),
    wizardStep: 3,
    mode: "escala",
    caps: new Set(DB.capsForMode("escala")),
    aiLevel: "triage",
    aiScopes: new Set(["publica"]),
    agentOpen: true,
    agentTab: "brief",
    paletteOpen: false,
    paletteMode: "preguntar",
    modal: null,
    ledgerFilter: "todo",
    appliedSuggestions: new Set(),
    theme: "light",
    density: "normal",
    ledger: DB.activity.map((l) => Object.assign({}, l)),
    railCollapsed: false,
    railOpenMobile: false,
  };

  const isMobile = () => window.innerWidth <= 860;

  /* ==========================================================================
     UTILIDADES DE ESTADO
     ========================================================================== */
  const num = (id) => (DB.records.find((r) => r.id === id) || {}).num || "";

  function setTheme(t) {
    state.theme = t;
    document.documentElement.setAttribute("data-theme", t);
  }

  function addLedger(entry) {
    state.ledger.unshift(
      Object.assign(
        {
          id: "l" + Date.now() + Math.floor(Math.random() * 99),
          cls: "A",
          icon: "check",
          title: "",
          meta: "Ahora · Tú",
          diff: "",
          undoable: true,
        },
        entry,
      ),
    );
  }

  /* ==========================================================================
     DESHACER
     ========================================================================== */
  const undoEntries = [];
  let undoSeq = 0;

  function pushUndo(opts) {
    const seconds = opts.seconds || 10;
    const entry = {
      id: "u" + ++undoSeq,
      title: opts.title,
      sub: opts.sub || "",
      seconds: seconds,
      expiresAt: Date.now() + seconds * 1000,
      onUndo: opts.onUndo,
      done: false,
    };
    undoEntries.push(entry);
    if (undoEntries.length > 3) undoEntries.shift();
    renderToasts();
  }

  function performUndo(id) {
    const entry = undoEntries.find((e) => e.id === id);
    if (!entry || entry.done) return;
    entry.done = true;
    if (entry.onUndo) entry.onUndo();
    const i = undoEntries.indexOf(entry);
    if (i >= 0) undoEntries.splice(i, 1);
    renderToasts();
    render();
    showToast("check", "Acción deshecha", "El estado volvió a como estaba y el acto quedó asentado.");
  }

  function showToast(iconName, title, sub) {
    const stack = document.getElementById("toasts");
    const el = document.createElement("div");
    el.className = "toast";
    el.style.position = "relative";
    el.innerHTML =
      '<span style="color:var(--db-ok)">' +
      icon(iconName) +
      "</span>" +
      '<span style="min-width:0"><b style="font-weight:600">' +
      window.esc(title) +
      "</b>" +
      (sub
        ? '<span style="display:block;font-size:11.5px;color:var(--db-ink-3)">' +
          window.esc(sub) +
          "</span>"
        : "") +
      "</span>";
    stack.appendChild(el);
    setTimeout(function () {
      el.style.transition = "opacity .2s, transform .2s";
      el.style.opacity = "0";
      el.style.transform = "translateY(6px)";
      setTimeout(() => el.remove(), 220);
    }, 2600);
  }

  function renderToasts() {
    const stack = document.getElementById("toasts");
    if (!stack) return;
    stack.innerHTML = undoEntries
      .map(
        (e) => `
      <div class="undo-bar" role="status">
        <span class="undo-bar__ring">
          <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
            <circle cx="11" cy="11" r="9" fill="none" stroke="rgba(255,255,255,.22)" stroke-width="2.5"></circle>
            <circle data-ring="${e.id}" cx="11" cy="11" r="9" fill="none" stroke="#7ce9f0" stroke-width="2.5"
              stroke-linecap="round" stroke-dasharray="56.5" stroke-dashoffset="0"></circle>
          </svg>
          <span data-secs="${e.id}">${e.seconds}</span>
        </span>
        <span class="undo-bar__text">
          <b>${window.esc(e.title)}</b>
          ${e.sub ? `<span class="undo-bar__sub">${window.esc(e.sub)}</span>` : ""}
        </span>
        <button class="undo-bar__btn" data-act="undo-toast" data-id="${e.id}">
          ${icon("undo")} Deshacer
        </button>
        <button class="undo-bar__close" data-act="undo-dismiss" data-id="${e.id}" aria-label="Descartar aviso">
          ${icon("x")}
        </button>
      </div>`,
      )
      .join("");
    tickUndo();
  }

  function tickUndo() {
    const now = Date.now();
    let changed = false;
    undoEntries.forEach((e) => {
      const remaining = Math.max(0, (e.expiresAt - now) / 1000);
      const ring = document.querySelector(`[data-ring="${e.id}"]`);
      const secs = document.querySelector(`[data-secs="${e.id}"]`);
      if (ring) ring.setAttribute("stroke-dashoffset", String(56.5 * (1 - remaining / e.seconds)));
      if (secs) secs.textContent = Math.ceil(remaining);
      if (remaining <= 0) {
        e.done = true;
        changed = true;
      }
    });
    if (changed) {
      for (let i = undoEntries.length - 1; i >= 0; i--) {
        if (undoEntries[i].done) undoEntries.splice(i, 1);
      }
      renderToasts();
    }
  }
  setInterval(tickUndo, 120);

  /* ==========================================================================
     RENDER
     ========================================================================== */
  const NAV = [
    {
      label: "Trabajo",
      items: [
        { id: "escritorio", label: "Escritorio", icon: "home" },
        { id: "documentos", label: "Documentos", icon: "file", count: 12 },
        { id: "distribucion", label: "Distribución", icon: "send", count: 3 },
        { id: "alertas", label: "Alertas", icon: "bell", count: 6, hot: true },
      ],
    },
    {
      label: "Gobierno",
      items: [
        { id: "capacidades", label: "Capacidades", icon: "layers" },
        { id: "inteligencia", label: "Inteligencia", icon: "sparkles", ai: true },
        { id: "actividad", label: "Actividad", icon: "undo" },
      ],
    },
  ];

  function renderRail() {
    return `
      <div class="rail__brand">
        <img class="rail__mark" src="assets/proboto-mark.svg" alt="">
        <span class="rail__wordmark">docu<em>boto</em></span>
        <span class="rail__version">3.0</span>
      </div>
      <div class="rail__scroll">
        ${NAV.map(
          (g) => `
          <div class="rail__group">
            <div class="rail__label">${g.label}</div>
            ${g.items
              .map(
                (it) => `
              <a class="rail__link" href="#/${it.id}" data-act="go" data-route="${it.id}"
                 ${state.route === it.id ? 'aria-current="page"' : ""} title="${it.label}">
                ${icon(it.icon)}
                <span class="rail__text">${it.label}${it.ai ? ` <span class="chip chip--ai" style="margin-left:4px">IA</span>` : ""}</span>
                ${it.count ? `<span class="rail__count ${it.hot ? "rail__count--hot" : ""}">${it.count}</span>` : ""}
              </a>`,
              )
              .join("")}
          </div>`,
        ).join("")}
      </div>
      <div class="rail__foot">
        <a class="rail__link" href="#/sistema" data-act="go" data-route="sistema" title="Sistema de diseño"
           ${state.route === "sistema" ? 'aria-current="page"' : ""}>
          ${icon("grid")}<span class="rail__text">Sistema</span>
        </a>
        <a class="rail__link" href="#/ajustes" data-act="toast" data-msg="Ajustes de la entidad" title="Ajustes">
          ${icon("settings")}<span class="rail__text">Ajustes</span>
        </a>
      </div>`;
  }

  function renderTopbar() {
    return `
      <button class="icon-btn btn-rail" data-act="rail-toggle" title="Contraer navegación" aria-label="Contraer navegación">
        ${icon("menu")}
      </button>
      <button class="ctx" data-act="toast" data-msg="Cambiar de entidad o dependencia">
        <span class="ctx__dot">${DB.tenant.initials}</span>
        <span class="ctx__body">
          <span class="ctx__name">${DB.tenant.name}</span>
          <span class="ctx__unit">${DB.tenant.unit}</span>
        </span>
        ${icon("down", "faint")}
      </button>

      <button class="omni" data-act="palette-open">
        ${icon("search")}
        <span>Buscar, preguntar o hacer…</span>
        <span class="spacer"></span>
        <span class="omni__hint">${icon("sparkles")} proboto</span>
        <span class="kbd">⌘</span><span class="kbd">K</span>
      </button>

      <div class="topbar__right">
        <button class="icon-btn" data-act="theme-toggle" title="Cambiar tema" aria-label="Cambiar tema">
          ${icon(state.theme === "light" ? "moon" : "sun")}
        </button>
        <button class="icon-btn" data-act="go" data-route="actividad" title="Deshacer y actividad" aria-label="Actividad">
          ${icon("undo")}
        </button>
        <button class="icon-btn" data-act="go" data-route="alertas" title="Alertas" aria-label="Alertas">
          ${icon("bell")}<span class="dot">6</span>
        </button>
        <button class="icon-btn" data-act="agent-toggle" aria-pressed="${state.agentOpen}"
                title="Panel de proboto" aria-label="Panel de proboto">
          ${icon("sparkles")}
        </button>
        <button class="avatar" data-act="toast" data-msg="Perfil de Camilo Romero" title="Mi perfil">${DB.user.initials}</button>
      </div>`;
  }

  function renderView() {
    switch (state.route) {
      case "documentos":
        return VIEWS.documentos({
          tab: state.docTab,
          activeRecord: state.activeRecord,
          selected: state.selected,
          recordTab: state.recordTab,
          wizardStep: state.wizardStep,
          appliedSuggestions: state.appliedSuggestions,
        });
      case "distribucion":
        return wrap(
          "Distribución",
          "Envío y entrega",
          "Enviar, entregar y probar la entrega. Cada remesa tiene una ruta y un acuse.",
          VIEWS.distribucion({ tab: state.distTab }),
          "send",
        );
      case "alertas":
        return wrap(
          "Alertas",
          "Plazos y escalamiento",
          "Lo que se vence, quién lo tiene y qué pasa si nadie lo atiende.",
          VIEWS.alertas({ tab: state.alertTab }),
          "bell",
        );
      case "capacidades":
        return '<div class="page">' + VIEWS.capacidades({ mode: state.mode, caps: state.caps }) + "</div>";
      case "inteligencia":
        return '<div class="page">' + VIEWS.inteligencia({ aiLevel: state.aiLevel, aiScopes: state.aiScopes }) + "</div>";
      case "actividad":
        return '<div class="page">' + VIEWS.actividad({ ledgerFilter: state.ledgerFilter, ledger: state.ledger }) + "</div>";
      case "sistema":
        return VIEWS.sistema();
      default:
        return VIEWS.escritorio();
    }
  }

  function wrap(title, eyebrow, sub, body, ic) {
    return `
      <div class="page">
        <div class="page__head">
          <div class="page__heading">
            <div class="page__eyebrow">${icon(ic)} ${eyebrow}</div>
            <h1 class="page__title">${title}</h1>
            <p class="page__sub">${sub}</p>
          </div>
        </div>
        ${body}
      </div>`;
  }

  function renderAgent() {
    return VIEWS.agent({
      agentTab: state.agentTab,
      aiLevel: state.aiLevel,
      appliedSuggestions: state.appliedSuggestions,
      mode: state.mode,
    });
  }

  function render() {
    const main = document.querySelector(".main");
    const inbox = document.querySelector(".inbox__list");
    const scroll = main ? main.scrollTop : 0;
    const inboxScroll = inbox ? inbox.scrollTop : 0;

    const app = document.getElementById("app");
    app.innerHTML = `
      <div class="shell"
           data-rail="${
             isMobile()
               ? state.railOpenMobile
                 ? "open"
                 : "expanded"
               : state.railCollapsed
                 ? "collapsed"
                 : "expanded"
           }"
           data-agent="${state.agentOpen ? "open" : "closed"}">
        <aside class="rail" aria-label="Navegación principal">${renderRail()}</aside>
        <header class="topbar">${renderTopbar()}</header>
        <main class="main" id="main">${renderView()}</main>
        <aside class="agent" aria-label="Panel de proboto">${renderAgent()}</aside>
      </div>
      <div id="overlays">${state.paletteOpen ? VIEWS.palette(state) : ""}${VIEWS.modal(state)}</div>`;

    const m = document.querySelector(".main");
    if (m) m.scrollTop = scroll;
    const ib = document.querySelector(".inbox__list");
    if (ib) ib.scrollTop = inboxScroll;
  }

  /* ==========================================================================
     NAVEGACIÓN
     ========================================================================== */
  function go(route, tab) {
    state.railOpenMobile = false;
    state.route = route;
    if (route === "documentos" && tab) state.docTab = tab;
    if (route === "distribucion" && tab) state.distTab = tab;
    if (route === "alertas" && tab) state.alertTab = tab;
    const map = { documentos: "documentos", distribucion: "distribucion", alertas: "alertas" };
    if (map[route]) {
      state["docTab"] = state.docTab;
    }
    // el rail marca Documentos/Distribución/Alertas como destinos propios
    location.hash = "#/" + route;
    render();
  }

  /* ==========================================================================
     ACCIONES
     ========================================================================== */
  const actions = {
    noop: function () {},

    go: function (el) {
      go(el.dataset.route, el.dataset.tab);
    },

    "doc-tab": function (el) {
      state.docTab = el.dataset.tab;
      render();
    },
    "dist-tab": function (el) {
      state.distTab = el.dataset.tab;
      render();
    },
    "alert-tab": function (el) {
      state.alertTab = el.dataset.tab;
      render();
    },
    "record-tab": function (el) {
      state.recordTab = el.dataset.tab;
      render();
    },
    "ledger-filter": function (el) {
      state.ledgerFilter = el.dataset.id;
      render();
    },

    "open-record": function (el) {
      state.activeRecord = el.dataset.id;
      state.recordTab = "resumen";
      state.route = "documentos";
      state.docTab = "bandeja";
      if (!isMobile()) state.agentOpen = true;
      render();
      if (window.innerWidth <= 1180) {
        const detail = document.getElementById("record-detail");
        if (detail) detail.scrollIntoView({ block: "start", behavior: "smooth" });
      }
    },

    "toggle-select": function (el) {
      const id = el.dataset.id;
      if (state.selected.has(id)) state.selected.delete(id);
      else state.selected.add(id);
      render();
    },
    "clear-selection": function () {
      state.selected.clear();
      render();
    },

    bulk: function () {
      showToast("filter", "Filtros", "Por dependencia, plazo, tipo documental y nivel de acceso.");
    },
    "bulk-assign": function () {
      const n = state.selected.size;
      const prev = new Set(state.selected);
      addLedger({
        cls: "A",
        icon: "user",
        title: "Asignaste " + n + " radicados a Planeación · Inspección",
        meta: "Ahora · Tú · acción en lote",
        diff: "responsable: (varios) → Planeación · Inspección",
      });
      state.selected.clear();
      render();
      pushUndo({
        title: n + " radicados reasignados",
        sub: "Planeación · Inspección",
        onUndo: function () {
          state.selected = prev;
        },
      });
    },
    "bulk-tag": function () {
      const n = state.selected.size;
      const prev = new Set(state.selected);
      addLedger({
        cls: "A",
        icon: "tag",
        title: "Etiquetaste " + n + " radicados como «licencias 2026»",
        meta: "Ahora · Tú · acción en lote",
        diff: "etiquetas: (varias) → + licencias 2026",
      });
      state.selected.clear();
      render();
      pushUndo({
        title: "Etiqueta aplicada a " + n + " radicados",
        sub: "licencias 2026",
        onUndo: function () {
          state.selected = prev;
        },
      });
    },
    "bulk-archive": function () {
      const n = state.selected.size;
      const prev = new Set(state.selected);
      addLedger({
        cls: "A",
        icon: "archive",
        title: "Archivaste " + n + " radicados de la bandeja",
        meta: "Ahora · Tú · acción en lote",
        diff: "bandeja: visible → archivado",
      });
      state.selected.clear();
      render();
      pushUndo({
        title: n + " radicados archivados",
        sub: "Vuelven a la bandeja si deshaces",
        onUndo: function () {
          state.selected = prev;
        },
      });
    },

    "apply-suggestion": function (el) {
      const id = el.dataset.id;
      const r = DB.records.find((x) => x.id === id);
      state.appliedSuggestions.add(id);
      addLedger({
        cls: "A",
        icon: "sparkles",
        title: "Aplicaste la sugerencia de proboto en " + r.num,
        meta: "Ahora · Tú · sugerencia aceptada",
        diff: "tipificacion: — → " + (DB.triage[id] || {}).title,
      });
      if (DB.triage[id]) r.serie = DB.triage[id].fields[3] ? DB.triage[id].fields[3][1] : r.serie;
      render();
      pushUndo({
        title: "Sugerencia aplicada en " + r.num,
        sub: "proboto · tipificación y reparto",
        onUndo: function () {
          state.appliedSuggestions.delete(id);
        },
      });
    },
    "reject-suggestion": function (el) {
      const id = el.dataset.id;
      const r = DB.records.find((x) => x.id === id);
      state.appliedSuggestions.add(id);
      render();
      pushUndo({
        title: "Sugerencia descartada en " + r.num,
        sub: "proboto no vuelve a proponerla en este radicado",
        onUndo: function () {
          state.appliedSuggestions.delete(id);
        },
      });
    },

    "toggle-cap": function (el) {
      const id = el.dataset.id;
      const cap = DB.capabilities.find((c) => c.id === id);
      if (state.caps.has(id)) {
        const dependents = DB.capabilities.filter(
          (c) => c.deps.includes(id) && state.caps.has(c.id),
        );
        if (dependents.length) {
          showToast(
            "alert",
            "No se puede apagar todavía",
            "Dependen: " +
              dependents
                .slice(0, 3)
                .map((d) => d.name)
                .join(", ") +
              (dependents.length > 3 ? " y " + (dependents.length - 3) + " más" : ""),
          );
          return;
        }
        state.caps.delete(id);
        addLedger({
          cls: "A",
          icon: "layers",
          title: "Apagaste la capacidad «" + cap.name + "»",
          meta: "Ahora · Tú · configuración de la entidad",
          diff: "capacidad: activa → inactiva · sin borrar datos",
        });
        render();
        pushUndo({
          title: "Capacidad apagada",
          sub: cap.name + " · no se borró ningún dato",
          onUndo: function () {
            state.caps.add(id);
          },
        });
      } else {
        state.caps.add(id);
        addLedger({
          cls: "A",
          icon: "layers",
          title: "Encendiste la capacidad «" + cap.name + "»",
          meta: "Ahora · Tú · configuración de la entidad",
          diff: "capacidad: inactiva → activa",
        });
        render();
        pushUndo({
          title: "Capacidad encendida",
          sub: cap.name,
          onUndo: function () {
            state.caps.delete(id);
          },
        });
      }
    },

    "mode-pick": function (el) {
      state.modal = { type: "mode", mode: el.dataset.mode };
      render();
    },
    "mode-apply": function (el) {
      const mode = el.dataset.mode;
      const prevMode = state.mode;
      const prevCaps = new Set(state.caps);
      state.mode = mode;
      state.caps = new Set(DB.capsForMode(mode));
      state.modal = null;
      addLedger({
        cls: "A",
        icon: "layers",
        title: "La entidad pasó al modo " + DB.modes[mode].name,
        meta: "Ahora · Tú · administración",
        diff: "modo: " + DB.modes[prevMode].name + " → " + DB.modes[mode].name,
      });
      render();
      pushUndo({
        title: "Modo " + DB.modes[mode].name + " activado",
        sub: "Aplica a toda la entidad · " + state.caps.size + " capacidades",
        onUndo: function () {
          state.mode = prevMode;
          state.caps = prevCaps;
        },
      });
    },
    "reset-mode": function () {
      const prevCaps = new Set(state.caps);
      state.caps = new Set(DB.capsForMode(state.mode));
      render();
      pushUndo({
        title: "Se restauró el preset del modo " + DB.modes[state.mode].name,
        sub: "Los ajustes propios se descartaron",
        onUndo: function () {
          state.caps = prevCaps;
        },
      });
    },

    "toggle-rule": function (el) {
      const id = el.dataset.id;
      const rule = DB.alertRules.find((r) => r.id === id);
      rule.active = !rule.active;
      render();
      pushUndo({
        title: (rule.active ? "Encendiste" : "Apagaste") + " una regla de alerta",
        sub: rule.when.join(" ") + " → " + rule.then.join(" "),
        onUndo: function () {
          rule.active = !rule.active;
        },
      });
    },
    "toggle-scope": function (el) {
      const id = el.dataset.id;
      const sc = DB.aiScopes.find((x) => x.id === id);
      sc.on = !sc.on;
      render();
      pushUndo({
        title: (sc.on ? "Habilitaste" : "Bloqueaste") + " documentos " + sc.name,
        sub: "Ningún radicado de ese nivel llega al proveedor",
        onUndo: function () {
          sc.on = !sc.on;
        },
      });
    },
    "ai-level": function (el) {
      const id = el.dataset.id;
      const prev = state.aiLevel;
      state.aiLevel = id;
      addLedger({
        cls: "A",
        icon: "sparkles",
        title: "proboto pasó al nivel «" + DB.aiLevels.find((l) => l.id === id).title + "»",
        meta: "Ahora · Tú · capa de inteligencia",
        diff: "nivel: " + prev + " → " + id,
      });
      render();
      pushUndo({
        title: "Nivel de inteligencia cambiado",
        sub: "ahora: " + DB.aiLevels.find((l) => l.id === id).title,
        onUndo: function () {
          state.aiLevel = prev;
        },
      });
    },

    "snooze-alert": function (el) {
      addLedger({
        cls: "A",
        icon: "bell",
        title: "Silenciaste una alerta por 7 días",
        meta: "Ahora · Tú",
        diff: "alerta: activa → silenciada hasta el 10 nov",
      });
      render();
      pushUndo({
        title: "Alerta silenciada 7 días",
        sub: "Volverá sola el 10 de noviembre",
        onUndo: function () {},
      });
    },

    "undo-entry": function (el) {
      const id = el.dataset.id;
      const item = state.ledger.find((l) => l.id === id);
      if (!item) return;
      if (item.cls !== "A") {
        state.modal = { type: "locked" };
        render();
        return;
      }
      item.undone = true;
      item.undoable = false;
      item.meta = item.meta + " · deshecho ahora";
      state.ledger = state.ledger.filter((l) => l.id !== id);
      render();
      showToast("undo", "Acto deshecho", "Se restauró el estado anterior; ambos asientos quedan en el libro.");
    },
    "undo-toast": function (el) {
      performUndo(el.dataset.id);
    },
    "undo-dismiss": function (el) {
      const i = undoEntries.findIndex((e) => e.id === el.dataset.id);
      if (i >= 0) undoEntries.splice(i, 1);
      renderToasts();
    },

    preflight: function (el) {
      state.modal = { type: "preflight", kind: el.dataset.kind, id: el.dataset.id };
      render();
    },
    "preflight-confirm": function (el) {
      const kind = el.dataset.kind;
      state.modal = null;
      if (kind === "tramitar") {
        addLedger({
          cls: "A",
          icon: "check",
          title: "Marcaste E-2026-4821 en trámite",
          meta: "Ahora · Tú",
          diff: "status: registered → in_progress",
        });
        render();
        pushUndo({
          title: "E-2026-4821 marcado en trámite",
          sub: "El plazo no cambió",
          onUndo: function () {},
        });
      } else if (kind === "devolver") {
        addLedger({
          cls: "B",
          icon: "rotate",
          title: "Devolviste E-2026-4821 a la ventanilla única",
          meta: "Ahora · Tú · motivo registrado",
          diff: "paso: Inspección → Ventanilla única · asiento nuevo",
        });
        render();
        showToast("rotate", "Devolución registrada", "Se revierte con un traslado nuevo, no borrando el acto.");
      } else if (kind === "escalar") {
        addLedger({
          cls: "B",
          icon: "flag",
          title: "Escalaste la alerta de E-2026-4819 a la jefatura",
          meta: "Ahora · Tú",
          diff: "prioridad: alta → crítica · jefe notificado",
        });
        render();
        showToast("flag", "Alerta escalada", "La jefatura recibió el aviso. Se revierte con un acto contrario.");
      } else if (kind === "revertir") {
        addLedger({
          cls: "B",
          icon: "rotate",
          title: "Revertiste un acto con constancia",
          meta: "Ahora · Tú · motivo registrado",
          diff: "se creó el acto contrario; el original permanece",
        });
        render();
        showToast("rotate", "Reversión registrada", "No se borró nada: hay un acto contrario y los dos quedan.");
      } else if (kind === "anular") {
        state.modal = { type: "locked" };
        render();
      }
    },
    "modal-close": function () {
      state.modal = null;
      render();
    },

    "wizard-next": function () {
      state.wizardStep = Math.min(3, state.wizardStep + 1);
      render();
    },
    "wizard-back": function () {
      state.wizardStep = Math.max(1, state.wizardStep - 1);
      render();
    },
    "radicar-submit": function () {
      const n = "E-2026-4825";
      if (!DB.records.find((r) => r.num === n)) {
        DB.records.unshift({
          id: "r9",
          num: n,
          subject: "Solicitud de licencia de construcción — predio Calle 12 #4-33",
          type: "Entrada",
          typeCode: "E",
          status: "registered",
          statusLabel: "Radicado",
          statusChip: "info",
          from: "María Fernanda Ospina",
          fromType: "Ciudadano",
          dept: "Planeación",
          current: "Sin repartir — Ventanilla única",
          owner: "Sin asignar",
          received: "Ahora",
          receivedLong: "3 nov 2026, " + new Date().toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit" }),
          due: "24 nov 2026",
          dueTone: "warn",
          unread: true,
          priority: "Media",
          ai: true,
          aiConfidence: 3,
          channel: "Ventanilla única en línea",
          serie: "Solicitudes de licencia urbanística",
          retention: "5 años gestión · 15 años central",
          folios: 23,
          attachments: 4,
        });
      }
      addLedger({
        cls: "C",
        icon: "lock",
        title: "Radicaste " + n,
        meta: "Ahora · Tú · el número queda en el consecutivo",
        diff: "consecutivo: 4824 → 4825 · inmutable",
      });
      state.activeRecord = "r9";
      state.wizardStep = 1;
      state.docTab = "bandeja";
      state.recordTab = "resumen";
      render();
      showToast("check", "Radicado " + n + " emitido", "El acuse se envió. El número no se puede devolver.");
    },

    cite: function (el) {
      state.modal = { type: "cite", n: el.dataset.n };
      render();
    },

    "agent-open": function (el) {
      state.agentOpen = true;
      if (el.dataset.agentTab) state.agentTab = el.dataset.agentTab;
      render();
    },
    "agent-close": function () {
      state.agentOpen = false;
      render();
    },
    "agent-toggle": function () {
      state.agentOpen = !state.agentOpen;
      render();
    },
    "agent-tab": function (el) {
      state.agentTab = el.dataset.tab;
      render();
    },
    "rail-toggle": function () {
      if (isMobile()) state.railOpenMobile = !state.railOpenMobile;
      else state.railCollapsed = !state.railCollapsed;
      render();
    },

    "palette-open": function () {
      state.paletteOpen = true;
      render();
    },
    "palette-close": function () {
      state.paletteOpen = false;
      render();
    },
    "palette-mode": function (el) {
      state.paletteMode = el.dataset.id;
      render();
    },
    "palette-action": function (el) {
      state.paletteOpen = false;
      const msg = el.dataset.msg;
      if (msg === "Radicar un documento") {
        state.route = "documentos";
        state.docTab = "radicar";
        state.wizardStep = 1;
      } else if (msg === "Preparar una remesa") {
        state.route = "distribucion";
        state.distTab = "envios";
      } else if (msg === "Crear una regla de alerta") {
        state.route = "alertas";
        state.alertTab = "reglas";
      } else if (msg === "Asignar radicados a una dependencia") {
        state.route = "documentos";
        state.docTab = "bandeja";
        state.selected = new Set(["r2", "r8"]);
      }
      render();
      if (msg) showToast("check", msg, "Flujo abierto en el lugar correcto.");
    },

    "theme-toggle": function () {
      setTheme(state.theme === "light" ? "dark" : "light");
      render();
    },
    "density-toggle": function () {
      state.density = state.density === "normal" ? "compact" : "normal";
      document.documentElement.setAttribute("data-density", state.density);
      showToast("sliders", "Densidad " + (state.density === "compact" ? "compacta" : "cómoda"), "Preferencia guardada en este navegador.");
    },
    toast: function (el) {
      showToast("info", el.dataset.msg || "Acción", "");
    },
  };

  /* ==========================================================================
     EVENTOS
     ========================================================================== */
  document.addEventListener("click", function (e) {
    const el = e.target.closest("[data-act]");
    if (!el) return;
    const act = el.dataset.act;
    if (act === "noop") {
      e.stopPropagation();
      return;
    }
    const fn = actions[act];
    if (fn) {
      e.preventDefault();
      fn(el, e);
    }
  });

  document.addEventListener("keydown", function (e) {
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.key.toLowerCase() === "k") {
      e.preventDefault();
      state.paletteOpen = !state.paletteOpen;
      render();
      return;
    }
    if (mod && e.key.toLowerCase() === "z" && undoEntries.length) {
      e.preventDefault();
      performUndo(undoEntries[undoEntries.length - 1].id);
      return;
    }
    if (e.key === "Escape") {
      if (state.modal) {
        state.modal = null;
        render();
      } else if (state.paletteOpen) {
        state.paletteOpen = false;
        render();
      }
    }
  });

  /* ==========================================================================
     ARRANQUE
     ========================================================================== */
  const initial = (location.hash || "").replace("#/", "") || "escritorio";
  if (initial) state.route = initial;
  if (isMobile()) state.agentOpen = false;
  render();
  renderToasts();

  // Enlaces profundos: #/capacidades abre esa pantalla y el botón «atrás» funciona.
  window.addEventListener("hashchange", function () {
    const route = (location.hash || "").replace("#/", "") || "escritorio";
    if (route !== state.route) {
      state.route = route;
      render();
    }
  });
})();
