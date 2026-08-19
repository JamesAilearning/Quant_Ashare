## 1. Fail-Closed Signal Version Boundary

- [x] 1.1 Name the daily-recommendation producer schema version and add the
  qlib-free workbench counterpart.
- [x] 1.2 Reject missing, boolean, non-integer, and unsupported artifact
  versions before workbench provenance or cadence classification.
- [x] 1.3 Add regression coverage that pins the producer/UI version contract
  and refuses incompatible artifacts.

## 2. Non-Mutating Operational Summary

- [x] 2.1 Extract the existing all-jobs pagination loop so reconciling and
  non-mutating readers share filtering, sorting, and normalisation.
- [x] 2.2 Add an explicitly named read-only all-jobs reader and use it from
  Today Workbench only.
- [x] 2.3 Add regression coverage proving the read-only reader leaves a
  confirmed-dead running job artifact unchanged while the standard list still
  reconciles it.

## 3. Validation

- [x] 3.1 Run targeted logic tests and import smoke checks for every touched
  source module.
- [ ] 3.2 Run repository logic and governance tests plus strict OpenSpec
  validation.
- [x] 3.3 Perform the required local P0-P3 review loop and resolve all P0-P2
  findings before publishing.
