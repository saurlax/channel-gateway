import { Bot, MessageCircle, Waypoints } from "lucide-react";
import { useLocale } from "@/i18n";

export function GatewaySignal({
  received,
  accepted,
  sequence,
}: {
  received: string[];
  accepted: boolean;
  sequence: number;
}) {
  const { t } = useLocale();
  return (
    <div className="flex items-center gap-3 py-2 text-xs text-muted-foreground sm:gap-4">
      <span className="flex shrink-0 items-center gap-2">
        <MessageCircle className="size-4" aria-hidden="true" />
        {t("Channels")}
      </span>
      <span className="signal-track" aria-hidden="true">
        {received.length > 0 && <span key={sequence} className="signal-packet" />}
      </span>
      <span className="flex shrink-0 items-center gap-2 font-medium text-primary">
        <Waypoints className="size-4" aria-hidden="true" />
        <span aria-label="Channel Gateway">CG</span>
      </span>
      <span className="signal-track" aria-hidden="true">
        {accepted && <span key={sequence} className="signal-packet" />}
      </span>
      <span className="flex shrink-0 items-center gap-2">
        <Bot className="size-4" aria-hidden="true" />
        {t("Agent hosts")}
      </span>
    </div>
  );
}
