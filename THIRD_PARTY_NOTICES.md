# Third-party notices

This BCA182 educational project retains third-party copyright notices and bundled license files. This document adds attribution and imposes no blanket license on unrelated project source. Consult individual file headers and license files for applicable terms.

| Component | Preserved version and notices |
| --- | --- |
| RT-Thread | 4.1.1, RT-Thread Development Team, bundled Apache-2.0 license and AUTHORS |
| Mbed TLS | 3.6.7, bundled dual Apache-2.0 OR GPL-2.0-or-later license |
| Eclipse Paho embedded MQTT | RT-Thread package `pahomqtt-v1.2.0`, bundled Eclipse Public License 1.0 and source notices |
| RW007 driver | RT-Thread package `rw007-v2.0.1`, bundled Apache-2.0 license, individual build-script notices retained |
| ICM20608 driver | RT-Thread package `icm20608-v1.0.0`, bundled Apache-2.0 license |
| STM32 HAL / CMSIS | STMicroelectronics and Arm copyright/license notices retained in the preserved sources |
| lwIP | Preserved 2.0.3 source and its bundled COPYING file |
| Backend dependencies | FastAPI, Starlette, Pydantic, Uvicorn, Paho MQTT, HTTPX, NumPy, SciPy, scikit-learn, joblib and threadpoolctl, versions pinned in requirements |
| Android build/test dependencies | Android SDK and AGP, Gradle, JUnit and org.json, referenced by the unchanged Gradle configuration |

Project-specific changes include the integrated sensing/transport/freshness guards, premium LCD and Android presentation, and the three-class inference integration. Vendor-derived WLAN copies retain upstream notices. Their credential-erasure and error-unlock changes are described in the firmware source. Generated model files retain their original hashes.

## Dataset and model provenance

The separate phone-baseline compatibility model derives from Viktor Malyi's **Run or Walk**, version 2. The original pipeline windows the phone measurements and fits a classifier. Raw training data is excluded. Attribute Viktor Malyi when redistributing data-derived material, retain the noncommercial and ShareAlike conditions applicable to adaptations, and identify these processing changes. [Dataset source](https://www.kaggle.com/datasets/vmalyi/run-or-walk), [CC BY-NC-SA 4.0 terms](https://creativecommons.org/licenses/by-nc-sa/4.0/)

The phone dataset has walking/running labels and does not establish ICM-20608-G mounting, units or board accuracy. The frozen board three-class model uses the project's separately recorded device-native motion data and fixed-placement validation. Its contract, adapter, feature selector and model manifest are preserved separately. The phone-baseline artifact remains present for compatibility with the unchanged backend preflight.

The three-class evaluation and November 13 demonstration are distinct records. Attribution to upstream software or phone data makes no claim that those authors validated this board system.
