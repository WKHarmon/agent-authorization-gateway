// grant-callback.js
// Receives grant approval/denial callbacks from the OpenClaw Authorization Gateway.
// The gateway POSTs here when the approver approves or denies a grant request.
//
// Uses action:'wake' to inject a system event into the main session.
// The hook mapping MUST set deliver:false to prevent a redundant agent run.
//
// Payload shape:
// {
//   "grantId": "g_...",
//   "resourceType": "gmail" | "ssh",
//   "level": 1,
//   "status": "active" | "denied",
//   "expiresAt": "2026-03-23T20:42:01Z"  // omitted on denial
// }

export default function (payload) {
  const p = payload?.payload ?? payload;

  const grantId      = p?.grantId  ?? p?.grant_id ?? 'unknown';
  const resourceType = p?.resourceType ?? 'gmail';
  const level        = p?.level    ?? '?';
  const status       = p?.status   ?? 'unknown';
  const expiresAt    = p?.expiresAt;

  const label = resourceType === 'ssh' ? 'SSH' : 'Email';

  if (status === 'active') {
    const expiry = expiresAt
      ? new Date(expiresAt).toUTCString()
      : 'unknown';
    return {
      action: 'wake',
      text: `${label} grant ${grantId} approved (Level ${level}, expires ${expiry}).`,
      mode: 'now',
    };
  }

  if (status === 'denied') {
    return {
      action: 'wake',
      text: `${label} grant ${grantId} denied (Level ${level}).`,
      mode: 'now',
    };
  }

  // Unexpected status — log and no-op
  console.log(`[grant-callback] Unexpected status "${status}" for grant ${grantId}`);
  return null;
}
