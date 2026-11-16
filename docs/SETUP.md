# Setup and offline review

## Prerequisites

| Component | Preserved configuration |
| --- | --- |
| Backend | Python 3.11, exact versions in the requirements files |
| Firmware | SCons and GNU Arm `arm-none-eabi-*` toolchain |
| Android | JDK 17, Gradle 8.11.1, Android SDK/build-tools 35 / 35.0.0 |
| Android application | AGP 8.9.2, minimum API 26, target API 35 |

Dependency installation requires an existing local cache for offline operation. The repository includes the source dependencies needed by the preserved firmware configuration. Android SDK, Gradle, toolchains and Python wheels remain external prerequisites.

## Backend environment

From the repository root, create a dedicated environment and install the pinned requirements. The following commands use a POSIX shell. On Windows, use the corresponding `.venv\Scripts\python.exe` path.

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -B -m unittest backend.tests.test_three_class backend.tests.test_exact_topics backend.tests.test_mqtt_rejection
```

For cached, offline installation, add `--no-index --find-links /path/to/wheel-cache` to the installation command. The three-class suite includes a portable C receiver test that expects the original Windows MinGW location. Run its Python-only test methods separately on a machine without that compiler. The publication review records the exact executed test set and results.

The additional `backend.tests.test_downlink` suite requires the original hash-pinned phone dataset at `ml/data/dataset.csv`. Raw dataset files are excluded from Git. Restore that existing dataset from a trusted local cache before running the additional suite. The dataset loader checks its SHA-256. These tests exercise the existing fixtures and inference path without retraining.

Model loading deserializes trusted joblib files. Verify the hashes in `ARTIFACTS.md` before loading them. The phone-baseline artifact remains required by the unchanged Codespaces preflight. It is separate from the board classifier.

## Production configuration reference

Supply these settings through the owner's existing protected configuration. The examples below describe names and requirements without supplying credentials.

| Setting | Required value or source |
| --- | --- |
| `APP_ENV` | `production` |
| `MQTT_HOST` | Existing broker DNS hostname, supplied privately |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | Existing exact-topic backend identity, supplied privately |
| `MQTT_TLS`, `MQTT_PORT` | `true`, `8883` |
| `MQTT_CLIENT_ID` | Unique backend client identity |
| `MQTT_DEVICE_IDS` | Exact device ID bound by the frozen adapter and model manifest |
| `API_TOKEN` | Separate privately supplied application bearer token |
| `MODEL_PATH` | Absolute path to `ml/artifacts/model.joblib` |
| `BOARD_MODEL_VERSION` | `three-class-v2` |
| `BOARD_MODEL_DIR` | Absolute path to `models/three-class-v2/` |
| `BOARD_PLACEMENT_ID` | `sling-lcddown-v1` |
| `BOARD_ACTIVATION_FILE` | Absolute path to the preserved owner authorization outside Git |
| `DATABASE_PATH` | Absolute path to the existing SQLite database within the workspace |
| `ALLOW_DATASET_DEMO`, `ARCHIVE_TELEMETRY` | `false`, `true` |
| `API_PORT` | `8000`, with Codespaces visibility Private |

The release-authorization file and full evaluation seal stay with the private evidence. Reuse the verified owner authorization for the original approved model/device/placement. The repository supplies no replacement authorization. The original model adapter enforces this gate.

After the owner has configured the existing approved environment, the launcher supports a network-free preflight.

```sh
.venv/bin/python -B backend/codespaces/launch.py --check
```

A separately authorized live session uses `--component all` in the existing Codespace. Keep one worker and one API, preserve the existing database and verify the current broker free quota/spend limit before resumption. Saved deployment receipts and live-session reservation scripts remain private. Original single-use evidence paths require fresh paths for a future session.

## Firmware build reference

The completed firmware uses SCons from `firmware/rtspark_integrated_v1/`. The earlier root PlatformIO scaffold is historical and is excluded from this source publication.

Set `RTT_CC=gcc`, `RTT_EXEC_PATH` to the compiler's `bin` directory, `PYTHONHASHSEED=0` and `SOURCE_DATE_EPOCH=1793404800`. Then run `scons -j4` from that firmware directory. The preserved configuration targets STM32F407ZG, RT-Thread 4.1.1, lwIP 2.0.3 and the active Mbed TLS 3.6.7 package. The configured `RTT_EXEC_PATH` overrides the historical placeholder in `rtconfig.py`.

The source-only repository omits compiled firmware and historical memory/fault records. Compare any future rebuild against the preserved BIN/ELF identities in `ARTIFACTS.md`. Flashing requires separate authorization.

## Android build reference

Set `JAVA_HOME` for JDK 17 and configure the Android SDK through the standard local SDK setting or environment. Run Gradle 8.11.1 from `android/`.

```sh
gradle testDebugUnitTest assembleDebug
```

The unchanged Gradle configuration expects a private local debug keystore at `android/.android-user/debug.keystore`. Restore it privately to reproduce the preserved signer, or supply a locally generated demonstration key at that ignored location for a separately signed build. Private keys and generated APKs remain outside publication.

The application ID is `edu.bca182.activity`, version code 2. Its historical version name is `2.0-three-class-offline-candidate`. The retained debug CA certificate is public trust material, scoped by the debug network configuration to emulator host alias `10.0.2.2`. Its private CA/server keys remain outside Git. Release builds use their preserved main trust configuration.

The demonstrated app connects to `https://10.0.2.2:8443`. The unchanged `private_android_bridge.py` accepts only the owner-authenticated loopback tunnel on port 8000 or 18000 and relays current/history GET requests. Its CLI requires the separately authorized tunnel-demo flag and existing certificate/private-key paths. Keep TLS hostname verification and enter the application token privately through the app.
