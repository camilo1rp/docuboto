import { C as COOKIE_ACCESS_TOKEN, a as COOKIE_REFRESH_TOKEN, r as refreshAccessToken, s as setSessionCookies, d as decodeJwt, e as extractSessionUser } from "./auth.server.js";
async function handle({ event, resolve }) {
  const { cookies } = event;
  let accessToken = cookies.get(COOKIE_ACCESS_TOKEN);
  const refreshToken = cookies.get(COOKIE_REFRESH_TOKEN);
  if (!accessToken && refreshToken) {
    const tokenSet = await refreshAccessToken(refreshToken).catch(() => null);
    if (tokenSet?.access_token) {
      setSessionCookies(cookies, tokenSet);
      accessToken = tokenSet.access_token;
    }
  }
  if (accessToken) {
    event.locals.accessToken = accessToken;
    const claims = decodeJwt(accessToken);
    if (claims) {
      event.locals.user = extractSessionUser(claims);
    }
  }
  return resolve(event);
}
function handleError({ error, event }) {
  const message = error instanceof Error ? error.message : "Error inesperado del servidor";
  console.error(`[hooks.server] Error en ${event.url.pathname}:`, error);
  return { message, code: "INTERNAL_ERROR" };
}
export {
  handle,
  handleError
};
