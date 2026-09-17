# ADR-0010 — گردش‌کار ویرایش، نقش‌ها و audit

**Status:** Accepted · **Date:** 2026-09-12 · **Context:** تیم پژوهشی ۲–۵ نفره

## Decision
### نقش‌ها
```
admin        همه‌چیز + مدیریت کاربر + publish + archive
reviewer     بازبینی و تأیید (accepted)، publish، حل اختلاف
editor       ایجاد/ویرایش entity، مقاله، assertion → وضعیت proposed/draft
contributor  فقط پیشنهاد (proposed) بدون write مستقیم روی published
```
### وضعیت محتوا (برای entity و article هر دو)
```
draft → in_review → published → archived
                 ↘ changes_requested → draft
assertion: proposed → accepted | rejected | disputed
```
### قواعد سخت (machine-enforced)
1. هیچ entity با `status=published` بدون حداقل یک `source` معتبر publish نمی‌شود.
2. هیچ assertion `accepted` بدون حداقل یک `evidence` با `stance=supports`.
3. `published` فقط توسط `reviewer|admin` و فقط از `in_review`.
4. ویرایش یک entity منتشرشده → یک **revision جدید** در وضعیت `draft` می‌سازد؛ نسخه‌ی منتشرشده
   تا تأیید دست‌نخورده می‌ماند (reviewed-copy pattern).
5. هر write یک ردیف `audit_log(actor, action, entity_type, entity_id, before, after, request_id, at)`.

### احراز هویت
- فعلاً: session cookie + password hash (argon2) برای پنل داخلی؛ API عمومی read-only بدون auth.
- OAuth/SAML بعداً اگر لازم شد (لایه‌ی `identity` جداست تا تعویض‌پذیر باشد).

## Consequences
- مثبت: داده‌ی منتشرشده قابل اعتماد است؛ تاریخچه‌ی کامل پژوهشی؛ قابل حسابرسی.
- منفی: سرعت ورود داده کمتر می‌شود → برای bulk import از مسیر `imported_unverified` استفاده می‌کنیم
  که نیاز به تأیید دارد ولی workflow را block نمی‌کند.
