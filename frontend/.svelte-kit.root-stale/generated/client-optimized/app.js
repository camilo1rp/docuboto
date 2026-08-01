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
	() => import('./nodes/21')
];

export const server_loads = [2];

export const dictionary = {
		"/(public)": [~17],
		"/403": [21],
		"/(app)/_kit": [3,[2]],
		"/(app)/admin/plantillas": [~4,[2]],
		"/(app)/archivo-fisico": [~5,[2]],
		"/(app)/bandeja": [~6,[2]],
		"/(app)/borradores": [~7,[2]],
		"/(app)/busqueda": [~8,[2]],
		"/(app)/dashboard": [~9,[2]],
		"/(app)/envios": [~10,[2]],
		"/(app)/expedientes": [~11,[2]],
		"/(app)/expedientes/[id]": [~12,[2]],
		"/(app)/firmas": [~13,[2]],
		"/(public)/login": [~18],
		"/(public)/login/callback": [~19],
		"/(public)/logout": [~20],
		"/(app)/radicar": [~14,[2]],
		"/(app)/reportes": [~15,[2]],
		"/(app)/transferencias": [~16,[2]]
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