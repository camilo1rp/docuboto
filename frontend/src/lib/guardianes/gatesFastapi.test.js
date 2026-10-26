// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Pruebas del lector estático de gates con fragmentos Python INLINE (no leen disco).
 * Cada caso fija una forma de código que el guardián real `admin-gate-contrato.test.js`
 * necesita entender; si el parser deja de entenderla, la lectura real se queda ciega
 * y el guardián diría «0 desajustes» por no ver nada.
 */
import { describe, it, expect } from "vitest";
import { extraerRutasFastapi, buscarRuta, limpiarPython } from "./gatesFastapi.js";
import { extraerEndpointsFront } from "./frontAdminEndpoints.js";
import { evaluarContrato } from "./contratoAdmin.js";

const una = (src, metodo, ruta) =>
  extraerRutasFastapi(src, "t.py").find((r) => r.metodo === metodo && r.ruta === ruta);
const perms = (r) => r.gates.map((g) => `${g.permiso}:${g.minCrud}`);

describe("extraerRutasFastapi", () => {
  it("gate en el decorador con min_crud", () => {
    const r = una(
      `router = APIRouter(prefix="/api/v1/x")
@router.post("/{id}/y", dependencies=[Depends(require_permission("P", min_crud=3))])
async def f(): ...
`,
      "POST",
      "/api/v1/x/{}/y",
    );
    expect(perms(r)).toEqual(["P:3"]);
  });

  it("gate en la firma, con retorno `-> T:` y parámetros multilínea", () => {
    const r = una(
      `router = APIRouter(prefix="/a")
@router.get("")
async def f(
    q: str = Query(default="(x)"),
    _c: Caller = Depends(require_permission("Q")),
) -> list[Out]:
    return []
`,
      "GET",
      "/a",
    );
    expect(perms(r)).toEqual(["Q:1"]);
  });

  it("dos rutas seguidas NO mezclan sus gates", () => {
    const rs = extraerRutasFastapi(
      `router = APIRouter(prefix="/a")
@router.get("/uno", dependencies=[Depends(require_permission("A"))])
async def uno(
    x: int,
) -> int:
    return 1
@router.get("/dos")
async def dos() -> int:
    return 2
`,
      "t.py",
    );
    expect(perms(rs.find((r) => r.ruta === "/a/uno"))).toEqual(["A:1"]);
    expect(perms(rs.find((r) => r.ruta === "/a/dos"))).toEqual([]);
  });

  it("gate a nivel de router (APIRouter multilínea) se hereda", () => {
    const r = una(
      `router = APIRouter(
    prefix="/api/v1/audit",
    tags=["audit"],
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
@router.get("/verify")
async def v(): ...
`,
      "GET",
      "/api/v1/audit/verify",
    );
    expect(perms(r)).toEqual(["USUA_PERM_ADMIN:1"]);
    expect(r.gates[0].origen).toMatch(/^router/);
  });

  it("dos routers en un archivo: cada uno con su prefijo y su gate", () => {
    const rs = extraerRutasFastapi(
      `main = APIRouter(prefix="/m", dependencies=[Depends(require_permission("ADM"))])
hermano = APIRouter(prefix="/m", dependencies=[Depends(require_permission("RADI"))])
@hermano.get("/pick")
async def a(): ...
@main.get("/{id}")
async def b(): ...
`,
      "t.py",
    );
    expect(perms(rs.find((r) => r.ruta === "/m/pick"))).toEqual(["RADI:1"]);
    expect(perms(rs.find((r) => r.ruta === "/m/{}"))).toEqual(["ADM:1"]);
  });

  it("gate any-of (require_any_permission) en decorador y en router: lleva todos sus brazos", () => {
    const rs = extraerRutasFastapi(
      `pick = APIRouter(prefix="/p", dependencies=[Depends(require_any_permission("PERM_RADI", "USUA_PERM_ADMIN"))])
router = APIRouter(prefix="/q")
@pick.get("/u")
async def a(): ...
@router.get("/v", dependencies=[Depends(require_any_permission("X", 'Y', min_crud=2))])
async def b(): ...
@router.get("/w", dependencies=[Depends(require_permission("Z"))])
async def c(): ...
`,
      "t.py",
    );
    const g = (ruta) => rs.find((r) => r.ruta === ruta).gates;
    expect(g("/p/u").map((x) => x.anyOf)).toEqual([["PERM_RADI", "USUA_PERM_ADMIN"]]);
    expect(g("/p/u")[0].origen).toMatch(/^router/);
    expect(g("/q/v").map((x) => [x.anyOf, x.minCrud])).toEqual([[["X", "Y"], 2]]);
    // El gate ordinario sigue siendo un any-of de uno.
    expect(g("/q/w").map((x) => x.anyOf)).toEqual([["Z"]]);
  });

  it("min_crud con constante de módulo se resuelve; sin resolver queda null", () => {
    const rs = extraerRutasFastapi(
      `_NIVEL = 3
router = APIRouter(prefix="/w")
@router.post("/a", dependencies=[Depends(require_permission("P", min_crud=_NIVEL))])
async def a(): ...
@router.post("/b", dependencies=[Depends(require_permission("P", min_crud=OTRA))])
async def b(): ...
`,
      "t.py",
    );
    expect(perms(rs.find((r) => r.ruta === "/w/a"))).toEqual(["P:3"]);
    expect(perms(rs.find((r) => r.ruta === "/w/b"))).toEqual(["P:null"]);
  });

  it("check_permission en el cuerpo marca la ruta como dinámica; en un comentario o docstring NO", () => {
    const rs = extraerRutasFastapi(
      `router = APIRouter(prefix="/d")
@router.get("/vivo")
async def a(conn):
    if await check_permission(conn, u, "ADM"):
        pass
@router.get("/muerto")
async def b(conn):
    """Antes usaba check_permission(conn, u, "ADM")."""
    # check_permission(conn)
    return 1
`,
      "t.py",
    );
    expect(rs.find((r) => r.ruta === "/d/vivo").dinamico).toBe(true);
    expect(rs.find((r) => r.ruta === "/d/muerto").dinamico).toBe(false);
  });

  it("require_permission sin nombre literal queda con permiso null", () => {
    const r = una(
      `router = APIRouter(prefix="/n")
@router.get("", dependencies=[Depends(require_permission(name, min_crud=2))])
async def f(): ...
`,
      "GET",
      "/n",
    );
    expect(r.gates[0].permiso).toBeNull();
  });

  it("un gate mencionado en un comentario del decorador no cuenta", () => {
    const r = una(
      `router = APIRouter(prefix="/c")
@router.get(
    "",
    # dependencies=[Depends(require_permission("FANTASMA"))],
)
async def f(): ...
`,
      "GET",
      "/c",
    );
    expect(r.gates).toEqual([]);
  });

  it("require_internal_token se reporta como especial, no como permiso", () => {
    const r = una(
      `router = APIRouter(prefix="/i")
@router.post("/x", dependencies=[Depends(require_internal_token)])
async def f(): ...
`,
      "POST",
      "/i/x",
    );
    expect(r.gates).toEqual([]);
    expect(r.especiales).toEqual(["require_internal_token"]);
  });

  it("limpiarPython conserva longitud y saltos de línea", () => {
    const s = `a = "x#y"  # c\n"""doc\nmás"""\n`;
    const l = limpiarPython(s);
    expect(l.length).toBe(s.length);
    expect(l.split("\n").length).toBe(s.split("\n").length);
    expect(l).toContain('"x#y"');
    expect(l).not.toContain("más");
  });
});

