const { Kafka } = require('kafkajs');
const config    = require('../config');
const logger    = require('../logger');
const { notificationLatency } = require('../metrics');
const {
  sendOrderConfirmation,
  sendInventoryConfirmation,
  sendInventoryFailureAlert,
  updateOrderStatus,
} = require('../notifier');

// shared producer — created once when the consumer boots up
let producer;

async function createProducer(kafka) {
  producer = kafka.producer();
  await producer.connect();
  return producer;
}

// tells analytics-service that we actually sent the notification
async function publishNotificationSent(event) {
  await producer.send({
    topic: config.topics.notificationSent,
    messages: [{
      key: event.orderId,
      value: JSON.stringify({
        eventType: 'NOTIFICATION_SENT',
        ...event,
        timestamp: Date.now(),
      }),
    }],
  });
}

async function startKafkaConsumer() {
  const kafka = new Kafka({
    clientId: 'notification-service',
    brokers: config.kafkaBrokers,
    retry: { retries: 8 },
  });

  const consumer = kafka.consumer({ groupId: config.kafkaGroupId });
  await createProducer(kafka);
  await consumer.connect();

  await consumer.subscribe({
    topics: [
      config.topics.orderCreated,
      config.topics.inventoryReserved,
      config.topics.inventoryFailed,
    ],
    fromBeginning: false,
  });

  logger.info({}, 'Kafka consumer started — listening on order.created, inventory.reserved, inventory.failed');

  await consumer.run({
    eachMessage: async ({ topic, partition, message }) => {
      const end = notificationLatency.labels({ event_type: topic }).startTimer();
      try {
        const event = JSON.parse(message.value.toString());
        logger.info({ topic, orderId: event.orderId }, 'Received Kafka event');

        // each topic triggers a different notification and status update on the order
        switch (topic) {
          case config.topics.orderCreated:
            await sendOrderConfirmation(event);
            await updateOrderStatus(event.orderId, 'CONFIRMED');
            await publishNotificationSent({ orderId: event.orderId, type: 'order_confirmation' });
            break;

          case config.topics.inventoryReserved:
            await sendInventoryConfirmation(event);
            await updateOrderStatus(event.orderId, 'INVENTORY_RESERVED');
            await publishNotificationSent({ orderId: event.orderId, type: 'inventory_confirmation' });
            break;

          case config.topics.inventoryFailed:
            await sendInventoryFailureAlert(event);
            await updateOrderStatus(event.orderId, 'CANCELLED');
            await publishNotificationSent({ orderId: event.orderId, type: 'inventory_failure_alert' });
            break;
        }
      } catch (err) {
        logger.error({ topic, err: err.message }, 'Error processing Kafka message');
      } finally {
        end();
      }
    },
  });

  return consumer;
}

module.exports = { startKafkaConsumer };
