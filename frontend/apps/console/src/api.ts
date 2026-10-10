export type Connection = {
  id: string;
  name: string;
  kind: "onebots" | "a13n";
  api_url: string;
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
  agent_id: string;
  enabled: boolean;
  auto_reply: boolean;
  reply_source: "output" | "mcp";
  mcp_connection_id: string | null;
  thread_id: string | null;
};
export type ChannelEvent = {
  id: string;
  route_id: string;
  state: string;
  thread_id: string | null;
  run_id: string | null;
  error: string | null;
  created_at: string;
  message: { sender_id: string; text: string };
};
export type Reply = {
  id: string;
  route_id: string;
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

export async function api<T>(token: string, path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1/${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...init.headers,
      },
    });
  } catch {
    throw new Error("Network request failed. Check the connection and try again.");
  }
  let value;
  try {
    value = await response.json();
  } catch {
    throw new Error("Unable to read the server response.");
  }
  if (!response.ok) {
    const detail = value.detail;
    throw new Error(
      Array.isArray(detail)
        ? detail.map((item: { msg: string }) => item.msg).join("; ")
        : typeof detail === "string"
          ? detail
          : `Request failed (${response.status}).`,
    );
  }
  return value as T;
}
