package com.shopflow.order.kafka;

import com.shopflow.order.model.Order;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.SendResult;
import org.springframework.stereotype.Component;
import java.util.*;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ThreadLocalRandom;

@Component
public class OrderEventProducer {

    private static final Logger log = LoggerFactory.getLogger(OrderEventProducer.class);

    private final KafkaTemplate<String, Map<String, Object>> kafkaTemplate;

    @Value("${kafka.topics.order-created}")
    private String orderCreatedTopic;

    @Value("${kafka.topics.order-updated}")
    private String orderUpdatedTopic;

    @Value("${kafka.topics.batch-test}")
    private String batchTestTopic;

    public OrderEventProducer(KafkaTemplate<String, Map<String, Object>> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    public void publishOrderCreated(Order order) {
        Map<String, Object> event = buildOrderEvent("ORDER_CREATED", order);
        sendEvent(orderCreatedTopic, order.getId(), event);
        log.info("Published ORDER_CREATED event for orderId={}", order.getId());
    }

    public void publishOrderUpdated(Order order) {
        Map<String, Object> event = buildOrderEvent("ORDER_UPDATED", order);
        sendEvent(orderUpdatedTopic, order.getId(), event);
        log.info("Published ORDER_UPDATED event for orderId={}", order.getId());
    }

    private static final String[] BATCH_ACTIONS = {
        "STOCK_CHECK", "PRICE_LOOKUP", "RESTOCK_ALERT",
        "AUDIT_LOG", "CACHE_WARM", "REPORT_GENERATE"
    };

    // sends N messages to batch.test in a loop — all within the same request span
    public List<Map<String, Object>> publishBatchTest(int count) {
        String batchId = UUID.randomUUID().toString().substring(0, 8);
        List<Map<String, Object>> sent = new ArrayList<>();

        for (int i = 0; i < count; i++) {
            String action = BATCH_ACTIONS[ThreadLocalRandom.current().nextInt(BATCH_ACTIONS.length)];
            String productId = "PROD-00" + (ThreadLocalRandom.current().nextInt(5) + 1);

            Map<String, Object> event = new HashMap<>();
            event.put("batchId", batchId);
            event.put("action", action);
            event.put("seq", i);
            event.put("productId", productId);
            event.put("quantity", ThreadLocalRandom.current().nextInt(1, 20));
            event.put("timestamp", System.currentTimeMillis());

            sendEvent(batchTestTopic, batchId + "-" + i, event);
            sent.add(event);
            log.info("Batch[{}] sent seq={} action={} product={}", batchId, i, action, productId);
        }

        return sent;
    }

    private Map<String, Object> buildOrderEvent(String eventType, Order order) {
        Map<String, Object> event = new HashMap<>();
        event.put("eventType", eventType);
        event.put("orderId", order.getId());
        event.put("customerId", order.getCustomerId());
        event.put("productId", order.getProductId());
        event.put("quantity", order.getQuantity());
        event.put("unitPrice", order.getUnitPrice());
        event.put("totalAmount", order.getTotalAmount());
        event.put("status", order.getStatus().name());
        event.put("timestamp", System.currentTimeMillis());
        return event;
    }

    // fire-and-forget with a log callback — we don't block the request thread
    private void sendEvent(String topic, String key, Map<String, Object> payload) {
        CompletableFuture<SendResult<String, Map<String, Object>>> future =
            kafkaTemplate.send(topic, key, payload);

        future.whenComplete((result, ex) -> {
            if (ex != null) {
                log.error("Failed to send event to topic={} key={}", topic, key, ex);
            } else {
                log.debug("Event sent topic={} partition={} offset={}",
                    topic,
                    result.getRecordMetadata().partition(),
                    result.getRecordMetadata().offset());
            }
        });
    }
}
