"""Research-only 56 -> 41 feature selection. No training or gravity subtraction.

The version name is an identifier, not a claim of complete gravity/rotation
invariance. Magnitude statistics and all gyroscope statistics remain unchanged.
"""
from dataclasses import dataclass
import hashlib
import importlib
import json
from pathlib import Path
import sys
import numpy as np

FEATURE_SET_ID = 'board_4s_v2_gravity_free'
SOURCE_EXTRACTOR_ID = 'board_4s_v1_56_unchanged'
ACCEL_AXES = ('acceleration_x', 'acceleration_y', 'acceleration_z')
CHANNELS = ACCEL_AXES + ('gyro_x', 'gyro_y', 'gyro_z', 'acceleration_magnitude', 'gyro_magnitude')
STATS = ('mean', 'std', 'min', 'max', 'rms', 'mean_abs_difference', 'lag1_correlation')
ORIGINAL_FEATURE_NAMES = tuple(f'{channel}.{stat}' for stat in STATS for channel in CHANNELS)
REMOVED_STATS = frozenset(('mean', 'min', 'max', 'rms', 'lag1_correlation'))
REMOVED_FEATURE_NAMES = tuple(name for name in ORIGINAL_FEATURE_NAMES
                              if name.split('.')[0] in ACCEL_AXES and name.split('.')[1] in REMOVED_STATS)
SELECTED_INDICES = tuple(i for i, name in enumerate(ORIGINAL_FEATURE_NAMES) if name not in REMOVED_FEATURE_NAMES)
FEATURE_NAMES = tuple(ORIGINAL_FEATURE_NAMES[i] for i in SELECTED_INDICES)
assert len(ORIGINAL_FEATURE_NAMES) == 56 and len(REMOVED_FEATURE_NAMES) == 15 and len(FEATURE_NAMES) == 41
HERE = Path(__file__).resolve().parent
_REFERENCE_HASHES = json.loads((HERE/'reference-source-hashes.json').read_text(encoding='utf-8'))['files']


class CompatibilityError(ValueError):
    """Reject ambiguous, reordered, legacy or incompatible feature/model contracts."""


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _require(condition, message):
    if not condition:
        raise CompatibilityError(message)


def contract():
    """Fresh JSON-safe copy of the exact ordered feature semantics and code hashes."""
    return {
        'schema': 'board-research-feature-contract-v2', 'feature_set_id': FEATURE_SET_ID,
        'source_extractor_id': SOURCE_EXTRACTOR_ID, 'source_dimension': 56, 'dimension': 41,
        'source_feature_names': list(ORIGINAL_FEATURE_NAMES), 'feature_names': list(FEATURE_NAMES),
        'selected_indices': list(SELECTED_INDICES), 'removed_feature_names': list(REMOVED_FEATURE_NAMES),
        'source_module_sha256': dict(_REFERENCE_HASHES), 'selector_sha256': sha(__file__),
        'input': {'points': 20, 'axes': list(CHANNELS[:6]), 'export_hz': 5,
                  'window_span_ms': [3700, 4100], 'resampling': 'unchanged board_pipeline.features_for_samples'},
        'sensor_contract': {'sensor_model': 'ICM-20608-G', 'frame_id': 'sensor_native_unmapped',
                            'calibration_id': None, 'acceleration_unit': 'g', 'gyro_unit': 'rad/s',
                            'gravity_handling': 'included'},
        'transformation': 'Select only; no centering, gravity subtraction, axis remap or new statistics',
        'limitations': ['Acceleration magnitude retains gravity and changes under constant axis offsets.',
                        'Per-axis std/MAD and gyroscope axes are not generally rotation invariant.'],
    }


def contract_sha256():
    encoded = json.dumps(contract(), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_contract(value):
    expected=json.dumps(contract(),sort_keys=True,separators=(',', ':'),allow_nan=False)
    actual=json.dumps(value,sort_keys=True,separators=(',', ':'),allow_nan=False)
    _require(isinstance(value, dict) and actual == expected, 'Feature version/order/dimension/extractor contract mismatch')


def verify_reference_sources():
    for filename, digest in _REFERENCE_HASHES.items():
        _require(sha(HERE/'legacy_reference'/filename) == digest, 'Frozen reference source changed')


def _legacy_modules():
    reference = HERE/'legacy_reference'
    verify_reference_sources()
    # Legacy modules use absolute imports. Existing same-name modules must be
    # byte-identical; otherwise fail instead of silently using another extractor.
    sys.path.insert(0, str(reference))
    try:
        modules = {}
        for name in ('download_dataset', 'baseline', 'board_pipeline'):
            existing=sys.modules.get(name)
            origin=existing.__file__ if existing is not None else importlib.util.find_spec(name).origin
            _require(sha(origin) == _REFERENCE_HASHES[name+'.py'], 'Wrong legacy source module selected')
            module = importlib.import_module(name)
            _require(sha(module.__file__) == _REFERENCE_HASHES[name+'.py'], 'Wrong legacy extractor module loaded')
            modules[name] = module
    finally:
        sys.path.remove(str(reference))
    _require(tuple(modules['baseline'].FEATURE_NAMES) == ORIGINAL_FEATURE_NAMES, 'Legacy feature order changed')
    _require(tuple(modules['board_pipeline'].FEATURE_NAMES) == ORIGINAL_FEATURE_NAMES, 'Board feature order changed')
    return modules['baseline'], modules['board_pipeline']


@dataclass(frozen=True)
class FeatureVector:
    values: tuple
    feature_names: tuple = FEATURE_NAMES
    feature_set_id: str = FEATURE_SET_ID
    definition_sha256: str = ''

    def __post_init__(self):
        _require(self.feature_set_id == FEATURE_SET_ID, 'Wrong feature-set identifier')
        _require(tuple(self.feature_names) == FEATURE_NAMES, 'Wrong feature names/order')
        _require(self.definition_sha256 == contract_sha256(), 'Wrong feature definition hash')
        array = np.asarray(self.values, dtype=float)
        _require(array.shape == (41,) and np.isfinite(array).all(), 'Exactly 41 finite scalar features required')
        object.__setattr__(self, 'values', tuple(float(x) for x in array))
        object.__setattr__(self, 'feature_names', FEATURE_NAMES)


def select_original(values, *, source_feature_names, source_extractor_id):
    _require(source_extractor_id == SOURCE_EXTRACTOR_ID, 'Wrong source extractor identifier')
    _require(tuple(source_feature_names) == ORIGINAL_FEATURE_NAMES, 'Source feature order mismatch')
    array = np.asarray(values, dtype=float)
    _require(array.shape == (56,) and np.isfinite(array).all(), 'Exactly 56 finite source features required')
    return FeatureVector(tuple(array[list(SELECTED_INDICES)]), definition_sha256=contract_sha256())


def extract_samples(samples):
    """Already-resampled 20x6 data -> unchanged original 56 -> selected 41."""
    baseline, _ = _legacy_modules()
    return select_original(baseline.extract_features(samples), source_feature_names=baseline.FEATURE_NAMES,
                           source_extractor_id=SOURCE_EXTRACTOR_ID)


def extract_window(samples):
    """Device-timestamped readings -> original admission/interpolation -> selection."""
    _, board = _legacy_modules()
    return select_original(board.features_for_samples(samples), source_feature_names=board.FEATURE_NAMES,
                           source_extractor_id=SOURCE_EXTRACTOR_ID)
