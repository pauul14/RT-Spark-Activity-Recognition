"""Offline-ready adapter for the immutable, C/D-tested v2 artifact.

The MQTT worker additionally requires a separately granted release authorization.
Constructing this adapter performs inference preparation only, never activation.
"""
import hashlib,json,sys
from importlib.metadata import version
from threadpoolctl import threadpool_limits
from pathlib import Path
from .downlink import THREE_CLASS_MODEL_ID

MODEL_SHA256='134446e8e056d45cb5cce01da0343e0d88e443aa55b2dc7843a15dfc06fa63eb'
FEATURE_ID='board_4s_v2_gravity_free'
DEVICE='rtspark-003000423433511539303938'
PLACEMENT='sling-lcddown-v1'
RUNTIME=Path(__file__).resolve().parent/'three_class_runtime'
DEPENDENCIES={'numpy':'2.2.6','scipy':'1.15.3','scikit-learn':'1.6.1','joblib':'1.4.2','threadpoolctl':'3.6.0'}

def require_activation(path):
    if path is None:
        raise ValueError('Three-class production activation requires separate explicit release approval')
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    if (value.get('schema')!='bca182-three-class-release-authorization-v1' or
        value.get('approved_for_hardware_inference') is not True or
        value.get('model_sha256')!=MODEL_SHA256 or value.get('placement')!=PLACEMENT or
        value.get('device_ids')!=[DEVICE] or not isinstance(value.get('reviewer'),str) or
        not value['reviewer'].strip() or value.get('validation_seal_sha256')!=
            '9c66c17c1bbe5e22bc708163282e295a1eb9326ea2c081a79446936942b85ff1'):
        raise ValueError('Explicit model/device/placement/validation-bound release approval required')

class ThreeClassBoardModel:
    def __init__(self,folder,placement):
        if placement!=PLACEMENT:
            raise ValueError('Three-class model requires sling-lcddown-v1: LCD down; USB toward wearer')
        if any(version(name)!=expected for name,expected in DEPENDENCIES.items()):
            raise ValueError('Frozen three-class dependency versions must match the validated environment')
        # The untouched selector pins itself and all original 56-feature sources.
        sys.path.insert(0,str(RUNTIME))
        import feature_set
        import research_model
        if Path(feature_set.__file__).resolve()!=RUNTIME/'feature_set.py' or Path(research_model.__file__).resolve()!=RUNTIME/'research_model.py':
            raise ValueError('Wrong model/extractor source module selected')
        pins=json.loads((RUNTIME/'integration-source-pins.json').read_text())
        if any(hashlib.sha256((RUNTIME/name).read_bytes()).hexdigest()!=h for name,h in pins.items()):
            raise ValueError('Versioned runtime source changed')
        if hashlib.sha256((Path(folder)/"model-manifest.json").read_bytes()).hexdigest()!='746fdd3ac4139382b358c38829f93efc3e46d4761099dfe2b64c650448218679':
            raise ValueError("Frozen model manifest changed")
        self.model=research_model.ResearchModel.load(folder,expected_feature_set_id=FEATURE_ID,expected_model_sha256=MODEL_SHA256)
        self.model_id=THREE_CLASS_MODEL_ID
        self.contract=feature_set.contract()['sensor_contract']

    def accepts(self,message):
        fields=json.loads(message.fingerprint)
        return message.device_id==DEVICE and all(fields.get(k)==v for k,v in self.contract.items())

    def predict(self,samples):
        # Production ingestion carries sequence at packet level. The unchanged
        # frozen extractor needs only timestamp and the six native sensor axes.
        with threadpool_limits(limits=1):
            return self.model.predict_window(samples)
