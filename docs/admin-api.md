# 后台 API 契约

本文是后台管理系统的对接基线。服务默认地址示例为 `https://ledger.example.com`，实际以部署域名为准。

## 通用规则

- 后台接口统一携带 `Authorization: Bearer <ADMIN_PASSWORD>`。
- 请求与响应均使用 JSON，时间使用带时区偏移的 ISO 8601 字符串。
- 金额在 JSON 中是数字，数据库精度为两位小数。
- 错误统一返回 `success: false`、`error_code`、`message` 和 `details`。
- `401` 表示后台密码缺失或错误；`422` 表示请求数据校验失败。
- 当前后端未开放跨域 CORS。生产后台应与 API 同源部署；本地开发使用前端开发服务器代理 `/api`。

## 数据类型

### Transaction

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | integer | 主键 |
| `type` | `expense \| income` | 支出或收入 |
| `amount` | number | 大于 0 |
| `currency` | string | 三位大写代码，默认 `CNY` |
| `category` | string | 固定一级分类 |
| `subcategory` | string/null | 子分类 |
| `merchant` | string/null | 商户 |
| `payment_method` | string/null | 支付方式 |
| `occurred_at` | datetime | 发生时间 |
| `note` | string/null | 备注 |
| `tags` | string[] | 自由标签 |
| `raw_text` | string | 自然语言原文；手工创建时为空字符串 |
| `confidence` | number/null | 模型置信度；手工创建时为 null |
| `created_at` | datetime | 创建时间 |
| `updated_at` | datetime | 更新时间 |

一级分类：`餐饮`、`交通`、`购物`、`娱乐`、`学习`、`生活缴费`、`医疗`、`社交`、`住房`、`收入`、`其他`。

## 手工创建

`POST /api/transactions/manual`

```json
{
  "type": "expense",
  "amount": 18.5,
  "currency": "CNY",
  "category": "餐饮",
  "subcategory": "午餐",
  "merchant": "学校食堂",
  "payment_method": "微信",
  "occurred_at": "2026-07-27T12:20:00+08:00",
  "note": "牛肉饭",
  "tags": ["食堂", "午餐"]
}
```

必填：`amount`、`category`、`occurred_at`。成功返回 `201` 和 `Transaction`。未知字段会被拒绝。该接口不调用大模型。

## 交易列表

`GET /api/transactions`

查询参数：

- `limit`：1～100，默认 20。
- `offset`：大于等于 0，默认 0。
- `start_date`、`end_date`：`YYYY-MM-DD`，按 `DEFAULT_TIMEZONE` 计算边界。
- `category`：固定一级分类。
- `type`：`expense` 或 `income`。
- `keyword`：搜索自然语言原文、备注和商户。

```json
{
  "items": [],
  "total": 0
}
```

`total` 应用于分页器，代表应用所有筛选条件后、分页前的命中数。页码从 1 开始时：`offset = (page - 1) * limit`。

## 详情、修改、删除

- `GET /api/transactions/{id}`：返回 `Transaction`。
- `PATCH /api/transactions/{id}`：至少提供一个字段，可修改 `type`、`amount`、`category`、`subcategory`、`occurred_at`、`merchant`、`note`、`payment_method`、`tags`。`subcategory`、`merchant`、`note` 和 `payment_method` 可设为 `null` 以清空；`currency` 不可修改。
- `DELETE /api/transactions/{id}`：永久删除，成功返回 `{"success": true, "message": "Transaction deleted."}`。

删除前应在 UI 中二次确认。收到 `404 TRANSACTION_NOT_FOUND` 时刷新当前列表。

## 统计

### 今日统计

`GET /api/summaries/daily`

返回日期、支出总额、收入总额、交易笔数和支出分类汇总。该接口同时接受 `APP_API_TOKEN` 和 `ADMIN_PASSWORD`。

### 月度统计

`GET /api/summaries/monthly?year=2026&month=7`

返回：

- `expense_total`：月度支出。
- `income_total`：月度收入。
- `net_amount`：收入减支出。
- `transaction_count`：交易总笔数。
- `categories`：支出分类金额，按金额倒序。
- `daily_totals`：有支出日期的每日支出。

## 不属于后台的接口

`POST /api/transactions/parse-and-create` 仅供 iPhone 快捷指令使用，要求 `APP_API_TOKEN`。后台表单不要调用它，也不要持有 `APP_API_TOKEN`。

一次请求可以包含多笔账单，例如：

```json
{"text": "午饭23，晚饭30，笔记本33", "timezone": "Asia/Hong_Kong"}
```

模型返回 `{"transactions": [...]}`，数组内每项沿用原来的单笔解析字段。
三笔账单分别写入现有 `transactions` 表，不新增表或字段，每笔的 `raw_text` 保留整段输入。

- 成功时：`success: true`、`requires_confirmation: false`，`transactions` 包含所有已保存账单；`message` 包含笔数与每笔摘要，可直接用于快捷指令通知。
- 单笔成功时：同时保留原来的 `transaction` 字段，旧快捷指令仍可读取。
- 任意一笔缺少金额时：`success: false`、`requires_confirmation: true`，`transactions` 为空，`parsed_items` 返回全部解析结果；整批不保存，补充后重新提交完整文本。单笔缺少金额仍保留 `parsed_data`。
- 模型结果无效或数据库写入失败时：返回原有错误格式；同一批写入使用一个事务，失败时全部回滚。
