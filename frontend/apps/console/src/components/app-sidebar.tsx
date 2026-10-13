import { useLocale } from "@/i18n";
// Adapted from the official shadcn sidebar-07 block.
import { Bot, Cable, GalleryVerticalEnd, LogOut, MessageSquare, Route } from "lucide-react";
import { NavMain } from "@/components/nav-main";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarRail,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
} from "@/components/ui/sidebar";

export function AppSidebar({ onLogout, page }: { onLogout: () => void; page: string }) {
  const { t } = useLocale();
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild>
              <a href="#channels">
                <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
                  <GalleryVerticalEnd className="size-4" />
                </div>
                <div className="grid flex-1 text-left text-sm leading-tight">
                  <span className="truncate font-medium">Channel Gateway</span>
                  <span className="truncate text-xs">{t("Channel gateway")}</span>
                </div>
              </a>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <NavMain
          activeUrl={`#${page}`}
          groups={[
            {
              id: "gateway",
              title: t("Gateway"),
              items: [
                { title: t("Channels"), url: "#channels", icon: Cable },
                { title: t("Agent hosts"), url: "#hosts", icon: Bot },
                { title: t("Routes & access"), url: "#routes", icon: Route },
              ],
            },
            {
              id: "activity",
              title: t("Activity"),
              items: [{ title: t("Messages"), url: "#events", icon: MessageSquare }],
            },
          ]}
        />
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton onClick={onLogout} tooltip={t("Sign out")}>
              <LogOut />
              <span>{t("Sign out")}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
