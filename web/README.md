# Web Layer (Skeleton)

Purpose:
- Future operator-facing workflow (parameter controls, run status, guardrails).

Boundary:
- No runtime trading logic in this layer.
- This layer must consume explicit services/contracts from `src/`.
- Official metrics governance remains canonical-path-only and must not be redefined in UI code.

Current state:
- Skeleton only. No executable workflow implementation in this change.
