from typer.testing import CliRunner

from meeting_pipeline.cli import app

runner = CliRunner()

EXPECTED_COMMANDS = [
    "inspect",
    "transcribe",
    "extract-context",
    "draft",
    "finalize",
    "validate",
    "process",
    "serve",
]


def test_help_lists_every_pipeline_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    for command in EXPECTED_COMMANDS:
        assert command in result.output


def test_every_command_has_its_own_help():
    for command in EXPECTED_COMMANDS:
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0, f"{command}: {result.output}"


def test_unknown_command_fails():
    assert runner.invoke(app, ["definitely-not-a-command"]).exit_code != 0
