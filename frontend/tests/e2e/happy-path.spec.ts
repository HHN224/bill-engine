import { expect, test } from "@playwright/test";

/**
 * 核心 happy path：登录 → 新增交易 → 列表可见 → 编辑 → 删除。
 * 凭证通过环境变量 E2E_ADMIN_TOKEN 注入，绝不写入源码。
 *
 * 前置：后端运行在 http://127.0.0.1:8000（Playwright 会尝试自动拉起，
 * 也可提前 `python run.py` 手动启动）。
 */

const adminToken = process.env.E2E_ADMIN_TOKEN;

test.beforeEach(async ({ page }) => {
  test.skip(!adminToken, "缺少 E2E_ADMIN_TOKEN 环境变量");
  await page.goto("/");
});

test("错误的凭证无法进入后台", async ({ page }) => {
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("后台密码").fill("definitely-wrong-token");
  await page.getByRole("button", { name: "进入后台" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "密码错误，请核对后重新输入。",
  );
  await expect(page).toHaveURL(/\/login$/);
});

test("登录 → 新增 → 列表可见 → 编辑 → 删除", async ({ page }) => {
  // 唯一标识，避免多次运行互相污染。
  const runId = Date.now().toString(36);
  const note = `E2E测试${runId}`;
  const initialAmount = "19.80";
  const editedAmount = "27.60";

  // 1. 登录：未持有凭证时受保护页面会跳转到登录页。
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("后台密码").fill(adminToken!);
  await page.getByRole("button", { name: "进入后台" }).click();

  // 2. 总览页加载成功。
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole("heading", { name: "总览" })).toBeVisible();

  // 3. 进入新增交易页。
  await page.getByRole("link", { name: "交易", exact: true }).first().click();
  await expect(page).toHaveURL(/\/transactions$/);
  await page.getByRole("link", { name: "新增交易" }).first().click();
  await expect(page).toHaveURL(/\/transactions\/new$/);

  // 4. 填写并保存（手工记账，不消耗大模型额度）。
  await page.getByLabel(/^金额/).fill(initialAmount);
  await page.getByLabel(/^分类/).selectOption("餐饮");
  await page.getByLabel(/^备注/).fill(note);
  await page.getByLabel(/^标签/).fill("e2e");
  await page.getByLabel(/^标签/).press("Enter");
  await page.getByRole("button", { name: "保存", exact: true }).click();

  // 5. 回到列表，用关键词定位这笔交易。
  await expect(page).toHaveURL(/\/transactions$/);
  await page.getByLabel("关键词").fill(note);
  // 桌面端为表格行、移动端为卡片项；getByRole 自动忽略另一形态的隐藏节点。
  const rowWithNote = () =>
    page
      .getByRole("row", { name: new RegExp(note) })
      .or(page.getByRole("listitem").filter({ hasText: note }));
  await expect(rowWithNote().first()).toBeVisible();
  await expect(rowWithNote().first()).toContainText(`¥${initialAmount}`);
  await expect(page.getByText(/第 1–1 条 \/ 共 1 条/)).toBeVisible();

  // 6. 编辑金额。
  await rowWithNote().first().getByRole("button", { name: "编辑" }).click();
  await expect(page).toHaveURL(/\/transactions\/\d+\/edit$/);
  await page.getByLabel(/^金额/).fill(editedAmount);
  await page.getByRole("button", { name: "保存修改" }).click();

  // 7. 列表中可见修改后的金额。
  await expect(page).toHaveURL(/\/transactions$/);
  await page.getByLabel("关键词").fill(note);
  await expect(rowWithNote().first()).toContainText(`¥${editedAmount}`);

  // 8. 删除：二次确认中展示金额、分类、日期与备注。
  await page.getByRole("button", { name: "删除" }).first().click();
  const dialog = page.getByRole("dialog", { name: "删除这笔交易？" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(`¥${editedAmount}`);
  await expect(dialog).toContainText("餐饮");
  await expect(dialog).toContainText(note);
  await dialog.getByRole("button", { name: "确认删除" }).click();

  // 9. 删除后列表为空（关键词仍命中不到任何记录）。
  await expect(page.getByText("没有符合条件的交易")).toBeVisible();
});
