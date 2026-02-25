export const config = {
  apiBaseUrl:
    import.meta.env.WXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000',
  keycloakRealmUrl:
    import.meta.env.WXT_PUBLIC_KEYCLOAK_REALM_URL ??
    'http://localhost:8080/realms/browser-agent',
  keycloakClientId:
    import.meta.env.WXT_PUBLIC_KEYCLOAK_CLIENT_ID ??
    'browser-agent-extension',
} as const;
