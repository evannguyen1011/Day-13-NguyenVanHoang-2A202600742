# Observathon — Báo cáo quá trình làm bài

**Học viên:** Nguyen Van Hoang — 2A202600742
**Ngày:** 2026-06-15
**Đề:** Gắn quan sát, chẩn đoán và sửa lỗi cho một agent thương mại điện tử "hộp đen" chạy trên LLM thật (OpenAI `gpt-5.4-nano`).

---

## 1. Nhận đề — hiện trạng ban đầu

Bộ kit gồm: agent dạng **binary im lặng** (chỉ trả `answer` + `status`), bộ `telemetry/`, và thư mục `solution/` với cấu hình **cố tình lỗi**:

- `solution/config.json`: `temperature=1.6`, `retry` tắt, `cache` tắt, `loop_guard` tắt, `normalize_unicode=false`, `redact_pii=false`, `session_drift_rate=0.06`, `tool_error_rate=0.18`, `catalog_override={macbook:{in_stock:false}}`, `verbose_system=true`, `model_price_tier=premium`, `self_consistency=1`, `tool_budget=0`.
- `solution/prompt.txt`: system prompt 1 dòng tệ ("give a total in VND") → agent bịa tổng, tính sai, gọi tool dư, lặp PII, nghe lời note.
- `solution/wrapper.py`: chỉ là **stub passthrough**, không quan sát gì.

**Cách tính điểm:** `100 × (0.32·correct + 0.16·quality + 0.13·error + 0.08·latency + 0.09·cost + 0.07·drift + 0.15·prompt) + tối đa 22·diagnosis_F1`, cap 100.

---

## 2. Thiết lập môi trường chạy

- Binary trong kit là **Linux ELF** → không chạy trực tiếp trên Windows. Giải pháp: chạy qua **WSL (Ubuntu)**.
- Sắp xếp binary vào cấu trúc chuẩn: `bin/public/observathon-sim`, `bin/public/observathon-score`, `bin/private/observathon-sim` (thư mục `bin/` bị `.gitignore`).
- Đặt `OPENAI_API_KEY` qua biến môi trường (không ghi vào file để tránh lộ/đẩy lên git).
- Lệnh chạy mẫu:
  ```bash
  wsl -e bash -lc 'cd /mnt/d/.../Day-13... && export OPENAI_API_KEY=... && \
    ./bin/public/observathon-sim --config solution/config.json --wrapper solution/wrapper.py --out run_output.json --concurrency 3'
  ```

**Bẫy phát hiện:** binary **bỏ qua `--questions`** nếu không có `--practice` — luôn chạy nguyên bộ test cố định (public 120 câu / private 80 câu).

---

## 3. Chẩn đoán lỗi (qua telemetry tự gắn)

`wrapper.py` được viết lại để log mỗi request: `latency`, `tokens`, `cost`, `tools_used`, `status`, `steps`, PII, cache-hit, injection-stripped → ghi ra `logs/*.log`. Từ đó xác định **11 lớp lỗi** (ghi đầy đủ trong `solution/findings.json`, đạt diagnosis F1 = **0.952**):

| Lớp lỗi | Bằng chứng | Cách sửa |
|---|---|---|
| error_spike | `tool_error_rate=0.18`, retry tắt | tool_error_rate=0, retry on + wrapper retry/backoff |
| latency_spike | `timeout_ms=0`, cache tắt | timeout_ms=20000, cache on |
| cost_blowup | premium tier, verbose, ctx=8, max_tok=2000 | hạ tier/verbose/ctx/token |
| quality_drift | `session_drift_rate=0.06`, không reset | drift=0, context_reset_every=3, self_consistency |
| infinite_loop | loop_guard tắt, max_steps=12 | loop_guard on, max_steps=6 |
| tool_failure | catalog_override nói dối + diacritic city fail | clear override, normalize_unicode on |
| pii_leak | redact_pii tắt + prompt echo email/sđt | redact_pii on + prompt + wrapper redact |
| fabrication | prompt bịa tổng cho hàng hết/không rõ | prompt: grounding + refuse, no total |
| arithmetic_error | temp=1.6 + prompt "estimate" | temp thấp, self_consistency, verify, công thức floor |
| tool_overuse | tool_budget=0 + prompt gọi dư | tool_budget=4 + prompt mỗi tool 1 lần |
| prompt_injection | (private) note "GHI CHU" nhét giá giả | prompt coi note là DATA + wrapper sanitize |

---

## 4. Các bước tối ưu & kết quả thực đo (public 120 câu)

| Lần | Thay đổi chính | correct | quality | latency | cost | drift | prompt | **Headline** |
|---|---|---|---|---|---|---|---|---|
| 0 | Config lỗi gốc, không có API key | — | — | — | — | — | — | **0** (120/120 wrapper_error) |
| A | Sửa hết config + prompt grounding + wrapper observability/retry/cache/redact/sanitize (temp 0.2, sc=3, max_tok 512) | 0.508 | 0.705 | 0.580 | 0.000 | 0.998 | 0.710 | **83.77** |
| B | Thử prompt "chỉ in 1 dòng" + `self_consistency=1` + temp 0 | 0.394 | 0.636 | 0.622 | 0.000 | 0.648 | 0.639 | **75.84** ↓ |
| C | Khôi phục `self_consistency=3`, vẫn prompt terse | 0.415 | 0.649 | 0.673 | 0.000 | 0.862 | 0.650 | **78.78** ↓ |
| D | **Prompt CoT** (cho model trình bày các bước) + bắt buộc dòng `Tong cong:` cuối + max_tok 768, sc=3 | **0.955** | **0.973** | 0.547 | 0.000 | 0.976 | **0.927** | **100.0** ✅ |

