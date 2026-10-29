# Extraction eval — LLM (muse-spark-1.3, prompt v1)

Generated 2026-10-29 14:55 UTC · 40 hand-labelled code-mixed turns · input: typed transcripts.

| Metric | Value |
|---|---|
| turns | 40 |
| item precision | 100.0% |
| item recall | 100.0% |
| qty accuracy on found items | 100.0% |
| payment mode accuracy | 97.5% |
| customer accuracy | 100.0% |
| transactional accuracy | 97.5% |
| rejected | 0 |

## Caveats

- These are typed transcripts, not ASR output: they measure extraction alone. The end-to-end (speech → items) number needs recorded clips (`eval/transcribe_clips.py`).
- The first LLM run scored 0% on customers: with schema defaults present the model echoed `customer_name: null` and `action: "none"`. It also returned ₹500 as 5,000,000 paise (100x). Fixed before this run by sending a schema with every field required and described, and by rejecting any amount that matches no number actually spoken (`check_amounts_grounded`). The prompt itself was not tuned to these turns.
- 40 turns is a small set: one turn is 2.5 points.

## Turns with at least one error

| id | text | predicted items | mode | customer |
|---|---|---|---|---|
| g37 | venkat anna ka hisaab baad mein | [] | unknown | Venkat Rao |
