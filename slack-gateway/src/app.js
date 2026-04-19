require('dotenv').config();
const { App, ExpressReceiver } = require('@slack/bolt');
const fetch = require('node-fetch');

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL || 'http://orchestrator:8000';

const receiver = new ExpressReceiver({
  signingSecret: process.env.SLACK_SIGNING_SECRET,
  endpoints: '/slack/events',
});

const app = new App({
  token: process.env.SLACK_BOT_TOKEN,
  receiver,
  socketMode: !!process.env.SLACK_APP_TOKEN,
  appToken: process.env.SLACK_APP_TOKEN,
});

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
        // reaction is best-effort; trigger_id isn't always a message ts
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

receiver.router.get('/health', (_req, res) => res.json({ status: 'ok' }));

(async () => {
  const port = process.env.PORT || 3000;
  await app.start(port);
  console.log(JSON.stringify({ level: 'INFO', msg: 'slack-gateway started', port }));
})();
