# Provenance

`kronos.py`, `module.py` and `LICENSE` are copied from
[shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) at commit
`67b630e67f6a18c9e9be918d9b4337c960db1e9a` (2026-04-13), MIT licensed.

Upstream SHA-256 before the patch:

| file | sha256 |
|---|---|
| kronos.py | `0a5f90282e2039c2de0771473419715c845def154896dbd0f5747837e6241032` |
| module.py | `a07edbadc0e96804c8158c021bbc6063bb7cc43b34d7fc470d5c8ff2005a409f` |

`module.py` is byte-identical to upstream. `kronos.py` has two lines changed so it
imports as a package instead of through `sys.path`:

- `sys.path.append("../")` is commented out.
- `from model.module import *` became `from .module import *`.

`__init__.py` is ours. Nothing else is modified. In particular,
`KronosPredictor.predict` still AVERAGES its samples (`np.mean(preds, axis=1)`).
That is why `ohcamel_kronos.sampler` does not call it: a variance forecast needs
the spread of the samples, and the mean throws it away.

The weights are not vendored. They are downloaded from the Hugging Face Hub at
pinned revisions, which `ohcamel_kronos.cli` records in the forecast file's
sidecar.
