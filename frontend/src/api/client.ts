// Thin fetch wrapper. `credentials: "include"` makes the session
// cookie travel on every request. The Vite dev server proxies /api
// to localhost:8000 so we stay same-origin.

export interface ValidationErrorItem {
  type: string;
  loc: (string | number)[];
  msg: string;
  ctx?: Record<string, unknown>;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
    public errors?: ValidationErrorItem[]
  ) {
    super(`HTTP ${status}: ${detail}`);
    this.name = "ApiError";
  }
}

interface ApiOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
}

export async function api<T = unknown>(
  path: string,
  options: ApiOptions = {}
): Promise<T> {
  const init: RequestInit = {
    method: options.method ?? "GET",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  };
  if (options.body !== undefined) {
    init.body = JSON.stringify(options.body);
  }

  const res = await fetch(path, init);

  if (!res.ok) {
    let detail = res.statusText;
    let errors: ValidationErrorItem[] | undefined;
    try {
      const body = (await res.json()) as {
        detail?: string | ValidationErrorItem[];
      };
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (Array.isArray(body.detail)) {
        errors = body.detail;
        detail = body.detail
          .map((e) => `${e.loc.slice(1).join(".")}: ${e.msg}`)
          .join("; ");
      }
    } catch {
      // body wasn't JSON; keep status text
    }
    throw new ApiError(res.status, detail, errors);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
