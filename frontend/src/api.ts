// REST client for the Aion backend. Every call carries the per-run session token.

const TOKEN_KEY = "aion-token";

/** `?debug` exposes the live store as `window.aion` (for checking animations by hand). */
export const debugMode = new URLSearchParams(location.search).has("debug");

/** `?mode=desktop`: this page renders the desktop mascot (read before the URL is cleaned). */
export const desktopMode = new URLSearchParams(location.search).get("mode") === "desktop";

function readToken(): string {
  const params = new URLSearchParams(location.search);
  const fromUrl = params.get("token");
  if (fromUrl) {
    try {
      sessionStorage.setItem(TOKEN_KEY, fromUrl);
    } catch {
      /* storage may be unavailable */
    }
    // keep the token out of the address bar / history
    history.replaceState(null, "", location.pathname + location.hash);
    return fromUrl;
  }
  try {
    return sessionStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export const token = readToken();

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** Validation messages of a 422 answer (`{"error": [...]}`), one per problem. */
    public details: string[] = [],
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const form = body instanceof FormData;
  const res = await fetch(path, {
    method,
    headers: {
      "X-Aion-Token": token,
      // a FormData body sets its own multipart boundary
      ...(body !== undefined && !form ? { "Content-Type": "application/json" } : {}),
    },
    body: body === undefined ? undefined : form ? body : JSON.stringify(body),
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    let details: string[] = [];
    try {
      const data = await res.json();
      if (typeof data.detail === "string") message = data.detail;
      else if (Array.isArray(data.error)) {
        details = data.error.map(String);
        message = details.join("; ");
      }
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, message, details);
  }
  const type = res.headers.get("content-type") ?? "";
  if (type.includes("application/json")) return (await res.json()) as T;
  return (await res.blob()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  /** POST a multipart form (file upload). */
  upload: <T>(path: string, form: FormData) => request<T>("POST", path, form),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export function avatarUrl(file: string): string {
  return `/avatar/${encodeURIComponent(token)}/${encodeURIComponent(file)}`;
}
