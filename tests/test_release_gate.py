from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8-sig")


def test_windows_release_gate_runs_every_required_stage() -> None:
    script = read("scripts/release_windows_gate.ps1")

    for required in (
        "git status --porcelain",
        "-m pytest -q",
        "-m ruff check",
        "-m compileall",
        "git diff --check",
        "prepare_rag_model.py --verify-only",
        "-m PyInstaller --noconfirm --clean",
        "sign_windows_release.ps1",
        "verify_windows_signature.ps1",
        "run_windows_sandbox_gate.ps1",
    ):
        assert required in script
    assert "'.env', '.auth', 'data', 'configs'" in script


def test_signature_gate_covers_all_loadable_windows_binaries() -> None:
    signing = read("scripts/sign_windows_release.ps1")
    verification = read("scripts/verify_windows_signature.ps1")

    for extension in (".exe", ".dll", ".pyd"):
        assert extension in signing
        assert extension in verification
    assert "'/fd', 'SHA256'" in signing
    assert "'/tr', $TimestampUrl" in signing
    assert "Code Signing EKU" in signing
    assert "TimeStamperCertificate" in verification
    assert "ExpectedSignerSubjectPattern" in verification
    assert "1.2.840.113549.1.1.1" in signing
    assert "1.2.840.113549.1.1.1" in verification


def test_clean_machine_gate_requires_offline_hybrid_rag_and_gui_smoke() -> None:
    guest = read("scripts/clean_machine_acceptance.ps1")
    sandbox = read("scripts/run_windows_sandbox_gate.ps1")

    assert "$search.retrieval_mode -eq 'hybrid'" in guest
    assert "-contains 'vector'" in guest
    assert "--acceptance-gui-smoke" in guest
    assert "databaseHashBefore -eq $databaseHashAfterSearch" in guest
    assert "'.env', '.auth', 'data', 'configs'" in guest
    assert "<Networking>Disable</Networking>" in sandbox
    assert "<ClipboardRedirection>Disable</ClipboardRedirection>" in sandbox
    assert "电力 软件 干净机验收" in sandbox
    assert "$quotedConfigurationPath" in sandbox
    assert "-WindowStyle Hidden" in sandbox


def test_frozen_onnx_runtime_and_vc_runtime_versions_are_reproducible() -> None:
    pyproject = read("pyproject.toml")
    spec = read("PowertradeCrawler.spec")

    assert '"fastembed==0.8.0"' in pyproject
    assert '"onnxruntime==1.28.0"' in pyproject
    for runtime in (
        "msvcp140.dll",
        "msvcp140_1.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
    ):
        assert runtime in spec
