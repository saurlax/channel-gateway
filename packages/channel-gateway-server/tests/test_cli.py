from channel_gateway_server.cli import main
from click.testing import CliRunner


def test_cli_defaults_to_loopback_and_explains_options() -> None:
    result = CliRunner().invoke(main, ["serve", "--help"])
    assert result.exit_code == 0
    assert "127.0.0.1" in result.output
    assert "8310" in result.output


def test_cli_rejects_invalid_port() -> None:
    result = CliRunner().invoke(main, ["serve", "--port", "0"])
    assert result.exit_code == 2
