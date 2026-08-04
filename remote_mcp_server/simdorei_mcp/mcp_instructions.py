from typing import Final


MCP_INSTRUCTIONS: Final = (
    "Use this connector directly in normal Chat with Pro reasoning; do not switch "
    "to Work. Call `select_project` once with the project scope supplied by Codex. "
    "Read existing project files before changing them and pass their SHA-256 values "
    "to `file_apply_patch`. Project file tools remain bound to the selected project. "
    "Use `retrieve_image` when visual inspection is needed. `terminal_exec` may run "
    "unrestricted user-authorized PowerShell, cmd, sh, or bash text, launch child "
    "processes, and use an explicit absolute working directory outside the selected "
    "project. Use `terminal_window_open`, `terminal_window_list`, "
    "`terminal_window_capture`, `terminal_window_activate`, `terminal_window_type`, "
    "`terminal_window_keys`, `terminal_window_interrupt`, and "
    "`terminal_window_close` to control visible terminal windows created by this "
    "ChatGPT session. Terminal commands may perform verification and Git commit or "
    "push; review `repo_status` and `show_changes` first. For computer use, launch an "
    "isolated Chrome or blank Notepad window before using the advertised desktop "
    "control tools. Only Notepad can be captured or receive screenshot-bound editing "
    "actions; Chrome allows launch, listing, activation, and emergency stop. Never "
    "use file, terminal, or computer tools to read, request, enter, extract, or "
    "transmit passwords, OTP codes, API keys, tokens, cookies, or other credentials. "
    "Never operate ChatGPT, Codex, password managers, remote desktop, sign-in, UAC, "
    "CAPTCHA, or security/privacy surfaces; leave those steps to the user."
)


__all__ = ["MCP_INSTRUCTIONS"]
