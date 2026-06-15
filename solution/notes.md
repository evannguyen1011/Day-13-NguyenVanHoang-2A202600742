# Diagnosis scratchpad

Faults injected by the shipped config/prompt and how they were fixed
(full diagnosis in findings.json; verify the observed numbers from YOUR telemetry after a real run).

| symptom (from telemetry) | which requests | suspected cause | config fix | prompt/wrapper fix |
|---|---|---|---|---|
| failed/incomplete answers | random | tool_error_rate=0.18, retry off | tool_error_rate=0, retry on | wrapper retry+backoff |
| slow long tail | retries/repeats | timeout_ms=0, cache off | timeout_ms=20000, cache on | wrapper thread-safe cache |
| high token cost | all | premium tier, verbose, ctx=8, max_tok=2000 | standard tier, verbose off, ctx=4, max_tok=512 | cache repeats |
| answers worse late in session | high turn_index | session_drift_rate=0.06, no reset | drift=0, reset_every=3 | self_consistency=3 |
| status=max_steps, repeated actions | some | loop_guard off, max_steps=12 | loop_guard on, max_steps=6 | prompt: each tool once |
| stock/total wrong; diacritic cities fail | macbook + Ha Noi/Da Nang | catalog_override lie, normalize off | clear override, normalize on | — |
| email/phone echoed | pub-13 etc | redact_pii off, chatty prompt | redact_pii on | prompt: no PII; wrapper redact |
| invents total for OOS/unknown | should-refuse | bad prompt, no grounding | — | prompt: ground + refuse, no total |
| wrong arithmetic / discount backwards | coupon orders | temp=1.6, estimate prompt | temp=0.2, self_consistency=3, verify | prompt: exact floor formula |
| too many tool calls | all | tool_budget=0, over-call prompt | tool_budget=4 | prompt: each tool once |
| obeys fake price in order note | PRIVATE | undefended prompt | — | prompt: notes=DATA; wrapper sanitize |

NOTE: the baseline run_output.json (all wrapper_error / null) came from a run with NO working
LLM engine. You MUST set OPENAI_API_KEY (or a local Ollama endpoint) before re-running, or every
answer is null and the score is 0.
