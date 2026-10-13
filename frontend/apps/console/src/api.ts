export type Connection = {
  id: string;
  name: string;
  kind: "onebots" | "qq" | "weixin" | "a13n" | "mcp";
  transport?: string;
  app_id?: string | null;
  state?: string;
  error?: string | null;
  capabilities?: { receive: boolean; send: boolean; conversations: string[]; content: string[] };
  api_url: string | null;
  self_id: string | null;
};
export type Route = {
  id: string;
  name: string;
  channel_id: string;
  host_id: string;
  conversation_kind: "private" | "group";
  conversation_id: string;
  allowed_user_ids: string[];
  sender_policy: "allowlist" | "any";
  access_mode: "restricted" | "full";
  parent_route_id: string | null;
  agent_id: string | null;
  enabled: boolean;
  reply_source: "output" | "mcp";
  mcp_connection_id: string | null;
  thread_id: string | null;
};
export type ChannelEvent = {
  id: string;
  route_id: string | null;
  route_name?: string | null;
  state: string;
  thread_id: string | null;
  run_id: string | null;
  error: string | null;
  created_at: string;
  message: {
    sender_id: string;
    text: string;
    address?: { account_id: string; conversation_id: string };
  };
};
export type Reply = {
  id: string;
  route_id: string;
  route_name?: string | null;
  address?: { account_id: string; conversation_id: string };
  state: string;
  text: string;
  error: string | null;
  created_at: string;
};
export type Snapshot = {
  connections: Connection[];
  routes: Route[];
  events: ChannelEvent[];
  replies: Reply[];
};

export type AdminSession = { username: string; csrf_token: string };

export type HistoryMessage = {
  id: string;
  route_id: string | null;
  direction: "incoming" | "outgoing";
  text: string;
  sender_id: string | null;
  state: string;
  error: string | null;
  created_at: string;
  thread_id: string | null;
  run_id: string | null;
};

export type HistoryPage = { messages: HistoryMessage[]; has_more: boolean };

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export async function api<T>(csrfToken: string, path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1/${path}`, {
      ...init,
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-Channel-Gateway-Request": "1",
        ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
        ...init.headers,
      },
    });
  } catch {
    throw new Error("Network request failed. Check the connection and try again.");
  }
  if (response.status === 204) return undefined as T;
  let value;
  try {
    value = await response.json();
  } catch {
    throw new Error("Unable to read the server response.");
  }
  if (!response.ok) {
    const detail = value.detail;
    throw new ApiError(
      Array.isArray(detail)
        ? detail.map((item: { msg: string }) => item.msg).join("; ")
        : typeof detail === "string"
          ? detail
          : `Request failed (${response.status}).`,
      response.status,
    );
  }
  return value as T;
}
