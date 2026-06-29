# Multi-stage Dockerfile for the Ballerina sample app.
#
# Stage 1: build the JAR inside a Ballerina builder image.
# Stage 2: run the JAR on a slim JRE.
#
# Build:   docker build -t autoscale-sample:v1 .
# Run:     docker run --rm -p 9000:9000 -p 9797:9797 autoscale-sample:v1
#
# Ports:
#   9000  -> application HTTP
#   9797  -> Prometheus metrics (/metrics)

# --------- Stage 1: build ---------------------------------------------------
FROM ballerina/ballerina:2201.10.0 AS builder

WORKDIR /home/ballerina/project
# Bring project files in as the 'ballerina' user.
COPY --chown=ballerina:ballerina Ballerina.toml Config.toml service.bal ./

# Produce target/bin/autoscale_test.jar
RUN bal build

# --------- Stage 2: runtime -------------------------------------------------
FROM eclipse-temurin:17-jre-alpine

WORKDIR /app

# Copy the built JAR and the runtime config
COPY --from=builder /home/ballerina/project/target/bin/autoscale_test.jar /app/autoscale_test.jar
COPY --from=builder /home/ballerina/project/Config.toml /app/Config.toml

ENV BAL_CONFIG_FILES=/app/Config.toml
EXPOSE 9000 9797

ENTRYPOINT ["java", "-jar", "/app/autoscale_test.jar"]
