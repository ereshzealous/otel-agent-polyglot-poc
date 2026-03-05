module.exports = {
  port:              parseInt(process.env.PORT || '8082'),
  kafkaBrokers:      (process.env.KAFKA_BROKERS || 'localhost:9092').split(','),
  orderServiceUrl:   process.env.ORDER_SERVICE_URL || 'http://localhost:8080',
  kafkaGroupId:      'notification-service-group',
  topics: {
    orderCreated:       'order.created',
    inventoryReserved:  'inventory.reserved',
    inventoryFailed:    'inventory.failed',
    notificationSent:   'notification.sent',
  },
};
