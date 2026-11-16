# Publication scope

The publication candidate is an isolated copy of the November 14 verified package. Its selected application source, model files and vendor build sources retain their original bytes. Runtime source directories were placed together at the repository root. The original pinned ML requirements file was restored from the preserved project because the package's backend requirements referenced it without including it.

## Included

- Premium RT-Thread application and board build configuration.
- Matching kernel, active lwIP and firmware package source with original notices.
- FastAPI/MQTT/SQLite backend, frozen feature extractor and selected synthetic offline tests.
- Hash-verified three-class and phone-baseline model artifacts with required metadata.
- Premium Android app source, contract tests and loopback bridge source.
- Sanitized technical documentation and attribution.

## Kept outside publication

- Wi-Fi and broker credentials, application tokens and account credentials.
- `.env` files, signing keystores, private certificate keys and generated secrets.
- Original release authorization, full evaluation seal, deployment receipts, cloud logs and private session evidence.
- UART/history/XML records, personal recordings and database contents.
- RAM/core/fault dumps, emulator userdata, environment caches and generated binaries.
- Full transfer ZIPs, historical backups, obsolete firmware packages and vendor test-key fixtures.

The included Android debug CA is a public certificate. Mbed TLS private-key marker strings are parser constants with no key bodies. Test credentials and unsafe URL examples are synthetic fixtures. The model contract's device identifiers and hash-pinned historical source-path metadata remain byte-identical.

The `.gitignore` provides defense in depth. The approved publication must use the externally reviewed exact-file manifest and a final staged-content check. A future change requires a new scan. Original local projects and evidence remain separate from this candidate.
