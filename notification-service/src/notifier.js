const axios  = require('axios');
const config = require('./config');
const { notificationsSent, orderStatusUpdates } = require('./metrics');
const logger = require('./logger');

async function sendOrderConfirmation(event) {
  const { orderId, customerId, productId, quantity } = event;
  logger.info({ orderId, customerId }, 'Sending order confirmation notification');

  await simulateDelay(50, 150);

  notificationsSent.labels({
    channel: 'email',
    event_type: 'ORDER_CONFIRMED',
    status: 'success',
  }).inc();

  logger.info({ orderId }, 'Order confirmation sent via email');
  return { channel: 'email', recipient: customerId, template: 'order-confirmation' };
}

async function sendInventoryConfirmation(event) {
  const { orderId, customerId, productId, quantity } = event;
  logger.info({ orderId }, 'Sending inventory reserved notification');

  await simulateDelay(30, 100);

  notificationsSent.labels({
    channel: 'push',
    event_type: 'INVENTORY_RESERVED',
    status: 'success',
  }).inc();

  return { channel: 'push', recipient: customerId, template: 'inventory-reserved' };
}

async function sendInventoryFailureAlert(event) {
  const { orderId, reason } = event;
  logger.warn({ orderId, reason }, 'Sending inventory failure notification');

  notificationsSent.labels({
    channel: 'email',
    event_type: 'INVENTORY_FAILED',
    status: 'failure_alert',
  }).inc();

  return { channel: 'email', template: 'inventory-failed' };
}

// calls back into order-service to persist the new status
async function updateOrderStatus(orderId, status) {
  logger.info({ orderId, status }, 'Updating order status via REST');
  try {
    const response = await axios.put(
      `${config.orderServiceUrl}/api/orders/${orderId}/status`,
      null,
      { params: { status }, timeout: 5000 }
    );
    orderStatusUpdates.labels({ status, outcome: 'success' }).inc();
    return response.data;
  } catch (err) {
    orderStatusUpdates.labels({ status, outcome: 'error' }).inc();
    logger.error({ orderId, status, err: err.message }, 'Failed to update order status');
    throw err;
  }
}

// fake latency to mimic a real email/push gateway
function simulateDelay(minMs, maxMs) {
  const ms = Math.floor(Math.random() * (maxMs - minMs + 1)) + minMs;
  return new Promise(resolve => setTimeout(resolve, ms));
}

module.exports = { sendOrderConfirmation, sendInventoryConfirmation, sendInventoryFailureAlert, updateOrderStatus };
