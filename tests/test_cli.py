from click.testing import CliRunner

from cartography.cli import cli


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "ingest" in result.output
    assert "cluster" in result.output
    assert "stats" in result.output
