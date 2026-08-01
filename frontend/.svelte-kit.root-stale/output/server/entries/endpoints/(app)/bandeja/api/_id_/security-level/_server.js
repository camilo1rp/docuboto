import { j as json } from "../../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../../chunks/config.server.js";
async function POST({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }
  const { nivel_seguridad, motivo, causal_reserva, fundamento_juridico, plazo_reserva_meses } = body ?? {};
  const nivelInt = Number(nivel_seguridad);
  if (!nivel_seguridad || ![1, 2, 3].includes(nivelInt)) {
    return json(
      {
        error: "validation_error",
        detail: "El nivel de seguridad debe ser 1 (Pública), 2 (Reservada) o 3 (Clasificada)."
      },
      { status: 422 }
    );
  }
  if (!motivo || typeof motivo !== "string" || !motivo.trim()) {
    return json(
      {
        error: "validation_error",
        detail: "El motivo de la reclasificación es obligatorio."
      },
      { status: 422 }
    );
  }
  if (motivo.trim().length > 500) {
    return json(
      {
        error: "validation_error",
        detail: "El motivo no puede superar los 500 caracteres."
      },
      { status: 422 }
    );
  }
  const payload = {
    nivel_seguridad: nivelInt,
    motivo: motivo.trim()
  };
  if (typeof causal_reserva === "string" && causal_reserva.trim()) {
    payload.causal_reserva = causal_reserva.trim().slice(0, 200);
  }
  if (typeof fundamento_juridico === "string" && fundamento_juridico.trim()) {
    payload.fundamento_juridico = fundamento_juridico.trim().slice(0, 300);
  }
  if (plazo_reserva_meses !== void 0 && plazo_reserva_meses !== null && plazo_reserva_meses !== "") {
    const plazoInt = parseInt(String(plazo_reserva_meses), 10);
    if (isNaN(plazoInt) || plazoInt < 0) {
      return json(
        {
          error: "validation_error",
          detail: "El plazo de reserva debe ser un número entero mayor o igual a 0."
        },
        { status: 422 }
      );
    }
    if (plazoInt > 600) {
      return json(
        {
          error: "validation_error",
          detail: "El plazo de reserva no puede superar los 600 meses."
        },
        { status: 422 }
      );
    }
    payload.plazo_reserva_meses = plazoInt;
  }
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  if (userId) headers["X-User-Id"] = userId;
  try {
    const res = await fetch(`${API_V1}/documents/${encodeURIComponent(params.id)}/security-level`, {
      method: "PATCH",
      headers,
      body: JSON.stringify(payload)
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 404) {
      return json(
        {
          error: "not_found",
          detail: "No fue posible reclasificar el radicado o no tiene acceso a él."
        },
        { status: 404 }
      );
    }
    if (res.status === 403) {
      return json(
        {
          error: "clearance_insuficiente",
          detail: data.detail ?? "No tiene el nivel de acceso necesario para asignar ese nivel de seguridad."
        },
        { status: 403 }
      );
    }
    return json(data, { status: res.status });
  } catch {
    return json(
      {
        error: "connection_error",
        detail: "No se pudo conectar al servidor de documentos."
      },
      { status: 503 }
    );
  }
}
export {
  POST
};
