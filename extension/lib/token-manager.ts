/**
 * Token management for the background service worker.
 *
 * Keeps access tokens in memory (most secure for extensions)
 * and refresh tokens in browser.storage.session.
 */

// ---------------------------------------------------------------------------
// Module-level state
// ---------------------------------------------------------------------------

let _accessToken: string | null = null;
let _tokenExpiry: number | null = null;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function setTokens(
  accessToken: string,
  expiresIn: number,
  refreshToken: string,
): Promise<void> {
  _accessToken = accessToken;
  _tokenExpiry = Date.now() + expiresIn * 1000;
  await browser.storage.session.set({ refreshToken });
}

export function getAccessToken(): string | null {
  if (!_accessToken || !_tokenExpiry) return null;
  if (Date.now() >= _tokenExpiry - 60_000) return null;
  return _accessToken;
}

export function clearTokens(): void {
  _accessToken = null;
  _tokenExpiry = null;
}
