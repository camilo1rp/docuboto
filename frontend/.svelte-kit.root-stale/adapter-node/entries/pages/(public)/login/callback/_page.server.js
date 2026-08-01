import { r as redirect } from "../../../../../chunks/index.js";
import { g as COOKIE_PKCE_STATE, h as COOKIE_PKCE_VERIFIER, i as exchangeCode, s as setSessionCookies, j as clearPkceCookies } from "../../../../../chunks/auth.server.js";
async function load({ url, cookies }) {
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const errorParam = url.searchParams.get("error");
  if (errorParam) {
    const desc = url.searchParams.get("error_description") ?? errorParam;
    redirect(302, `/login?error=${encodeURIComponent(desc)}`);
  }
  if (!code || !state) {
    redirect(302, "/login?error=invalid_callback");
  }
  const storedState = cookies.get(COOKIE_PKCE_STATE);
  if (!storedState) {
    redirect(302, "/login?error=state_missing");
  }
  const [storedRandom] = storedState.split("|");
  const [receivedRandom, encodedNext] = state.split("|");
  if (storedRandom !== receivedRandom) {
    redirect(302, "/login?error=state_mismatch");
  }
  const next = encodedNext ? decodeURIComponent(encodedNext) : "/dashboard";
  let safeNext = "/dashboard";
  try {
    const candidate = new URL(next, url.origin);
    if (candidate.origin === url.origin) {
      safeNext = candidate.pathname + candidate.search + candidate.hash;
    }
  } catch {
  }
  const codeVerifier = cookies.get(COOKIE_PKCE_VERIFIER);
  if (!codeVerifier) {
    redirect(302, "/login?error=verifier_missing");
  }
  const redirectUri = `${url.origin}/login/callback`;
  let tokenSet;
  try {
    tokenSet = await exchangeCode(code, codeVerifier, redirectUri);
  } catch (err) {
    console.error("[callback] Token exchange error:", err?.message);
    redirect(302, `/login?error=${encodeURIComponent("authentication_failed")}`);
  }
  setSessionCookies(cookies, tokenSet);
  clearPkceCookies(cookies);
  redirect(302, safeNext);
}
export {
  load
};
