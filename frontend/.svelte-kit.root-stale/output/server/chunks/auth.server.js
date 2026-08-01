import { K as KEYCLOAK_CLIENT_ID } from "./config.js";
import { K as KEYCLOAK_TOKEN_URL, a as KEYCLOAK_LOGOUT_URL } from "./config.server.js";
const COOKIE_ACCESS_TOKEN = "op_access_token";
const COOKIE_REFRESH_TOKEN = "op_refresh_token";
const COOKIE_PKCE_VERIFIER = "op_pkce_verifier";
const COOKIE_PKCE_STATE = "op_pkce_state";
const COOKIE_BASE = (
  /** @type {const} */
  {
    path: "/",
    httpOnly: true,
    secure: true,
    // true en producción (requiere HTTPS); false solo en dev local
    sameSite: (
      /** @type {"lax"} */
      "lax"
    )
  }
);
async function exchangeCode(code, codeVerifier, redirectUri) {
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: KEYCLOAK_CLIENT_ID,
    code,
    code_verifier: codeVerifier,
    redirect_uri: redirectUri
  });
  const res = await fetch(KEYCLOAK_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString()
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(
      `Token exchange failed: ${err.error_description ?? err.error ?? `HTTP ${res.status}`}`
    );
  }
  return res.json();
}
async function refreshAccessToken(refreshToken) {
  const body = new URLSearchParams({
    grant_type: "refresh_token",
    client_id: KEYCLOAK_CLIENT_ID,
    refresh_token: refreshToken
  });
  const res = await fetch(KEYCLOAK_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString()
  });
  if (!res.ok) return null;
  return res.json();
}
async function revokeSession(refreshToken) {
  if (!refreshToken) return;
  const body = new URLSearchParams({
    client_id: KEYCLOAK_CLIENT_ID,
    refresh_token: refreshToken
  });
  await fetch(KEYCLOAK_LOGOUT_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString()
  }).catch(() => {
  });
}
function decodeJwt(token) {
  if (!token) return null;
  try {
    const [, payload] = token.split(".");
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(json);
  } catch {
    return null;
  }
}
function extractSessionUser(claims) {
  if (!claims) return null;
  const roles = claims.realm_access?.roles ?? [];
  const tenantSlug = claims.tenant_slug ?? claims["tenant_slug"] ?? null;
  return {
    sub: claims.sub,
    email: claims.email ?? null,
    name: claims.name ?? claims.preferred_username ?? claims.sub,
    preferred_username: claims.preferred_username ?? null,
    roles,
    tenant_slug: tenantSlug,
    // Permisos granulares: se cargan de /auth/me post-login (ver layout.server.js)
    permissions: []
  };
}
function setSessionCookies(cookies, tokenSet) {
  const accessMaxAge = Math.min(tokenSet.expires_in ?? 300, 600);
  cookies.set(COOKIE_ACCESS_TOKEN, tokenSet.access_token, {
    ...COOKIE_BASE,
    maxAge: accessMaxAge
  });
  if (tokenSet.refresh_token) {
    const refreshMaxAge = tokenSet.refresh_expires_in ?? 1800;
    cookies.set(COOKIE_REFRESH_TOKEN, tokenSet.refresh_token, {
      ...COOKIE_BASE,
      maxAge: refreshMaxAge
    });
  }
}
function clearSessionCookies(cookies) {
  cookies.delete(COOKIE_ACCESS_TOKEN, { path: "/" });
  cookies.delete(COOKIE_REFRESH_TOKEN, { path: "/" });
}
function storePkceVerifierCookie(cookies, verifier) {
  cookies.set(COOKIE_PKCE_VERIFIER, verifier, {
    ...COOKIE_BASE,
    maxAge: 300
    // 5 minutos — solo durante el flujo de autorización
  });
}
function storePkceStateCookie(cookies, state) {
  cookies.set(COOKIE_PKCE_STATE, state, {
    ...COOKIE_BASE,
    maxAge: 300
  });
}
function clearPkceCookies(cookies) {
  cookies.delete(COOKIE_PKCE_VERIFIER, { path: "/" });
  cookies.delete(COOKIE_PKCE_STATE, { path: "/" });
}
export {
  COOKIE_ACCESS_TOKEN as C,
  COOKIE_REFRESH_TOKEN as a,
  storePkceVerifierCookie as b,
  clearSessionCookies as c,
  decodeJwt as d,
  extractSessionUser as e,
  storePkceStateCookie as f,
  COOKIE_PKCE_STATE as g,
  COOKIE_PKCE_VERIFIER as h,
  exchangeCode as i,
  clearPkceCookies as j,
  revokeSession as k,
  refreshAccessToken as r,
  setSessionCookies as s
};
