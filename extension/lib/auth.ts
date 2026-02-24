/**
 * PKCE (Proof Key for Code Exchange) utilities for OAuth2 authorization.
 *
 * Used by the background service worker to perform the Keycloak PKCE flow
 * via browser.identity.launchWebAuthFlow().
 */

/**
 * Generate a cryptographically random URL-safe string.
 * Used for PKCE code_verifier and OAuth state parameters.
 */
export function generateRandomString(length: number): string {
  const array = new Uint8Array(length);
  crypto.getRandomValues(array);
  return btoa(String.fromCharCode(...array))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '')
    .slice(0, length);
}

/**
 * Derive a S256 code_challenge from a code_verifier.
 * SHA-256 hash, then base64url-encode per RFC 7636.
 */
export async function generateCodeChallenge(
  verifier: string,
): Promise<string> {
  const digest = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(verifier),
  );
  return btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '');
}
