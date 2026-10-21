# Final Ablation（同一分析器口径）

## 全阶段

| 指标 | Baseline (v1_validated) | Phase 1: grep/glob runtime | Phase 1b: workspace semantics | Phase 1c: benchmark isolation | Phase 2B-1: bash encoding | Phase 2B-2: early stop | Phase 2B-3: lazy tool exposure |
|---|---|---|---|---|---|---|---|
| Task Success Rate | 85.7% | 100.0% | 96.4% | 96.4% | 96.4% | 100.0% | 96.4% (89.3–100.0%) |
| TIMEOUT | 3 | 0 | 0 | 0 | 0 | 0 | 0 |
| median latency (s) | 9.78 | 19.37 | 6.36 | 5.29 | 5.53 | 5.12 | 4.71 |
| avg LLM calls | 5.71 | 13.20 | 6.36 | 4.68 | 4.29 | 4.43 | 3.75 |
| avg Tool calls | 6.07 | 15 | 6 | 3.96 | 3.64 | 3.39 | 2.43 |
| Tool-Free Compliance | 0/5 | 0/2 | 1/5 | 1/5 | 0/5 | 1/5 | 5/5 |
| OVER_VERIFICATION | 16 | — | 38 | 13 | 15 | 7 | 2 |
| U+FFFD tool results | 41 | 10 | 31 | 24 | 0 | 0 | 0 |

## README 四阶段版

| 指标 | Baseline (v1_validated) | Phase 2B-1: bash encoding | Phase 2B-2: early stop | Phase 2B-3: lazy tool exposure |
|---|---|---|---|---|
| Task Success Rate | 85.7% | 96.4% | 100.0% | 96.4% (89.3–100.0%) |
| TIMEOUT | 3 | 0 | 0 | 0 |
| median latency (s) | 9.78 | 5.53 | 5.12 | 4.71 |
| avg LLM calls | 5.71 | 4.29 | 4.43 | 3.75 |
| avg Tool calls | 6.07 | 3.64 | 3.39 | 2.43 |
| Tool-Free Compliance | 0/5 | 0/5 | 1/5 | 5/5 |
| OVER_VERIFICATION | 16 | 15 | 7 | 2 |
| U+FFFD tool results | 41 | 0 | 0 | 0 |

注：`Phase 2B-1` 一列同时承载了此前的 search runtime / workspace semantics / benchmark isolation 等基础设施修复，并非“仅编码修复”的净效应；逐阶段差分请见全阶段表。
