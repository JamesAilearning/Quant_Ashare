## 1. Contract and acquisition

- [x] 1.1 Add a closed combined descriptor, exact missing-date/query bounds and backward-compatible context validation.
- [x] 1.2 Enforce exact original/observed/retained key sets, preserved evidence and unchanged durable publication/recovery.
- [x] 1.3 Add failing synthetic acquisition, evidence, repeat/recovery and refusal regressions.

## 2. Consumers

- [x] 2.1 Migrate serving, CSV/JSON/CLI and read-only UI to both-security isolation/disclosure while retaining old shapes.
- [x] 2.2 Test both-security leakage, malformed contexts, zero/variable-row exports and historical/unauthorized gates.

## 3. Review and handoff

- [x] 3.1 Converge independent local review; run serial targeted/data/full suites, imports, lint/types and strict OpenSpec checks.
- [ ] 3.2 Publish PR, request fresh Codex review on every push and merge only exact clean-reviewed head with passing CI.
- [x] 3.3 Record evidence-bound live-acceptance handoff; do not claim production restored by code tests or this PR.
