import { Download, ReceiptText } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { useDeleteTransaction, useTransactions } from "@/api/hooks";
import { ApiError } from "@/api/client";
import type { Transaction } from "@/api/types";
import { AmountText } from "@/components/amount-text";
import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { EmptyState } from "@/components/empty-state";
import { ErrorState, describeError } from "@/components/error-state";
import { PageHeader } from "@/components/layout/page-header";
import { TransactionsFilterBar } from "@/components/transactions-filter-bar";
import { TypeBadge } from "@/components/type-badge";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Pagination } from "@/components/ui/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDebouncedValue } from "@/hooks/use-debounce";
import { useToast } from "@/components/ui/toaster";
import { KEYWORD_DEBOUNCE_MS, PAGE_SIZE } from "@/lib/constants";
import { formatLocalDateTime } from "@/lib/datetime";
import {
  downloadTransactionsCsv,
  fetchAllTransactions,
} from "@/lib/transaction-export";
import {
  DEFAULT_FILTERS,
  filtersFromSearchParams,
  filtersToApiQuery,
  filtersToSearchParams,
  hasActiveFilters,
  pageAfterDelete,
  withResetPage,
  type TransactionFilters,
} from "@/lib/query-params";
import { cn } from "@/lib/utils";

function merchantOrNote(transaction: Transaction): string {
  if (transaction.merchant && transaction.note) {
    return `${transaction.merchant} · ${transaction.note}`;
  }
  return transaction.merchant ?? transaction.note ?? "—";
}

