# Extraction eval — rule-based baseline (rules-v1)

Generated 2026-10-29 17:47 UTC · 40 hand-labelled code-mixed turns · input: typed transcripts.

| Metric | Value |
|---|---|
| turns | 40 |
| item precision | 97.7% |
| item recall | 95.5% |
| qty accuracy on found items | 97.6% |
| payment mode accuracy | 100.0% |
| customer accuracy | 100.0% |
| transactional accuracy | 95.0% |
| rejected | 0 |

## Caveats

- These are typed transcripts, not ASR output: they measure extraction alone. The end-to-end (speech → items) number needs recorded clips (`eval/transcribe_clips.py`).
- The 40 turns were written by the same author as the rule extractor, so the rules baseline is optimistic. One rules bug (an honorific captured as the customer name in "irfan bhai ke khate mein") was found by this eval and fixed before this run.
- Remaining rules misses are by design of exact alias matching: ASR-style misspellings ("chini", "magi") and price questions ("kitne ka hai atta?") read as purchases.

## Turns with at least one error

| id | text | predicted items | mode | customer |
|---|---|---|---|---|
| g27 | kitne ka hai atta? | [['Atta', '1']] | unknown | None |
| g34 | aata do kilo aur chini ek kilo | [['Atta', '2']] | unknown | None |
| g35 | magi teen packet | [] | unknown | None |
