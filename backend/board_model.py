"""Optional, explicitly reviewed real-board model. No phone-model fallback."""
import hashlib
import importlib
import json
import math
from pathlib import Path
import sys
import joblib
from .config import PROJECT

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

class ValidatedBoardModel:
    def __init__(self,folder,placement):
        folder=Path(folder)
        approval=json.loads((folder/'approval.json').read_text())
        evaluation=json.loads((folder/'evaluation.json').read_text())
        model=folder/'board-model.joblib'
        if str(PROJECT/'ml') not in sys.path: sys.path.insert(0,str(PROJECT/'ml'))
        self.pipeline_module=importlib.import_module('board_pipeline')
        self.pipeline_module.verify_review_evidence(evaluation)
        if (approval.get('approved_for_hardware_inference') is not True or
            not isinstance(approval.get('reviewer'),str) or not approval['reviewer'].strip() or
            evaluation.get('hardware_validation_eligible') is not True or
            any(r.get('provenance')!='operator_attested_board' for r in evaluation.get('sessions',[])) or
            not evaluation.get('sessions') or approval.get('model_sha256')!=sha(model) or
            approval.get('evaluation_sha256')!=sha(folder/'evaluation.json') or
            evaluation.get('model_sha256')!=sha(model)):
            raise ValueError('Board model requires hash-matched real-session evaluation and explicit operator approval')
        # joblib is executable data: load ONLY this operator-trusted local artifact,
        # after hash checks. Never accept model uploads from MQTT/API clients.
        self.bundle=joblib.load(model)
        if (self.bundle.get('schema_version')!=2 or self.bundle.get('placement')!=placement or
                evaluation.get('placement')!=placement or
                self.bundle.get('device_ids')!=evaluation.get('device_ids')):
            raise ValueError('Explicit runtime placement must match the reviewed collection protocol')
        if self.bundle.get('contract')!=self.pipeline_module.CONTRACT or self.bundle.get('feature_names')!=self.pipeline_module.FEATURE_NAMES:
            raise ValueError('Board feature/units/frame contract mismatch')
        self.model_id='board-sha256:'+sha(model)+':board_4s_v1'
    def accepts(self,message):
        fields=json.loads(message.fingerprint)
        return (message.device_id in self.bundle['device_ids'] and
                all(fields.get(key)==value for key,value in self.bundle['contract'].items()))
    def predict(self,samples):
        vector=self.pipeline_module.features_for_samples(samples)
        pipeline=self.bundle['pipeline']; probabilities=pipeline.predict_proba([vector])[0]
        classes=list(pipeline.classes_)
        if set(classes)!={'walking','running'}: raise ValueError('Unexpected board model labels')
        if (len(probabilities)!=2 or any(not math.isfinite(float(p)) or not 0<=p<=1 for p in probabilities) or
                not math.isclose(float(sum(probabilities)),1,abs_tol=1e-6)):
            raise ValueError('Invalid classifier probabilities')
        activity=classes[int(probabilities.argmax())]
        return {'activity':activity,'probabilities':dict(zip(classes,map(float,probabilities)))}
