import { useEffect, useRef, useState } from "react";
import { api, type Connection } from "@/api";
import { useLocale } from "@/i18n";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/form-controls";
import { Alert, AlertDescription } from "@/components/ui/alert";

type Login = { login_id: string; qr_image: string; status: string };
const terminal = new Set(["expired", "verify_code_blocked", "binded_redirect"]);

export function WeixinLogin({
  connection,
  csrfToken,
  refresh,
}: {
  connection: Connection;
  csrfToken: string;
  refresh: () => Promise<void>;
}) {
  const { t, message } = useLocale();
  const [login, setLogin] = useState<Login | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [verifyCode, setVerifyCode] = useState("");
  const [submittedCode, setSubmittedCode] = useState<string | undefined>();
  const controller = useRef<AbortController | null>(null);
  const current = useRef<Login | null>(null);
  useEffect(
    () => () => {
      controller.current?.abort();
      if (current.current)
        void api(csrfToken, `connections/${connection.id}/login/cancel`, {
          method: "POST",
          body: JSON.stringify({ login_id: current.current.login_id }),
        }).catch(() => {});
    },
    [connection.id, csrfToken],
  );
  useEffect(() => {
    if (!login || terminal.has(status) || (status === "need_verifycode" && !submittedCode)) return;
    let stopped = false;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await api<{ status: string }>(
          csrfToken,
          `connections/${connection.id}/login/status`,
          {
            method: "POST",
            signal: abort.signal,
            body: JSON.stringify({
              login_id: login.login_id,
              ...(submittedCode ? { verify_code: submittedCode } : {}),
            }),
          },
        );
        if (stopped) return;
        setStatus(result.status);
        if (result.status === "confirmed") {
          current.current = null;
          setLogin(null);
          setVerifyCode("");
          setSubmittedCode(undefined);
          await refresh();
        } else if (result.status === "need_verifycode") {
          setSubmittedCode(undefined);
        } else if (!terminal.has(result.status)) timer = setTimeout(() => void poll(), 2000);
      } catch (cause) {
        if (!stopped) setError(cause instanceof Error ? cause.message : "Request failed.");
      }
    };
    void poll();
    return () => {
      stopped = true;
      abort.abort();
      clearTimeout(timer);
    };
    // Status updates do not restart a long poll; verification and terminal states do.
  }, [login, submittedCode, csrfToken, connection.id, refresh]);
  async function start() {
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    setStarting(true);
    setError("");
    setLogin(null);
    setStatus("");
    setVerifyCode("");
    setSubmittedCode(undefined);
    try {
      const value = await api<Login>(csrfToken, `connections/${connection.id}/login`, {
        method: "POST",
        signal: abort.signal,
      });
      if (!abort.signal.aborted) {
        current.current = value;
        setLogin(value);
        setStatus(value.status);
      }
    } catch (cause) {
      if (!abort.signal.aborted)
        setError(cause instanceof Error ? cause.message : "Request failed.");
    } finally {
      if (!abort.signal.aborted) setStarting(false);
    }
  }
  return (
    <div className="grid gap-4">
      <p className="text-sm text-muted-foreground">
        {t("Pair one Weixin bot account. Re-pairing must use the same account.")}
      </p>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{message(error)}</AlertDescription>
        </Alert>
      )}
      {login && !terminal.has(status) && (
        <img src={login.qr_image} alt={t("Weixin login QR code")} className="mx-auto h-56 w-56" />
      )}
      {status && (
        <p className="text-sm" role="status">
          {message(`weixin_login_${status}`)}
        </p>
      )}
      {status === "need_verifycode" && (
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            setError("");
            setSubmittedCode(verifyCode);
          }}
        >
          <FormField id="weixin-verify" label={t("Verification code")}>
            <Input
              id="weixin-verify"
              value={verifyCode}
              onChange={(event) => setVerifyCode(event.target.value)}
              inputMode="numeric"
              pattern="[0-9]{1,16}"
              maxLength={16}
              required
              autoComplete="one-time-code"
            />
          </FormField>
          <Button type="submit">{t("Verify")}</Button>
        </form>
      )}
      <Button
        type="button"
        variant={login ? "outline" : "default"}
        disabled={starting}
        onClick={() => void start()}
      >
        {starting
          ? t("Starting login…")
          : login
            ? t("Generate new QR code")
            : t("Scan to pair Weixin")}
      </Button>
    </div>
  );
}
