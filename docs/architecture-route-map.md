# Architecture Route Map

This page is the shortest path through the repository. It shows how one message
moves from Discord into the mapped Codex thread and how the result returns.
The Python files stay small on purpose: each one owns one part of the trip.

## Legend

- A solid arrow is the normal mapped-thread route.
- A dotted arrow is an optional path or a fallback.
- The persisted mapping is the transfer ticket between one Discord thread and
  one Codex thread.
- The mirror cursor is a bookmark in the Codex session log. It lets the next
  scan continue after the last delivered event instead of sending it again.

## End-to-end route

```mermaid
flowchart TB
    subgraph discord_in["1. Discord input"]
        direction LR
        D1["Message in a Discord<br/>channel or thread"]
        D2[("Persisted mapping<br/>Discord ID ↔ Codex thread ID")]
        D3["Prepare prompt<br/>and attachments"]
        D1 --> D2 --> D3
    end

    subgraph codex_in["2. Deliver into the mapped Codex thread"]
        direction LR
        P1["Mapped prompt delivery"]
        P2["Ensure the mapped thread<br/>is loaded or resumed"]
        P3{"Turn active?"}
        P4["Yes: turn/steer"]
        P5["No: turn/start"]
        P1 --> P2 --> P3
        P3 -->|yes| P4
        P3 -->|no| P5
    end

    subgraph codex_run["3. Codex does the work"]
        C1["Codex thread<br/>text and tool activity<br/>including Computer Use or @Chrome"]
    end

    subgraph mirror["4. Mirror Codex output"]
        direction LR
        M1["Read new rollout/session events<br/>from the saved cursor"]
        M2["Collect text, tool calls,<br/>interactive requests, images, and files"]
        M3{"Discord output type"}
        M4["Text chunks"]
        M5["Image or file attachments"]
        M6["Approval or input buttons"]
        M1 --> M2 --> M3
        M3 --> M4
        M3 --> M5
        M3 --> M6
    end

    subgraph discord_out["5. Discord receives the result"]
        D4["Same Discord thread"]
    end

    subgraph interaction["6. Interactive answer returns to Codex"]
        direction LR
        I1["User clicks approval<br/>or chooses an input answer"]
        I2["Reply to the pending request<br/>for the same Codex thread"]
        I1 --> I2
    end

    D3 --> P1
    P4 --> C1
    P5 --> C1
    C1 --> M1
    M4 --> D4
    M5 --> D4
    M6 --> D4
    M6 --> I1
    I2 -->|continue same thread| C1

    D3 -. "optional !pro rewrite" .-> O1["ChatGPT Pro skill and<br/>local-project MCP binding"]
    O1 -. "then use normal delivery" .-> P1
    P1 -. "resident/legacy fallback" .-> F1["Desktop transport fallback"]
    F1 -.-> C1
    I2 -. "approval only, when pending<br/>request IPC is unavailable" .-> F2["Codex UI permission fallback"]
    F2 -.-> C1

    classDef normal fill:#e8f2ff,stroke:#2563eb,color:#111827;
    classDef state fill:#fff7d6,stroke:#ca8a04,color:#111827;
    classDef optional fill:#f3f4f6,stroke:#6b7280,color:#111827,stroke-dasharray:5 5;
    class D1,D3,P1,P2,P3,P4,P5,C1,M1,M2,M3,M4,M5,M6,D4,I1,I2 normal;
    class D2 state;
    class O1,F1,F2 optional;
```

`Ensure the mapped thread is loaded or resumed` is deliberately broader than
`thread/resume`. The transport can reuse an already loaded subscription; it
does not need to send a new resume request for every Discord message.

## Source anchors

