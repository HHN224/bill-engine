import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { ApiError, NetworkError, verifyAdminToken } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/use-auth";

type VerifyState = "idle" | "verifying" | "failed";

/**
 * 单用户后台密码登录；密码只保存在内存，刷新后需要重新输入。
 */
export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [token, setToken] = useState("");
  const [state, setState] = useState<VerifyState>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const from =
    (location.state as { from?: string } | null)?.from ?? "/dashboard";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const candidate = token.trim();
    if (!candidate || state === "verifying") {
      return;
    }
    setState("verifying");
    setErrorMessage(null);
    try {
      await verifyAdminToken(candidate);
      login(candidate);
      navigate(from, { replace: true });
    } catch (error) {
      setState("failed");
      if (error instanceof ApiError && error.isUnauthorized) {
        setErrorMessage("密码错误，请核对后重新输入。");
      } else if (error instanceof NetworkError) {
        setErrorMessage(error.message);
      } else if (error instanceof ApiError) {
        setErrorMessage(`验证失败：${error.message}`);
      } else {
        setErrorMessage("验证失败，请稍后重试。");
      }
    }
  }

  return (
    <div className="bg-background flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <span
            aria-hidden
            className="bg-primary text-primary-foreground shadow-pop-sm mx-auto mb-4 flex size-12 rotate-3 items-center justify-center rounded-lg text-lg font-bold"
          >
            账
          </span>
          <h1 className="text-xl font-extrabold tracking-tight">
            Pocket Ledger 后台
          </h1>
          <p className="text-muted-foreground mt-1 text-sm">
            输入后台密码进入。
          </p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="border-border bg-card shadow-pop rounded-lg border-2 p-6"
          noValidate
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="admin-token">后台密码</Label>
            <Input
              id="admin-token"
              type="password"
              value={token}
              onChange={(event) => {
                setToken(event.target.value);
                if (state === "failed") {
                  setState("idle");
                  setErrorMessage(null);
                }
              }}
              placeholder="输入后台密码"
              autoComplete="off"
              autoFocus
              required
              invalid={state === "failed"}
              aria-describedby={errorMessage ? "login-error" : "login-hint"}
            />
            {errorMessage ? (
              <p
                id="login-error"
                role="alert"
                className="text-destructive text-xs"
              >
                {errorMessage}
              </p>
            ) : (
              <p id="login-hint" className="text-muted-foreground text-xs">
                密码仅保存在当前页面内存中，刷新后需重新输入。
              </p>
            )}
          </div>
          <Button
            type="submit"
            className="mt-4 w-full"
            disabled={state === "verifying" || token.trim() === ""}
          >
            {state === "verifying" ? "验证中…" : "进入后台"}
          </Button>
        </form>
      </div>
    </div>
  );
}
