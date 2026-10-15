import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { getAuthToken } from "@/api/client";
import { ToasterProvider } from "@/components/ui/toaster";
import { AuthProvider } from "@/hooks/use-auth";
import { LoginPage } from "@/pages/login-page";

import { ORIGIN } from "../helpers/fixtures";
import { createTestQueryClient } from "../helpers/render";
import { server } from "../helpers/server";

const locationProbe = { pathname: "" };

function LocationProbe() {
  const location = useLocation();
  locationProbe.pathname = location.pathname;
  return null;
}

function renderLoginPage() {
  locationProbe.pathname = "";
  const queryClient = createTestQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ToasterProvider>
          <MemoryRouter initialEntries={["/login"]}>
            <LocationProbe />
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/dashboard" element={<div>总览占位页</div>} />
            </Routes>
          </MemoryRouter>
        </ToasterProvider>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("登录页", () => {
  it("凭证有效：保存到内存并跳转总览", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${ORIGIN}/api/transactions`, () =>
        HttpResponse.json({ items: [], total: 0 }),
      ),
    );

    renderLoginPage();
    await user.type(screen.getByLabelText("后台密码"), "valid-token");
    await user.click(screen.getByRole("button", { name: "进入后台" }));

    expect(await screen.findByText("总览占位页")).toBeInTheDocument();
    expect(getAuthToken()).toBe("valid-token");
    expect(locationProbe.pathname).toBe("/dashboard");
  });

  it("凭证无效：显示错误并停留在登录页", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${ORIGIN}/api/transactions`, () =>
        HttpResponse.json(
          {
            success: false,
            error_code: "UNAUTHORIZED",
            message: "Missing or invalid admin token.",
            details: null,
          },
          { status: 401 },
        ),
      ),
    );

    renderLoginPage();
    await user.type(screen.getByLabelText("后台密码"), "wrong-token");
    await user.click(screen.getByRole("button", { name: "进入后台" }));

    expect(
      await screen.findByText("密码错误，请核对后重新输入。"),
    ).toBeInTheDocument();
    expect(getAuthToken()).toBeNull();
    expect(locationProbe.pathname).toBe("/login");
  });

  it("后端不可达：显示网络错误", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${ORIGIN}/api/transactions`, () => HttpResponse.error()),
    );

    renderLoginPage();
    await user.type(screen.getByLabelText("后台密码"), "any-token");
    await user.click(screen.getByRole("button", { name: "进入后台" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/无法连接到服务器/),
    );
    expect(getAuthToken()).toBeNull();
  });

  it("空凭证不可提交", () => {
    renderLoginPage();
    expect(screen.getByRole("button", { name: "进入后台" })).toBeDisabled();
  });
});
