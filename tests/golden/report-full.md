# Review feature/CHATS-16018 → develop

- Run: `20260802-2104-3f1c`
- Merge base: `abcdef123456` · HEAD: `1234567890ab`
- Model: `qwen3.6-27b`
- Files changed: 3 (+50 / -191)
- Tokens: 167 100
- From cache: 108 000 prompt tokens (67% of input)

## Summary

- 🛑 Blocker: 1
- ⚠️ Major: 1
- 🔹 Minor: 1
- 💬 Nit: 1

> Ключевое — расчёт ширины.
> Остальное косметика.

## Findings

### 🛑 F1 · Половина ширины вместо обрезки

**Where:** `Sources/UI/BubbleContentLayout.swift:88`  
**Severity:** Blocker · **Category:** correctness · **Confidence:** 82%  
**Checklist items:** 10-correctness, 80-architecture

`availableWidth / 2` считается до вычета инсетов, поэтому длинная
строка обрезается вместо переноса.

**Fix:** Считать ширину после `layoutMargins`.

> Judge (confirmed): Подтверждено чтением файла целиком.

### ⚠️ F2 · Жадный frame(maxWidth: .infinity)

**Where:** `Sources/UI/BubbleReplyBlock.swift:31`  
**Severity:** Major · **Category:** performance · **Confidence:** 60%  
**Checklist items:** 60-performance

Блок растягивается на всю доступную ширину и ломает выравнивание ответа.

### 🔹 F3 · Нет теста на пустой reply

**Where:** `Sources/UI/BubbleReplyBlock.swift`  
**Severity:** Minor · **Category:** tests · **Confidence:** 45%  
**Checklist items:** —

Ветка с пустым текстом ответа не покрыта.

**Fix:** Добавить кейс в `BubbleReplyBlockTests`.

> Judge (nitpick): Тест желателен, но не блокирует.

### 💬 F4 · Лишний import

**Where:** `Sources/UI/BubbleContentLayout.swift:12`  
**Severity:** Nit · **Category:** style · **Confidence:** 90%  
**Checklist items:** 80-architecture

`import Combine` не используется.

<details>
<summary>Rejected by the judge (2)</summary>

- `Sources/UI/Legacy.swift:5` Ключ в исходниках (false_positive) — Это идентификатор ресурса, не ключ.
- `Sources/UI/BubbleContentLayout.swift:90` Дубль про ширину (duplicate) — Совпадает с F1.

</details>

## Checklist items

| Item | Status | Findings | Turns | Tokens | Cached | Time |
|---|---|---|---|---|---|---|
| Correctness | ✅ | 1 | 9 | 124200 | 80% | 74s |
| Concurrency | ❌ | 0 | 2 | 24000 | 0% | 13s |
| Tests | ⏭ | 0 | 0 | 0 | n/a | 0s |

### Failed items

- **Concurrency**: the provider returned 502 after 3 attempts

## Changed files

```
M   +42    -7     Sources/UI/BubbleContentLayout.swift
M   +8     -120   Sources/UI/BubbleReplyBlock.swift
D   +0     -64    Sources/UI/Legacy.swift
```
