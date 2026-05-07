## 1. Registry

- [x] 1.1 Add feature-handler registry helpers and default Alpha158 factory.
- [x] 1.2 Update builder validation to use the registered handler list.
- [x] 1.3 Update builder construction to call the selected registered factory.

## 2. Tests

- [x] 2.1 Add tests for default Alpha158 registry state.
- [x] 2.2 Add tests for custom handler registration and unknown handler rejection.
- [x] 2.3 Add tests proving arbitrary dotted paths are rejected when unregistered.

## 3. Verification

- [x] 3.1 Run targeted feature dataset builder tests.
- [x] 3.2 Run `openspec validate add-feature-handler-registry --strict`.
