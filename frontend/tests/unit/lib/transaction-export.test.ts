import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import {
  fetchAllTransactions,
  transactionsToCsv,
} from "@/lib/transaction-export";

import { makeTransaction, ORIGIN } from "../helpers/fixtures";
import { server } from "../helpers/server";

describe("账单导出", () => {
  it("分页读取全部账单且不携带当前页面筛选", async () => {
    const seenUrls: string[] = [];
    server.use(
      http.get(`${ORIGIN}/api/transactions`, ({ request }) => {
        const url = new URL(request.url);
        seenUrls.push(`${url.pathname}${url.search}`);
        const offset = Number(url.searchParams.get("offset"));
        return HttpResponse.json({
          items:
            offset === 0
              ? [makeTransaction({ id: 1 }), makeTransaction({ id: 2 })]
              : [makeTransaction({ id: 3 })],
          total: 3,
        });
      }),
    );

    const transactions = await fetchAllTransactions();

    expect(transactions.map(({ id }) => id)).toEqual([1, 2, 3]);
    expect(seenUrls).toEqual([
      "/api/transactions?limit=100&offset=0",
      "/api/transactions?limit=100&offset=2",
    ]);
  });

  it("生成带 BOM 的 CSV，并正确处理引号、换行与公式前缀", () => {
    const csv = transactionsToCsv([
      makeTransaction({
        merchant: '商户"甲',
        note: "第一行\n第二行",
        raw_text: "=1+1",
        tags: ["工作", "报销"],
      }),
    ]);

    expect(csv.startsWith("\uFEFF")).toBe(true);
    expect(csv).toContain('"商户""甲"');
    expect(csv).toContain('"第一行\n第二行"');
    expect(csv).toContain('"工作、报销"');
    expect(csv).toContain('"\'=1+1"');
  });
});
