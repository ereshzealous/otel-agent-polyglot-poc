// Loaded before anything else via --require so spans wrap all imports.
'use strict';

const { NodeSDK }                        = require('@opentelemetry/sdk-node');
const { getNodeAutoInstrumentations }    = require('@opentelemetry/auto-instrumentations-node');
const { KafkaJsInstrumentation }         = require('@opentelemetry/instrumentation-kafkajs');
const { OTLPTraceExporter }              = require('@opentelemetry/exporter-trace-otlp-proto');
const { OTLPMetricExporter }             = require('@opentelemetry/exporter-metrics-otlp-proto');
const { PeriodicExportingMetricReader }  = require('@opentelemetry/sdk-metrics');
const { Resource }                       = require('@opentelemetry/resources');
const { SemanticResourceAttributes }     = require('@opentelemetry/semantic-conventions');

const otlpEndpoint = process.env.OTEL_EXPORTER_OTLP_ENDPOINT || 'http://localhost:4318';

const sdk = new NodeSDK({
  resource: new Resource({
    [SemanticResourceAttributes.SERVICE_NAME]:    process.env.OTEL_SERVICE_NAME || 'notification-service',
    [SemanticResourceAttributes.SERVICE_VERSION]: process.env.OTEL_SERVICE_VERSION || '1.0.0',
    'deployment.environment': 'local',
    'team': 'platform',
  }),

  traceExporter: new OTLPTraceExporter({
    url: `${otlpEndpoint}/v1/traces`,
  }),

  metricReader: new PeriodicExportingMetricReader({
    exporter: new OTLPMetricExporter({
      url: `${otlpEndpoint}/v1/metrics`,
    }),
    exportIntervalMillis: 15000,
  }),

  instrumentations: [
    getNodeAutoInstrumentations({
      // fs instrumentation is super noisy — floods traces with file reads
      '@opentelemetry/instrumentation-fs': { enabled: false },
      '@opentelemetry/instrumentation-http': { enabled: true },
      '@opentelemetry/instrumentation-express': { enabled: true },
    }),
    // auto-instrumentations-node doesn't cover kafkajs, so we add it explicitly
    new KafkaJsInstrumentation(),
  ],
});

sdk.start();
console.log('OTel SDK initialized for notification-service');

// flush spans and metrics before the process dies
process.on('SIGTERM', () => sdk.shutdown().then(() => process.exit(0)));
process.on('SIGINT',  () => sdk.shutdown().then(() => process.exit(0)));
