# Preserved artifact identities

| Artifact | SHA-256 | Repository status |
| --- | --- | --- |
| Frozen three-class model | `134446e8e056d45cb5cce01da0343e0d88e443aa55b2dc7843a15dfc06fa63eb` | Included at `models/three-class-v2/research-model.joblib` |
| Original three-class manifest | `746fdd3ac4139382b358c38829f93efc3e46d4761099dfe2b64c650448218679` | Included beside the model |
| Phone-baseline compatibility model | `189e20e5fd67dd9a76223ba6d738896f000f42e8b0a4cabd956794864685252b` | Included at `ml/artifacts/model.joblib` |
| Premium firmware BIN | `734e2f7549b70894affc1558ff49d42bcc722aaac53dab5bf4234973a59a8bd3` | Preserved locally |
| Premium firmware ELF | `ab4fe5688fb6b15e5e7e7df5447abcb907da5f6d7eded48548dccfdcfaa30dec` | Preserved locally |
| Signed premium debug APK | `109326dbc949b314f8a8d3ff463d0cddf35ec2edd8f841c5328370b20a96209e` | Preserved locally |
| November 14 final package ZIP | `629f0dc39d02d036284acf19bc29ef7a73c22b05ecba26b6d44e75e1286d834b` | Preserved locally |

The original manifest describes its research-time status. Later hardware inference was enabled through a separate, model/device/placement-bound owner authorization. That private document and the full evaluation-verification seal are excluded from publication. The implementation continues to check the expected authorization fields and seal hash.

The fixed device identifier and placement remain in the original model contract and source. A hash-pinned reference-source metadata file contains a historical local source path. These values establish model compatibility and provenance. They carry no broker credentials or deployment access. Editing pinned metadata would invalidate the preserved source checks.
