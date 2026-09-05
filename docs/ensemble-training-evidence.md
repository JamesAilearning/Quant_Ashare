# Ensemble training evidence

The ensemble loader verifies the declared training dates against the model's
persisted training configuration. This is factual evidence binding, **not** a
new certification of the model family or its returns.

## Required artifacts

Keep each complete pipeline training run together:

```text
<run>/
  config.yaml
  artifacts/
    model.pkl
    model.pkl.meta.json
```

The existing producer writes the SHA-256 of `config.yaml` into the trainer
sidecar's `run_config_sha256`. The manifest already binds that sidecar through
`meta_sha256`. Serving now checks this whole chain and requires the config's
`train_start` and `train_end` to equal the manifest's `fit_start` and `fit_end`.
Both dates must be real, canonical `YYYY-MM-DD` strings. Config parsing and
hashing use one read of the same bytes.

## What a refusal means

- A missing/unreadable config, copied flat model file, or second config under
  `artifacts/` cannot prove which training run produced the model.
- An edited config no longer matches the trainer's digest, even if the edit
  changes only whitespace or comments.
- Plausible declared dates, including a shifted window of the same length,
  cannot replace the actual training dates recorded by the producer.

Restore the original complete run artifacts from a trusted copy, or rerun the
existing approved training and gate workflow when that evidence is unavailable.
Do not edit digests or dates merely to pass the check. Legacy artifacts without
the producer's config binding have no bypass. No live artifacts are migrated
automatically by this code change.

The check happens before deserializing the affected member. Earlier valid
members may already have loaded, but no partial ensemble is returned. Rotation
reuses the same loader and refuses an invalid candidate before replacing the
production manifest or creating a rotation backup.

## Limits

Passing this check proves that the declared training dates describe the
digest-bound configuration. It does not authenticate arbitrary pickle files,
certify a registered universe/feature/model family, validate source ancestry,
or establish that gate measurements used an approved validation window. Those
are separate controls; this change must not be presented as fixing them.

Blend mathematics, quarterly spacing, canonical qlib metrics, live deployment,
production model artifacts, and scheduled jobs are unchanged.
