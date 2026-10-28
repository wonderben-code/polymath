# Scientific-context compatibility baseline

`pre_scientific_context_baseline.json` was generated from the actual pushed D2
commit `6e3d75b663ff65e1f9ae3b518efcbaeb71368f56`, before the new context protocols
were integrated. It uses synthetic inputs and actual archived controllers;
there is no external model, provider or database connection.

The generator verifies all tracked backend inputs against their exact Git blobs,
rejects unexpected executable files, forces imports into the archive and traps
connections. Use a fresh archive directory: later Python imports may create
bytecode, which the verifier deliberately refuses. Generate with bytecode disabled:

```sh
git archive --format=tar --output=/tmp/polymath-d2.tar 6e3d75b663ff65e1f9ae3b518efcbaeb71368f56
mkdir /tmp/polymath-d2-exact
tar -xf /tmp/polymath-d2.tar -C /tmp/polymath-d2-exact
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/polymath-uv-cache uv --project backend run python \
  backend/tests/scientific_context_baseline.py \
  --repository . --archive /tmp/polymath-d2-exact \
  --source-commit 6e3d75b663ff65e1f9ae3b518efcbaeb71368f56 \
  --output /tmp/polymath-d2-expected.json
```

The initial repository fixture SHA-256 is
`a65c6d58bf1805cf11658418eb7af3628afd2511d6c3bb299431a067d86ac4f1`.
The fixture records the generator digest and source provenance. Formatting the
generator changed its digest, so a fresh exact-archive capture was performed;
all historical fixture values and backend provenance matched the initial capture.

Coverage includes action schemas, original native profiles/manifests, initial
messages, research outcomes, abstract/full-text artifact bases, incorporated
finite formalisation, exact Markdown and pure basis/native-step digests.
Complete persisted native-input/checkpoint/envelope hashes and applied database
lineage remain explicitly listed omissions. They require separate PostgreSQL
fixtures and must not be represented as covered by pure controller snapshots.

Never regenerate expected old values from the new implementation to make a
compatibility failure pass. Runtime tests recompute actual values and compare
them with this archived baseline; the archive remains the source of expectations.
