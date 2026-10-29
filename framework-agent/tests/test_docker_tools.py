from __future__ import annotations

import pytest
from framework_agent.docker_tools import DockerToolbox, run_isolated_python
from framework_agent.tools import ToolTimeout


def test_container_blocks_outbound_network_and_writes_to_root() -> None:
    result = run_isolated_python(
        "import urllib.request, pathlib\n"
        "try:\n"
        "    urllib.request.urlopen('http://example.com', timeout=2)\n"
        "    print('network-ok')\n"
        "except Exception:\n"
        "    print('network-blocked')\n"
        "try:\n"
        "    pathlib.Path('/etc').joinpath('agent-test').write_text('x')\n"
        "    print('root-write-ok')\n"
        "except Exception:\n"
        "    print('root-write-blocked')\n",
        timeout_s=15,
    )
    assert result.exit_code == 0
    assert "network-blocked" in result.stdout
    assert "root-write-blocked" in result.stdout


def test_docker_toolbox_returns_payload_and_retries_timeout() -> None:
    toolbox = DockerToolbox("docker-calc", fault_type="tool_timeout")
    with pytest.raises(ToolTimeout):
        toolbox.execute("calculator", {"expression": "1+1"})
    result = toolbox.execute("calculator", {"expression": "2*21"})
    assert result["result"] == 42


def test_unsupported_tool_is_rejected() -> None:
    with pytest.raises(ValueError):
        DockerToolbox("bad-tool").execute("email_send", {})
