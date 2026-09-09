import { api } from "@/api/client";
import type { Transaction, TransactionListResponse } from "@/api/types";

const EXPORT_PAGE_SIZE = 100;

const CSV_COLUMNS: ReadonlyArray<{
  header: string;
  value: (transaction: Transaction) => unknown;
}> = [
  { header: "ID", value: (transaction) => transaction.id },
  { header: "类型", value: (transaction) => transaction.type },
  { header: "金额", value: (transaction) => transaction.amount.toFixed(2) },
  { header: "币种", value: (transaction) => transaction.currency },
  { header: "分类", value: (transaction) => transaction.category },
  { header: "子分类", value: (transaction) => transaction.subcategory },
  { header: "商户", value: (transaction) => transaction.merchant },
  { header: "支付方式", value: (transaction) => transaction.payment_method },
  { header: "发生时间", value: (transaction) => transaction.occurred_at },
  { header: "备注", value: (transaction) => transaction.note },
  { header: "标签", value: (transaction) => transaction.tags.join("、") },
  { header: "原始文本", value: (transaction) => transaction.raw_text },
  { header: "置信度", value: (transaction) => transaction.confidence },
  { header: "创建时间", value: (transaction) => transaction.created_at },
  { header: "更新时间", value: (transaction) => transaction.updated_at },
];

/** 分页读取全部账单，不受当前列表页筛选或页码影响。 */
export async function fetchAllTransactions(): Promise<Transaction[]> {
  const transactions: Transaction[] = [];
  let offset = 0;

  while (true) {
    const page = await api.get<TransactionListResponse>("/api/transactions", {
      limit: EXPORT_PAGE_SIZE,
      offset,
    });
    transactions.push(...page.items);

    if (page.items.length === 0 || transactions.length >= page.total) {
      return transactions;
    }
    offset += page.items.length;
  }
}

function csvCell(value: unknown): string {
  const text = value == null ? "" : String(value);
  // 防止电子表格把用户输入当作公式执行。
  const safeText = /^[=+\-@]/.test(text) ? `'${text}` : text;
  return `"${safeText.replaceAll('"', '""')}"`;
}

export function transactionsToCsv(transactions: Transaction[]): string {
  const rows = [
    CSV_COLUMNS.map(({ header }) => csvCell(header)).join(","),
    ...transactions.map((transaction) =>
      CSV_COLUMNS.map(({ value }) => csvCell(value(transaction))).join(","),
    ),
  ];
  return `\uFEFF${rows.join("\r\n")}`;
}

export function downloadTransactionsCsv(transactions: Transaction[]): void {
  const blob = new Blob([transactionsToCsv(transactions)], {
    type: "text/csv;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `账单记录-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
