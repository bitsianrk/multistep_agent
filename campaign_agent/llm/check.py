"""Preflight check for the live Tesco LRM.

    python -m campaign_agent.llm.check

Says whether the agents can run live, and if not, exactly why: credentials
missing from this terminal, the endpoint unreachable (not on the Tesco
network / VPN), the key rejected, or a working round trip with its latency.
It sends one tiny prompt; nothing about any campaign leaves the machine.
"""

from __future__ import annotations

import os
import sys
import time

from .lrm_client import HTTPTransport, LRMConfig

POWERSHELL = '$env:LRM_EMPLOYEE_ID="<your id>"\n$env:LRM_API_KEY="<your key>"'
BASH = 'export LRM_EMPLOYEE_ID=<your id>\nexport LRM_API_KEY=<your key>'


def _mask(v: str | None) -> str:
    if not v:
        return "(not set)"
    return v[:3] + "…" + v[-2:] if len(v) > 6 else "set"


def mode_reason(config: LRMConfig | None = None, mode: str | None = None) -> str:
    """One line explaining which LRM mode 'auto' resolves to, and why."""
    config = config or LRMConfig.from_env()
    mode = mode or os.getenv("LRM_MODE", "auto")
    if mode == "off":
        return "off — LRM_MODE=off (rule-based agents only)"
    if mode == "simulated":
        return "simulated — LRM_MODE=simulated"
    if config.configured:
        return f"live — {config.url.replace(config.employee_id or '', _mask(config.employee_id))}"
    missing = [n for n, v in (("LRM_EMPLOYEE_ID", config.employee_id), ("LRM_API_KEY", config.api_key)) if not v]
    return f"simulated — {' and '.join(missing)} not set in this terminal"


def check(config: LRMConfig | None = None, timeout: int = 60) -> tuple[bool, str]:
    config = config or LRMConfig.from_env()
    lines = [f"LRM_EMPLOYEE_ID  {_mask(config.employee_id)}",
             f"LRM_API_KEY      {_mask(config.api_key)}",
             f"LRM_MODEL        {config.model}",
             f"Endpoint         {config.base_url}/<employee id>/v1/lrm/"]
    if not config.configured:
        shell = POWERSHELL if os.name == "nt" else BASH
        lines += ["", "NOT LIVE: credentials are missing from this terminal. Set them in the SAME terminal",
                  "that starts the agent (variables do not carry over between terminals):", "", shell, "",
                  "then run:  python -m campaign_agent.web"]
        return False, "\n".join(lines)

    transport = HTTPTransport(LRMConfig(employee_id=config.employee_id, api_key=config.api_key, model=config.model,
                                        base_url=config.base_url, timeout_s=timeout, retries=1))
    payload = {"system_prompt": "You are a connectivity check.", "user_prompt": "Reply with the single word: OK",
               "model": config.model, "reasoning_effort": "low", "max_tokens": 20}
    started = time.perf_counter()
    try:
        out = transport.post(payload, timeout)
    except Exception as exc:  # classify the failure for the user
        msg = str(exc)
        if "401" in msg or "403" in msg:
            hint = ("the endpoint refused the request. Check LRM_API_KEY and LRM_EMPLOYEE_ID; a 403 can also mean "
                    "you are not on the Tesco network.")
        elif "504" in msg or "502" in msg or "unreachable" in msg.lower() or "timed out" in msg.lower():
            hint = "the endpoint could not be reached. Connect to the Tesco VPN and try again."
        else:
            hint = "the call failed."
        lines += ["", f"NOT LIVE: {hint}", f"Detail: {msg[:300]}"]
        return False, "\n".join(lines)
    ms = (time.perf_counter() - started) * 1000
    text = (out.get("response") or "").strip()
    lines += ["", f"LIVE OK: the LRM answered in {ms:,.0f} ms: {text[:80]!r}",
              "Start the UI in this terminal and the badge will read 'LRM live':  python -m campaign_agent.web"]
    return True, "\n".join(lines)


def main() -> int:
    ok, report = check()
    print(report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
