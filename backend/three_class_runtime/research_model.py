"""Hash-bound, research-only loader for future v2 models. Contains no fitting path.

joblib is executable serialization. Load only a deliberately trusted local artifact
with a separately supplied SHA-256. Metadata validation is not a pickle sandbox.
"""
import json
from pathlib import Path
import re
import joblib
import numpy as np
from feature_set import (FEATURE_SET_ID, FeatureVector, CompatibilityError, contract,
                         contract_sha256, extract_window, sha, validate_contract, verify_reference_sources)

SCHEMA = 'board-research-model-v2'
LABELS = ('running', 'stationary', 'walking')
DEVICE_ID = 'rtspark-003000423433511539303938'
PLACEMENT_ID = 'sling-lcddown-v1'
ARTIFACT = 'research-model.joblib'


def require(condition, message):
    if not condition:
        raise CompatibilityError(message)


def model_metadata():
    """Metadata for a FUTURE model; not an approval record or trained artifact."""
    return {'schema': SCHEMA, 'feature_contract': contract(), 'feature_contract_sha256': contract_sha256(),
            'purpose': 'development_only', 'hardware_validated': False, 'production_enabled': False,
            'placement': PLACEMENT_ID, 'device_ids': [DEVICE_ID], 'classes': list(LABELS)}


def validate_metadata(value):
    require(isinstance(value, dict), 'Versioned research model metadata required')
    for key, expected in model_metadata().items():
        # Strict bool types prevent 0/1 being accepted as an approval flag.
        require(key in value and type(value[key]) is type(expected) and value[key] == expected,
                'Research model metadata mismatch: '+key)
    validate_contract(value['feature_contract'])


def _finite_shape(value, shape):
    a = np.asarray(value, dtype=float)
    return a.shape == shape and np.isfinite(a).all()


def validate_pipeline(pipeline):
    require(getattr(pipeline, 'n_features_in_', None) == 41, 'Estimator input dimension is not 41')
    require(tuple(getattr(pipeline, 'classes_', ())) == LABELS, 'Estimator class order mismatch')
    steps = getattr(pipeline, 'steps', ())
    require(len(steps) == 2 and [step[0] for step in steps] == ['standardscaler', 'logisticregression'],
            'Expected scaler/logistic research pipeline')
    scaler, estimator = steps[0][1], steps[1][1]
    require(getattr(scaler, 'n_features_in_', None) == 41 and getattr(estimator, 'n_features_in_', None) == 41,
            'Scaler/classifier dimension mismatch')
    require(_finite_shape(getattr(scaler, 'mean_', None), (41,)) and
            _finite_shape(getattr(scaler, 'scale_', None), (41,)) and np.all(np.asarray(scaler.scale_) > 0),
            'Invalid fitted scaler state')
    require(tuple(getattr(estimator, 'classes_', ())) == LABELS and
            _finite_shape(getattr(estimator, 'coef_', None), (3, 41)) and
            _finite_shape(getattr(estimator, 'intercept_', None), (3,)), 'Invalid fitted classifier state')
    require(callable(getattr(pipeline, 'predict_proba', None)), 'Estimator inference interface missing')


class ResearchModel:
    @classmethod
    def load(cls, folder, *, expected_feature_set_id, expected_model_sha256):
        require(expected_feature_set_id == FEATURE_SET_ID, 'Caller requested the wrong feature set')
        require(isinstance(expected_model_sha256, str) and re.fullmatch('[0-9a-f]{64}', expected_model_sha256),
                'Separately verified model SHA-256 required')
        verify_reference_sources()
        folder = Path(folder)
        manifest_path = folder/'model-manifest.json'
        require(manifest_path.is_file() and manifest_path.stat().st_size <= 65536, 'Bounded v2 manifest required; legacy bundles are rejected')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        validate_metadata(manifest)
        require(manifest.get('artifact') == ARTIFACT and manifest.get('artifact_sha256') == expected_model_sha256,
                'Model artifact declaration/hash mismatch')
        artifact = folder/ARTIFACT
        require(artifact.is_file() and artifact.stat().st_size <= 16*1024*1024 and sha(artifact) == expected_model_sha256,
                'Model artifact missing, too large or hash mismatch')
        # Wrong version/order/feature source/hash fails before deserialization.
        bundle = joblib.load(artifact)
        validate_metadata(bundle)
        require({k: bundle[k] for k in model_metadata()} == {k: manifest[k] for k in model_metadata()},
                'Manifest/bundle mismatch')
        require('pipeline' in bundle, 'Fitted pipeline missing')
        validate_pipeline(bundle['pipeline'])
        instance = cls()
        instance._pipeline = bundle['pipeline']
        instance._definition = contract_sha256()
        instance.model_sha256 = expected_model_sha256
        return instance

    def predict(self, features):
        require(isinstance(features, FeatureVector), 'Identified FeatureVector required; raw arrays are rejected')
        require(features.feature_set_id == FEATURE_SET_ID and features.definition_sha256 == self._definition and
                features.definition_sha256 == contract_sha256() and tuple(features.feature_names) == tuple(contract()['feature_names']),
                'Inference feature/model contract mismatch')
        values = np.asarray(features.values, dtype=float)
        require(values.shape == (41,) and np.isfinite(values).all(), 'Invalid inference vector')
        probabilities = np.asarray(self._pipeline.predict_proba(values.reshape(1, 41)), dtype=float)
        require(probabilities.shape == (1, 3) and np.isfinite(probabilities).all() and
                np.all(probabilities >= 0) and np.all(probabilities <= 1) and
                np.isclose(probabilities.sum(), 1, rtol=0, atol=1e-8), 'Invalid model probabilities')
        return {'activity': LABELS[int(np.argmax(probabilities[0]))],
                'probabilities': dict(zip(LABELS, map(float, probabilities[0]))),
                'feature_set_id': FEATURE_SET_ID, 'model_sha256': self.model_sha256,
                'evidence_role': 'development_only', 'hardware_validated': False, 'production_enabled': False}

    def predict_window(self, samples):
        return self.predict(extract_window(samples))