describe("buscarRuta", () => {
  const rutas = extraerRutasFastapi(
    `router = APIRouter(prefix="/api/v1/t")
@router.get("/duplicados")
async def d(): ...
@router.get("/{tid}")
async def g(): ...
`,
    "t.py",
  );
  it("prefiere el literal al parámetro", () => {
    expect(buscarRuta(rutas, "GET", "/t/duplicados").ruta).toBe("/api/v1/t/duplicados");
  });
  it("un parámetro del front solo casa con parámetro", () => {
    expect(buscarRuta(rutas, "GET", "/t/{}").ruta).toBe("/api/v1/t/{}");
  });
  it("método distinto o ruta ausente → null", () => {
    expect(buscarRuta(rutas, "POST", "/t/duplicados")).toBeNull();
    expect(buscarRuta(rutas, "GET", "/nada")).toBeNull();
  });
});

describe("extraerEndpointsFront", () => {
  it("template con parámetros, query y método de la misma llamada", () => {
    const e = extraerEndpointsFront(
      'const a = await fetch(`${API_V1}/cat/${encodeURIComponent(p.c)}/${p.id}?x=1`, {\n method: "PATCH",\n});\n' +
        "const b = await fetch(`${API_V1}/otra`, { headers });\n",
      "f.js",
    );
    expect(e.map((x) => `${x.metodo} ${x.ruta}`)).toEqual(["PATCH /cat/{}/{}", "GET /otra"]);
  });

  it("`${qs}` pegado al segmento es query, no parámetro", () => {
    const e = extraerEndpointsFront('fetch(`${API_V1}/pinar/planes${qs ? `?${qs}` : ""}`)', "f.js");
    expect(e.map((x) => x.ruta)).toEqual(["/pinar/planes"]);
  });

  it("servicePath se expande con SERVICE_PATHS del mismo archivo", () => {
    const e = extraerEndpointsFront(
      'const SERVICE_PATHS = { a: "a/dl", b: "b/dl" };\nfetch(`${API_V1}/${servicePath}/${id}/replay`, { method: "POST" });',
      "f.js",
    );
    expect(e.map((x) => `${x.metodo} ${x.ruta}`)).toEqual([
      "POST /a/dl/{}/replay",
      "POST /b/dl/{}/replay",
    ]);
  });

  it("ruta calculada: toma los literales de ruta, no los del BFF", () => {
    const e = extraerEndpointsFront(
      'const r = await fetch(`${API_V1}${ruta}`);\nconst a = "/terceros/duplicados?limit=5";\ngoto("/admin/x");\nconst l = "/login";',
      "f.js",
    );
    expect(e.map((x) => x.ruta)).toEqual(["/terceros/duplicados"]);
  });

  it("método en un objeto `init` declarado antes de la llamada", () => {
    const e = extraerEndpointsFront(
      'const init = { method: "POST", headers };\nawait fetch(`${API_V1}/import`, init);',
      "f.js",
    );
    expect(e[0].metodo).toBe("POST");
  });
});

