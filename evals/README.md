# Evals

`book-agent eval` runs the release evals and writes a report with the metrics, the thresholds and the harness
configuration (backend, model, prompt version and profile, git commit). Every run uses its own SQLite database under
`<output>/work/` (deleted afterwards; the rendered exports stay); the configured translation provider is used for
model calls, so a run against a paid provider costs money.

```bash
uv run book-agent eval                              # all suites -> evals/reports/<timestamp>/
uv run book-agent eval --suite terminology --suite review --output /tmp/eval-run
```

| Suite | Dataset | Metrics | Threshold |
|---|---|---|---|
| `terminology` | `datasets/terminology/momentum_book.json`: a two-chapter book with five locked terms | locked-term consistency, translation coverage | consistency >= 0.95, coverage = 1.0 |
| `review` | `datasets/review/annotated_pairs.jsonl`: source/translation pairs annotated with expected issue types | precision, recall (a problem is found on the right pair), type accuracy | precision >= 0.8, recall >= 0.7 |
| `structure` | the 56 PDF fixtures and snapshots under `tests/golden/pdf_structure` (repository checkout only) | exact snapshot matches, block-type agreement by anchor | agreement >= 0.99 |
| `export` | the terminology book, exported to merged HTML | export QA checks passed (rendered without the export gate) | pass ratio >= 0.9 |

With the default `echo` backend the translation and review numbers only prove the pipeline runs (echo does not
translate, and the echo agent model reports nothing); configure a provider to measure quality. The review set is a
seed: the roadmap asks for 200 human-annotated pairs; add lines in the same format.

The exit code is 1 when a threshold fails or a suite raises, so the command can gate a release. `export` runs
`terminology` first when it is selected alone.
