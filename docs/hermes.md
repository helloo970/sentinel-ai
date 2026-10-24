# Hermes Agent with SENTINEL

Hermes (Nous Research) uses SENTINEL through MCP. Every action goes through SENTINEL's
permission engine: reads run, low-risk actions run on their own in Autopilot, everything
else waits for your approval in the app. The emergency stop blocks Hermes too.

1. Install Hermes on the PC (official installer, PowerShell):
   `iex (irm https://hermes-agent.nousresearch.com/install.ps1)`
2. Choose the model: `hermes model` (for example OpenRouter with its own key and a spending limit).
3. Switch off Hermes' own terminal and file tools: `hermes tools`.
4. Connect SENTINEL: `sentinel hermes setup`, then restart SENTINEL and paste the printed
   `mcp_servers` block into the Hermes config (`%LOCALAPPDATA%\hermes\config.yaml`).
5. Optional: chat from your phone with `hermes gateway setup` (for example Telegram, allow only your own account).

`sentinel hermes off` removes the agent token again.