describe("evaluarContrato (reglas sobre datos sintéticos)", () => {
  const rutas = extraerRutasFastapi(
    `router = APIRouter(prefix="/api/v1/a")
@router.get("", dependencies=[Depends(require_permission("P"))])
async def l(): ...
@router.post("", dependencies=[Depends(require_permission("P", min_crud=3))])
async def c(): ...
@router.get("/abierta")
async def o(): ...
`,
    "t.py",
  );
  const base = {
    mapa: { a: ["P"] },
    defecto: "ADM",
    carpetas: ["a"],
    tarjetas: [{ area: "a", permiso: "P" }],
    rutas,
    endpoints: [
      { area: "a", tipo: "entrada", metodo: "GET", ruta: "/a", archivo: "f", linea: 1 },
      { area: "a", tipo: "secundario", metodo: "POST", ruta: "/a", archivo: "f", linea: 2 },
    ],
    excepciones: {},
  };
  it("contrato consistente → sin hallazgos", () => {
    expect(evaluarContrato(base)).toEqual([]);
  });
  it("R2: el mapa admite otro permiso que el que gatea la entrada", () => {
    const h = evaluarContrato({ ...base, mapa: { a: ["Z"] } });
    expect(h.map((x) => x.regla)).toContain("R2");
    expect(h.map((x) => x.regla)).toContain("R3");
  });
  it("R5: ruta ausente y ruta sin gate fallan, y una excepción CON motivo las apaga", () => {
    const extra = [
      { area: "a", tipo: "secundario", metodo: "GET", ruta: "/a/abierta", archivo: "f", linea: 3 },
      { area: "a", tipo: "secundario", metodo: "GET", ruta: "/fantasma", archivo: "f", linea: 4 },
    ];
    const d = { ...base, endpoints: [...base.endpoints, ...extra] };
    expect(evaluarContrato(d).filter((x) => x.regla === "R5")).toHaveLength(2);
    const ok = evaluarContrato({
      ...d,
      excepciones: { "a GET /a/abierta": "motivo", "* GET /fantasma": "motivo" },
    });
    expect(ok).toEqual([]);
    const vacio = evaluarContrato({ ...d, excepciones: { "a GET /a/abierta": "  " } });
    expect(vacio.some((x) => x.detalle === "excepción sin motivo")).toBe(true);
  });
  it("any-of: el área lo satisface con UNO de sus brazos; sin ninguno es R2/R4", () => {
    const anyRutas = extraerRutasFastapi(
      `router = APIRouter(prefix="/api/v1/a")
@router.get("", dependencies=[Depends(require_any_permission("PERM_RADI", "P"))])
async def l(): ...
@router.post("", dependencies=[Depends(require_any_permission("PERM_RADI", "P"))])
async def c(): ...
`,
      "t.py",
    );
    const d = { ...base, rutas: anyRutas };
    expect(evaluarContrato(d)).toEqual([]);
    // El área admite solo «Q»: ningún brazo del gate la satisface.
    const h = evaluarContrato({
      ...d,
      mapa: { a: ["Q"] },
      tarjetas: [{ area: "a", permiso: "Q" }],
    });
    expect(h.map((x) => x.regla)).toEqual(expect.arrayContaining(["R2", "R4"]));
  });

  it("R3: un permiso del mapa que no gatea nada", () => {
    const h = evaluarContrato({ ...base, mapa: { a: ["P", "Q"] }, endpoints: [...base.endpoints] });
    expect(h.some((x) => x.regla === "R3" && x.clave === "a Q")).toBe(true);
  });
});
