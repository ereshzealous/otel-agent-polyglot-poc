package com.shopflow.order.service;

import com.shopflow.order.kafka.OrderEventProducer;
import com.shopflow.order.model.*;
import com.shopflow.order.repository.OrderRepository;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.List;

@Service
public class OrderService {

    private static final Logger log = LoggerFactory.getLogger(OrderService.class);

    private final OrderRepository orderRepository;
    private final OrderEventProducer eventProducer;

    private final Counter ordersCreatedCounter;
    private final Counter ordersCancelledCounter;
    private final Timer orderCreationTimer;

    public OrderService(OrderRepository orderRepository,
                        OrderEventProducer eventProducer,
                        MeterRegistry meterRegistry) {
        this.orderRepository = orderRepository;
        this.eventProducer    = eventProducer;

        this.ordersCreatedCounter = Counter.builder("shopflow.orders.created.total")
            .description("Total number of orders created")
            .tag("service", "order-service")
            .register(meterRegistry);

        this.ordersCancelledCounter = Counter.builder("shopflow.orders.cancelled.total")
            .description("Total number of orders cancelled")
            .tag("service", "order-service")
            .register(meterRegistry);

        this.orderCreationTimer = Timer.builder("shopflow.order.creation.duration")
            .description("Time taken to create an order")
            .register(meterRegistry);
    }

    // whole creation is wrapped in a timer so we can track p50/p99 in grafana
    @Transactional
    public Order createOrder(CreateOrderRequest req) {
        return orderCreationTimer.record(() -> {
            log.info("Creating order for customer={} product={} qty={}",
                req.getCustomerId(), req.getProductId(), req.getQuantity());

            Order order = new Order();
            order.setCustomerId(req.getCustomerId());
            order.setProductId(req.getProductId());
            order.setQuantity(req.getQuantity());
            order.setUnitPrice(req.getUnitPrice());
            order.setTotalAmount(
                req.getUnitPrice().multiply(BigDecimal.valueOf(req.getQuantity()))
            );
            order.setStatus(OrderStatus.PENDING);

            Order saved = orderRepository.save(order);
            ordersCreatedCounter.increment();

            // kicks off the whole event chain: inventory reservation, notification, analytics
            eventProducer.publishOrderCreated(saved);

            log.info("Order created successfully id={} total={}", saved.getId(), saved.getTotalAmount());
            return saved;
        });
    }

    public Order getOrder(String id) {
        return orderRepository.findById(id)
            .orElseThrow(() -> new OrderNotFoundException("Order not found: " + id));
    }

    public List<Order> getAllOrders() {
        return orderRepository.findAll();
    }

    @Transactional
    public Order updateStatus(String id, OrderStatus newStatus) {
        Order order = getOrder(id);
        log.info("Updating order={} status={} -> {}", id, order.getStatus(), newStatus);

        if (newStatus == OrderStatus.CANCELLED) {
            ordersCancelledCounter.increment();
        }

        order.setStatus(newStatus);
        Order updated = orderRepository.save(order);
        eventProducer.publishOrderUpdated(updated);
        return updated;
    }
}
