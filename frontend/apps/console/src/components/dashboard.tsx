import { useState } from "react";
import { ArrowUpRight, Bot, Cable, MessageSquare, Route } from "lucide-react";
import type { Snapshot } from "@/api";
import { eventAddress } from "@/conversations";
import { useLocale } from "@/i18n";
import { dashboardSummary } from "@/lib/dashboard";
import { GatewaySignal } from "@/components/gateway-signal";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { EmptyRow, ListToolbar, matchesQuery, TableFrame } from "@/components/management";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export function Dashboard({
  snapshot,
  activity,
  updatedAt,
}: {
  snapshot: Snapshot;
  activity: { received: string[]; accepted: boolean; sequence: number };
  updatedAt: string;
}) {
  const { t, message, date } = useLocale();
  const [query, setQuery] = useState("");
  const summary = dashboardSummary(snapshot);
  const metrics = [
    {
      title: t("Channels"),
      value: summary.channels,
      icon: Cable,
      href: "#channels",
      detail: "",
    },
    {
      title: t("Agent hosts"),
      value: summary.hosts,
      icon: Bot,
      href: "#hosts",
      detail: t("API {api} / MCP {mcp}", {
        api: summary.apiHosts,
        mcp: summary.mcpHosts,
      }),
    },
    {
      title: t("Routes & access"),
      value: summary.routes,
      icon: Route,
      href: "#routes",
      detail: t("{count} enabled", { count: summary.enabledRoutes }),
    },
    {
      title: t("Recent messages"),
      value: summary.messages,
      icon: MessageSquare,
      href: "#events",
      detail: t("Latest 100"),
    },
  ];
  const states = ["queued", "submitted", "available", "completed"] as const;
  const attention = [
    { label: t("Replies blocked or unconfirmed"), count: summary.replyIssues, href: "#events" },
    { label: t("Messages without a route"), count: summary.unmatched, href: "#events" },
    {
      label: t("Messages failed or blocked"),
      count: summary.failed + summary.blocked,
      href: "#events",
    },
  ];
  const channelName = (event: Snapshot["events"][number]) => {
    const address = eventAddress(snapshot, event);
    return (
      snapshot.connections.find((item) => item.id === address?.account_id)?.name ??
      address?.account_id ??
      t("Not created")
    );
  };
  const events = [...snapshot.events]
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
    .filter((event) =>
      matchesQuery(query, [
        channelName(event),
        event.message.text,
        event.message.sender_id,
        event.state,
        message(event.state),
      ]),
    )
    .slice(0, 5);
  return (
    <div className="grid min-w-0 gap-5">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {metrics.map((metric) => (
          <Card key={metric.href} className="gap-3">
            <CardHeader>
              <CardTitle className="text-sm font-medium">
                <a
                  href={metric.href}
                  className="flex items-center justify-between gap-2 rounded-sm hover:text-primary"
                >
                  {metric.title}
                  <metric.icon className="size-4 text-primary" aria-hidden="true" />
                </a>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-3xl font-semibold tabular-nums">{metric.value}</p>
              {metric.detail && (
                <p className="mt-2 text-xs text-muted-foreground">{metric.detail}</p>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
      <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>
              <h2>{t("Gateway activity")}</h2>
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-6">
            <GatewaySignal {...activity} />
            <dl className="grid grid-cols-2 gap-5 sm:grid-cols-4">
              {states.map((state) => (
                <div key={state}>
                  <dt className="text-xs text-muted-foreground">{message(state)}</dt>
                  <dd className="mt-2 text-2xl font-semibold tabular-nums">{summary[state]}</dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>
              <h2>{t("Needs attention")}</h2>
            </CardTitle>
            <span className="text-xs text-muted-foreground">{t("Latest 100 per list")}</span>
          </CardHeader>
          <CardContent>
            <ul className="divide-y">
              {attention.map((item) => (
                <li key={item.label}>
                  <a
                    href={item.href}
                    className="flex items-center gap-3 rounded-sm py-3 text-sm hover:text-primary"
                  >
                    <span className="flex-1">{item.label}</span>
                    <Badge variant={item.count ? "secondary" : "outline"} className="tabular-nums">
                      {item.count}
                    </Badge>
                    <ArrowUpRight className="size-4 text-muted-foreground" aria-hidden="true" />
                  </a>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>
      <section className="grid min-w-0 gap-4" aria-labelledby="recent-messages-title">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="recent-messages-title" className="font-semibold">
            {t("Recent messages")}
          </h2>
        </div>
        <ListToolbar query={query} onQueryChange={setQuery} label={t("Search messages…")}>
          <Button asChild variant="outline">
            <a href="#events">
              {t("View all messages")}
              <ArrowUpRight aria-hidden="true" />
            </a>
          </Button>
        </ListToolbar>
        <TableFrame>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("Channels")}</TableHead>
                <TableHead>{t("Sender / message")}</TableHead>
                <TableHead>{t("Delivery")}</TableHead>
                <TableHead>{t("Created at")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.length === 0 ? (
                <EmptyRow columns={4} filtered={!!query.trim()}>
                  {t("No messages yet.")}
                </EmptyRow>
              ) : (
                events.map((event) => (
                  <TableRow
                    key={event.id}
                    className={
                      activity.received.includes(event.id) ? "signal-new-message" : undefined
                    }
                  >
                    <TableCell className="max-w-40 truncate">{channelName(event)}</TableCell>
                    <TableCell className="max-w-72">
                      <p className="truncate text-xs text-muted-foreground">
                        {event.message.sender_id}
                      </p>
                      <p className="truncate">{event.message.text}</p>
                    </TableCell>
                    <TableCell>
                      <Badge variant={event.state === "failed" ? "destructive" : "secondary"}>
                        {message(event.state)}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {date(event.created_at)}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </TableFrame>
      </section>
      {updatedAt && (
        <p className="text-xs text-muted-foreground">
          {t("Last refreshed: {time}", { time: date(updatedAt) })}
        </p>
      )}
    </div>
  );
}