export function TransactionsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const { toast } = useToast();

  const filters = useMemo(
    () => filtersFromSearchParams(searchParams),
    [searchParams],
  );

  // 关键词输入本地防抖，防抖后的值才进入 URL 与请求。
  const [keywordInput, setKeywordInput] = useState(filters.keyword);
  const debouncedKeyword = useDebouncedValue(keywordInput, KEYWORD_DEBOUNCE_MS);

  useEffect(() => {
    setKeywordInput(filters.keyword);
  }, [filters.keyword]);

  useEffect(() => {
    if (debouncedKeyword.trim() !== filters.keyword) {
      applyFilters({ keyword: debouncedKeyword });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedKeyword]);

  function applyFilters(patch: Partial<TransactionFilters>) {
    const next: TransactionFilters = { ...filters, ...withResetPage(patch) };
    setSearchParams(filtersToSearchParams(next), { replace: true });
  }

  function applyPage(page: number) {
    setSearchParams(filtersToSearchParams({ ...filters, page }));
  }

  function clearFilters() {
    setKeywordInput("");
    setSearchParams(filtersToSearchParams(DEFAULT_FILTERS), {
      replace: true,
    });
  }

  const query = useMemo(() => filtersToApiQuery(filters, PAGE_SIZE), [filters]);
  const list = useTransactions(query);

  const deleteMutation = useDeleteTransaction();
  const [deleteTarget, setDeleteTarget] = useState<Transaction | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);

  async function handleExportAll() {
    setIsExporting(true);
    try {
      const transactions = await fetchAllTransactions();
      downloadTransactionsCsv(transactions);
      toast({
        variant: "success",
        title: "导出成功",
        description: `共导出 ${transactions.length} 条账单记录。`,
      });
    } catch (error) {
      toast({
        variant: "destructive",
        title: "导出失败",
        description: describeError(error),
      });
    } finally {
      setIsExporting(false);
    }
  }

  function handleDeleteConfirm() {
    if (!deleteTarget) {
      return;
    }
    setDeleteError(null);
    const itemsOnPage = list.data?.items.length ?? 1;
    deleteMutation.mutate(deleteTarget.id, {
      onSuccess: () => {
        toast({
          variant: "success",
          title: "已删除",
          description: `${deleteTarget.category} ${deleteTarget.amount.toFixed(2)} 元`,
        });
        setDeleteTarget(null);
        // 当前页最后一条被删除时回退一页（分页边界处理）。
        const nextPage = pageAfterDelete(filters.page, itemsOnPage);
        if (nextPage !== filters.page) {
          applyPage(nextPage);
        }
      },
      onError: (error) => {
        if (error instanceof ApiError && error.status === 404) {
          setDeleteTarget(null);
          void list.refetch();
          toast({
            variant: "destructive",
            title: "该交易已不存在，列表已刷新。",
          });
        } else {
          setDeleteError(describeError(error));
        }
      },
    });
  }

  const total = list.data?.total ?? 0;
  const showInitialSkeleton = list.isPending;
  const isRefetching = list.isFetching && !list.isPending;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="交易"
        description="筛选条件与页码会同步到地址栏，刷新后视图保持一致。"
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={isExporting}
              onClick={() => void handleExportAll()}
            >
              <Download aria-hidden />
              {isExporting ? "正在导出…" : "导出全部账单记录"}
            </Button>
            <Link
              to="/transactions/new"
              className={buttonVariants({ size: "sm" })}
            >
              新增交易
            </Link>
          </div>
        }
      />

      <TransactionsFilterBar
        filters={filters}
        onFiltersChange={applyFilters}
        onClear={clearFilters}
        keywordValue={keywordInput}
        onKeywordValueChange={setKeywordInput}
      />

      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : showInitialSkeleton ? (
        <TransactionListSkeleton />
      ) : list.data && list.data.items.length === 0 ? (
        <EmptyState
          icon={ReceiptText}
          title={
            hasActiveFilters(filters) ? "没有符合条件的交易" : "还没有交易记录"
          }
          description={
            hasActiveFilters(filters)
              ? "试试调整筛选条件，或清空后查看全部。"
              : "从新增交易开始记录第一笔。"
          }
          action={
            hasActiveFilters(filters) ? (
              <Button variant="secondary" size="sm" onClick={clearFilters}>
                清空筛选
              </Button>
            ) : (
              <Link
                to="/transactions/new"
                className={buttonVariants({ variant: "secondary", size: "sm" })}
              >
                新增交易
              </Link>
            )
          }
        />
      ) : list.data ? (
        <div
          className={cn(
            "flex flex-col gap-4 transition-opacity",
            isRefetching && "opacity-60",
          )}
          aria-busy={isRefetching}
        >
          {/* 桌面端表格 */}
          <div className="border-border bg-card hidden overflow-hidden rounded-lg border-2 shadow-pop md:block">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-36">发生时间</TableHead>
                  <TableHead className="w-16">类型</TableHead>
                  <TableHead className="w-28 text-right">金额</TableHead>
                  <TableHead className="w-28">分类</TableHead>
                  <TableHead>商户 / 备注</TableHead>
                  <TableHead className="w-20">支付方式</TableHead>
                  <TableHead className="w-32">标签</TableHead>
                  <TableHead className="w-24 text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.data.items.map((transaction) => (
                  <TableRow key={transaction.id}>
                    <TableCell className="tnum text-muted-foreground whitespace-nowrap">
                      {formatLocalDateTime(transaction.occurred_at)}
                      {transaction.subcategory ? null : null}
                    </TableCell>
                    <TableCell>
                      <TypeBadge type={transaction.type} />
                    </TableCell>
                    <TableCell className="text-right">
                      <AmountText
                        type={transaction.type}
                        amount={transaction.amount}
                      />
                    </TableCell>
                    <TableCell>
                      {transaction.category}
                      {transaction.subcategory ? (
                        <span className="text-muted-foreground">
                          {" "}
                          / {transaction.subcategory}
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell className="max-w-48">
                      <span className="block truncate">
                        {merchantOrNote(transaction)}
                      </span>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {transaction.payment_method ?? "—"}
                    </TableCell>
                    <TableCell>
                      <div className="flex max-w-32 flex-wrap gap-1">
                        {transaction.tags.length > 0 ? (
                          transaction.tags.map((tag) => (
                            <Badge key={tag} variant="outline">
                              {tag}
                            </Badge>
                          ))
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() =>
                            navigate(`/transactions/${transaction.id}/edit`)
                          }
                        >
                          编辑
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-destructive hover:text-destructive"
                          onClick={() => {
                            setDeleteError(null);
                            setDeleteTarget(transaction);
                          }}
                        >
                          删除
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {/* 移动端卡片列表 */}
          <ul className="flex flex-col gap-2.5 md:hidden">
            {list.data.items.map((transaction) => (
              <li
                key={transaction.id}
                className="border-border bg-card rounded-lg border-2 p-3.5 shadow-pop-sm"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <TypeBadge type={transaction.type} />
                      <span className="text-sm font-medium">
                        {transaction.category}
                        {transaction.subcategory
                          ? ` / ${transaction.subcategory}`
                          : ""}
                      </span>
                    </div>
                    <p className="text-muted-foreground mt-1 truncate text-sm">
                      {merchantOrNote(transaction)}
                    </p>
                  </div>
                  <AmountText
                    type={transaction.type}
                    amount={transaction.amount}
                    className="text-base"
                  />
                </div>
                <div className="text-muted-foreground mt-2.5 flex items-center justify-between gap-2 text-xs">
                  <span className="tnum">
                    {formatLocalDateTime(transaction.occurred_at)}
                    {transaction.payment_method
                      ? ` · ${transaction.payment_method}`
                      : ""}
                  </span>
                  <div className="flex shrink-0 gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2"
                      onClick={() =>
                        navigate(`/transactions/${transaction.id}/edit`)
                      }
                    >
                      编辑
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive hover:text-destructive h-7 px-2"
                      onClick={() => {
                        setDeleteError(null);
                        setDeleteTarget(transaction);
                      }}
                    >
                      删除
                    </Button>
                  </div>
                </div>
                {transaction.tags.length > 0 ? (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {transaction.tags.map((tag) => (
                      <Badge key={tag} variant="outline">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>

          <Pagination
            page={filters.page}
            limit={PAGE_SIZE}
            total={total}
            onPageChange={applyPage}
          />
        </div>
      ) : null}

      <ConfirmDeleteDialog
        transaction={deleteTarget}
        open={deleteTarget !== null}
        pending={deleteMutation.isPending}
        error={deleteError}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteTarget(null);
            setDeleteError(null);
          }
        }}
        onConfirm={handleDeleteConfirm}
      />
    </div>
  );
}

function TransactionListSkeleton() {
  return (
    <div
      aria-label="加载中"
      className="border-border bg-card overflow-hidden rounded-lg border-2 shadow-pop"
    >
      <div className="divide-border flex flex-col divide-y">
        {Array.from({ length: 8 }, (_, index) => (
          <div key={index} className="flex items-center gap-4 px-4 py-3.5">
            <Skeleton className="h-4 w-28" />
            <Skeleton className="h-5 w-10 rounded-full" />
            <Skeleton className="ml-auto h-4 w-16" />
            <Skeleton className="h-4 w-14" />
            <Skeleton className="h-4 w-32 max-sm:hidden" />
          </div>
        ))}
      </div>
    </div>
  );
}
