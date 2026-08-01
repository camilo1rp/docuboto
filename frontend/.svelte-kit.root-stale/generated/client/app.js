export { matchers } from './matchers.js';

export const nodes = [
	() => import('./nodes/0'),
	() => import('./nodes/1'),
	() => import('./nodes/2'),
	() => import('./nodes/3'),
	() => import('./nodes/4'),
	() => import('./nodes/5'),
	() => import('./nodes/6'),
	() => import('./nodes/7'),
	() => import('./nodes/8'),
	() => import('./nodes/9'),
	() => import('./nodes/10'),
	() => import('./nodes/11'),
	() => import('./nodes/12'),
	() => import('./nodes/13'),
	() => import('./nodes/14'),
	() => import('./nodes/15'),
	() => import('./nodes/16'),
	() => import('./nodes/17'),
	() => import('./nodes/18'),
	() => import('./nodes/19'),
	() => import('./nodes/20'),
	() => import('./nodes/21'),
	() => import('./nodes/22'),
	() => import('./nodes/23'),
	() => import('./nodes/24'),
	() => import('./nodes/25'),
	() => import('./nodes/26'),
	() => import('./nodes/27'),
	() => import('./nodes/28'),
	() => import('./nodes/29')
];

export const server_loads = [2];

export const dictionary = {
		"/(public)": [~25],
		"/403": [29],
		"/(app)/_kit": [3,[2]],
		"/(app)/admin": [~4,[2]],
		"/(app)/admin/flujos/reasignacion": [~5,[2]],
		"/(app)/admin/flujos/reglas": [~6,[2]],
		"/(app)/admin/plantillas": [~7,[2]],
		"/(app)/admin/preservacion": [~8,[2]],
		"/(app)/admin/seguridad/claves": [~9,[2]],
		"/(app)/anulaciones": [~10,[2]],
		"/(app)/archivo-fisico": [~11,[2]],
		"/(app)/bandeja": [~12,[2]],
		"/(app)/borradores": [~13,[2]],
		"/(app)/busqueda": [~14,[2]],
		"/(app)/dashboard": [~15,[2]],
		"/(app)/envios": [~16,[2]],
		"/(app)/expedientes": [~17,[2]],
		"/(app)/expedientes/[id]": [~18,[2]],
		"/(app)/firmas": [~19,[2]],
		"/(public)/login": [~26],
		"/(public)/login/callback": [~27],
		"/(public)/logout": [~28],
		"/(app)/perfil": [~20,[2]],
		"/(app)/radicar": [~21,[2]],
		"/(app)/reportes": [~22,[2]],
		"/(app)/transferencias": [~23,[2]],
		"/(app)/transferencias/[id]": [~24,[2]]
	};

export const hooks = {
	handleError: (({ error }) => { console.error(error) }),
	
	reroute: (() => {}),
	transport: {}
};

export const decoders = Object.fromEntries(Object.entries(hooks.transport).map(([k, v]) => [k, v.decode]));

export const hash = false;

export const decode = (type, value) => decoders[type](value);

export { default as root } from '../root.svelte';