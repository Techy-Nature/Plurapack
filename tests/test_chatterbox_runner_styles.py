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


def test_runner_routes_paralinguistic_tags_to_nano():
    tags = _assignment("PARALINGUISTIC_TAGS")
    assert {"[sigh]", "[chuckle]", "[laugh]"} <= set(tags)
    source = RUNNER.read_text()
    start = source.index("def generate_parts")
    end = source.index("@modal.fastapi_endpoint", start)
    parts_function = source[start:end]
    assert "has_paralinguistic_tag(part[\"text\"])" in parts_function
    assert "self._generate_nano" in parts_function
    assert "not has_paralinguistic_tag(prompt)" in source


def _runner_namespace(*names: str) -> dict:
    """Run selected pure runner definitions without Modal or torch."""
    import re

    tree = ast.parse(RUNNER.read_text())
    wanted = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            wanted.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and (target.id in names or target.id.startswith(("PAD_", "SHORT_TEXT_")))
                for target in node.targets):
            wanted.append(node)
    namespace = {"re": re}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(RUNNER), "exec"), namespace)
    return namespace


PADDING = ("PARALINGUISTIC_TAGS", "has_paralinguistic_tag", "spoken_word_count",
           "needs_padding", "padded_text", "find_padding_cut")


def test_short_lines_are_padded_but_long_and_tagged_lines_are_not():
    runner = _runner_namespace(*PADDING)
    assert runner["needs_padding"]("You rang?")
    assert runner["needs_padding"]("It's time!")
    assert runner["needs_padding"]("test")
    assert not runner["needs_padding"]("This line has plenty of words in it.")
    assert not runner["needs_padding"]("[sigh] okay")
    assert not runner["needs_padding"]("...")
    assert runner["padded_text"]("test") == "test. ... " + runner["SHORT_TEXT_CARRIER"]
    assert runner["padded_text"]("You rang?") == "You rang? ... " + runner["SHORT_TEXT_CARRIER"]


def _frames(*segments):
    energies = []
    for seconds, energy in segments:
        energies += [energy] * round(seconds / 0.02)
    return energies


def test_cut_lands_on_the_long_pause_before_the_carrier():
    runner = _runner_namespace(*PADDING)
    # silence, "you" , short breath, "rang", long pause, carrier, silence
    energies = _frames((0.1, 0.0), (0.2, 0.3), (0.16, 0.0), (0.3, 0.3),
                       (0.5, 0.0), (2.0, 0.3), (0.2, 0.0))
    assert runner["find_padding_cut"](energies, 2) == round(0.76 / 0.02)


def test_no_cut_without_a_pause_or_carrier():
    runner = _runner_namespace(*PADDING)
    find = runner["find_padding_cut"]
    assert find([], 1) is None
    assert find(_frames((1.0, 0.0)), 1) is None
    assert find(_frames((0.1, 0.0), (3.0, 0.3)), 1) is None
    # A pause with nothing meaningful after it means the carrier was dropped.
    assert find(_frames((0.4, 0.3), (0.5, 0.0), (0.2, 0.3)), 1) is None


def test_runner_renders_every_line_through_padding():
    source = RUNNER.read_text()
    start = source.index("    def generate(self")
    end = source.index("@modal.fastapi_endpoint", start)
    body = source[start:end]
    assert "self._generate_nano(" not in body and "self._generate_original(" not in body
    assert body.count("self._render(") == 4
