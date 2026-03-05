package com.shopflow.order.controller;

import com.shopflow.order.kafka.OrderEventProducer;
import com.shopflow.order.model.*;
import com.shopflow.order.service.OrderService;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/orders")
public class OrderController {

    private static final Logger log = LoggerFactory.getLogger(OrderController.class);
    private final OrderService orderService;
    private final OrderEventProducer eventProducer;

    public OrderController(OrderService orderService, OrderEventProducer eventProducer) {
        this.orderService = orderService;
        this.eventProducer = eventProducer;
    }

    @PostMapping
    public ResponseEntity<Order> createOrder(@Valid @RequestBody CreateOrderRequest request) {
        log.info("POST /api/orders — customer={}", request.getCustomerId());
        Order order = orderService.createOrder(request);
        return ResponseEntity.status(201).body(order);
    }

    @GetMapping("/{id}")
    public ResponseEntity<Order> getOrder(@PathVariable String id) {
        return ResponseEntity.ok(orderService.getOrder(id));
    }

    @GetMapping
    public ResponseEntity<List<Order>> getAllOrders() {
        return ResponseEntity.ok(orderService.getAllOrders());
    }

    @PutMapping("/{id}/status")
    public ResponseEntity<Order> updateStatus(
            @PathVariable String id,
            @RequestParam OrderStatus status) {
        return ResponseEntity.ok(orderService.updateStatus(id, status));
    }

    // fires N random messages to batch.test topic in a single request —
    // all kafka.produce spans should fan out under the same HTTP span
    @PostMapping("/batch-test")
    public ResponseEntity<Map<String, Object>> batchTest(
            @RequestParam(defaultValue = "6") int count) {
        log.info("POST /api/orders/batch-test — count={}", count);
        List<Map<String, Object>> events = eventProducer.publishBatchTest(count);
        return ResponseEntity.ok(Map.of(
            "sent", events.size(),
            "topic", "batch.test",
            "events", events
        ));
    }

    @GetMapping("/health")
    public ResponseEntity<String> health() {
        return ResponseEntity.ok("OK");
    }
}
