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
    "push; review `repo_status` and `show_changes` first. Project mode starts with "
    "`select_project` and keeps computer use limited to session-launched Chrome or "
    "Notepad. PC mode starts with `list_devices` and `select_device`, needs no project "
    "scope, and can use an absolute working folder plus existing visible Windows app "
    "windows. Use `set_working_directory` to change folders and `device_info` to "
    "confirm the selection. Every input action in either mode consumes one fresh "
    "screenshot observation. Windows secure-desktop and locked-session surfaces remain "
    "unavailable. Never "
    "use file, terminal, or computer tools to read, request, enter, extract, or "
    "transmit passwords, OTP codes, API keys, tokens, cookies, or other credentials. "
    "Never enter passwords or OTPs; leave login, CAPTCHA, and other direct identity "
    "checks to the user."
)


__all__ = ["MCP_INSTRUCTIONS"]
