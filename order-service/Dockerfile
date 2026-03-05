FROM gradle:8.5-jdk21-alpine AS build
WORKDIR /app
COPY build.gradle settings.gradle ./
COPY src ./src
RUN gradle bootJar --no-daemon -x test -q

FROM eclipse-temurin:21-jre-alpine
WORKDIR /app

RUN apk add --no-cache curl && \
    mkdir -p /otel && \
    curl -sL https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v2.1.0/opentelemetry-javaagent.jar \
    -o /otel/opentelemetry-javaagent.jar

COPY --from=build /app/build/libs/order-service-1.0.0.jar app.jar
ENTRYPOINT ["java", "-jar", "app.jar"]