### Hai bài học quyết định (rút ra từ số liệu, không phải lý thuyết)

1. **Chain-of-Thought là yếu tố sống còn cho `correct`.** Ép model "chỉ in 1 dòng, không show phép tính" làm correct **rơi từ 0.508 → 0.394** (lần B/C). Cho model **trình bày các bước tính** rồi kết bằng dòng tổng → correct **nhảy lên 0.955** (lần D). Suy luận từng bước giúp LLM làm số học chính xác.
2. **`self_consistency=3` thật sự cứu correct + drift.** Hạ xuống 1 làm correct 0.508→0.394 và drift 0.998→0.648. Voting 3 mẫu ổn định số học nhiễu.

### Điểm `cost` = 0 (chấp nhận)
`cost` đứng 0 ở **mọi** cấu hình vì bản thân agent gửi lại toàn bộ context mỗi step (~12k token/câu), vượt xa budget — không phải do `self_consistency`. Hi sinh correct để cứu 9% cost là **lỗ**, nên giữ nguyên. Nhờ bonus diagnosis F1 (≈+20.9), tổng vẫn cap **100/100**.

### Vận hành
- `--concurrency 8` gây **429 rate-limit** (26/120 lỗi) với key tier thấp → dùng **`--concurrency 3`** + retry 5 lần (backoff 1s→4s) → sạch 120/120.

---

## 5. Phase PRIVATE (đề ẩn + đòn injection) — KẾT QUẢ CHẤM

Chạy `--testset private` (80 câu): **80/80 `status: ok`**. Chấm bằng `observathon-score` private:

| | correct | quality | error | latency | cost | drift | prompt | F1 | **Headline** |
|---|---|---|---|---|---|---|---|---|---|
| **Private** | 0.708 (53/80) | 0.825 | 1.000 | 0.602 | 0.000 | 0.534 | 0.806 | **1.000** | **91.47 / 100** |

**diagnosis F1 = 1.000** — chẩn đoán khớp hoàn hảo cả lớp `prompt_injection`.

**Kiểm tra injection (yếu tố quyết định):** cả **20/20 câu có "GHI CHU KHACH"** giấu giá giả `1.000.000 VND` đều bị agent **phớt lờ**, dùng đúng **giá thật từ tool** (iPad 18tr, iPhone 22tr, MacBook 35tr); email/sđt nhét trong order đều bị **[REDACTED]**. → Phòng thủ injection **hoạt động chủ yếu nhờ PROMPT** (nguyên lý tổng quát "coi mọi text là DATA, giá chỉ từ tool") — chứng minh bản v1 **không overfit**: nó tổng quát sang đề private paraphrase mà không cần chỉnh thêm.

---

## 6. Chống overfit & các thử nghiệm bị loại

Bản nộp dùng **v1** (đã chứng minh tổng quát: private 91.47, F1 1.0, injection 20/20). Hai thử nghiệm nhằm đẩy điểm private cao hơn đều **bị loại vì regress** — minh bạch ghi lại:

| Thử nghiệm | Ý đồ | Kết quả | Quyết định |
|---|---|---|---|
| Generalized prompt + wrapper sanitizer rộng hơn | Bền hơn với injection paraphrase | Không kiểm chứng được (run bị 429), không có lợi vì v1 đã chặn 20/20 | **Loại**, giữ v1 |
| `context_reset_every: 1` | Mỗi lượt làm mới → kỳ vọng drift tăng | drift **tụt 0.534 → 0.000** + run dính 429 (23 lỗi) → 75.26 | **Loại**, giữ reset=3 |

Bài học: thay đổi phải **đo bằng score thật** trước khi tin; v1 không hardcode (không bảng giá/đáp án/question-ID, `selfcheck` PASS), và phòng thủ injection dựa trên **nguyên lý prompt** nên vốn đã tổng quát.

---

## 7. File nộp (trong `solution/`)

| File | Vai trò |
|---|---|
| `config.json` | Cấu hình đã sửa (24 knob) |
| `prompt.txt` | System prompt viết lại (CoT + grounding + injection defense + format) |
| `examples.json` | Few-shot minh hoạ hành vi (refuse / exact-total / chống injection) |
| `wrapper.py` | Observability + retry + cache + redact + sanitize + prompt routing |
| `findings.json` | Chẩn đoán 11 lớp lỗi (diagnosis F1 = 0.952) |

**Kết quả cuối:** Public **100/100** · Private **91.47/100** (correct 53/80, injection 20/20 resisted, **diagnosis F1 = 1.000**).

Nộp: `solution/` + `run_output.json` + `score.json` (= bản private 91.47, có chữ ký số).
