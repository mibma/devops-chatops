const path = require('path');
const envPath = path.resolve(__dirname, '../..', '.env');
require('dotenv').config({ path: envPath, override: true });

const { App, ExpressReceiver } = require('@slack/bolt');
const fetch = require('node-fetch');

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL || 'http://localhost:8000';
const BOT_TOKEN = process.env.SLACK_BOT_TOKEN;
const SIGNING_SECRET = process.env.SLACK_SIGNING_SECRET;

if (!BOT_TOKEN || BOT_TOKEN.startsWith('xoxb-your')) {
  console.warn(
    '[chatops] WARNING: SLACK_BOT_TOKEN is not set or is a placeholder.\n' +
    '  Slack command handling is DISABLED — the HTTP server will still start.\n' +
    '  Set SLACK_BOT_TOKEN (and optionally SLACK_APP_TOKEN for Socket Mode) in .env to enable it.'
  );
}

const useSocketMode = !!process.env.SLACK_APP_TOKEN;
const hasRealToken = BOT_TOKEN && !BOT_TOKEN.startsWith('xoxb-your');

// Socket Mode and a custom receiver are mutually exclusive in Bolt.
const receiver = useSocketMode ? null : new ExpressReceiver({
  signingSecret: SIGNING_SECRET || 'dev-signing-secret',
  endpoints: '/slack/events',
});

const app = hasRealToken
  ? new App(useSocketMode
      ? { token: BOT_TOKEN, socketMode: true, appToken: process.env.SLACK_APP_TOKEN }
      : { token: BOT_TOKEN, receiver })
  : null;

// Separate Express server for /health (needed when using Socket Mode)
const express = require('express');
const healthApp = express();
healthApp.get('/health', (_req, res) => res.json({ status: 'ok' }));
if (!useSocketMode && receiver) {
  receiver.router.get('/health', (_req, res) => res.json({ status: 'ok' }));
}

async function forwardToOrchestrator(action, command) {
  const res = await fetch(`${ORCHESTRATOR_URL}/commands/execute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      raw_text: command.text || '',
      action,
      user_id: command.user_id,
      channel_id: command.channel_id,
      team_id: command.team_id,
    }),
  });
  if (!res.ok) {
    throw new Error(`Orchestrator returned ${res.status}`);
  }
  return res.json();
}

function registerCommand(slashName, action) {
  if (!app) return;
  app.command(slashName, async ({ command, ack, respond, client }) => {
    await ack();
    try {
      try {
        await client.reactions.add({
          name: 'hourglass_flowing_sand',
          channel: command.channel_id,
          timestamp: command.trigger_id,
        });
      } catch (_) {
        // reaction is best-effort
      }

      const result = await forwardToOrchestrator(action, command);
      await respond({
        response_type: 'in_channel',
        text: `:rocket: ${action} request received. Tracking ID: \`${result.tracking_id}\``,
      });
    } catch (err) {
      await respond({
        response_type: 'ephemeral',
        text: `:x: Failed to process command: ${err.message}`,
      });
    }
  });
}

registerCommand('/deploy', 'DEPLOY');
registerCommand('/build', 'BUILD');
registerCommand('/status', 'STATUS');
registerCommand('/rollback', 'ROLLBACK');
registerCommand('/logs', 'LOGS');
registerCommand('/restart', 'RESTART');
registerCommand('/scale', 'SCALE');

if (app) {
  app.action(/^approve_(.+)$/, async ({ action, ack, body, respond }) => {
    await ack();
    const trackingId = action.action_id.replace(/^approve_/, '');
    await fetch(`${ORCHESTRATOR_URL}/operations/${trackingId}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approved_by: body.user.id }),
    });
    await respond({
      replace_original: false,
      text: `:white_check_mark: <@${body.user.id}> approved operation \`${trackingId}\``,
    });
  });

  app.action(/^reject_(.+)$/, async ({ action, ack, body, respond }) => {
    await ack();
    const trackingId = action.action_id.replace(/^reject_/, '');
    await fetch(`${ORCHESTRATOR_URL}/operations/${trackingId}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rejected_by: body.user.id }),
    });
    await respond({
      replace_original: false,
      text: `:no_entry: <@${body.user.id}> rejected operation \`${trackingId}\``,
    });
  });
}

(async () => {
  const port = process.env.PORT || 3000;
  if (app && useSocketMode) {
    await app.start();                   // Socket Mode: no port — Bolt manages the WebSocket
    healthApp.listen(port);             // separate Express for /health
  } else if (app) {
    await app.start(port);             // HTTP Mode: Bolt+ExpressReceiver on port
  } else {
    healthApp.listen(port);            // no token — just health endpoint
  }
  console.log(JSON.stringify({ level: 'INFO', msg: 'slack-gateway started', mode: useSocketMode ? 'socket' : 'http', port }));
})();
