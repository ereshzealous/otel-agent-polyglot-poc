'use strict';

const express = require('express');
const config  = require('./config');
const { register } = require('./metrics');
const { startKafkaConsumer } = require('./kafka/consumer');
const logger  = require('./logger');

const app = express();
app.use(express.json());

app.get('/health', (req, res) => {
  res.json({ status: 'ok', service: 'notification-service' });
});

app.get('/metrics', async (req, res) => {
  res.set('Content-Type', register.contentType);
  res.end(await register.metrics());
});

app.get('/api/notifications/status', (req, res) => {
  res.json({ status: 'running', consumers: ['order.created', 'inventory.reserved', 'inventory.failed'] });
});

async function bootstrap() {
  await startKafkaConsumer();

  app.listen(config.port, () => {
    logger.info({}, `Notification service listening on port ${config.port}`);
  });
}

bootstrap().catch(err => {
  logger.error({ err: err.message }, 'Fatal startup error');
  process.exit(1);
});