| Route stop | Responsibility | Start with |
| --- | --- | --- |
| Persisted mapping | Store and look up the Discord-to-Codex thread relationship | [`codex_discord_store_mirror_threads.py`](../codex_discord_store_mirror_threads.py), [`codex_discord_session_mirror.py`](../codex_discord_session_mirror.py) |
| Prompt preparation | Resolve the target and build the prompt/attachment delivery | [`codex_discord_prompt_delivery_prepare.py`](../codex_discord_prompt_delivery_prepare.py) |
| Mapped delivery | Keep delivery tied to the mapped thread, then choose a transport | [`codex_discord_prompt_mapped_delivery.py`](../codex_discord_prompt_mapped_delivery.py), [`codex_discord_prompt_transport_factory.py`](../codex_discord_prompt_transport_factory.py) |
| Load, resume, steer, or start | Use the app-server thread and turn methods | [`codex_app_server_transport_threads.py`](../codex_app_server_transport_threads.py), [`codex_app_server_transport_delivery.py`](../codex_app_server_transport_delivery.py), [`codex_app_server_transport.py`](../codex_app_server_transport.py) |
| Find new output | Read rollout events after the saved cursor | [`codex_discord_session_mirror_event_flow.py`](../codex_discord_session_mirror_event_flow.py), [`codex_discord_session_mirror_target.py`](../codex_discord_session_mirror_target.py) |
| Convert output | Turn response and tool events into Discord-ready items | [`codex_discord_session_mirror_item_collection.py`](../codex_discord_session_mirror_item_collection.py), [`codex_discord_session_mirror_activity_items.py`](../codex_discord_session_mirror_activity_items.py), [`codex_discord_session_mirror_function_items.py`](../codex_discord_session_mirror_function_items.py) |
| Send and commit | Send chunks, attachments, or buttons; then save claims and the next cursor | [`codex_discord_session_mirror_item_delivery.py`](../codex_discord_session_mirror_item_delivery.py), [`codex_discord_session_mirror_delivery_flow.py`](../codex_discord_session_mirror_delivery_flow.py), [`codex_discord_session_mirror_commit.py`](../codex_discord_session_mirror_commit.py) |
| Approval return | Turn a Discord approval button into a reply to Codex | [`codex_discord_approval_view.py`](../codex_discord_approval_view.py), [`codex_discord_approval_button_action.py`](../codex_discord_approval_button_action.py), [`codex_desktop_bridge_reply_approval.py`](../codex_desktop_bridge_reply_approval.py) |
| Input return | Turn a Discord choice button into a `requestUserInput` reply | [`codex_discord_input_choice_view.py`](../codex_discord_input_choice_view.py), [`codex_discord_input_choice_button_action.py`](../codex_discord_input_choice_button_action.py), [`codex_discord_app_server_bot_bridge.py`](../codex_discord_app_server_bot_bridge.py) |
| Optional `!pro` branch | Rewrite the prompt and bind the local project for the Pro conversation | [`codex_discord_prompt_rewrite.py`](../codex_discord_prompt_rewrite.py), [`codex_remote_mcp_binding.py`](../codex_remote_mcp_binding.py), [remote MCP details](remote-mcp.md) |

## Boundaries that matter

1. **The mapping is the identity.** A mapped Discord message must not silently
   follow whichever Codex tab happens to be visible.
2. **Steer and start are different operations.** An active turn is extended with
   `turn/steer`; an idle thread receives `turn/start`.
3. **Delivery is cursor- and claim-based.** Event claims prevent duplicate items,
   and the cursor advances after delivery so restarts can continue safely.
4. **Computer Use and `@Chrome` do not need another bridge route.** When the
   selected Codex surface records their calls and outputs as normal session/tool
   events, they use the same collection and Discord delivery path. The bridge
   does not independently control the Chrome extension, and it cannot mirror raw
   screen state that Codex did not serialize as text, an image, or a file.
5. **`!pro` is optional.** Its prompt rewrite and local-project MCP binding sit
   beside the normal route; ordinary Discord-to-Codex messages do not depend on
   them.
6. **UI automation is a fallback, not the main approval route.** Normal approval
   replies target the pending app-server request. The permission UI fallback is
   used only when that pending request cannot be reached through IPC.

## Focused verification

Run the tests closest to the route that changed:

```powershell
py -3 -m unittest `
  tests.test_codex_app_server_transport_delivery `
  tests.test_codex_discord_prompt_mapped_delivery `
  tests.test_codex_discord_steering_prompt_delivery_integration `
  tests.test_codex_discord_session_mirror_activity_items `
  tests.test_codex_discord_session_mirror_file_attachments `
  tests.test_codex_discord_session_mirror_output_integration `
  tests.test_codex_discord_pending_approval_plain_integration `
  tests.test_codex_discord_input_choice_view
```

When changing the route, verify one complete loop: send a message in a mapped
Discord thread, observe it reach that exact Codex thread, observe text or an
attachment return, and complete one approval or input button when the turn asks
for it.
