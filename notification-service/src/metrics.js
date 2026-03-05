const client = require('prom-client');

const register = new client.Registry();
client.collectDefaultMetrics({ register });

const notificationsSent = new client.Counter({
  name: 'notifications_sent_total',
  help: 'Total notifications dispatched',
  labelNames: ['channel', 'event_type', 'status'],
  registers: [register],
});

const notificationLatency = new client.Histogram({
  name: 'notification_processing_duration_seconds',
  help: 'Time to process and send notification',
  labelNames: ['event_type'],
  buckets: [0.005, 0.01, 0.05, 0.1, 0.5, 1],
  registers: [register],
});

const orderStatusUpdates = new client.Counter({
  name: 'order_status_updates_total',
  help: 'Total order status update calls made to order-service',
  labelNames: ['status', 'outcome'],
  registers: [register],
});

module.exports = { register, notificationsSent, notificationLatency, orderStatusUpdates };
