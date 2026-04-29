## MODIFIED Requirements

### Requirement: Universe artifact loader SHALL surface unreadable artifact files as typed loader errors

The universe artifact loader SHALL distinguish missing artifact files from
other OS-level read failures. Missing artifacts remain data-contract status
inputs; non-missing unreadable files SHALL raise `UniverseArtifactLoaderError`
with the artifact file path and original OSError context.

#### Scenario: artifact CSV raises OSError while opening
- **WHEN** the artifact CSV path exists conceptually but opening it raises an
  OSError other than `FileNotFoundError`
- **THEN** `UniverseArtifactLoader.load(...)` raises
  `UniverseArtifactLoaderError`
- **AND** the error message includes the artifact CSV path
