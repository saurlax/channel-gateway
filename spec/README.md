# Channel Gateway specifications

Channel Gateway is a communication gateway for AI agents. It receives channel
events, delivers authorized input to an Agent Host, and exposes channel reads
and actions through MCP. A channel identity, a routing destination, and permission
to act are separate concepts.

This directory owns accepted technical contracts. The current implementation is
a single-process OneBot v11, QQ official Bot, Weixin iLink, Slack, Lark and Discord bridge to a13n with conversation-scoped
MCP tools, plus generic MCP Hosts with independent keys and authorized
cross-channel route reads. The contracts below describe that supported baseline. They do not
claim a complete RBAC system or access outside assigned routes.
QQ includes guild/DM routing, media, structured messages, streaming, and scoped
platform resource actions. OneBot supports images and optional message reactions.
Weixin is text-only. Application permissions remain separate from gateway support.

The [multi-platform integration design](07-channel-integration.md) specifies the
target contract for additional adapters. Its implementation-progress section identifies the current adapter slice; all
remaining transports and capabilities stay unimplemented until their release gates pass.
The dated [platform API reference](../docs/platform-api-reference.md) separates
documented APIs, first-party implementations, reference integrations and
unverified capabilities.

## Reading path and ownership

| Document                                           | Owns                                                              |
| -------------------------------------------------- | ----------------------------------------------------------------- |
| [Architecture](00-overview.md)                     | Components, dependency boundaries, and runtime scope              |
| [Channel adapters](01-channel-adapters.md)         | Account identity, protocols, pairing, and message projection      |
| [Identity and access](02-identity-and-access.md)   | Credentials, route authorization, revocation, and privacy limits  |
| [Routing and delivery](03-routing-and-delivery.md) | Conversation binding, input/reply lifecycles, and retry semantics |
| [Agent tools](04-agent-tools.md)                   | MCP reads/actions and a13n tool binding                           |
| [Images and reactions](05-images-and-reactions.md) | Image projection, authorized media reads/sends, and optional reactions |
| [QQ platform](06-qq-platform.md) | Guild/DM identity, resource action grants, operation catalog, and action lifecycle |
| [Multi-platform integration](07-channel-integration.md) | Target adapter expansion, channel/thread identity, capability evidence, five-tool mappings and release gates; not an implementation claim |

Start with architecture; read the owning contract before changing its behavior.
The generated [OpenAPI document](../README.md#run-from-source) describes management
wire schemas. The specifications own the semantics that those schemas implement.

## Document boundaries

- `spec/` contains accepted, testable behavior and architecture contracts.
- `docs/` contains user instructions and troubleshooting, including the
  [OneBots and a13n setup guide](../docs/onebots-a13n.md) and
  [QQ and Weixin guide](../docs/official-channels.md), and
  [Agent Host guide](../docs/agent-hosts.md).
- [DEVELOPMENT.md](../DEVELOPMENT.md) owns engineering standards and console
  interaction/localization rules.
- [CONTRIBUTING.md](../CONTRIBUTING.md) owns proposals, contribution workflow,
  and validation. Unresolved designs and future plans belong in GitHub Issues.

Each durable rule has one owner. Other documents link to that owner instead of
maintaining another state machine, permission model, or protocol definition.
Changes to accepted behavior update the owning spec, implementation, affected
instructions, and relevant validation together in the same pull request.

## Compatibility boundary

One channel is added at a time and represents one configured bot account. Instance
discovery, multi-account selection, and implicit aggregation are not part of this
baseline. OneBot HTTP, reverse HTTP, forward/reverse WebSocket, QQ official
Gateway/callbacks, and Weixin iLink polling are implemented. See the
[channel contract](01-channel-adapters.md#transport-compatibility).

Cross-channel identity linking, full RBAC, live media/voice,
telephone, and radio are outside the supported baseline. No stable published
adapter API, schema migration framework, or distributed worker protocol is
defined. Future contracts must explicitly identify compatibility and migration
requirements rather than imply that a new package can replace an existing one
without changing configuration or state.
