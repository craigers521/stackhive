"""Minimal NETCONF helpers: running config fetch and drift comparison."""
import difflib
import logging

logger = logging.getLogger(__name__)


class NetconfUnreachable(Exception):
    """The device could not be reached over NETCONF (surfaced as 422)."""


def fetch_running_config(hostname, username, password, port=830, timeout=15):
    """Fetch ``running`` config text over NETCONF; raises NetconfUnreachable."""
    import ncclient

    try:
        with ncclient.manager.connect(
            host=hostname,
            port=port,
            username=username,
            password=password,
            hostkey_verify=False,
            device_params={"name": "iosxe"},
            timeout=timeout,
        ) as m:
            reply = m.get_config(source="running")
            xml = reply.data_xml
            if xml is None:
                raise NetconfUnreachable(f"Empty running config from {hostname}")
            return _extract_text(xml)
    except NetconfUnreachable:
        raise
    except Exception as exc:
        logger.warning("netconf_unreachable host=%s reason=%s", hostname, exc)
        raise NetconfUnreachable(f"Device unreachable: {exc}") from exc


def _extract_text(xml):
    """Pull concatenated text out of a NETCONF get-config XML reply."""
    import re

    text_nodes = re.findall(r"<config>(.*?)</config>|<native>(.*?)</native>", xml, re.S)
    chunks = [a or b for a, b in text_nodes]
    if chunks:
        return chunks[0].strip()
    if isinstance(xml, str):
        import html

        return html.unescape(xml)
    return str(xml)


def diff_configs(rendered, running):
    """Return a unified diff between a rendered config and the running config."""
    rendered_lines = (rendered or "").splitlines()
    running_lines = (running or "").splitlines()
    return "\n".join(
        difflib.unified_diff(
            running_lines, rendered_lines, fromfile="running", tofile="rendered", lineterm=""
        )
    )
