import json

import pytest

from scripts.export_p0_models import validate_p0_bundle


def test_p0_bundle_requires_both_production_model_packages(tmp_path):
    (tmp_path / "bundle-manifest.json").write_text(json.dumps({
        "packages": [{"model_id": "speed-loss-baseline", "path": "speed-loss-baseline"}],
    }))

    with pytest.raises(ValueError, match="fuel102-ensemble"):
        validate_p0_bundle(tmp_path)
