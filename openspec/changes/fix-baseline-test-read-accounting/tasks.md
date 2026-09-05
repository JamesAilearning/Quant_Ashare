## 1. Test correction

- [x] 1.1 Reproduce the real-artifact failure and verify the runtime stops before reading across a gap.
- [x] 1.2 Compare scan counts to recorded reader calls and pin initial/interior gap read boundaries synthetically.

## 2. Verification

- [x] 2.1 Run targeted and full logic/governance suites, lint and OpenSpec validation.
- [x] 2.2 Complete local independent review before publishing.

Validation: targeted 52 passed / 53 subtests; full logic + governance 5,304
passed / 33 skipped / 1,911 subtests. The real-artifact case was executed, not
skipped. Ruff 0.16.6 and strict OpenSpec validation passed. Independent local
review found no P0/P1/P2. No production source module was changed.
