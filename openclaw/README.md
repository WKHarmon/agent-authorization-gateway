# OpenClaw Integration

This directory contains the OpenClaw-side integration for the Authorization Gateway.

## Files

- **`SKILL.md`** — OpenClaw skill file. Drop this into your workspace skills directory (e.g. `~/.openclaw/workspace/skills/authorization-gateway/SKILL.md`). The agent reads this to understand the API, auth, access tiers, and usage patterns for both Gmail and SSH providers.
- **`grant-callback.js`** — Hook transform. Drop this into your OpenClaw hooks transforms directory (e.g. `~/.openclaw/hooks/transforms/grant-callback.js`). Handles approval/denial callbacks from the gateway and resumes the agent session automatically. Supports both Gmail and SSH grant types.
- **`gmail-grant.js`** — Legacy hook transform (Gmail-only). Kept for backward compatibility. New deployments should use `grant-callback.js` instead.

## Setup

### 1. Install the skill

```bash
mkdir -p ~/.openclaw/workspace/skills/authorization-gateway
cp openclaw/SKILL.md ~/.openclaw/workspace/skills/authorization-gateway/SKILL.md
```

### 2. Install the hook transform

```bash
cp openclaw/grant-callback.js ~/.openclaw/hooks/transforms/grant-callback.js
```

### 3. Register the hook in `openclaw.json`

Add an entry to the `hooks.mappings` array in your OpenClaw config (`~/.openclaw/openclaw.json`):

```json
{
  "id": "grant-callback",
  "match": { "path": "/grant-callback" },
  "deliver": false,
  "transform": { "module": "grant-callback.js" }
}
```

**Important:** `deliver: false` is required. The transform uses `action: 'wake'` to inject a system event directly into the main session. Without `deliver: false`, OpenClaw will also spawn an agent run that produces a confusing response on your messaging channel.

Then restart the OpenClaw gateway for the new mapping to take effect.

### 4. Configure callback credentials in the gateway (optional)

If your OpenClaw instance is behind Cloudflare Access, the gateway needs CF Access credentials to reach your OpenClaw hooks endpoint when firing grant callbacks. Store them in the gateway's Vault path:

```bash
bao kv patch secret/openclaw/authorization-gateway \
  CF-Access-Client-Id="<your-cf-service-token-client-id>" \
  CF-Access-Client-Secret="<your-cf-service-token-client-secret>"
```

The service token needs access to the Cloudflare Access application protecting your OpenClaw instance.

If your OpenClaw instance is not behind Cloudflare Access, you can skip this step.

### 5. Verify

Make a grant request:

```json
{
  "level": 1,
  "messageId": "...",
  "description": "Test callback",
  "callbackSessionKey": "agent:main:main"
}
```

Approve it on the approver's phone — your OpenClaw session should wake automatically.

## How It Works

```
Agent requests grant (Gmail or SSH)
  -> Gateway sends Signal notification to the approver
    -> The approver approves on phone
      -> Gateway POSTs to /hooks/grant-callback
        -> OpenClaw transform wakes agent session
          -> Agent resumes task with active grant
```

No polling required. The callback is fire-and-forget from the gateway's perspective; OpenClaw handles routing to the right session.
