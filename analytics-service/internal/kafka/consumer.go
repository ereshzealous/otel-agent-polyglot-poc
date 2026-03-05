package kafka

import (
	"context"
	"encoding/json"
	"log"
	"os"
	"strings"

	"github.com/IBM/sarama"
	"github.com/shopflow/analytics-service/internal/repository"
	"github.com/shopflow/analytics-service/telemetry"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/propagation"
)

type consumerGroupHandler struct{}

func (h consumerGroupHandler) Setup(_ sarama.ConsumerGroupSession) error   { return nil }
func (h consumerGroupHandler) Cleanup(_ sarama.ConsumerGroupSession) error { return nil }
func (h consumerGroupHandler) ConsumeClaim(session sarama.ConsumerGroupSession, claim sarama.ConsumerGroupClaim) error {
	tracer := telemetry.Tracer("analytics-service/kafka-consumer")

	for msg := range claim.Messages() {
		// grab the trace context that the producer stuffed into kafka headers
		ctx := extractTraceContext(context.Background(), msg.Headers)

		ctx, span := tracer.Start(ctx, "kafka.consume "+msg.Topic)
		span.SetAttributes(
			attribute.String("messaging.system", "kafka"),
			attribute.String("messaging.destination", msg.Topic),
			attribute.String("messaging.operation", "receive"),
			attribute.Int64("messaging.kafka.partition", int64(msg.Partition)),
			attribute.Int64("messaging.kafka.offset", msg.Offset),
		)

		var event repository.OrderEvent
		if err := json.Unmarshal(msg.Value, &event); err != nil {
			log.Printf("Failed to unmarshal event: %v", err)
			span.RecordError(err)
			span.SetStatus(codes.Error, err.Error())
			span.End()
			session.MarkMessage(msg, "")
			continue
		}

		if err := repository.SaveOrderEvent(ctx, event); err != nil {
			log.Printf("Failed to save event to DB: %v", err)
			span.RecordError(err)
			span.SetStatus(codes.Error, err.Error())
		} else {
			log.Printf("Stored event: type=%s orderId=%s", event.EventType, event.OrderID)
			span.SetStatus(codes.Ok, "event stored")
		}

		span.End()
		session.MarkMessage(msg, "")
	}
	return nil
}

func StartConsumer(ctx context.Context) {
	brokers := strings.Split(os.Getenv("KAFKA_BROKERS"), ",")
	if len(brokers) == 0 || brokers[0] == "" {
		brokers = []string{"localhost:9092"}
	}

	config := sarama.NewConfig()
	config.Consumer.Group.Rebalance.Strategy = sarama.NewBalanceStrategyRoundRobin()
	config.Consumer.Offsets.Initial = sarama.OffsetNewest
	config.Version = sarama.V2_8_0_0

	client, err := sarama.NewConsumerGroup(brokers, "analytics-service-group", config)
	if err != nil {
		log.Fatalf("Failed to create Kafka consumer group: %v", err)
	}

	topics := []string{"order.created", "inventory.reserved", "notification.sent", "order.updated"}
	log.Printf("Analytics Kafka consumer started — topics: %v", topics)

	go func() {
		for {
			if err := client.Consume(ctx, topics, consumerGroupHandler{}); err != nil {
				log.Printf("Kafka consumer error: %v", err)
			}
			if ctx.Err() != nil {
				return
			}
		}
	}()
}

func extractTraceContext(ctx context.Context, headers []*sarama.RecordHeader) context.Context {
	carrier := make(map[string]string)
	for _, h := range headers {
		carrier[string(h.Key)] = string(h.Value)
	}
	propagator := otel.GetTextMapPropagator()
	return propagator.Extract(ctx, propagation.MapCarrier(carrier))
}
