import ast
from pathlib import Path


RUNNER = Path(__file__).parents[1] / "deployment" / "chatterbox_runner_v05.py"


def _assignment(name: str):
    tree = ast.parse(RUNNER.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found")


def test_builtin_semantic_presets_do_not_time_stretch():
    presets = _assignment("STYLE_GENERATION_PRESETS")
    assert set(presets) == {"normal", "emphasis", "mumble", "whisper"}
    assert all("speed_factor" not in settings for settings in presets.values())


def test_whisper_and_mumble_avoid_extreme_generation_controls():
    presets = _assignment("STYLE_GENERATION_PRESETS")
    assert presets["whisper"]["exaggeration"] >= 0.25
    assert presets["whisper"]["cfg_weight"] >= 0.30
    assert presets["mumble"]["exaggeration"] >= 0.30
    assert presets["mumble"]["cfg_weight"] >= 0.25


def test_runner_keeps_style_processing_non_temporal():
    source = RUNNER.read_text()
    start = source.index("def apply_style_effect")
    end = source.index("\n\n@app.cls", start)
    style_function = source[start:end]
    assert "time_stretch" not in style_function
    assert "interpolate" not in style_function
    assert "resample" not in style_function
    assert "_moving_average" in style_function
    assert "randn_like" not in style_function
    assert "_breath_component" not in source


def test_explicit_speed_factor_remains_supported_separately():
    source = RUNNER.read_text()
    assert '"speed_factor"' in source
    assert "librosa.effects.time_stretch" in source
    assert "Built-in semantic styles deliberately do not use this path" in source
