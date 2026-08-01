export const manifest = (() => {
function __memo(fn) {
	let value;
	return () => value ??= (value = fn());
}

return {
	appDir: "_app",
	appPath: "_app",
	assets: new Set([".gitkeep","apple-touch-icon.png","favicon-16x16.png","favicon-32x32.png","favicon.ico","orpyca-logo.png"]),
	mimeTypes: {".png":"image/png"},
	_: {
		client: {start:"_app/immutable/entry/start.CD6Jtsj3.js",app:"_app/immutable/entry/app.BK4LPkG4.js",imports:["_app/immutable/entry/start.CD6Jtsj3.js","_app/immutable/chunks/Cy0ZpuEm.js","_app/immutable/chunks/CtVBvrjE.js","_app/immutable/chunks/CrG8ZwFW.js","_app/immutable/entry/app.BK4LPkG4.js","_app/immutable/chunks/B6NFP72y.js","_app/immutable/chunks/CtVBvrjE.js","_app/immutable/chunks/D7g4fVw7.js"],stylesheets:[],fonts:[],uses_env_dynamic_public:true},
		nodes: [
			__memo(() => import('./nodes/0.js')),
			__memo(() => import('./nodes/1.js')),
			__memo(() => import('./nodes/2.js')),
			__memo(() => import('./nodes/3.js')),
			__memo(() => import('./nodes/4.js')),
			__memo(() => import('./nodes/5.js')),
			__memo(() => import('./nodes/6.js')),
			__memo(() => import('./nodes/7.js')),
			__memo(() => import('./nodes/8.js')),
			__memo(() => import('./nodes/9.js')),
			__memo(() => import('./nodes/10.js')),
			__memo(() => import('./nodes/11.js')),
			__memo(() => import('./nodes/12.js')),
			__memo(() => import('./nodes/13.js')),
			__memo(() => import('./nodes/14.js')),
			__memo(() => import('./nodes/15.js')),
			__memo(() => import('./nodes/16.js')),
			__memo(() => import('./nodes/17.js')),
			__memo(() => import('./nodes/18.js')),
			__memo(() => import('./nodes/19.js')),
			__memo(() => import('./nodes/20.js')),
			__memo(() => import('./nodes/21.js'))
		],
		routes: [
			{
				id: "/(public)",
				pattern: /^\/?$/,
				params: [],
				page: { layouts: [0,], errors: [1,], leaf: 17 },
				endpoint: null
			},
			{
				id: "/403",
				pattern: /^\/403\/?$/,
				params: [],
				page: { layouts: [0,], errors: [1,], leaf: 21 },
				endpoint: null
			},
			{
				id: "/(app)/_kit",
				pattern: /^\/_kit\/?$/,
				params: [],
				page: { layouts: [0,2,], errors: [1,,], leaf: 3 },
				endpoint: null
			},
			{
				id: "/(app)/admin/plantillas",
				pattern: /^\/admin\/plantillas\/?$/,
				params: [],
				page: { layouts: [0,2,], errors: [1,,], leaf: 4 },
				endpoint: null
			},
			{
				id: "/(app)/admin/plantillas/api",
				pattern: /^\/admin\/plantillas\/api\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/admin/plantillas/api/_server.js'))
			},
			{
				id: "/(app)/admin/plantillas/api/[id]",
				pattern: /^\/admin\/plantillas\/api\/([^/]+?)\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/admin/plantillas/api/_id_/_server.js'))
			},
			{
				id: "/(app)/archivo-fisico",
				pattern: /^\/archivo-fisico\/?$/,
				params: [],
				page: { layouts: [0,2,], errors: [1,,], leaf: 5 },
				endpoint: null
			},
			{
				id: "/(app)/archivo-fisico/api/expedientes/[id]/unidades",
				pattern: /^\/archivo-fisico\/api\/expedientes\/([^/]+?)\/unidades\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/archivo-fisico/api/expedientes/_id_/unidades/_server.js'))
			},
			{
				id: "/(app)/archivo-fisico/api/fuid.xml",
				pattern: /^\/archivo-fisico\/api\/fuid\.xml\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/archivo-fisico/api/fuid.xml/_server.js'))
			},
			{
				id: "/(app)/archivo-fisico/api/fuid",
				pattern: /^\/archivo-fisico\/api\/fuid\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/archivo-fisico/api/fuid/_server.js'))
			},
			{
				id: "/(app)/archivo-fisico/api/prestamos",
				pattern: /^\/archivo-fisico\/api\/prestamos\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/archivo-fisico/api/prestamos/_server.js'))
			},
			{
				id: "/(app)/archivo-fisico/api/prestamos/[id]/devolver",
				pattern: /^\/archivo-fisico\/api\/prestamos\/([^/]+?)\/devolver\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/archivo-fisico/api/prestamos/_id_/devolver/_server.js'))
			},
			{
				id: "/(app)/archivo-fisico/api/ubicaciones",
				pattern: /^\/archivo-fisico\/api\/ubicaciones\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/archivo-fisico/api/ubicaciones/_server.js'))
			},
			{
				id: "/(app)/archivo-fisico/api/ubicaciones/[id]",
				pattern: /^\/archivo-fisico\/api\/ubicaciones\/([^/]+?)\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/archivo-fisico/api/ubicaciones/_id_/_server.js'))
			},
			{
				id: "/(app)/archivo-fisico/api/unidades",
				pattern: /^\/archivo-fisico\/api\/unidades\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/archivo-fisico/api/unidades/_server.js'))
			},
			{
				id: "/(app)/archivo-fisico/api/unidades/[id]/expedientes",
				pattern: /^\/archivo-fisico\/api\/unidades\/([^/]+?)\/expedientes\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/archivo-fisico/api/unidades/_id_/expedientes/_server.js'))
			},
			{
				id: "/(app)/archivo-fisico/api/unidades/[id]/movimientos",
				pattern: /^\/archivo-fisico\/api\/unidades\/([^/]+?)\/movimientos\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/archivo-fisico/api/unidades/_id_/movimientos/_server.js'))
			},
			{
				id: "/(app)/archivo-fisico/api/unidades/[id]/prestamos",
				pattern: /^\/archivo-fisico\/api\/unidades\/([^/]+?)\/prestamos\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/archivo-fisico/api/unidades/_id_/prestamos/_server.js'))
			},
			{
				id: "/(app)/asistente/api/message",
				pattern: /^\/asistente\/api\/message\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/asistente/api/message/_server.js'))
			},
			{
				id: "/(app)/bandeja",
				pattern: /^\/bandeja\/?$/,
				params: [],
				page: { layouts: [0,2,], errors: [1,,], leaf: 6 },
				endpoint: null
			},
			{
				id: "/(app)/bandeja/api",
				pattern: /^\/bandeja\/api\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/bandeja/api/_server.js'))
			},
			{
				id: "/(app)/bandeja/api/users",
				pattern: /^\/bandeja\/api\/users\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/bandeja/api/users/_server.js'))
			},
			{
				id: "/(app)/bandeja/api/[id]",
				pattern: /^\/bandeja\/api\/([^/]+?)\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/bandeja/api/_id_/_server.js'))
			},
			{
				id: "/(app)/bandeja/api/[id]/anulacion",
				pattern: /^\/bandeja\/api\/([^/]+?)\/anulacion\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/bandeja/api/_id_/anulacion/_server.js'))
			},
			{
				id: "/(app)/bandeja/api/[id]/devolver",
				pattern: /^\/bandeja\/api\/([^/]+?)\/devolver\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/bandeja/api/_id_/devolver/_server.js'))
			},
			{
				id: "/(app)/bandeja/api/[id]/respuesta",
				pattern: /^\/bandeja\/api\/([^/]+?)\/respuesta\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/bandeja/api/_id_/respuesta/_server.js'))
			},
			{
				id: "/(app)/bandeja/api/[id]/security-level",
				pattern: /^\/bandeja\/api\/([^/]+?)\/security-level\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/bandeja/api/_id_/security-level/_server.js'))
			},
			{
				id: "/(app)/bandeja/api/[id]/vobo-cadena",
				pattern: /^\/bandeja\/api\/([^/]+?)\/vobo-cadena\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/bandeja/api/_id_/vobo-cadena/_server.js'))
			},
			{
				id: "/(app)/bandeja/api/[id]/vobo",
				pattern: /^\/bandeja\/api\/([^/]+?)\/vobo\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/bandeja/api/_id_/vobo/_server.js'))
			},
			{
				id: "/(app)/borradores",
				pattern: /^\/borradores\/?$/,
				params: [],
				page: { layouts: [0,2,], errors: [1,,], leaf: 7 },
				endpoint: null
			},
			{
				id: "/(app)/borradores/api",
				pattern: /^\/borradores\/api\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/borradores/api/_server.js'))
			},
			{
				id: "/(app)/borradores/api/[id]",
				pattern: /^\/borradores\/api\/([^/]+?)\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/borradores/api/_id_/_server.js'))
			},
			{
				id: "/(app)/borradores/api/[id]/aprobar",
				pattern: /^\/borradores\/api\/([^/]+?)\/aprobar\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/borradores/api/_id_/aprobar/_server.js'))
			},
			{
				id: "/(app)/borradores/api/[id]/radicar",
				pattern: /^\/borradores\/api\/([^/]+?)\/radicar\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/borradores/api/_id_/radicar/_server.js'))
			},
			{
				id: "/(app)/busqueda",
				pattern: /^\/busqueda\/?$/,
				params: [],
				page: { layouts: [0,2,], errors: [1,,], leaf: 8 },
				endpoint: null
			},
			{
				id: "/(app)/busqueda/api",
				pattern: /^\/busqueda\/api\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/busqueda/api/_server.js'))
			},
			{
				id: "/(app)/dashboard",
				pattern: /^\/dashboard\/?$/,
				params: [],
				page: { layouts: [0,2,], errors: [1,,], leaf: 9 },
				endpoint: null
			},
			{
				id: "/(app)/envios",
				pattern: /^\/envios\/?$/,
				params: [],
				page: { layouts: [0,2,], errors: [1,,], leaf: 10 },
				endpoint: null
			},
			{
				id: "/(app)/envios/api",
				pattern: /^\/envios\/api\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/envios/api/_server.js'))
			},
			{
				id: "/(app)/envios/api/crear",
				pattern: /^\/envios\/api\/crear\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/envios/api/crear/_server.js'))
			},
			{
				id: "/(app)/envios/api/[id]",
				pattern: /^\/envios\/api\/([^/]+?)\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/envios/api/_id_/_server.js'))
			},
			{
				id: "/(app)/envios/api/[id]/estado",
				pattern: /^\/envios\/api\/([^/]+?)\/estado\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/envios/api/_id_/estado/_server.js'))
			},
			{
				id: "/(app)/expedientes",
				pattern: /^\/expedientes\/?$/,
				params: [],
				page: { layouts: [0,2,], errors: [1,,], leaf: 11 },
				endpoint: null
			},
			{
				id: "/(app)/expedientes/api/metadata-templates",
				pattern: /^\/expedientes\/api\/metadata-templates\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/expedientes/api/metadata-templates/_server.js'))
			},
			{
				id: "/(app)/expedientes/[id]",
				pattern: /^\/expedientes\/([^/]+?)\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: { layouts: [0,2,], errors: [1,,], leaf: 12 },
				endpoint: null
			},
			{
				id: "/(app)/expedientes/[id]/api/anexos/[documentId]",
				pattern: /^\/expedientes\/([^/]+?)\/api\/anexos\/([^/]+?)\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false},{"name":"documentId","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/expedientes/_id_/api/anexos/_documentId_/_server.js'))
			},
			{
				id: "/(app)/expedientes/[id]/api/close",
				pattern: /^\/expedientes\/([^/]+?)\/api\/close\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/expedientes/_id_/api/close/_server.js'))
			},
			{
				id: "/(app)/expedientes/[id]/api/download/[documentId]/[fileId]",
				pattern: /^\/expedientes\/([^/]+?)\/api\/download\/([^/]+?)\/([^/]+?)\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false},{"name":"documentId","optional":false,"rest":false,"chained":false},{"name":"fileId","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/expedientes/_id_/api/download/_documentId_/_fileId_/_server.js'))
			},
			{
				id: "/(app)/expedientes/[id]/api/indice-xml",
				pattern: /^\/expedientes\/([^/]+?)\/api\/indice-xml\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/expedientes/_id_/api/indice-xml/_server.js'))
			},
			{
				id: "/(app)/expedientes/[id]/api/transfer",
				pattern: /^\/expedientes\/([^/]+?)\/api\/transfer\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/expedientes/_id_/api/transfer/_server.js'))
			},
			{
				id: "/(app)/firmas",
				pattern: /^\/firmas\/?$/,
				params: [],
				page: { layouts: [0,2,], errors: [1,,], leaf: 13 },
				endpoint: null
			},
			{
				id: "/(app)/firmas/api",
				pattern: /^\/firmas\/api\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/firmas/api/_server.js'))
			},
			{
				id: "/(app)/firmas/api/batch",
				pattern: /^\/firmas\/api\/batch\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/firmas/api/batch/_server.js'))
			},
			{
				id: "/(app)/firmas/api/[id]",
				pattern: /^\/firmas\/api\/([^/]+?)\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/firmas/api/_id_/_server.js'))
			},
			{
				id: "/(app)/firmas/api/[id]/reject",
				pattern: /^\/firmas\/api\/([^/]+?)\/reject\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/firmas/api/_id_/reject/_server.js'))
			},
			{
				id: "/(app)/firmas/api/[id]/sign",
				pattern: /^\/firmas\/api\/([^/]+?)\/sign\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/firmas/api/_id_/sign/_server.js'))
			},
			{
				id: "/(public)/login",
				pattern: /^\/login\/?$/,
				params: [],
				page: { layouts: [0,], errors: [1,], leaf: 18 },
				endpoint: null
			},
			{
				id: "/(public)/login/callback",
				pattern: /^\/login\/callback\/?$/,
				params: [],
				page: { layouts: [0,], errors: [1,], leaf: 19 },
				endpoint: null
			},
			{
				id: "/(public)/logout",
				pattern: /^\/logout\/?$/,
				params: [],
				page: { layouts: [0,], errors: [1,], leaf: 20 },
				endpoint: null
			},
			{
				id: "/(app)/radicar",
				pattern: /^\/radicar\/?$/,
				params: [],
				page: { layouts: [0,2,], errors: [1,,], leaf: 14 },
				endpoint: null
			},
			{
				id: "/(app)/radicar/api/documents",
				pattern: /^\/radicar\/api\/documents\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/radicar/api/documents/_server.js'))
			},
			{
				id: "/(app)/radicar/api/storage",
				pattern: /^\/radicar\/api\/storage\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/radicar/api/storage/_server.js'))
			},
			{
				id: "/(app)/radicar/api/templates",
				pattern: /^\/radicar\/api\/templates\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/radicar/api/templates/_server.js'))
			},
			{
				id: "/(app)/reportes",
				pattern: /^\/reportes\/?$/,
				params: [],
				page: { layouts: [0,2,], errors: [1,,], leaf: 15 },
				endpoint: null
			},
			{
				id: "/(app)/reportes/api/csv",
				pattern: /^\/reportes\/api\/csv\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/reportes/api/csv/_server.js'))
			},
			{
				id: "/(app)/transferencias",
				pattern: /^\/transferencias\/?$/,
				params: [],
				page: { layouts: [0,2,], errors: [1,,], leaf: 16 },
				endpoint: null
			},
			{
				id: "/(app)/transferencias/api",
				pattern: /^\/transferencias\/api\/?$/,
				params: [],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/transferencias/api/_server.js'))
			},
			{
				id: "/(app)/transferencias/api/[id]/enviar",
				pattern: /^\/transferencias\/api\/([^/]+?)\/enviar\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/transferencias/api/_id_/enviar/_server.js'))
			},
			{
				id: "/(app)/transferencias/api/[id]/fuid",
				pattern: /^\/transferencias\/api\/([^/]+?)\/fuid\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/transferencias/api/_id_/fuid/_server.js'))
			},
			{
				id: "/(app)/transferencias/api/[id]/rechazar",
				pattern: /^\/transferencias\/api\/([^/]+?)\/rechazar\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/transferencias/api/_id_/rechazar/_server.js'))
			},
			{
				id: "/(app)/transferencias/api/[id]/recibir",
				pattern: /^\/transferencias\/api\/([^/]+?)\/recibir\/?$/,
				params: [{"name":"id","optional":false,"rest":false,"chained":false}],
				page: null,
				endpoint: __memo(() => import('./entries/endpoints/(app)/transferencias/api/_id_/recibir/_server.js'))
			}
		],
		prerendered_routes: new Set([]),
		matchers: async () => {
			
			return {  };
		},
		server_assets: {}
	}
}
})();
