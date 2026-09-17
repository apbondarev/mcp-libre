"""Tests that what a tool advertises is what its handler takes.

The JSON Schema in `self.tools[...]` is the only thing a client sees: it is
how a caller learns a parameter exists and whether it may be left out. A
handler and its schema are written in two places, so they drift — and the
drift is invisible from inside, because every test here and every check in
tests/live calls the bridge or the handler directly, never the schema.

Measured on this very server: `add_comment` grew `reply_to`, the handler took
it, the bridge did the work, 481 unit tests and the whole live harness passed
— and the schema never mentioned it, so over MCP the feature did not exist
and `address` was still advertised as required.
"""

import inspect

import pytest

from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from mcp_server import LibreOfficeMCPServer  # noqa: E402


@pytest.fixture
def tools():
    server = LibreOfficeMCPServer.__new__(LibreOfficeMCPServer)
    server.tools = {}
    server._register_tools()
    return server.tools


def parameters_of(handler):
    """(named, needed, takes_anything) for a tool's handler."""
    signature = inspect.signature(handler)
    named, needed, anything = set(), set(), False
    for name, parameter in signature.parameters.items():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            anything = True            # **formatting takes whatever comes
            continue
        named.add(name)
        if parameter.default is inspect.Parameter.empty:
            needed.add(name)
    return named, needed, anything


def test_every_advertised_parameter_is_one_the_handler_takes(tools):
    wrong = {}
    for name, tool in tools.items():
        named, _, anything = parameters_of(tool["handler"])
        if anything:
            continue
        declared = set(tool["parameters"].get("properties", {}))
        if declared - named:
            wrong[name] = declared - named

    assert wrong == {}, f"advertised but not taken: {wrong}"


def test_every_parameter_the_handler_takes_is_advertised(tools):
    hidden = {}
    for name, tool in tools.items():
        named, _, _ = parameters_of(tool["handler"])
        declared = set(tool["parameters"].get("properties", {}))
        if named - declared:
            hidden[name] = named - declared

    assert hidden == {}, (f"the handler takes these and no client can know: "
                          f"{hidden}")


def test_required_says_exactly_what_cannot_be_left_out(tools):
    wrong = {}
    for name, tool in tools.items():
        _, needed, anything = parameters_of(tool["handler"])
        if anything:
            continue
        required = set(tool["parameters"].get("required", []))
        if required != needed:
            wrong[name] = {"schema": sorted(required),
                           "handler": sorted(needed)}

    assert wrong == {}, f"required does not match the signature: {wrong}"


def test_every_tool_says_what_it_is_for(tools):
    for name, tool in tools.items():
        assert tool["description"].strip(), f"{name} has no description"
        assert tool["parameters"].get("type") == "object", name
