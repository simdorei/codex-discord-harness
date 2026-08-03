from typing import Final


MCP_INSTRUCTIONS: Final = (
    "Call select_project once with the project scope supplied by Codex. "
    "Then inspect or edit only that local project. Read existing files "
    "before changing them and pass their SHA-256 values to file_apply_patch. "
    "Use retrieve_image when visual inspection is needed. Use command_run only "
    "with commands returned by command_list. terminal_exec may run arbitrary "
    "user-authorized PowerShell or cmd text from the selected project working "
    "directory. Terminal window tools may open, list, capture, activate, type "
    "into, send keys to, interrupt, and close only terminal windows created by "
    "this ChatGPT session. Review repo_status and show_changes before git_commit "
    "or git_push. For computer use, first launch an isolated Chrome or blank "
    "Notepad window, list and activate that session-owned window. Only Notepad "
    "can be captured. Spend each Notepad observation ID on exactly one action "
    "within 30 seconds. Take a new screenshot after every action. Chrome allows "
    "launch, listing, activation, and emergency stop only because web pixels can "
    "contain unverifiable secret surfaces. Clipboard writes also require a fresh "
    "Notepad observation. Never use terminal or computer tools to read, request, "
    "enter, extract, or transmit passwords, OTP codes, API keys, tokens, cookies, "
    "or other credentials. Never operate ChatGPT, Codex, password managers, "
    "remote desktop, security/privacy, sign-in, UAC, or CAPTCHA surfaces; leave "
    "those steps to the user."
)


__all__ = ["MCP_INSTRUCTIONS"]
