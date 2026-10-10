import type { Connection } from "@/api";
import type { MessageKey } from "@/i18n/messages";

export const channelTypes = [
  {
    id: "http_reverse",
    kind: "onebots",
    transport: "http_reverse",
    label: "OneBot reverse HTTP",
    help: "Receive signed HTTP callbacks and send through the account HTTP API.",
  },
  {
    id: "http",
    kind: "onebots",
    transport: "http",
    label: "OneBot forward HTTP",
    help: "Send through the account HTTP API. This mode does not receive messages or wake agents.",
  },
  {
    id: "ws",
    kind: "onebots",
    transport: "ws",
    label: "OneBot forward WebSocket",
    help: "Connect to the account’s universal WebSocket endpoint to receive events and send messages.",
  },
  {
    id: "ws_reverse",
    kind: "onebots",
    transport: "ws_reverse",
    label: "OneBot reverse WebSocket",
    help: "Configure the bot to connect to the gateway URL with the access token, self ID, and Universal client role.",
  },
  {
    id: "qq",
    kind: "qq",
    transport: "websocket",
    label: "QQ",
    help: "Use a QQ official Bot AppID and AppSecret. Supports private and group text messages.",
  },
  {
    id: "weixin",
    kind: "weixin",
    transport: "polling",
    label: "Weixin",
    help: "Save the channel, then scan the login QR code with Weixin. Supports private text messages.",
  },
] as const satisfies ReadonlyArray<{
  id: string;
  kind: Connection["kind"];
  transport: string;
  label: MessageKey;
  help: MessageKey;
}>;

export function channelType(connection: Connection) {
  return channelTypes.find(
    (type) =>
      type.kind === connection.kind &&
      (connection.kind !== "onebots" ||
        type.transport === (connection.transport || "http_reverse")),
  );
}

export function channelEndpoint(connection: Connection) {
  if (connection.kind === "onebots" && connection.transport === "ws_reverse") {
    const url = new URL(`/api/v1/ws/onebots/${connection.id}`, location.origin);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    return url.toString();
  }
  return connection.api_url ?? "";
}
