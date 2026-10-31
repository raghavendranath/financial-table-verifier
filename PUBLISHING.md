# Publishing this repository

This folder is the code release, separate from the private paper archive. It contains
no real API key, filing PDFs, private billing export or paid-response history.

1. The remote repository is `https://github.com/raghavendranath/financial-table-verifier`.
2. The Desktop folder is initialized locally on `main`. Push with your own GitHub authentication when ready; no push has been performed.
   Keep the supplied `.gitignore`. Do not add the separate private paper archive.
3. Confirm CI passes and create a versioned release/tag for `0.2.0rc1`.
4. Add the actual repository URL to `CITATION.cff` as `repository-code` and to the
   README. Do not invent a DOI; a software archive such as Zenodo can supply one
   after you publish a release, if you choose to use it.
5. Put the real repository URL and release tag or commit in the paper's short
   code/data availability statement. Keep private billing records out of the
   public repository. Share study data separately after selecting the intended
   public files and retaining applicable source/third-party notices.

The MIT license is author-approved. Dependencies and optional third-party material
retain their own rights; see `THIRD-PARTY-NOTICES.md`.

## Checks already run

- 85 application/release regression tests.
- 129 portable frozen CSV/scoring tests.
- Fresh wheel installation outside the original checkout.
- Offline CSV/JSON demos and the saved 50-report numerical recount.

See `verification/` for exact checks and limits. CI is configured; it has not been
run on GitHub yet. This is research CLI software, not a deployed financial service.
The actual paper CSV modules and full50 scoring code are preserved byte-for-byte;
portable adapters are separate from original absolute-path provenance replay.
