"""Research-side modules, isolated from the canonical runtime.

Code here is for factor research / data exploration only. It MUST NOT be
imported by the canonical feature registry, model training, or
``daily_recommend`` — a governance gate
(``tests/governance/test_financial_pit_view_isolation.py``) enforces this the
same way the D5 gate protects ``src/factor_mining/``.
"""
