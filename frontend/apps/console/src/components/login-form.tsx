import { useLocale } from "@/i18n";
// Adapted from the official shadcn login-01 block.
import { useState, type FormEvent } from "react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

export function LoginForm({
  onLogin,
  className,
}: {
  onLogin: (token: string) => Promise<void>;
  className?: string;
}) {
  const { t, message } = useLocale();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const token = String(new FormData(event.currentTarget).get("token"));
    setPending(true);
    setError("");
    try {
      await onLogin(token);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to sign in.");
    } finally {
      setPending(false);
    }
  }
  return (
    <div className={cn("flex flex-col gap-6", className)}>
      <Card>
        <CardHeader>
          <CardTitle>{t("Sign in to Channel Gateway")}</CardTitle>
          <CardDescription>
            {t("Manage your channels, access rules, and agent conversations.")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="token">{t("Administrator token")}</FieldLabel>
                <Input
                  id="token"
                  name="token"
                  type="password"
                  autoComplete="current-password"
                  required
                  disabled={pending}
                />
                <FieldDescription>
                  {t("Your token is kept in memory until you sign out or close this page.")}
                </FieldDescription>
              </Field>
              {error && <FieldDescription role="alert">{message(error)}</FieldDescription>}
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
