import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "check_repository_hygiene.py"


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK), "--root", str(root)],
        capture_output=True,
        text=True,
    )


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / ".gitignore").write_text(
        ".hermes-qa-*\nmeeting-artifacts/\nrecordings/\n*.m4a\n*.mp3\n*.mp4\n*.wav\n*.webm\n*.ogg\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# Fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)


def test_hygiene_check_accepts_a_repository_without_meeting_artifacts(tmp_path: Path):
    _init_repo(tmp_path)

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "repository hygiene: ok" in result.stdout


def test_hygiene_check_rejects_staged_meeting_artifacts(tmp_path: Path):
    _init_repo(tmp_path)
    artifact = tmp_path / "meeting-artifacts" / "2026-09-07" / "build" / "transcript.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "-f", str(artifact.relative_to(tmp_path))], check=True
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "tracked meeting artifact: meeting-artifacts/2026-09-07/build/transcript.json" in result.stderr


def test_hygiene_check_rejects_staged_recordings_and_transcripts(tmp_path: Path):
    _init_repo(tmp_path)
    recording = tmp_path / "meeting.mp4"
    transcript = tmp_path / "transcript.md"
    recording.write_bytes(b"recording")
    transcript.write_text("# Transcript\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "meeting.mp4", "transcript.md"], check=True)

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "tracked meeting artifact: meeting.mp4" in result.stderr
    assert "tracked meeting artifact: transcript.md" in result.stderr


def test_hygiene_check_rejects_a_staged_evaluation_report(tmp_path: Path):
    _init_repo(tmp_path)
    report = tmp_path / "evaluation-report.json"
    report.write_text('{"unsupported_claims": 0}\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", report.name], check=True)

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "tracked meeting artifact: evaluation-report.json" in result.stderr


def test_hygiene_check_rejects_tracked_hermes_qa_transcripts(tmp_path: Path):
    _init_repo(tmp_path)
    transcript = tmp_path / ".hermes-qa-transcript-2026-09-07.txt"
    transcript.write_text("private meeting transcript\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", transcript.name], check=True)

    result = _run(tmp_path)

    assert result.returncode == 1
    assert f"tracked meeting artifact: {transcript.name}" in result.stderr


def test_hygiene_check_rejects_staged_dated_meeting_output(tmp_path: Path):
    _init_repo(tmp_path)
    artifact = tmp_path / "2026-09-07" / "acta.pdf"
    artifact.parent.mkdir()
    artifact.write_bytes(b"%PDF")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "2026-09-07/acta.pdf"], check=True
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "tracked meeting artifact: 2026-09-07/acta.pdf" in result.stderr


def test_hygiene_check_passes_for_this_project():
    result = _run(ROOT)

    assert result.returncode == 0, result.stderr
