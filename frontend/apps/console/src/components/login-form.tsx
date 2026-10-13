import { useLocale } from "@/i18n";
// Adapted from the official shadcn login-01 block.
import { useState, type FormEvent } from "react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Waypoints } from "lucide-react";

export function LoginForm({
  onLogin,
  className,
  sessionError = "",
}: {
  onLogin: (username: string, password: string) => Promise<void>;
  sessionError?: string;
  className?: string;
}) {
  const { t, message } = useLocale();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setPending(true);
    setError("");
    try {
      await onLogin(String(data.get("username")), String(data.get("password")));
      form.reset();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to sign in.");
    } finally {
      setPending(false);
    }
  }
  return (
    <div className={cn("flex flex-col gap-6", className)}>
      <div className="flex items-center justify-center gap-3">
        <div className="flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <Waypoints className="size-5" aria-hidden="true" />
        </div>
        <div>
          <p className="text-sm font-medium">Channel Gateway</p>
          <p className="text-xs text-muted-foreground">{t("Channel gateway")}</p>
        </div>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>{t("Sign in to Channel Gateway")}</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="username">{t("Username")}</FieldLabel>
                <Input
                  id="username"
                  name="username"
                  autoComplete="username"
                  required
                  maxLength={128}
                  disabled={pending}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="password">{t("Password")}</FieldLabel>
                <Input
                  id="password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  minLength={8}
                  required
                  maxLength={4096}
                  disabled={pending}
                />
              </Field>
              {(error || sessionError) && (
                <FieldDescription role="alert">{message(error || sessionError)}</FieldDescription>
              )}
              <Field>
                <Button type="submit" disabled={pending}>
                  {pending ? t("Signing in…") : t("Sign in")}
                </Button>
              </Field>
            </FieldGroup>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
